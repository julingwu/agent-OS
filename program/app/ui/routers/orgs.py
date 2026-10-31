"""UI Sub-router: Organizations & Identities."""
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

class LoginIn(BaseModel):
    identity_id: str
    secret: str


@router.post("/login")
def login(payload: LoginIn, response: Response):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM identities WHERE id=? AND state='active'",
                           (payload.identity_id,)).fetchone()
    if row is None or not hmac.compare_digest(row["secret"] or "", payload.secret):
        raise HTTPException(401, detail={"code": "BadCredentials"})
    token = _make_cookie(row["id"])
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_TTL, path="/")
    return {"ok": True, "identity_id": row["id"], "name": row["name"], "role": row["role"],
            "tenant_id": row["tenant_id"], "token": token}


@router.get("/dev-login")
def dev_login(response: Response, as_id: str = "", tab: str = ""):
    """Screenshot/e2e helper. Off unless UI_DEV_LOGIN=1 in .env — never leave it on."""
    if os.environ.get("UI_DEV_LOGIN") != "1":
        raise HTTPException(404, detail={"code": "Disabled"})
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM identities WHERE id=? AND state='active'",
                           (as_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "UnknownIdentity"})
    response.set_cookie(COOKIE, _make_cookie(row["id"]), httponly=True, samesite="lax",
                        max_age=SESSION_TTL, path="/")
    response.status_code = 302
    response.headers["location"] = "/app/" + (f"?tab={tab}" if tab else "")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(oa_ui: str | None = Depends(_token)):
    from ..access import is_deployment_admin
    ident = _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        row = conn.execute(
            """SELECT i.*, e.name AS entity_name FROM identities i
               LEFT JOIN legal_entities e ON e.id = i.legal_entity_id WHERE i.id=?""",
            (ident,)).fetchone()
        tenant_name = tenancy.name_of(conn, actor["tenant_id"])
    return {"identity_id": row["id"], "name": row["name"], "role": row["role"],
            "entity_id": row["legal_entity_id"], "entity_name": row["entity_name"],
            "tenant_id": actor["tenant_id"], "tenant_name": tenant_name,
            "can_manage_deployment": is_deployment_admin(actor)}


