"""全链路 Trace（v2.3）：一次 Run 的分段耗时与 token。

表结构刻意扁平：一行 = 一个 span。
  kind: parse | acp_session | thinking | tool | model | approval | acceptance
  started_ms / ended_ms 相对 trace 起点（monotonic），前端按总时长画等比例瀑布。

v2.3：runner 在 ACP 回调里逐事件落 span（每个 thinking 块、每次 tool_call），
不再只有「组装 prompt / 一次 ACP」两根大条。
"""
from __future__ import annotations

import json
import time
import uuid

DDL = """
CREATE TABLE IF NOT EXISTS agent_traces (
  trace_id TEXT PRIMARY KEY,
  run_id TEXT,
  task_id TEXT,
  agent_id TEXT,
  project_id TEXT,
  state TEXT DEFAULT 'running',      -- running | done | failed
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  total_ms INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  audit_ref TEXT
);

CREATE TABLE IF NOT EXISTS trace_spans (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  started_ms INTEGER DEFAULT 0,
  ended_ms INTEGER DEFAULT 0,
  tokens INTEGER DEFAULT 0,
  status TEXT DEFAULT 'ok',
  detail TEXT,
  audit_id TEXT
);
"""

# run_id -> 进程内 trace 句柄（monotonic t0）。跨进程/重启时退回 origin_ms（墙钟）。
_ACTIVE: dict[str, dict] = {}


def ensure(conn) -> None:
    conn.executescript(DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_traces)")}
    if "origin_ms" not in cols:
        conn.execute("ALTER TABLE agent_traces ADD COLUMN origin_ms INTEGER DEFAULT 0")
    conn.commit()


def start(conn, *, run_id: str, task_id: str | None = None, agent_id: str | None = None,
          project_id: str | None = None) -> dict:
    ensure(conn)
    tid = "TR-" + uuid.uuid4().hex[:12]
    origin = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO agent_traces (trace_id, run_id, task_id, agent_id, project_id, origin_ms)"
        " VALUES (?,?,?,?,?,?)", (tid, run_id, task_id, agent_id, project_id, origin))
    conn.commit()
    handle = {"trace_id": tid, "t0": time.monotonic(), "origin_ms": origin, "run_id": run_id}
    _ACTIVE[run_id] = handle
    return handle


def span(conn, trace: dict, *, kind: str, name: str, started_ms: int, ended_ms: int,
         tokens: int = 0, status: str = "ok", detail: str | None = None,
         audit_id: str | None = None) -> str:
    sid = "SP-" + uuid.uuid4().hex[:10]
    conn.execute(
        "INSERT INTO trace_spans (id, trace_id, kind, name, started_ms, ended_ms,"
        " tokens, status, detail, audit_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, trace["trace_id"], kind, name, max(0, started_ms), max(0, ended_ms),
         tokens, status, (detail or "")[:500], audit_id))
    conn.commit()
    return sid


def elapsed_ms(trace: dict) -> int:
    return int((time.monotonic() - trace["t0"]) * 1000)


def now_ms(run_id: str) -> int | None:
    """同一进程内用 monotonic，保证毫秒级不跳变。"""
    handle = _ACTIVE.get(run_id)
    return elapsed_ms(handle) if handle else None


