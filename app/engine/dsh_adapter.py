"""Runner adapter: the shell owns state, DSH is a child process.

Transports, one contract:
  * `dsh`       — `dsh --profile headless --json -`, task on stdin, JSONL on stdout
  * `acp`       — `dsh --profile acp`, JSON-RPC over stdio, live `usage_update`
  * `mock` / `mock_acp` — same shapes without a model call (tests, zero cost)

Budget semantics (identical for every transport):
  * before the run: reserve the estimate; a denial must not create a child process
  * during the run: usage is only observable *after* a model call, so the gate is
    "cancel the turn the moment observed usage crosses the reservation" — every
    later call in that turn is stopped, the one already in flight cannot be
    un-sent. Only a DSH plugin on the `agent/request` waterfall could prevent
    that one (see DESIGN_GAPS.md §13).
  * after the run: settle with the observed usage
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import threading
import time

from fastapi import HTTPException

from ..api import verbs
from .. import locks
from ..audit import write_audit
from ..db import connect
from ..models import AttachArtifactIn, EmitEventIn, HeartbeatIn, ReserveBudgetIn

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOCK_AGENT = ROOT / "tools" / "mock_agent.py"
DSH_CLI = os.environ.get(
    "DSH_CLI",
    str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness"
        / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"),
)

WRITE_TOOLS = {"write", "edit", "write_file", "create_file", "apply_patch", "patch"}


def kill_tree(pid: int) -> None:
    """Kill the whole tree: the CLI is a .cmd wrapper, so killing it leaves the
    electron child alive (and holding session write handles)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        import signal as _signal
        try:
            os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass


def workspace() -> pathlib.Path:
    """Workspace root. Tasks never share a directory below this point.

    默认放在 **用户自己的 LOCALAPPDATA** 下：仓库目录的 ACL 属于 Administrators，
    在里面打 Low 标签要 WRITE_OWNER -> 触发 UAC；用户目录是天生的 owner，静默可写。
    """
    default = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "oaagent-m0" / "workspace"
    p = pathlib.Path(os.environ.get("M0_WORKSPACE") or default)
    p.mkdir(parents=True, exist_ok=True)
    return p


def shared_root() -> pathlib.Path:
    """集团共享资源根（RES-DEV / RES-FIN 等真实目录）。"""
    p = workspace() / "resources"
    p.mkdir(parents=True, exist_ok=True)
    return p


def mount_shared(ws: pathlib.Path) -> None:
    """把共享资源根挂成任务工作区的 `shared/` junction。

    Agent 用相对路径 `shared/RES-FIN/x.md` 写文件时，DSH 的 workspace-write 与
    Low 沙箱都放行；真正的边界由 rbac.gate_tool_call 在 tool_call 通知时判定。
    realpath 会把 junction 还原成真实目录，所以审批单上的资源命中是准的。
    """
    link = ws / "shared"
    if link.exists():
        return
    try:
        target = shared_root()
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, check=False, timeout=10)
        else:
            link.symlink_to(target, target_is_directory=True)
    except Exception:                        # noqa: BLE001 - 没有 shared 也能跑
        pass


def task_workspace(task_id: str, actor: str | None = None) -> pathlib.Path:
    """workspace/<agent>/<task>/ — one directory per agent, one per task."""
    p = workspace() / (actor or "system") / task_id
    p.mkdir(parents=True, exist_ok=True)
    mount_shared(p)
    return p



SANDBOX_LAUNCHER = ROOT / "tools" / "sandbox_win.py"


def sandbox_enabled() -> bool:
    return os.name == "nt" and (os.environ.get("M0_SANDBOX", "lowil").lower() == "lowil")


def sandbox_cmd(cwd: pathlib.Path, cmd: list[str]) -> list[str]:
    """Wrap a command so it runs at Low integrity, unable to write outside cwd.

    打标签在**父进程**里做一次（workspace 根 + 本次任务目录）；子目录靠
    (OI)(CI) 继承。全程 Win32 原生 API / 静默 icacls，不触发 UAC。
    """
    if not sandbox_enabled():
        return cmd
    try:
        # 只给任务目录打 Low：工作区根必须保持 Medium，否则 Low 子进程可以往上写
        # （test_m0_20 的逃逸探针就是往 ../escape.txt 写）
        _label_low(pathlib.Path(cwd))
    except Exception as exc:                       # noqa: BLE001
        # 标不上 Low 就别假装沙箱生效：让调用方看到明确的失败
        raise RuntimeError(f"sandbox unavailable: {exc}") from exc
    return [sys.executable, str(SANDBOX_LAUNCHER), "--cwd", str(cwd), "--", *cmd]


