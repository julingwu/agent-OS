"""Shared UI identity/ownership checks; tenant and entity checks are cumulative."""
import sqlite3

from fastapi import HTTPException, Request

from .. import tenant, tenancy
from ..db import get_conn


def actor_for(conn, token, header=None, *, manage=False):
    from .api import _task_actor
    actor = _task_actor(conn, token, "task:*" if manage else "task:read")
    try:
        tenancy.resolve(conn, actor["id"], header)
    except PermissionError as exc:
        code = str(exc)
        raise HTTPException(401 if code == "UnknownIdentity" else 403,
                            detail={"code": code}) from None
    return actor


def visible_entities(conn, actor):
    permitted = set(tenant.visible_entities(conn, actor))
    return [row["id"] for row in conn.execute(
        "SELECT id FROM legal_entities WHERE COALESCE(tenant_id,'TENANT-HQ')=?",
        (actor["tenant_id"],)) if row["id"] in permitted]


def owned(conn, table, identifier, actor, *, write=False, code="NotFound"):
    """Only fixed internal table names are accepted; never interpolate user input."""
    queries = {
        "tasks": "SELECT tenant_id,legal_entity_id FROM tasks WHERE id=?",
        "projects": "SELECT tenant_id,legal_entity_id FROM projects WHERE id=?",
        "runs": """SELECT t.tenant_id,t.legal_entity_id FROM task_runs r
                   JOIN tasks t ON t.id=r.task_id WHERE r.id=?""",
        "artifacts": """SELECT t.tenant_id,t.legal_entity_id FROM artifacts a
                        JOIN task_runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id WHERE a.id=?""",
        "employees": """SELECT l.tenant_id,e.legal_entity_id FROM ai_employees e
                        JOIN legal_entities l ON l.id=e.legal_entity_id WHERE e.id=?""",
        "chats": """SELECT c.tenant_id,COALESCE(c.entity_id,e.legal_entity_id,
                     (SELECT em.legal_entity_id FROM chat_members cm JOIN ai_employees em
                      ON em.id=cm.employee_id WHERE cm.chat_id=c.id LIMIT 1),
                     'LE-GROUP-HQ') AS legal_entity_id
                    FROM chats c LEFT JOIN ai_employees e ON e.id=c.employee_id WHERE c.id=?""",
        "spans": """SELECT t.tenant_id,t.legal_entity_id FROM trace_spans s
                   JOIN agent_traces tr ON tr.trace_id=s.trace_id
                   JOIN task_runs r ON r.id=tr.run_id JOIN tasks t ON t.id=r.task_id WHERE s.id=?""",
    }
    try:
        row = conn.execute(queries[table], (identifier,)).fetchone()
    except sqlite3.OperationalError as exc:
        # A cold installation may not have a lazy project/trace store yet.
        if "no such table" not in str(exc):
            raise
        row = None
    entities = ([tenant.entity_of(actor)] if write and actor["role"] != "system"
                else visible_entities(conn, actor))
    if (row is None or (row["tenant_id"] or tenancy.DEFAULT_TENANT) != actor["tenant_id"]
            or row["legal_entity_id"] not in entities):
        raise HTTPException(404, detail={"code": code})
    return row


def request_access(request: Request):
    """Protect legacy UI routes as well as their list-specific query filters."""
    path = request.url.path
    if path in {"/ui/login", "/ui/dev-login", "/ui/logout"}:
        return
    # Preview has its own artifact-specific ticket authentication and scoped lookup.
    if path.endswith("/preview") and "artifact_id" in request.path_params:
        return
    from .api import _token
    token = _token(request.cookies.get("oa_ui"), request.headers.get("authorization"),
                   request.query_params.get("t"))
    write = request.method not in {"GET", "HEAD", "OPTIONS"}
    with get_conn() as conn:
        actor = actor_for(conn, token, request.headers.get("x-tenant-id"))
        request.state.ui_actor = actor
        for key, table, code in (
            ("task_id", "tasks", "TaskNotFound"), ("run_id", "runs", "RunNotFound"),
            ("artifact_id", "artifacts", "FileNotFound"), ("project_id", "projects", "ProjectNotFound"),
            ("chat_id", "chats", "ChatNotFound"), ("employee_id", "employees", "EmployeeNotFound"),
            ("agent_id", "employees", "EmployeeNotFound"), ("span_id", "spans", "TraceNotFound"),
        ):
            if key in request.path_params:
                owned(conn, table, request.path_params[key], actor, write=write, code=code)
        # These legacy configuration stores are deployment-wide, not tenant data.
        # Until migrated, only the deployment administrator can mutate/read them.
        global_prefixes = ("/ui/settings", "/ui/jobs", "/ui/kb", "/ui/departments",
                           "/ui/resources", "/ui/rbac/resources", "/ui/evals", "/ui/alerts",
                           "/ui/rbac/grant", "/ui/rbac/revoke", "/ui/rbac/check")
        global_role_write = write and path.startswith("/ui/roles")
        if path.startswith(global_prefixes) or global_role_write:
            if actor["tenant_id"] != tenancy.DEFAULT_TENANT:
                raise HTTPException(403, detail={"code": "DeploymentAdminOnly"})
            if write and actor["role"] not in {"ceo", "system"}:
                raise HTTPException(403, detail={"code": "Forbidden"})
        if write and path in {"/ui/members", "/ui/rbac/grant", "/ui/rbac/revoke"}:
            if actor["role"] not in {"ceo", "system"}:
                raise HTTPException(403, detail={"code": "Forbidden"})
        if write and path.startswith("/ui/employees/") and actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        if write and path.startswith("/ui/groups/") and actor["role"] not in {"ceo", "manager", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
