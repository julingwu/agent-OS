"""Admin-only MCP and SMTP settings with active identity and tenant checks."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import mailer, mcp, tenancy
from ..db import get_conn
from .api import _require, _token

router = APIRouter(prefix="/ui", tags=["extensions"])


@contextmanager
def _context(token: str | None, header: str | None):
    actor = _require(token)
    try:
        with get_conn() as conn:
            tid = tenancy.resolve(conn, actor, header)
            mcp.require_admin(conn, actor, tid)
            yield conn, tid, actor
    except PermissionError as exc:
        code = str(exc)
        raise HTTPException(401 if code in {"InactiveIdentity", "UnknownIdentity"} else 403,
                            detail={"code": code}) from None
    except KeyError as exc:
        raise HTTPException(404, detail={"code": str(exc).strip("'")}) from None
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc)}) from None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class McpServerIn(Input):
    name: str
    url: str
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    transport: str = "http"


class McpPatchIn(Input):
    name: str | None = None
    url: str | None = None
    api_key: str | None = None
    auth_header: str | None = None
    auth_scheme: str | None = None
    enabled: bool | None = None


class McpBindIn(Input):
    server_ids: list[str]


class SmtpIn(Input):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str | None = None
    from_addr: str = ""
    secure: bool = False


class SmtpTestIn(Input):
    to: str


@router.get("/mcp")
def servers(token: str | None = Depends(_token), x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return {"servers": mcp.list_servers(conn, tenant_id=tid, actor=actor), "tenant_id": tid}


@router.post("/mcp")
def add(payload: McpServerIn, token: str | None = Depends(_token),
        x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return {"ok": True, "server": mcp.add_server(
            conn, tenant_id=tid, actor=actor, **payload.model_dump())}


@router.patch("/mcp/{sid}")
def update(sid: str, payload: McpPatchIn, token: str | None = Depends(_token),
           x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return {"ok": True, "server": mcp.update_server(
            conn, sid, tenant_id=tid, actor=actor, **payload.model_dump(exclude_none=True))}


@router.delete("/mcp/{sid}")
def delete(sid: str, token: str | None = Depends(_token),
           x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        mcp.delete_server(conn, sid, tenant_id=tid, actor=actor)
        return {"ok": True}


@router.post("/mcp/{sid}/test")
def probe(sid: str, token: str | None = Depends(_token),
          x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return mcp.probe(conn, sid, tenant_id=tid, actor=actor)


@router.get("/mcp/bindings/{agent_id}")
def bindings(agent_id: str, token: str | None = Depends(_token),
             x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return {"agent_id": agent_id, "server_ids": mcp.bindings(
            conn, agent_id, tenant_id=tid, actor=actor)}


@router.put("/mcp/bindings/{agent_id}")
def bind(agent_id: str, payload: McpBindIn, token: str | None = Depends(_token),
         x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return {"ok": True, "agent_id": agent_id, "server_ids": mcp.set_bindings(
            conn, agent_id, payload.server_ids, tenant_id=tid, actor=actor)}


@router.get("/smtp")
def smtp(token: str | None = Depends(_token), x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return mailer.smtp_settings(conn, tenant_id=tid, actor=actor)


@router.put("/smtp")
def smtp_save(payload: SmtpIn, token: str | None = Depends(_token),
              x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return mailer.save_smtp(conn, tenant_id=tid, actor=actor, **payload.model_dump())


@router.post("/smtp/test")
def smtp_test(payload: SmtpTestIn, token: str | None = Depends(_token),
              x_tenant_id: str | None = Header(default=None)):
    with _context(token, x_tenant_id) as (conn, tid, actor):
        return mailer.test_smtp(conn, tenant_id=tid, actor=actor, to=payload.to)