def _label_low(path: pathlib.Path) -> None:
    """调用 tools/sandbox_win.py 里的打标签逻辑（无 UAC）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sandbox_win", SANDBOX_LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                   # type: ignore[union-attr]
    mod.set_dir_low(path)


def _log_path(run_id: str) -> pathlib.Path:
    d = ROOT / "docs" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{run_id}.jsonl"


def parse_stream(text: str) -> dict:
    session_id, usage, final, events, writes = None, 0, None, 0, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        kind = ev.get("type")
        if kind == "session":
            session_id = ev.get("sessionId") or session_id
        elif kind == "tool_call":
            tool = (ev.get("tool") or "").lower()
            inp = ev.get("input") or {}
            fp = inp.get("file_path") or inp.get("path")
            if fp and tool in WRITE_TOOLS:
                writes.append(fp)
        elif kind == "status" and ev.get("phase") == "step_end":
            u = ev.get("usage") or {}
            usage = max(usage, int(u.get("totalTokens") or 0))
        elif kind == "final":
            final = ev.get("text")
    return {"session_id": session_id, "usage_tokens": usage, "final": final,
            "event_count": events, "writes": writes}


# ------------------------------------------------------------------ shared steps
def start_heartbeat(run_id: str, interval: int, stop: threading.Event):
    if interval <= 0:
        return None

    def beat() -> None:
        seq = 0
        while True:
            seq += 1
            try:
                conn = connect()
                try:
                    verbs.do_report_heartbeat(
                        conn, HeartbeatIn(run_id=run_id, hb_seq=seq, estimated_next_cost=None))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass
            if stop.wait(interval):
                break

    t = threading.Thread(target=beat, name=f"hb-{run_id}", daemon=True)
    t.start()
    return t


def session_baseline(conn: sqlite3.Connection, session_id: str | None, run_id: str) -> int:
    """usage_update / totalTokens are cumulative per SESSION, not per turn.

    A resumed session already reports the whole conversation's tokens, so the gate
    and the settlement both need the baseline this session had before this run.
    """
    if not session_id:
        return 0
    row = conn.execute(
        "SELECT COALESCE(MAX(usage_tokens), 0) FROM task_runs WHERE session_id = ? AND id != ?",
        (session_id, run_id)).fetchone()
    return int(row[0] or 0)


def budget_gate(conn: sqlite3.Connection, run_id: str, est_tokens: int) -> dict | None:
    """None = approved; otherwise the denial body."""
    resp = verbs.do_reserve_budget(
        conn, ReserveBudgetIn(run_id=run_id, call_seq=1, estimated_tokens=est_tokens))
    conn.commit()
    return None if resp["result"] == "approved" else resp


def fail_without_spawn(conn: sqlite3.Connection, run_id: str, task_id: str,
                       reason: str, budget: dict | None = None) -> dict:
    try:
        verbs.do_emit_event(conn, EmitEventIn(
            run_id=run_id, evt_seq=1, event_type="run_failed", payload={"reason": reason}))
        conn.commit()
    except HTTPException:
        conn.rollback()
    conn.execute(
        "UPDATE task_runs SET state='failed', ended_at=CURRENT_TIMESTAMP, exit_code=-2 WHERE id=?",
        (run_id,))
    conn.commit()
    return {"run_id": run_id, "spawned": False, "result": "denied", "reason": reason,
            "budget": budget}


def attach_files(conn: sqlite3.Connection, run_id: str, files: list[str]) -> list[dict]:
    out, seq = [], 0
    for fp in files:
        seq += 1
        try:
            r = verbs.do_attach_artifact(
                conn, AttachArtifactIn(run_id=run_id, art_seq=seq, file_path=str(fp)))
            out.append({"file_path": str(fp), **r})
        except HTTPException:
            continue
    return out


def settle_budget(conn: sqlite3.Connection, task_id: str, used: int, est_tokens: int) -> None:
    conn.execute(
        """UPDATE budgets SET used_tokens = used_tokens + ?,
             reserved_tokens = MAX(0, reserved_tokens - ?) WHERE task_id = ?""",
        (used, est_tokens, task_id))
    # and the entity pool the task was carved out of
    conn.execute(
        """UPDATE budgets SET used_tokens = used_tokens + ?
           WHERE id = (SELECT b2.id FROM budgets b2 JOIN tasks t ON t.legal_entity_id = b2.legal_entity_id
                       WHERE t.id = ? AND b2.task_id IS NULL AND b2.identity_id IS NULL
                         AND b2.limit_tokens > 0
                       ORDER BY b2.rowid DESC LIMIT 1)""",
        (used, task_id))


def park_run(conn: sqlite3.Connection, *, run_id: str, task_id: str, entity: str,
             runner: str, approval_id: str | None, session_id: str | None, used: int,
             event_count: int, final: str | None, usage_total: int | None = None,
             baseline: int = 0, extra: dict | None = None) -> dict:
    """挂起而不是失败：任务状态保持 in_progress，Run 等审批后原会话续跑。

    不发 run_failed —— 那会把任务推进 failed，审批通过后只能「重跑」而不能
    「接着跑」。挂起是可逆状态，审计里 action=run_parked。
    """
    conn.execute(
        """UPDATE task_runs SET exit_code = 9, ended_at = CURRENT_TIMESTAMP,
             state = 'awaiting_approval', session_id = ?, usage_tokens = ?,
             usage_baseline = ?, approval_id = ? WHERE id = ?""",
        (session_id, usage_total if usage_total is not None else used, baseline,
         approval_id, run_id))
    if approval_id:
        conn.execute("UPDATE tool_approvals SET parked_at_ms = ? WHERE id = ?",
                     (int(extra.get("parked_ms") or 0) if extra else 0, approval_id))
    write_audit(
        conn, legal_entity_id=entity, verb="run_executed", task_id=task_id, run_id=run_id,
        action="run_parked", target=run_id,
        payload={"runner": runner, "approval_id": approval_id, "session_id": session_id,
                 "usage_tokens": used, "resumable": bool(session_id)},
        response={"state": "awaiting_approval", "event_count": event_count,
                  **(extra or {})}, is_deterministic=0)
    conn.commit()
    return {"run_id": run_id, "spawned": True, "runner": runner, "exit_code": 9,
            "task_state": "in_progress", "state": "awaiting_approval",
            "approval_id": approval_id, "session_id": session_id,
            "usage_tokens": used, "usage_total": usage_total if usage_total is not None else used,
            "usage_baseline": baseline, "event_count": event_count, "artifacts": [],
            "final": final, "resumable": bool(session_id), **(extra or {})}


def finish(conn: sqlite3.Connection, *, run_id: str, task_id: str, entity: str, runner: str,
           ok: bool, reason: str, exit_code: int, session_id: str | None, used: int,
           event_count: int, artifacts: list[dict], final: str | None,
           timed_out: bool = False, extra: dict | None = None,
           usage_total: int | None = None, baseline: int = 0) -> dict:
    conn.execute(
        """UPDATE task_runs SET exit_code = ?, ended_at = CURRENT_TIMESTAMP, session_id = ?,
             usage_tokens = ?, usage_baseline = ? WHERE id = ?""",
        (exit_code, session_id, usage_total if usage_total is not None else used, baseline, run_id))
    event = "run_succeeded" if ok else "run_failed"
    try:
        emitted = verbs.do_emit_event(conn, EmitEventIn(
            run_id=run_id, evt_seq=3, event_type=event,
            payload={"exit_code": exit_code, "reason": reason}))
        new_state = emitted["new_task_state"]
    except HTTPException as exc:
        new_state = None
        write_audit(conn, legal_entity_id=entity, verb="run_executed", task_id=task_id,
                    run_id=run_id, action="terminal_event_rejected", target=run_id,
                    payload={"event": event, "detail": exc.detail}, response={"ok": False})
    conn.execute("UPDATE task_runs SET state = ? WHERE id = ?",
                 ("succeeded" if ok else ("timeout" if timed_out else "failed"), run_id))
    write_audit(
        conn, legal_entity_id=entity, verb="run_executed", task_id=task_id, run_id=run_id,
        action="run_executed", target=run_id,
        payload={"runner": runner, "exit_code": exit_code, "timed_out": timed_out, "event": event},
        response={"task_state": new_state, "artifacts": len(artifacts), "usage_tokens": used,
                  "event_count": event_count, **(extra or {})},
        is_deterministic=0)
    conn.commit()
    try:
        from .. import trace as _trace
        _trace.finish_run(conn, run_id,
                          tokens=usage_total if usage_total is not None else used,
                          state="done" if ok else "failed")
    except Exception:                    # noqa: BLE001 - trace 失败不影响业务
        pass
    return {"run_id": run_id, "spawned": True, "runner": runner, "exit_code": exit_code,
            "timed_out": timed_out, "task_state": new_state, "session_id": session_id,
            "usage_tokens": used, "usage_total": usage_total if usage_total is not None else used,
            "usage_baseline": baseline, "event_count": event_count, "artifacts": artifacts,
            "final": final, "log": str(_log_path(run_id)), **(extra or {})}


# ------------------------------------------------------------------ entry point
ARTIFACT_BY_PACK = {
    "content-v1": ("article.md", "正文"),
    "code-v1": ("solution.py", "可运行的 Python 脚本"),
    "code-local-v1": ("solution.py", "可运行的 Python 脚本"),
    "finance-v1": ("audit.md", "费用审核单"),
    "review-v1": ("review.md", "评审报告"),
    "web-v1": ("index.html", "一个可直接在浏览器打开的完整 HTML 落地页（含内联样式与文案）"),
}


def default_prompt(task) -> str:
    """A bare title is not an instruction: tell the agent to leave a file behind.

    Without an artifact the L2 layer has nothing to judge, and acceptance rejects
    a perfectly good answer that only lived in the chat transcript.
    """
    pack = task["pack_id"] if "pack_id" in task.keys() else None
    name, what = ARTIFACT_BY_PACK.get(pack or "", ("article.md", "正文"))
    title = task["title"] or "完成这个任务"
    return (f"任务：{title}" + "\n"
            f"请在当前目录产出文件 {name}，里面是{what}。"
            f"不要只在回复里写内容，必须落盘成文件。完成后只回复 DONE。")


def execute_run(conn: sqlite3.Connection, run_id: str, *, prompt: str | None = None,
                runner: str | None = None, resume_session: str | None = None,
                budget_tokens: int | None = None) -> dict:
    run = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise HTTPException(404, detail={"code": "RunNotFound", "run_id": run_id})
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    task_id, entity = task["id"], run["legal_entity_id"]
    actor = (run["actor_identity"] if "actor_identity" in run.keys() else None) or "system"
    prompt = prompt or default_prompt(task)
    runner = (runner or os.environ.get("RUNNER") or "mock").lower()
    try:
        conn.execute("UPDATE task_runs SET prompt_text=? WHERE id=?",
                     ((prompt or "")[:20000], run_id))
        conn.commit()
    except sqlite3.OperationalError:      # 老库还没迁移时不让 Run 失败
        conn.rollback()

    est_tokens = (budget_tokens if budget_tokens is not None
                  else int(os.environ.get("M0_BUDGET_EST_TOKENS", "20000")))
    timeout = int(os.environ.get("M0_RUN_TIMEOUT", "600"))
    hb_interval = int(os.environ.get("M0_HEARTBEAT_SECONDS", "15"))

    task_resource = f"task:{task_id}"
    session_resource = f"session:{resume_session}" if resume_session else None
    try:
        locks.acquire(conn, task_resource, run_id, entity=entity, task_id=task_id)
        if session_resource:
            locks.acquire(conn, session_resource, run_id, entity=entity, task_id=task_id)
    except locks.Locked as exc:
        raise locks.conflict(exc.resource, exc.holder)

    try:
        return _execute_locked(conn, run_id=run_id, task_id=task_id, entity=entity,
                               actor=actor, prompt=prompt, runner=runner,
                               resume_session=resume_session, est_tokens=est_tokens,
                               timeout=timeout, hb_interval=hb_interval)
    finally:
        locks.release(conn, task_resource, run_id)
        if session_resource:
            locks.release(conn, session_resource, run_id)


def _execute_locked(conn: sqlite3.Connection, *, run_id: str, task_id: str, entity: str,
                    actor: str, prompt: str, runner: str, resume_session: str | None,
                    est_tokens: int, timeout: int, hb_interval: int) -> dict:
    denial = budget_gate(conn, run_id, est_tokens)
    if denial:
        return fail_without_spawn(conn, run_id, task_id, "budget_exhausted", denial)

    # 每次 Run 都有 trace（调用方未显式 start 时在这里补）；挂起时不 finish，
    # 续跑阶段继续往同一条 trace 追加 span。
    from .. import trace as _trace
    if not _trace.exists(conn, run_id):
        _trace.start(conn, run_id=run_id, task_id=task_id)

    # A fresh run moves assigned -> in_progress here. A retry already sits in
    # in_progress (rejected/failed -> retry), so emitting run_started again would
    # be an illegal transition and would abort the retry.
    task_state = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
    if task_state == "assigned":
        verbs.do_emit_event(conn, EmitEventIn(
            run_id=run_id, evt_seq=2, event_type="run_started",
            payload={"runner": runner, "resumed": bool(resume_session)}))
        conn.commit()
    else:
        write_audit(conn, legal_entity_id=entity, verb="run_started_skipped", task_id=task_id,
                    run_id=run_id, action="run_started_skipped", target=run_id,
                    payload={"task_state": task_state, "resumed": bool(resume_session)},
                    response={"ok": True})
        conn.commit()

    if runner in ("acp", "mock_acp"):
        from .runner_acp import execute_acp
        return execute_acp(conn, run_id, task_id=task_id, entity=entity, actor=actor,
                           prompt=prompt, runner=runner, resume_session=resume_session,
                           est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval)

    return _execute_headless(conn, run_id, task_id=task_id, entity=entity, actor=actor,
                             prompt=prompt, runner=runner, resume_session=resume_session,
                             est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval)


def _execute_headless(conn: sqlite3.Connection, run_id: str, *, task_id: str, entity: str,
                      actor: str, prompt: str, runner: str, resume_session: str | None,
                      est_tokens: int, timeout: int, hb_interval: int) -> dict:
    ws, started = task_workspace(task_id, actor), time.time()
    # Marks "a child process really was created" for the budget-denial test.
    # It has to live inside the task dir: the sandbox denies writes elsewhere.
    marker = ws / f".spawned-{run_id}.txt"
    from . import sandbox_env
    env = sandbox_env.build_env(ws)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "MOCK_MARKER": str(marker), "MOCK_WORKSPACE": str(ws)})

    if runner == "dsh":
        cmd = [DSH_CLI, "--profile", "headless", "--json", "-"]
        if resume_session:
            cmd = [DSH_CLI, "--profile", "headless", "--json",
                   "--session-id", resume_session, "-"]
    else:
        cmd = [sys.executable, str(MOCK_AGENT)]
    cmd = sandbox_cmd(ws, cmd)

    stop = threading.Event()
    hb = start_heartbeat(run_id, hb_interval, stop)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=str(ws), env=env, shell=False)
    conn.execute("UPDATE task_runs SET pid = ?, runner = ?, state = 'running' WHERE id = ?",
                 (proc.pid, runner, run_id))
    conn.commit()

    timed_out = False
    try:
        out, err = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc.pid)
        out, err = proc.communicate()
    finally:
        stop.set()
        if hb:
            hb.join(timeout=2)

    exit_code = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    _log_path(run_id).write_text((out or "") + "\n--- STDERR ---\n" + (err or ""), encoding="utf-8")
    parsed = parse_stream(out or "")

    written = [fp for fp in parsed["writes"] if pathlib.Path(fp).is_file()]
    if not written and exit_code == 0:
        written = _scan_workspace(started, task_id, actor)
    artifacts = attach_files(conn, run_id, written)
    baseline = session_baseline(conn, parsed["session_id"] or resume_session, run_id)
    total = parsed["usage_tokens"]
    delta = max(0, total - baseline)
    settle_budget(conn, task_id, delta, est_tokens)

    ok = (exit_code == 0) and not timed_out
    reason = "timeout" if timed_out else ("exit_nonzero" if exit_code != 0 else "completed")
    return finish(conn, run_id=run_id, task_id=task_id, entity=entity, runner=runner, ok=ok,
                  reason=reason, exit_code=exit_code, session_id=parsed["session_id"],
                  used=delta, event_count=parsed["event_count"],
                  artifacts=artifacts, final=parsed["final"], timed_out=timed_out,
                  usage_total=total, baseline=baseline,
                  extra={"stderr_tail": (err or "")[-300:]})


NOISE_NAMES = {"cordis.yml", "cordis.patch.yml", "package.json", "pnpm-workspace.yaml",
               "pnpm-lock.yaml"}


def _is_noise(p: pathlib.Path, ws: pathlib.Path) -> bool:
    rel = p.relative_to(ws)
    if any(part.startswith(".") for part in rel.parts):
        return True
    return p.name in NOISE_NAMES


def _scan_workspace(since: float, task_id: str, actor: str | None = None) -> list[str]:
    ws, found = task_workspace(task_id, actor), []
    for p in ws.rglob("*"):
        # shared/ 是 junction：里面的财务/研发文件属于资源库，不是本次任务的产物
        try:
            if p.relative_to(ws).parts[0] == "shared":
                continue
        except (ValueError, IndexError):
            continue
        if p.is_file() and p.stat().st_mtime >= since - 2 and p.stat().st_size > 0:
            if _is_noise(p, ws):
                continue
            found.append((p.stat().st_mtime, str(p)))
    found.sort(reverse=True)
    return [f for _, f in found[:10]]
