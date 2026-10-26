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
import re
import sqlite3
import stat
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

# Only terminate process handles created by this server. A persisted PID can be
# stale after a restart, and must never be used to kill an unrelated process.
_RUN_PROCESSES: dict[str, subprocess.Popen] = {}
_PROCESS_LOCK = threading.Lock()


def register_process(run_id: str, process: subprocess.Popen) -> None:
    with _PROCESS_LOCK:
        _RUN_PROCESSES[run_id] = process


def unregister_process(run_id: str) -> None:
    with _PROCESS_LOCK:
        _RUN_PROCESSES.pop(run_id, None)


def stop_process(run_id: str, *, expected_pid: int | None = None) -> bool:
    with _PROCESS_LOCK:
        process = _RUN_PROCESSES.get(run_id)
        if process is None:
            if expected_pid:
                raise RuntimeError("runner handle unavailable; refusing to terminate a stored PID")
            return False
        if expected_pid and process.pid != expected_pid:
            raise RuntimeError("runner process identity changed")
        if process.poll() is not None:
            return False
        kill_tree(process.pid)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("runner process did not stop") from exc
        return True


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
    from ..config import get_workspace_root
    return get_workspace_root()


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
    # A stop request can race with the worker's final result. Preserve the
    # explicit user decision even if the subprocess had already returned 0.
    stopped = conn.execute(
        "SELECT t.state AS task_state, r.state AS run_state, r.exit_code"
        " FROM task_runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
        (run_id,)).fetchone()
    stopped_state = (stopped["task_state"] if stopped and
                     stopped["run_state"] in ("cancelled", "failed") and
                     stopped["exit_code"] == -9 else None)
    if stopped_state:
        ok, exit_code, reason = False, -9, "user_stopped"
    conn.execute(
        """UPDATE task_runs SET exit_code = ?, ended_at = CURRENT_TIMESTAMP, session_id = ?,
             usage_tokens = ?, usage_baseline = ?, error = ? WHERE id = ?""",
        (exit_code, session_id, usage_total if usage_total is not None else used, baseline,
         None if ok else reason[:500], run_id))
    event = "run_succeeded" if ok else "run_failed"
    try:
        if stopped_state:
            new_state = stopped_state
        else:
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
                 ("cancelled" if stopped_state == "cancelled" else
                  ("succeeded" if ok else ("timeout" if timed_out else "failed")), run_id))
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
                          state="cancelled" if stopped_state == "cancelled" else
                          ("done" if ok else "failed"))
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


def task_requirement(task, *, conn: sqlite3.Connection | None = None,
                     request_text: str | None = None) -> str:
    """A task title is a display label; its creation audit keeps the full request."""
    if request_text and request_text.strip():
        return request_text.strip()
    task = dict(task)
    if conn is not None and task.get("id"):
        row = conn.execute(
            "SELECT payload FROM ai_audit_logs WHERE task_id=? AND verb='task_created'"
            " AND action='create_task' ORDER BY rowid LIMIT 1", (task["id"],)).fetchone()
        if row:
            try:
                text = json.loads(row["payload"] or "{}").get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
            except (ValueError, AttributeError, TypeError):
                pass
    return task.get("title") or "完成这个任务"


def _requested_main_file(text: str, suffix: str) -> str | None:
    name = r"([A-Za-z_][A-Za-z0-9_.-]*\." + suffix + r")(?![A-Za-z0-9_])"
    # Prefer an explicit output instruction over a referenced input filename.
    for prefix in (r"(?:保存为|命名为|文件名(?:为|是)?|输出为|产出|生成|创建|编写|实现|交付)\s*[`\"'“《]?",
                   r"(?:脚本|程序|文件|页面)\s*[`\"'“《]?",
                   r"(?<![A-Za-z0-9_/\\.-])"):
        found = re.search(prefix + name, text, re.I)
        if found:
            return found.group(1)
    return None


