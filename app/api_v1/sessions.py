"""会话中止与在途控制接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..agent import loop as agent_loop
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["sessions"])


@router.post("/sessions/{session_id}/abort")
def abort_session(session_id: str, device: dict = Depends(require_device)) -> dict:
    """中止指定会话当前运行中的任务。"""
    agent_loop.request_abort(session_id)
    return {"ok": True, "session_id": session_id, "aborted": True}