def now_run(conn, run_id: str) -> int:
    """当前时间相对 trace 起点的毫秒数：进程内 monotonic，跨进程退回墙钟。"""
    handle = _ACTIVE.get(run_id)
    if handle is not None:
        return elapsed_ms(handle)
    row = conn.execute("SELECT origin_ms FROM agent_traces WHERE run_id=?"
                       " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
    if row is None or not row["origin_ms"]:
        return 0
    return int(time.time() * 1000) - int(row["origin_ms"])


def record(conn, run_id: str, *, kind: str, name: str, started_ms: int | None = None,
           ended_ms: int | None = None, tokens: int = 0, status: str = "ok",
           detail: str | None = None, audit_id: str | None = None) -> str | None:
    """按 run_id 找当前 trace，落一根 span；没有 trace 就是 no-op。

    跨进程（例如审批在另一个请求线程里恢复）用墙钟 origin_ms 换算，误差 < 1s。
    """
    handle = _ACTIVE.get(run_id)
    if handle is not None:
        trace = handle
        now = elapsed_ms(handle)
    else:
        trace = conn.execute("SELECT * FROM agent_traces WHERE run_id=?"
                             " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        if trace is None:
            return None
        origin = int(trace["origin_ms"] or 0)
        now = (int(time.time() * 1000) - origin) if origin else 0
        trace = {"trace_id": trace["trace_id"]}
    a = now if started_ms is None else int(started_ms)
    b = now if ended_ms is None else int(ended_ms)
    return span(conn, trace, kind=kind, name=name, started_ms=a, ended_ms=max(a, b),
                tokens=tokens, status=status, detail=detail, audit_id=audit_id)


class Recorder:
    """runner_acp 用：开/关 span + 把 usage 增量记到最近关闭的 span 上。"""

    def __init__(self, conn, run_id: str):
        self.conn = conn
        self.run_id = run_id
        self.handle = _ACTIVE.get(run_id)
        self.origin = None
        if self.handle is None:
            # 审批续跑可能在另一个进程/重启之后：用 trace 的墙钟 origin 接着记
            row = conn.execute("SELECT origin_ms FROM agent_traces WHERE run_id=?"
                               " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
            if row is not None and row["origin_ms"]:
                self.origin = int(row["origin_ms"])
        self.enabled = self.handle is not None or self.origin is not None
        self.open: dict[str, dict] = {}
        self.pending_tokens = 0
        self.last_event = self._now()

    def _now(self) -> int:
        if self.handle is not None:
            return elapsed_ms(self.handle)
        return int(time.time() * 1000) - self.origin if self.origin else 0

    def mark_event(self) -> None:
        self.last_event = self._now()

    def open_span(self, key: str, kind: str, name: str, detail: str = "",
                  start: int | None = None) -> None:
        if not self.enabled or key in self.open:
            return
        self.open[key] = {"kind": kind, "name": name,
                          "start": self._now() if start is None else start,
                          "detail": detail}

    def close_span(self, key: str, status: str = "ok", detail: str | None = None) -> None:
        if not self.enabled:
            return
        sp = self.open.pop(key, None)
        if sp is None:
            return
        tokens, self.pending_tokens = self.pending_tokens, 0
        record(self.conn, self.run_id, kind=sp["kind"], name=sp["name"],
               started_ms=sp["start"], ended_ms=max(sp["start"], self._now()),
               tokens=tokens, status=status, detail=detail or sp["detail"])

    def close_kind(self, kind: str, status: str = "ok") -> None:
        for key in [k for k, v in self.open.items() if v["kind"] == kind]:
            self.close_span(key, status=status)

    def add_tokens(self, n: int) -> None:
        if n > 0:
            self.pending_tokens += n

    def flush(self, status: str = "ok") -> None:
        for key in list(self.open):
            self.close_span(key, status=status)


def finish(conn, trace: dict, *, tokens: int, state: str = "done",
           audit_id: str | None = None) -> None:
    conn.execute(
        "UPDATE agent_traces SET state=?, ended_at=CURRENT_TIMESTAMP, total_ms=?,"
        " total_tokens=?, audit_ref=? WHERE trace_id=?",
        (state, elapsed_ms(trace), tokens, audit_id, trace["trace_id"]))
    conn.commit()
    _ACTIVE.pop(trace.get("run_id") or "", None)


def finish_run(conn, run_id: str, *, tokens: int = 0, state: str = "done") -> None:
    """审批续跑结束后收尾：同一进程用 registry，跨进程按 run_id 找最近一条 trace。"""
    handle = _ACTIVE.get(run_id)
    if handle is not None:
        finish(conn, handle, tokens=tokens, state=state)
        return
    row = conn.execute("SELECT * FROM agent_traces WHERE run_id=?"
                       " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
    if row is not None:
        origin = int(row["origin_ms"] or 0)
        total_ms = max(0, int(time.time() * 1000) - origin) if origin else 0
        conn.execute("UPDATE agent_traces SET state=?, ended_at=CURRENT_TIMESTAMP,"
                     " total_ms=?, total_tokens=? WHERE trace_id=?",
                     (state, total_ms, tokens, row["trace_id"]))
        conn.commit()


def exists(conn, run_id: str) -> bool:
    ensure(conn)
    return conn.execute("SELECT 1 FROM agent_traces WHERE run_id=? LIMIT 1",
                        (run_id,)).fetchone() is not None


def load(conn, run_id: str) -> dict | None:
    ensure(conn)
    t = conn.execute("SELECT * FROM agent_traces WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                     (run_id,)).fetchone()
    if t is None:
        return None
    spans = [dict(r) for r in conn.execute(
        "SELECT * FROM trace_spans WHERE trace_id=? ORDER BY started_ms, rowid",
        (t["trace_id"],))]
    return {"trace": dict(t), "spans": spans}


def by_run(conn, run_id: str) -> list[dict]:
    ensure(conn)
    rows = conn.execute("SELECT * FROM agent_traces WHERE run_id=?", (run_id,)).fetchall()
    return [dict(r) for r in rows]


def audit_for_span(conn, span_id: str) -> list[dict]:
    row = conn.execute("SELECT audit_id, trace_id FROM trace_spans WHERE id=?", (span_id,)).fetchone()
    if row is None:
        return []
    if row["audit_id"]:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ai_audit_logs WHERE id=?", (row["audit_id"],))]
    t = conn.execute("SELECT run_id FROM agent_traces WHERE trace_id=?",
                     (row["trace_id"],)).fetchone()
    if not t:
        return []
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ai_audit_logs WHERE run_id=? ORDER BY rowid", (t["run_id"],))]
