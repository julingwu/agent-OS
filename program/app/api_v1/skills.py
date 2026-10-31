"""技能的 REST 接口（v1.8）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, skills
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["skills"])


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    bot_id: str | None = None


class SkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    body: str | None = None


@router.get("/skills")
def list_skills(bot_id: str | None = Query(default=None),
                device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        items = skills.list_for(conn, bot_id=bot_id)
    return {"items": items, "count": len(items)}


@router.post("/skills")
def create_skill(body: SkillIn, device: dict = Depends(require_device)) -> dict:
    try:
        with db.get_conn() as conn:
            if skills.exists(conn, bot_id=body.bot_id or None, name=body.name):
                raise HTTPException(status_code=409, detail="同名技能已存在")
            item = skills.create(conn, bot_id=body.bot_id or None, name=body.name,
                                 description=body.description, body=body.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.patch("/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatch,
                device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        item = skills.update(conn, skill_id, name=body.name,
                             description=body.description, body=body.body)
    if item is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return {"item": item}


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        ok = skills.delete(conn, skill_id)
    if not ok:
        raise HTTPException(status_code=404, detail="skill not found")
    return {"deleted": skill_id}
