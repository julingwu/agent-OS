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
    # Reading down does not authorize an approval that grants another entity's
    # permissions. Task ownership wins over an optional agent association.
    return ("COALESCE(t.legal_entity_id,r.legal_entity_id,e.legal_entity_id,"
            "i.legal_entity_id)=? AND " + tenant_scope, [tenant.entity_of(who), *args])


def require_owned(conn, approval_id, who):
    rbac.ensure(conn)
    where, args = _scope(who)
    found = conn.execute("SELECT a.id" + _JOINS + " WHERE a.id=? AND " + where,
                         [approval_id, *args]).fetchone()
    if found is None:
        raise HTTPException(404, detail={"code": "ApprovalNotFound"})


def listing(conn, who, limit=50):
    if who["role"] not in APPROVER_ROLES:
        return {"pending": [], "history": [], "count": 0}
    rbac.ensure(conn)
    where, args = _scope(who)
    count = conn.execute("SELECT COUNT(*)" + _JOINS +
                         " WHERE a.state='pending' AND " + where, args).fetchone()[0]

    def rows(pending, maximum):
        predicate = "a.state='pending'" if pending else "a.state<>'pending'"
        ids = conn.execute("SELECT a.id" + _JOINS + " WHERE " + predicate +
                           " AND " + where + " ORDER BY a.rowid DESC LIMIT ?",
                           [*args, maximum]).fetchall()
        return [dict(rbac.get(conn, row["id"])) for row in ids]

    return {"pending": rows(True, 500), "history": rows(False, max(1, min(500, limit))),
            "count": count}
