"""Common helpers and session authentication for UI BFF."""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time

from fastapi import Cookie, Header, HTTPException, Query

from ..db import get_conn
from .. import tenant, tenancy

COOKIE = "oa_ui"
SESSION_TTL = 7 * 24 * 3600


def _secret() -> bytes:
    return (os.environ.get("UI_SESSION_SECRET")
            or os.environ.get("WECOM_TOKEN") or "dev-ui").encode()


def _sign(ident: str, exp: int) -> str:
    return hmac.new(_secret(), f"{ident}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def _make_cookie(ident: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    return f"{ident}.{exp}.{_sign(ident, exp)}"


def _read_cookie(token: str | None) -> str | None:
    if not token or token.count(".") != 2:
        return None
    ident, exp, sig = token.split(".")
    if not exp.isdigit() or int(exp) < time.time():
        return None
    if not hmac.compare_digest(_sign(ident, int(exp)), sig):
        return None
    return ident


def _token(oa_ui: str | None = Cookie(default=None),
           authorization: str | None = Header(default=None),
           t: str | None = Query(default=None)) -> str | None:
    """Cookie in the browser, Bearer in the desktop shell, `?t=` for <iframe> previews.

    iframe 不能带自定义头，所以预览链接把同一张签名票据放进查询串；
    票据本身就是 HMAC 签名 + 过期时间，不额外增加可伪造面。
    """
    if authorization and isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    for cand in (oa_ui, t):
        if isinstance(cand, str) and cand:
            return cand
    return None


def _require(token: str | None) -> str:
    ident = _read_cookie(token)
    if ident is None:
        raise HTTPException(401, detail={"code": "NotLoggedIn"})
    return ident


def _tenant_of(conn, ident: str, header: str | None = None) -> str:
    try:
        return tenancy.resolve(conn, ident, header)
    except PermissionError as exc:
        code = str(exc)
        raise HTTPException(401 if code == "UnknownIdentity" else 403,
                            detail={"code": code}) from None


def _task_actor(conn, token: str | None, scope: str) -> dict:
    from .. import identity
    row = identity.get_identity(conn, _require(token))
    if row is None:
        raise HTTPException(401, detail={"code": "UnknownIdentity"})
    if not identity.has_scope(row, scope):
        raise HTTPException(403, detail={"code": "Forbidden", "scope": scope})
    return dict(row)


def _task_write_entities(conn, actor: dict) -> list[str]:
    return (tenant.visible_entities(conn, actor) if actor["role"] == "system"
            else [tenant.entity_of(actor)])


def _task_allowed_actions(task, actor: dict) -> list[str]:
    from ..identity import has_scope
    if actor["role"] != "system" and task["legal_entity_id"] != tenant.entity_of(actor):
        return []
    if task["state"] == "pending_review" and has_scope(actor, "accept:judge"):
        return ["accept", "reject"]
    if has_scope(actor, "task:*"):
        if task["state"] == "in_progress":
            return ["abort"]
        if task["state"] in ("rejected", "failed"):
            return ["retry"]
    return []


def _task_execution_summary(conn, task, actor: dict) -> dict:
    """A bounded execution status, with no process/session or approval arguments."""
    run = conn.execute(
        """SELECT id,run_seq,state,runner,usage_tokens,started_at,ended_at
           FROM task_runs WHERE task_id=? ORDER BY run_seq DESC,rowid DESC LIMIT 1""",
        (task["id"],)).fetchone()
    summary = {"latest_run": dict(run) if run else None,
               "awaiting_approval": False, "pending_approval": None}
    if not run or run["state"] != "awaiting_approval" or task["state"] != "in_progress":
        return summary
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_approvals'").fetchone():
        return summary
    approval = conn.execute(
        """SELECT id,state FROM tool_approvals
           WHERE run_id=? AND task_id=? AND state='pending' ORDER BY rowid DESC LIMIT 1""",
        (run["id"], task["id"])).fetchone()
    if approval:
        from .approval_access import APPROVER_ROLES
        summary["awaiting_approval"] = True
        if actor["role"] == "system" or (actor["role"] in APPROVER_ROLES
                and task["legal_entity_id"] == tenant.entity_of(actor)):
            summary["pending_approval"] = dict(approval)
    return summary


def _get_api_attr(name: str, fallback):
    import sys
    mod = sys.modules.get("app.ui.api")
    return getattr(mod, name, fallback) if mod else fallback


def _budget_for(conn, entity: str) -> int:
    """Never ask for more than the entity pool has left, or the run is denied outright."""
    want = int(os.environ.get("UI_TASK_BUDGET", "30000"))
    row = conn.execute(
        """SELECT limit_tokens, used_tokens FROM budgets
           WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
           ORDER BY rowid DESC LIMIT 1""", (entity,)).fetchone()
    if row and row["limit_tokens"]:
        left = int(row["limit_tokens"]) - int(row["used_tokens"] or 0)
        return max(4000, min(want, left))
    return want
