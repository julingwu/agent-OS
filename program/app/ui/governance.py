"""Scoped human review for organization knowledge and project recovery."""
from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import experience, pipeline, tenancy
from ..db import get_conn
from .api import _task_actor, _token

router = APIRouter(prefix="/ui", tags=["governance"])


def _actor(conn, token, *, admin=False):
    actor = _task_actor(conn, token, "task:read")
    if admin and actor["role"] not in {"ceo", "system"}:
        raise HTTPException(403, detail={"code": "GovernanceReviewerRequired"})
    try:
        tenant_id = tenancy.of_identity(conn, actor["id"])
    except PermissionError as exc:
        raise HTTPException(403, detail={"code": str(exc)}) from exc
    return actor, {"tenant_id": tenant_id, "entity": actor["legal_entity_id"]}


def _error(exc):
    if isinstance(exc, KeyError):
        return HTTPException(404, detail={"code": "NotFound"})
    if isinstance(exc, PermissionError):
        return HTTPException(403, detail={"code": str(exc)})
    if isinstance(exc, ValueError):
        return HTTPException(422, detail={"code": str(exc)})
    return HTTPException(409, detail={"code": str(exc)})


class ExperienceIn(BaseModel):
    kind: str = "decision"
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    mandatory: bool = False
    redline_terms: str | list[str] | None = None
    tags: str | list[str] | None = None


class ExperiencePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    mandatory: bool | None = None
    redline_terms: str | list[str] | None = None
    tags: str | list[str] | None = None


class ReviewIn(BaseModel):
    action: Literal["approve", "reject"]
    expected_version: int = Field(ge=1, strict=True)
    note: str = Field(default="", max_length=500)


class QueryIn(BaseModel):
    text: str = Field(max_length=20000)
    entity: str | None = None


