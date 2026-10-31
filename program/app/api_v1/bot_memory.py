"""每个 Bot 的长期记忆（读、手动写、删）。记忆写入默认由对话自动完成。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import bot_memory, db
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["memory"])


class MemoryIn(BaseModel):
    bot_id: str
    content: str = Field(min_length=1, max_length=500)
    kind: str = "fact"
    topic: str = ""
    importance: int = 1


@router.get("/memories")
def list_memories(bot_id: str = Query(...), limit: int = Query(default=50, ge=1, le=200),
                  offset: int = Query(default=0, ge=0),
                  device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        items = bot_memory.list_for_bot(conn, bot_id, limit=limit, offset=offset)
        total = bot_memory.count(conn, bot_id)
    return {"items": items, "total": total}


@router.post("/memories")
def create_memory(body: MemoryIn, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        saved = bot_memory.add(conn, body.bot_id, body.content,
                               kind=body.kind, importance=body.importance,
                               topic=body.topic)
        if saved is None:
            return {"ok": True, "saved": False, "item": None, "duplicate": True, "message": "这条记忆已存在或同主题已更新", "total": bot_memory.count(conn, body.bot_id)}
        return {"ok": True, "saved": True, "item": saved, "duplicate": False, "total": bot_memory.count(conn, body.bot_id)}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        ok = bot_memory.delete(conn, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": memory_id}