def default_prompt(task, *, conn: sqlite3.Connection | None = None,
                   request_text: str | None = None) -> str:
    """A bare title is not an instruction: tell the agent to leave a file behind.

    Without an artifact the L2 layer has nothing to judge, and acceptance rejects
    a perfectly good answer that only lived in the chat transcript.
    """
    pack = task["pack_id"] if "pack_id" in task.keys() else None
    name, what = ARTIFACT_BY_PACK.get(pack or "", ("article.md", "正文"))
    requirement = task_requirement(task, conn=conn, request_text=request_text)
    if pack in ("code-v1", "code-local-v1"):
        name = _requested_main_file(requirement, "py") or name
    elif pack == "web-v1":
        name = _requested_main_file(requirement, "html?") or name
    return (f"完整任务要求：\n{requirement}\n\n"
            f"请在当前目录产出主文件 {name}，内容是{what}。"
            "同时交付需求中指定的全部附属文件、数据和说明。"
            "需求中要求的执行、测试和断言必须实际运行并检查结果。"
            "所有产物必须落盘。完成后只回复 DONE。")


class UserStopped(RuntimeError):
    """The caller stopped this run while its worker was preparing to launch."""

    def __init__(self, *, spawned: bool = False):
        super().__init__("run was stopped before model prompt")
        self.spawned = spawned


def _execution_state(conn: sqlite3.Connection, run_id: str):
    return conn.execute(
        "SELECT r.*,t.state AS task_state FROM task_runs r JOIN tasks t ON t.id=r.task_id"
        " WHERE r.id=?", (run_id,)).fetchone()


def _assert_startable(conn: sqlite3.Connection, run_id: str, resume_session: str | None) -> None:
    run = _execution_state(conn, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "RunNotFound", "run_id": run_id})
    resumable = (run["state"] == "awaiting_approval" and resume_session
                 and run["session_id"] == resume_session)
    if run["task_state"] not in ("assigned", "in_progress") or not (run["state"] == "spawned" or resumable):
        raise HTTPException(409, detail={"code": "RunNotRunnable", "run_id": run_id,
                                         "state": run["state"], "task_state": run["task_state"]})


def ensure_not_stopped(conn: sqlite3.Connection, run_id: str) -> None:
    run = _execution_state(conn, run_id)
    if run and (run["state"] == "cancelled" or run["task_state"] == "cancelled"
                or (run["state"] == "failed" and run["exit_code"] == -9)):
        with _PROCESS_LOCK:
            spawned = run_id in _RUN_PROCESSES
        stop_process(run_id)  # owned handle only; never kill a persisted PID
        raise UserStopped(spawned=spawned)


def _stopped_before_prompt(conn: sqlite3.Connection, run_id: str, *, runner: str,
                           reserved: int, spawned: bool = False) -> dict:
    run = _execution_state(conn, run_id)
    if reserved:
        settle_budget(conn, run["task_id"], 0, reserved)
    state = "cancelled" if run["state"] == "cancelled" or run["task_state"] == "cancelled" else "failed"
    conn.execute("UPDATE task_runs SET state=?,exit_code=-9,ended_at=COALESCE(ended_at,CURRENT_TIMESTAMP)"
                 " WHERE id=?", (state, run_id))
    write_audit(conn, legal_entity_id=run["legal_entity_id"], task_id=run["task_id"], run_id=run_id,
                verb="run_executed", action="run_start_stopped", target=run_id,
                payload={"reason": "user_stopped"}, response={"state": state, "usage_tokens": 0})
    conn.commit()
    from .. import trace as _trace
    if _trace.exists(conn, run_id):
        _trace.finish_run(conn, run_id, tokens=int(run["usage_tokens"] or 0), state=state)
    return {"run_id": run_id, "spawned": spawned, "runner": runner, "exit_code": -9,
            "state": state, "task_state": run["task_state"], "session_id": run["session_id"],
            "usage_tokens": 0, "usage_total": int(run["usage_tokens"] or 0),
            "usage_baseline": int(run["usage_baseline"] or 0), "event_count": 0,
            "artifacts": [], "final": None, "reason": "user_stopped", "resumable": False}


