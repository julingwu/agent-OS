"""Private, fail-closed drain protocol used by the local release supervisor.

Standby never owns execution. A release waits for the active process to drain;
it does not attempt to transfer Python threads, approvals or device sockets.
"""
from __future__ import annotations

import hmac
import os
import threading

from fastapi import APIRouter, Header, HTTPException

from . import db

router = APIRouter(prefix="/internal/runtime", include_in_schema=False)
draining = False


def standby() -> bool:
    return os.environ.get("AGENTOS_TRACK_ROLE") == "standby"


class StandbySocketGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and standby():
            await send({"type": "websocket.close", "code": 1013})
            return
        await self.app(scope, receive, send)


def authorize(value: str | None) -> None:
    expected = os.environ.get("AGENTOS_CONTROL_TOKEN", "")
    if not expected or not hmac.compare_digest(value or "", expected):
        raise HTTPException(403, "private supervisor endpoint")


def snapshot() -> dict:
    from .agent import loop
    from . import local_bridge, processes

    with db.get_conn() as conn:
        conn.execute("SELECT id FROM events LIMIT 1").fetchall()
        managed = conn.execute(
            "SELECT COUNT(*) FROM processes WHERE COALESCE(state,status)='running'"
        ).fetchone()[0]
    with loop._LOCKS_GUARD:
        sessions = len(set(loop._QUEUED_SESSIONS) |
                       {sid for sid, lock in loop._LOCKS.items() if lock.locked()})
    with local_bridge._lock:
        pending = len(local_bridge._pending) + len(local_bridge._approvals)
    # Unknown application threads block promotion rather than getting killed.
    infrastructure = {"MainThread", "AnyIO worker thread", "process-supervisor"}
    workers = [t.name for t in threading.enumerate()
               if t is not threading.current_thread()
               and t.name not in infrastructure
               and not t.name.startswith("asyncio_")]
    busy = bool(sessions or managed or pending or db.active_bg_threads() or workers)
    schedulers = {"routine-scheduler", "ui-cron", "wecom-watcher", "wecom-approval-poller", "octo-runner"}
    workload_busy = bool(sessions or managed or pending or db.active_bg_threads()
                         or any(name not in schedulers for name in workers))
    return {"ok": True, "pid": os.getpid(), "instance_id": os.environ.get("AGENTOS_INSTANCE_ID"),
            "role": "standby" if standby() else "active", "draining": draining,
            "busy": busy, "sessions": sessions, "processes": managed,
            "workload_busy": workload_busy,
            "pending_calls": pending, "workers": workers}


def recover_turns(conn) -> int:
    """Only the sole active executor may close turns left by a previous process."""
    from . import events
    rows = conn.execute(
        "SELECT s.bot_id,s.session_id,s.turn_id FROM events s "
        "WHERE s.kind='turn.started' AND s.turn_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM events e WHERE e.kind='turn.ended' AND e.turn_id=s.turn_id)"
    ).fetchall()
    for row in rows:
        events.emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
                    turn_id=row["turn_id"], kind="turn.ended",
                    payload={"status": "interrupted", "reason": "executor restarted; not automatically replayed"})
        events.emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
                    turn_id=row["turn_id"], kind="message.system",
                    payload={"text": "上轮任务因服务重启而中断。已保留记录；请检查已完成的操作后发送继续。"})
    conn.commit()
    return len(rows)


@router.get("")
def status(x_agentos_control: str | None = Header(default=None)) -> dict:
    authorize(x_agentos_control)
    return snapshot()


@router.post("/drain")
def drain(x_agentos_control: str | None = Header(default=None)) -> dict:
    global draining
    authorize(x_agentos_control)
    draining = True
    from . import routines
    from .ui import ops
    from .wecom import watcher, approval
    from .octo import runner
    routines.stop_scheduler()
    ops.stop_cron()
    watcher.stop()
    approval.stop()
    runner.stop()
    return {"ok": True, "draining": True}