@router.get("/experiences")
def experiences(kind: str | None = None, state: str | None = None,
                group: str | None = None, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        _, scope = _actor(conn, oa_ui)
        # v5.2 TASK-3.1：复盘来源 c 由 experience.list_all/stats 读取时幂等补齐
        # （复盘发生在 pipeline 内，本任务不能改 pipeline，故挂在治理读取入口）。
        rows = experience.list_all(conn, kind=kind, state=state, **scope)
        return {"experiences": rows,
                "stats": experience.stats(conn, **scope),
                "groups": experience.groups(conn, **scope),
                "group": group or "kind"}


@router.post("/experiences")
def add_experience(payload: ExperienceIn, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor, scope = _actor(conn, oa_ui, admin=True)
        try:
            row = experience.add(conn, **payload.model_dump(), **scope, actor=actor,
                                 source_type="manual", source_id=actor["id"])
        except (KeyError, PermissionError, ValueError) as exc:
            raise _error(exc) from exc
        return {"ok": True, "experience": row}


@router.patch("/experiences/{exp_id}")
def edit_experience(exp_id: str, payload: ExperiencePatch,
                    oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor, scope = _actor(conn, oa_ui, admin=True)
        try:
            row = experience.edit(conn, exp_id, payload.model_dump(exclude_none=True),
                                  actor=actor["id"], **scope)
        except (KeyError, PermissionError, ValueError) as exc:
            raise _error(exc) from exc
        return {"ok": True, "experience": row}


@router.post("/experiences/{exp_id}/review")
def review_experience(exp_id: str, payload: ReviewIn,
                      oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor, scope = _actor(conn, oa_ui, admin=True)
        try:
            row = experience.review(conn, exp_id, payload.action, reviewer=actor["id"],
                                    note=payload.note, expected_version=payload.expected_version,
                                    require_pending=True, **scope)
        except (KeyError, PermissionError, ValueError, experience.ReviewConflict) as exc:
            raise _error(exc) from exc
        return {"ok": True, "experience": row}


class BatchReviewIn(BaseModel):
    experience_ids: list[str] = Field(default_factory=list)
    # 计划原文用的是 exp_ids；两个名字都收，避免调用方踩空。
    exp_ids: list[str] = Field(default_factory=list)
    action: str = Field(default="reject")
    note: str | None = None
    archive_unreviewed_redlines: bool = False
    require_pending: bool = True


@router.post("/experiences/batch-review")
def batch_review_experiences(payload: BatchReviewIn,
                              oa_ui: str | None = Depends(_token)):
    """单事务批量审核（approve/reject），逐条写 experience_reviewed 审计。

    v5.2 TASK-3.1：只返回**真正处理成功**的 id；越权/不存在/已审核的 id 进 skipped，
    不会因一条坏数据整批失败，也不会跨租户写库。
    """
    with get_conn() as conn:
        actor, scope = _actor(conn, oa_ui, admin=True)
        act = (payload.action or "reject").strip().lower()
        if act not in {"approve", "passed", "reject", "rejected"}:
            raise HTTPException(422, detail={"code": "InvalidReviewAction"})
        action = "approve" if act in ("approve", "passed") else "reject"
        target_ids = list(payload.experience_ids or payload.exp_ids or [])
        if payload.archive_unreviewed_redlines or not target_ids:
            rows = conn.execute(
                "SELECT id FROM org_experiences WHERE state='pending'"
                " AND tenant_id=? AND legal_entity_id=? ORDER BY rowid DESC LIMIT 500",
                (scope["tenant_id"], scope["entity"])
            ).fetchall()
            target_ids = [r["id"] for r in rows]
        if not target_ids:
            return {"ok": True, "count": 0, "action": action,
                    "processed_ids": [], "skipped": []}
        try:
            out = experience.batch_review(
                conn, target_ids, action, reviewer=actor["id"],
                note=payload.note or "批量归档操作",
                require_pending=payload.require_pending, **scope)
        except (KeyError, PermissionError, ValueError, experience.ReviewConflict) as exc:
            raise _error(exc) from exc
        return {"ok": True, "count": out["processed"], "action": action,
                "processed_ids": out["processed_ids"], "skipped": out["skipped"]}


@router.delete("/experiences/{exp_id}")
def delete_experience(exp_id: str, oa_ui: str | None = Depends(_token)):
    """彻底删除一条组织经验（含审阅历史）；强制规则删除后立刻不再生效。"""
    with get_conn() as conn:
        actor, scope = _actor(conn, oa_ui, admin=True)
        try:
            out = experience.delete(conn, exp_id, actor=actor, **scope)
        except (KeyError, PermissionError, ValueError) as exc:
            raise _error(exc) from exc
        return {"ok": True, **out}


@router.post("/experiences/preview")
def preview_experiences(payload: QueryIn, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        _, scope = _actor(conn, oa_ui)
        if payload.entity and payload.entity != scope["entity"]:
            raise HTTPException(403, detail={"code": "CrossEntityForbidden"})
        return experience.context_for(conn, payload.text, record=False, **scope)


class RetryIn(BaseModel):
    requirement: str = Field(min_length=1, max_length=20000)


def _retry_worker(claim):
    with get_conn() as conn:
        try:
            pipeline.run_retry(conn, claim)
        except Exception:
            # run_retry records a blocking state and a short error, never success.
            return


@router.post("/projects/{project_id}/retry")
def retry_project(project_id: str, payload: RetryIn,
                  oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor, _ = _actor(conn, oa_ui, admin=True)
        try:
            claim = pipeline.prepare_retry(conn, project_id, payload.requirement, actor=actor)
        except (KeyError, PermissionError, ValueError, RuntimeError) as exc:
            raise _error(exc) from exc
    try:
        threading.Thread(target=_retry_worker, args=(claim,), daemon=True).start()
    except Exception as exc:
        with get_conn() as conn:
            pipeline.fail_retry(conn, claim, "WorkerStartFailed")
        raise HTTPException(503, detail={"code": "WorkerStartFailed"}) from exc
    return {"ok": True, "project_id": project_id, "state": "retrying"}