def execute_run(conn: sqlite3.Connection, run_id: str, *, prompt: str | None = None,
                runner: str | None = None, resume_session: str | None = None,
                budget_tokens: int | None = None,
                pause_at_tokens: int | None = None) -> dict:
    run = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise HTTPException(404, detail={"code": "RunNotFound", "run_id": run_id})
    _assert_startable(conn, run_id, resume_session)
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    task_id, entity = task["id"], run["legal_entity_id"]
    actor = (run["actor_identity"] if "actor_identity" in run.keys() else None) or "system"
    prompt = prompt or default_prompt(task, conn=conn)
    # Every execution path, including approval resume, uses authoritative guidance.
    from .. import role_alignment, experience
    prompt = role_alignment.inject(conn, prompt, task["owner_employee_id"],
                                   task_id=task_id, run_id=run_id)
    red_lines = experience.red_lines(conn, tenant_id=task["tenant_id"],
                                     entity=task["legal_entity_id"])
    if red_lines:
        prompt += "\n\n【公司红线（已审核，必须遵守）】\n" + "\n".join(
            f"- {rule['title']}：{rule['content']}" for rule in red_lines)
        write_audit(conn, legal_entity_id=entity, verb="memory_read",
                    task_id=task_id, run_id=run_id, layer="system",
                    action="red_lines_injected", target=run_id,
                    payload={"ids": [rule["id"] for rule in red_lines]},
                    response={"count": len(red_lines)})
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
                               timeout=timeout, hb_interval=hb_interval,
                               pause_at_tokens=pause_at_tokens)
    finally:
        locks.release(conn, task_resource, run_id)
        if session_resource:
            locks.release(conn, session_resource, run_id)


def _execute_locked(conn: sqlite3.Connection, *, run_id: str, task_id: str, entity: str,
                    actor: str, prompt: str, runner: str, resume_session: str | None,
                    est_tokens: int, timeout: int, hb_interval: int,
                    pause_at_tokens: int | None = None) -> dict:
    _assert_startable(conn, run_id, resume_session)
    try:
        ensure_not_stopped(conn, run_id)
    except UserStopped:
        return _stopped_before_prompt(conn, run_id, runner=runner, reserved=0)
    denial = budget_gate(conn, run_id, est_tokens)
    try:
        ensure_not_stopped(conn, run_id)
    except UserStopped:
        return _stopped_before_prompt(conn, run_id, runner=runner, reserved=est_tokens if denial is None else 0)
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
        try:
            verbs.do_emit_event(conn, EmitEventIn(
                run_id=run_id, evt_seq=2, event_type="run_started",
                payload={"runner": runner, "resumed": bool(resume_session)}))
        except HTTPException:
            try:
                ensure_not_stopped(conn, run_id)
            except UserStopped as exc:
                return _stopped_before_prompt(conn, run_id, runner=runner,
                                               reserved=est_tokens, spawned=exc.spawned)
            raise
        conn.commit()
    else:
        write_audit(conn, legal_entity_id=entity, verb="run_started_skipped", task_id=task_id,
                    run_id=run_id, action="run_started_skipped", target=run_id,
                    payload={"task_state": task_state, "resumed": bool(resume_session)},
                    response={"ok": True})
        conn.commit()

    try:
        ensure_not_stopped(conn, run_id)
        if runner in ("acp", "mock_acp", "native", "auto", "dsh"):
            from .kernel_router import execute_kernel
            return execute_kernel(conn, run_id, task_id=task_id, entity=entity, actor=actor,
                                  prompt=prompt, runner=runner, resume_session=resume_session,
                                  est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
                                  pause_at_tokens=pause_at_tokens)
        return _execute_headless(conn, run_id, task_id=task_id, entity=entity, actor=actor,
                                 prompt=prompt, runner=runner, resume_session=resume_session,
                                 est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval)
    except UserStopped as exc:
        return _stopped_before_prompt(conn, run_id, runner=runner, reserved=est_tokens, spawned=exc.spawned)


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
    proc = None
    timed_out = False
    try:
        ensure_not_stopped(conn, run_id)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", cwd=str(ws), env=env, shell=False)
        register_process(run_id, proc)
        ensure_not_stopped(conn, run_id)
        conn.execute("UPDATE task_runs SET pid = ?, runner = ?, state = 'running'"
                     " WHERE id = ? AND state NOT IN ('cancelled', 'failed')",
                     (proc.pid, runner, run_id))
        conn.commit()
        ensure_not_stopped(conn, run_id)
        try:
            out, err = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc.pid)
            out, err = proc.communicate()
    finally:
        unregister_process(run_id)
        stop.set()
        if hb:
            hb.join(timeout=2)
        if proc is not None:
            for pipe in ("stdin", "stdout", "stderr"):
                stream = getattr(proc, pipe, None)
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    exit_code = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    _log_path(run_id).write_text((out or "") + "\n--- STDERR ---\n" + (err or ""), encoding="utf-8")
    parsed = parse_stream(out or "")

    written = collect_artifact_files(parsed["writes"], since=started, task_id=task_id,
                                     actor=actor, success=exit_code == 0 and not timed_out)
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
               "pnpm-lock.yaml", "package-lock.json", "yarn.lock"}
