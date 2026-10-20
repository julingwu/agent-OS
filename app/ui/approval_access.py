"""UI approval access stays within the administrator's writable entity."""
from __future__ import annotations

from fastapi import HTTPException

from .. import rbac, tenant

APPROVER_ROLES = {"ceo", "manager", "system"}
_JOINS = (" FROM tool_approvals a"
          " LEFT JOIN tasks t ON t.id=a.task_id"
          " LEFT JOIN task_runs r ON r.id=a.run_id"
          " LEFT JOIN ai_employees e ON e.id=a.agent_id"
          " LEFT JOIN identities i ON i.id=a.agent_id")


def actor(conn, token, *, deciding=False):
    from .api import _task_actor
    who = _task_actor(conn, token, "task:*" if deciding else "task:read")
    if deciding and who["role"] not in APPROVER_ROLES:
        raise HTTPException(403, detail={"code": "ApprovalForbidden"})
    return who


def _scope(who):
    tenant_scope = "a.tenant_id=?"
    args = [who.get("tenant_id") or "TENANT-HQ"]
    if who["role"] == "system":
        return tenant_scope, args
    # Persisted ownership also supports standalone approvals. Every selected row
    # is then checked cumulatively; COALESCE alone cannot validate conflicting links.
    return ("COALESCE(a.legal_entity_id,t.legal_entity_id,r.legal_entity_id,e.legal_entity_id,"
            "i.legal_entity_id)=? AND " + tenant_scope, [tenant.entity_of(who), *args])


def _owned_context(conn, approval, who):
    try:
        tenant_id, entity_id = rbac.approval_owner(conn, approval)
    except PermissionError:
        return False
    return (tenant_id == (who.get("tenant_id") or "TENANT-HQ")
            and (who["role"] == "system" or entity_id == tenant.entity_of(who)))


def require_owned(conn, approval_id, who):
    rbac.ensure(conn)
    where, args = _scope(who)
    found = conn.execute("SELECT a.*" + _JOINS + " WHERE a.id=? AND " + where,
                         [approval_id, *args]).fetchone()
    if found is None or not _owned_context(conn, found, who):
        raise HTTPException(404, detail={"code": "ApprovalNotFound"})


def listing(conn, who, limit=50):
    if who["role"] not in APPROVER_ROLES:
        return {"pending": [], "history": [], "count": 0}
    rbac.ensure(conn)
    where, args = _scope(who)
    pending, history, count = [], [], 0
    candidates = conn.execute("SELECT a.*" + _JOINS + " WHERE " + where +
                              " ORDER BY a.rowid DESC", args).fetchall()
    history_limit = max(1, min(500, limit))
    for row in candidates:
        if not _owned_context(conn, row, who):
            continue
        if row["state"] == "pending":
            count += 1
            if len(pending) < 500:
                pending.append(dict(rbac.get(conn, row["id"])))
        elif len(history) < history_limit:
            history.append(dict(rbac.get(conn, row["id"])))
    return {"pending": pending, "history": history, "count": count}
