"""例行任务的 REST 接口（v1.8）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, routines
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["routines"])


class RoutineIn(BaseModel):
    bot_id: str
    session_id: str
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000)
    schedule: str = Field(description="interval:30m / interval:2h / daily:09:00")
    enabled: bool = True


class RoutinePatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    schedule: str | None = None
    enabled: bool | None = None


@router.get("/routines")
def list_routines(bot_id: str | None = Query(default=None),
                  device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        items = routines.list_routines(conn, bot_id=bot_id)
    return {"items": items, "count": len(items)}


@router.post("/routines")
def create_routine(body: RoutineIn, device: dict = Depends(require_device)) -> dict:
    try:
        with db.get_conn() as conn:
            item = routines.create(conn, bot_id=body.bot_id, session_id=body.session_id,
                                   title=body.title, prompt=body.prompt,
                                   schedule=body.schedule, enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.patch("/routines/{routine_id}")
def patch_routine(routine_id: str, body: RoutinePatch,
                  device: dict = Depends(require_device)) -> dict:
    try:
        with db.get_conn() as conn:
            item = routines.update(conn, routine_id, title=body.title,
                                   prompt=body.prompt, schedule=body.schedule,
                                   enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="routine not found")
    return {"item": item}


@router.delete("/routines/{routine_id}")
def delete_routine(routine_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        ok = routines.delete(conn, routine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="routine not found")
    return {"deleted": routine_id}


@router.post("/routines/{routine_id}/run")
def run_routine(routine_id: str, device: dict = Depends(require_device)) -> dict:
    out = routines.run_now(routine_id)
    if out["status"] == "not_found":
        raise HTTPException(status_code=404, detail="routine not found")
    return out


@router.get("/routines/schedule/preview")
def preview_schedule(schedule: str = Query(...),
                     device: dict = Depends(require_device)) -> dict:
    try:
        nxt = routines.next_run_at(schedule)
        human = routines.describe_schedule(schedule)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"schedule": schedule, "human": human,
            "next_run_at": nxt.isoformat(timespec="seconds")}