NOISE_DIRS = {"shared", "node_modules", "__pycache__"}
# LowIL redirects USERPROFILE to the task workspace. PowerShell then creates
# telemetry.uuid and StartupProfileData here; these are runtime state, not outputs.
RUNTIME_PATH_PREFIXES = {("appdata", "local", "microsoft", "powershell")}
SCAN_MAX_ENTRIES = 2000
SCAN_MAX_DEPTH = 8
SCAN_MAX_FILES = 50


def _is_noise(p: pathlib.Path, ws: pathlib.Path) -> bool:
    rel = p.relative_to(ws)
    parts = tuple(part.lower() for part in rel.parts)
    if any(parts[:len(prefix)] == prefix for prefix in RUNTIME_PATH_PREFIXES):
        return True
    if any(part.startswith(".") or part.lower() in NOISE_DIRS for part in rel.parts):
        return True
    return p.name.lower() in NOISE_NAMES or p.suffix.lower() in {".pyc", ".pyo"}


def _artifact_path(target: str, ws: pathlib.Path) -> pathlib.Path | None:
    """Resolve tool paths against task cwd and refuse links, resources and escapes."""
    try:
        path = pathlib.Path(target)
        if not path.is_absolute():
            path = ws / path
        rel = path.relative_to(ws)
        if _is_noise(path, ws):
            return None
        current = ws
        for part in rel.parts:
            current = current / part
            info = current.lstat()
            # Includes Windows junctions, which is_symlink alone does not cover.
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return None
        resolved = path.resolve(strict=True)
        resolved.relative_to(ws)
        if not resolved.is_file() or _is_noise(resolved, ws):
            return None
        return resolved
    except (OSError, ValueError, RuntimeError, TypeError):
        return None


def collect_artifact_files(writes: list[str], *, since: float, task_id: str,
                           actor: str | None = None, success: bool) -> list[str]:
    """Keep known partial outputs; successful runs also collect shell outputs."""
    ws = task_workspace(task_id, actor).resolve()
    candidates = list(writes)
    if success:
        candidates.extend(_scan_workspace(since, task_id, actor))
    found: dict[str, str] = {}
    for target in candidates:
        path = _artifact_path(target, ws)
        if path is not None:
            found.setdefault(os.path.normcase(str(path)), str(path))
    return list(found.values())


def _scan_workspace(since: float, task_id: str, actor: str | None = None) -> list[str]:
    """Bounded walk of fresh task files, pruning resources before descending."""
    ws, found = task_workspace(task_id, actor).resolve(), []
    pending, visited = [(ws, 0)], 0
    while pending and visited < SCAN_MAX_ENTRIES:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > SCAN_MAX_ENTRIES:
                        break
                    path = pathlib.Path(entry.path)
                    if _is_noise(path, ws):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if (stat.S_ISLNK(info.st_mode)
                                or getattr(info, "st_file_attributes", 0) & 0x400):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if depth < SCAN_MAX_DEPTH:
                                pending.append((path, depth + 1))
                        elif (stat.S_ISREG(info.st_mode) and info.st_mtime >= since
                              and info.st_size > 0):
                            found.append((info.st_mtime, str(path)))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(reverse=True)
    return [f for _, f in found[:SCAN_MAX_FILES]]
