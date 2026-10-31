"""UI Sub-router: Evals, FinOps & RBAC."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

from fastapi import (APIRouter, Cookie, Depends, File, Header, HTTPException, Query,
                      Response, UploadFile)
from pydantic import BaseModel, Field

from ...audit import write_audit
from ...db import get_conn
from ...engine import dsh_adapter
from ... import tenant, tenancy
from .. import intel, store
from ..common import (
    COOKIE, SESSION_TTL, _secret, _sign, _make_cookie, _read_cookie,
    _token, _require, _tenant_of, _task_actor, _task_write_entities,
    _task_allowed_actions, _task_execution_summary, _budget_for,
)

router = APIRouter()

class ResourceIn(BaseModel):
    id: str
    kind: str
    name: str
    path: str | None = None
    parent_id: str | None = None


class GrantIn(BaseModel):
    agent_id: str
    resource_id: str
    permission: str = "read"


class ApprovalIn(BaseModel):
    approve: bool = True


@router.get("/rbac/resources")
def list_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        rbac.ensure(conn)
        rows = conn.execute("SELECT * FROM resources ORDER BY kind, name").fetchall()
        perms = conn.execute("SELECT * FROM agent_permissions ORDER BY rowid DESC").fetchall()
    return {"resources": [dict(r) for r in rows], "permissions": [dict(r) for r in perms]}


@router.post("/rbac/resources")
def create_resource(payload: ResourceIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        rid = rbac.upsert_resource(conn, rid=payload.id, kind=payload.kind,
                                   name=payload.name, path=payload.path,
                                   parent_id=payload.parent_id)
    return {"ok": True, "resource_id": rid}


@router.post("/rbac/grant")
def grant_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        from ..access import actor_for, owned
        actor = actor_for(conn, oa_ui, manage=True)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
        if payload.permission not in rbac.LEVELS:
            raise HTTPException(422, detail={"code": "InvalidPermission"})
        rbac.grant(conn, payload.agent_id, payload.resource_id, payload.permission,
                   granted_by=ident)
    return {"ok": True}


@router.post("/rbac/check")
def check_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        from ..access import actor_for, owned
        actor = actor_for(conn, oa_ui)
        owned(conn, "employees", payload.agent_id, actor, code="EmployeeNotFound")
        try:
            rbac.require(conn, payload.agent_id, payload.resource_id, payload.permission)
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "PermissionDenied", "message": str(exc)})
    return {"ok": True}


@router.get("/rbac/approvals")
def list_approvals(oa_ui: str | None = Depends(_token)):
    from .. import approval_access
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui)
        return approval_access.listing(conn, who)


@router.post("/rbac/approvals/{approval_id}")
def decide_approval(approval_id: str, payload: ApprovalIn,
                    oa_ui: str | None = Depends(_token)):
    from .. import approval_access, approval_flow
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui, deciding=True)
        approval_access.require_owned(conn, approval_id, who)
    try:
        return approval_flow.decide(approval_id, payload.approve, by=who["id"])
    except ValueError:
        raise HTTPException(404, detail={"code": "ApprovalNotFound"})


class HighRiskIn(BaseModel):
    agent_id: str
    tool: str
    args: dict | None = None


@router.post("/rbac/guard")
def guard_tool(payload: HighRiskIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        from ..access import actor_for, owned
        actor = actor_for(conn, oa_ui)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
        if actor["role"] == "agent" and actor.get("employee_id") != payload.agent_id:
            raise HTTPException(403, detail={"code": "Forbidden"})
        return rbac.guard_tool(conn, agent_id=payload.agent_id, tool=payload.tool,
                               args=payload.args, entity=tenant.entity_of(actor))


# ------------------------------------------------------- 审批中心（v2.3）
@router.get("/approvals")
def ui_approvals(limit: int = 50, oa_ui: str | None = Depends(_token)):
    from .. import approval_access
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui)
        return approval_access.listing(conn, who, limit)


@router.post("/approvals/{approval_id}")
def ui_decide_approval(approval_id: str, payload: ApprovalIn,
                       oa_ui: str | None = Depends(_token)):
    return decide_approval(approval_id, payload, oa_ui)


@router.post("/rbac/revoke")
def revoke_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import rbac
        from ..access import actor_for, owned
        actor = actor_for(conn, oa_ui, manage=True)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
        n = rbac.revoke(conn, payload.agent_id, payload.resource_id,
                        payload.permission or None)
    return {"ok": True, "revoked": n}


# ------------------------------------------------------- 资源目录（v2.3）
class DrillIn(BaseModel):
    resource_id: str = "RES-FIN"
    agent_id: str = "AI-QUANT-01"
    mode: str = "auto"
    text: str | None = None


@router.get("/resources")
def ui_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import resources as res_mod
        return res_mod.ensure_demo(conn)


@router.post("/resources/seed")
def ui_seed_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import resources as res_mod
        return res_mod.ensure_demo(conn)


@router.post("/resources/drill")
def ui_drill(payload: DrillIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import resources as res_mod
        try:
            return res_mod.start_drill(conn, resource_id=payload.resource_id,
                                       agent_id=payload.agent_id, mode=payload.mode,
                                       text=payload.text)
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "DrillRejected", "message": str(exc)})


# ------------------------------------------------------- FinOps（v2.4）
@router.get("/finops")
def ui_finops(days: int = 30, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import finops
        actor = _task_actor(conn, oa_ui, "task:read")
        return finops.overview(conn, days=days, tenant_id=actor["tenant_id"])


# ------------------------------------------------------- 离线评测（v2.4）
class BaselineIn(BaseModel):
    run_id: str
    name: str | None = None


class EvalRunIn(BaseModel):
    runner: str = "mock_acp"
    prompt_mode: str = "stored"
    set_ids: list[str] | None = None
    name: str | None = None


def _eval_thread(batch_id: str, set_ids: list[str], runner: str,
                 prompt_mode: str = "stored", tenant_id: str | None = None) -> None:
    with get_conn() as conn:
        try:
            from ... import evals
            evals.execute_regression(conn, batch_id, set_ids=set_ids, runner=runner,
                                     prompt_mode=prompt_mode, tenant_id=tenant_id)
        except Exception as exc:                 # noqa: BLE001
            # v5.3 TASK-4.1：失败回写同样按租户收敛，不按 id 盲写批次。
            detail = json.dumps({"error": str(exc)}, ensure_ascii=False)
            if tenant_id:
                conn.execute("UPDATE eval_runs SET state='failed', detail_json=?,"
                             " ended_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                             (detail, batch_id, tenant_id))
            else:
                conn.execute("UPDATE eval_runs SET state='failed', detail_json=?,"
                             " ended_at=CURRENT_TIMESTAMP WHERE id=?",
                             (detail, batch_id))
            conn.commit()


@router.get("/evals")
def ui_evals(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ... import evals
        tenant_id = actor["tenant_id"]
        return {"sets": evals.list_sets(conn, tenant_id=tenant_id),
                "batches": evals.list_batches(conn, tenant_id=tenant_id)}


@router.post("/evals/baselines")
def ui_eval_baseline(payload: BaselineIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ... import evals
        try:
            return {"ok": True, "set": evals.create_from_run(
                conn, payload.run_id, name=payload.name, by=actor["id"],
                tenant_id=actor["tenant_id"])}
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BaselineRejected", "message": str(exc)})


@router.post("/evals/regress")
def ui_eval_regress(payload: EvalRunIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ... import evals
        try:
            tenant_id = actor["tenant_id"]
            sets = evals.resolve_set_ids(conn, payload.set_ids, tenant_id, limit=8)
            bid = evals.start_regression(conn, set_ids=sets,
                                         runner=payload.runner, prompt_mode=payload.prompt_mode,
                                         name=payload.name, by=actor["id"],
                                         tenant_id=tenant_id)
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "EvalRejected", "message": str(exc)})
    threading.Thread(target=_eval_thread,
                     args=(bid, sets, payload.runner, payload.prompt_mode, tenant_id),
                     daemon=True).start()
    return {"ok": True, "batch_id": bid, "sets": len(sets), "runner": payload.runner,
            "prompt_mode": payload.prompt_mode}


@router.post("/evals/{set_id}/run")
def ui_eval_run(set_id: str, payload: EvalRunIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    payload.set_ids = [set_id]
    return ui_eval_regress(payload, oa_ui)


@router.get("/evals/batches/{batch_id}")
def ui_eval_batch(batch_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ... import evals
        return evals.batch_detail(conn, batch_id, tenant_id=actor["tenant_id"])


@router.delete("/evals/sets/{set_id}")
def ui_eval_delete_set(set_id: str, oa_ui: str | None = Depends(_token)):
    """删除一条评测样本（基准用例）及其全部结果。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        from ... import evals
        try:
            return {"ok": True, **evals.delete_set(conn, set_id,
                                                   tenant_id=actor["tenant_id"])}
        except KeyError:
            raise HTTPException(404, detail={"code": "EvalSetNotFound"}) from None


@router.delete("/evals/batches/{batch_id}")
def ui_eval_delete_batch(batch_id: str, oa_ui: str | None = Depends(_token)):
    """删除一次回归评测批次及其全部结果。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        from ... import evals
        try:
            return {"ok": True, **evals.delete_batch(conn, batch_id,
                                                     tenant_id=actor["tenant_id"])}
        except KeyError:
            raise HTTPException(404, detail={"code": "EvalBatchNotFound"}) from None


# ------------------------------------------------------- 组织经验（v2.4）
# Experience review and project recovery live in ui.governance.