# ------------------------------------------------------------------ org tree
@router.get("/org")
def org(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ..access import visible_entities
        allowed = set(visible_entities(conn, actor))
        entities = [dict(r) for r in conn.execute(
            "SELECT * FROM legal_entities WHERE state='active' ORDER BY parent_entity_id, name") if r["id"] in allowed]
        positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE state='active'") if r["legal_entity_id"] in allowed]
        employees = [dict(r) for r in conn.execute(
            "SELECT * FROM ai_employees") if r["legal_entity_id"] in allowed]
    by_parent: dict[str, list] = {}
    for e in entities:
        by_parent.setdefault(e["parent_entity_id"] or "", []).append(e)

    def build(entity: dict) -> dict:
        kids = [build(x) for x in by_parent.get(entity["id"], [])]
        pos = [p for p in positions if p["legal_entity_id"] == entity["id"]]
        for p in pos:
            p["employees"] = [{"id": e["id"], "name": e["name"], "title": p["title"],
                               "model": e["model"], "status": e["status"],
                               "soul": e["soul"]}
                              for e in employees if e["position_id"] == p["id"]]
        return {**entity, "children": kids, "positions": pos}

    roots = [build(x) for x in entities if not x["parent_entity_id"] or x["parent_entity_id"] not in allowed]
    return {"roots": roots,
            "employees": [{"id": e["id"], "name": e["name"], "model": e["model"],
                           "status": e["status"], "entity_id": e["legal_entity_id"],
                           "position_id": e["position_id"]} for e in employees]}


class MemberIn(BaseModel):
    name: str
    title: str = "AI 员工"
    persona: str = ""
    model: str = "deepseek-chat"
    entity_id: str = "LE-GROUP-HQ"
    api_key: str | None = None
    base_url: str | None = None


@router.post("/members")
def add_member(payload: MemberIn, oa_ui: str | None = Depends(_token)):
    """Create position (if needed) + AI employee + signed identity, then a DM."""
    _require(oa_ui)
    slug = hashlib.sha256((payload.entity_id + ":" + payload.name).encode()).hexdigest()[:12].upper()
    pos_id = f"POS-{slug}"
    emp_id = f"AI-{slug}"
    ident_id = f"ID-{secrets.token_hex(8)}"
    with get_conn() as conn:
        store.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        tenant.resolve(actor, payload.entity_id)
        if tenancy.of_entity(conn, payload.entity_id) != actor["tenant_id"]:
            raise HTTPException(403, detail={"code": "TenantMismatch"})
        if conn.execute("SELECT 1 FROM positions WHERE id=?", (pos_id,)).fetchone() is None:
            conn.execute("""INSERT INTO positions (id, legal_entity_id, title, pack_id, state)
                            VALUES (?,?,?, 'content-v1', 'active')""",
                         (pos_id, payload.entity_id, payload.title))
        conn.execute("""INSERT INTO ai_employees
                        (id, legal_entity_id, name, position_id, model, soul, focus, status)
                        VALUES (?,?,?,?,?,?,?, 'online')""",
                     (emp_id, payload.entity_id, payload.name, pos_id, payload.model,
                      payload.persona, "按判据交付，不夸大、不越权"))
        conn.execute("""INSERT INTO identities
                        (id, name, role, legal_entity_id, secret, scopes, state, employee_id,tenant_id)
                        VALUES (?,?, 'agent', ?,?,?, 'active', ?,?)""",
                     (ident_id, payload.name, payload.entity_id, secrets.token_hex(16),
                      '["task:create","task:read","accept:judge"]', emp_id, actor["tenant_id"]))
        conn.commit()
        chat = store.get_or_create_dm(conn, emp_id, payload.name, payload.entity_id)
    if payload.api_key:
        # per-employee model credentials live in memory only for now
        os.environ[f"EMPLOYEE_{emp_id}_API_KEY"] = payload.api_key
        if payload.base_url:
            os.environ[f"EMPLOYEE_{emp_id}_BASE_URL"] = payload.base_url
    return {"employee_id": emp_id, "identity_id": ident_id, "chat_id": chat["id"],
            "name": payload.name, "title": payload.title, "model": payload.model}


# ------------------------------------------------------------------ chats
@router.get("/employees")
def employees(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from ..access import visible_entities
        where, args = tenant._in("e.legal_entity_id", visible_entities(conn, actor))
        rows = conn.execute(
            f"""SELECT e.id, e.name, e.model, e.status, e.legal_entity_id, e.preset, p.title
               FROM ai_employees e LEFT JOIN positions p ON p.id = e.position_id
               WHERE {where} ORDER BY e.name""", args).fetchall()
    return {"employees": [dict(r) for r in rows]}


class RoleIn(BaseModel):
    name: str
    tag: str
    prompt: str = ""
    model: str = "deepseek-chat"
    skills: str = ""
    entity_id: str = "LE-GROUP-HQ"
    spawn: bool = True          # 建完立刻实例化成员工，出现在组织架构里


@router.get("/roles")
def list_roles(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import roles as roles_mod
        roles_mod.ensure(conn)
        return {"roles": roles_mod.list_roles(conn)}


@router.post("/roles")
def create_role(payload: RoleIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    if not payload.name.strip() or not payload.tag.strip():
        raise HTTPException(400, detail={"code": "BadRole"})
    with get_conn() as conn:
        from .. import roles as roles_mod
        roles_mod.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        tenant.resolve(actor, payload.entity_id)
        if tenancy.of_entity(conn, payload.entity_id) != actor["tenant_id"]:
            raise HTTPException(403, detail={"code": "TenantMismatch"})
        role = roles_mod.create_role(conn, name=payload.name.strip(), tag=payload.tag.strip(),
                                     prompt=payload.prompt, model=payload.model,
                                     skills=payload.skills)
        emp_id = roles_mod.instantiate(conn, role["id"], payload.entity_id) if payload.spawn else None
    return {"ok": True, "role": role, "employee_id": emp_id}


# ---------------------------------------------------- PM 申请调人（M15 模块三）
class RolePatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    model: str | None = None
    skills: str | None = None
    tools: str | None = None
    deliverable: str | None = None
    industry: str | None = None


@router.patch("/roles/{role_id}")
def patch_role(role_id: str, payload: RolePatch, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import roles as roles_mod
        roles_mod.ensure(conn)
        try:
            role = roles_mod.update_role(conn, role_id, **payload.model_dump(exclude_none=True))
        except ValueError:
            raise HTTPException(404, detail={"code": "RoleNotFound"})
    return {"ok": True, "role": role}


# ------------------------------------------------- 实时流（SSE，v2.0）
@router.delete("/roles/{role_id}")
def delete_role(role_id: str, oa_ui: str | None = Depends(_token)):
    """软删角色：归档模板，员工保留（历史产出仍可追溯）。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import roles as roles_mod
        roles_mod.ensure(conn)
        row = conn.execute("SELECT name FROM role_templates WHERE id=?", (role_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "RoleNotFound"})
        conn.execute("UPDATE role_templates SET state='archived' WHERE id=?", (role_id,))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="role", task_id=None,
                    layer="system", action="role_deleted", target=role_id,
                    payload={"name": row["name"]}, response={"by": ident})
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------- 链路追踪（v2.2）
