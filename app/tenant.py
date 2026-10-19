"""Tenant (legal entity) helpers: every data access goes through here.

Rules:
  * the entity comes from the verified identity, never from the request body;
  * another entity's row is reported as 404, not 403 — no existence leak;
  * `system` may opt into another entity explicitly (operators only).
"""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException

DEFAULT_ENTITY = "LE-GROUP-HQ"


def entity_of(ident: dict | None) -> str:
    if not ident:
        return DEFAULT_ENTITY
    return ident.get("legal_entity_id") or DEFAULT_ENTITY


def resolve(ident: dict | None, requested: str | None) -> str:
    """The identity's entity wins; only `system` may cross over."""
    own = entity_of(ident)
    if requested and requested != own:
        if (ident or {}).get("role") != "system":
            raise HTTPException(403, detail={"code": "CrossEntityForbidden",
                                             "entity": own, "requested": requested})
        return requested
    return own


def visible_entities(conn: sqlite3.Connection, ident: dict | None) -> list[str]:
    """Which entities this identity may read.

    `system` sees everything; a group-level `ceo` sees its own entity plus every
    descendant (governance_mode='group_direct'); everyone else sees only its own.
    Writes stay pinned to the identity's own entity — reading down is not writing down.
    """
    own = entity_of(ident)
    role = (ident or {}).get("role")
    if role == "system":
        return [r["id"] for r in conn.execute("SELECT id FROM legal_entities ORDER BY id")]
    if role != "ceo":
        return [own]
    row = conn.execute("SELECT governance_mode FROM legal_entities WHERE id = ?", (own,)).fetchone()
    if row is None or row["governance_mode"] != "group_direct":
        return [own]
    out, frontier = [own], [own]
    while frontier:
        parent = frontier.pop()
        kids = [r["id"] for r in conn.execute(
            "SELECT id FROM legal_entities WHERE parent_entity_id = ?", (parent,))]
        out.extend(kids)
        frontier.extend(kids)
    return out


def _in(column: str, values: list[str]) -> tuple[str, list[str]]:
    if len(values) == 1:
        return f"{column} = ?", list(values)
    return f"{column} IN ({','.join('?' * len(values))})", list(values)


def clause(conn: sqlite3.Connection, ident: dict | None,
           column: str = "legal_entity_id") -> tuple[str, list[str]]:
    return _in(column, visible_entities(conn, ident))


def scoped_task(conn: sqlite3.Connection, task_id: str, ents: list[str]) -> sqlite3.Row:
    where, args = _in("legal_entity_id", ents)
    row = conn.execute(f"SELECT * FROM tasks WHERE id = ? AND {where}",
                       [task_id] + args).fetchone()
    if row is None:
        raise _not_found("TaskNotFound", task_id)
    return row


def scoped_criterion(conn: sqlite3.Connection, criterion_id: str, ents: list[str]) -> sqlite3.Row:
    where, args = _in("t.legal_entity_id", ents)
    row = conn.execute(
        f"""SELECT c.* FROM acceptance_criteria c JOIN tasks t ON t.id = c.task_id
            WHERE c.id = ? AND {where}""", [criterion_id] + args).fetchone()
    if row is None:
        raise _not_found("CriterionNotFound", criterion_id)
    return row


def scoped_decision(conn: sqlite3.Connection, decision_id: str, ents: list[str]) -> sqlite3.Row:
    where, args = _in("legal_entity_id", ents)
    row = conn.execute(f"SELECT * FROM decision_packages WHERE id = ? AND {where}",
                       [decision_id] + args).fetchone()
    if row is None:
        raise _not_found("DecisionNotFound", decision_id)
    return row


def _not_found(code: str, ident: str) -> HTTPException:
    return HTTPException(404, detail={"code": code, "id": ident})


def task(conn: sqlite3.Connection, task_id: str, entity: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND legal_entity_id = ?", (task_id, entity)
    ).fetchone()
    if row is None:
        raise _not_found("TaskNotFound", task_id)
    return row


def run(conn: sqlite3.Connection, run_id: str, entity: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT r.*, t.legal_entity_id AS task_entity FROM task_runs r
           JOIN tasks t ON t.id = r.task_id
           WHERE r.id = ? AND t.legal_entity_id = ?""", (run_id, entity)
    ).fetchone()
    if row is None:
        raise _not_found("RunNotFound", run_id)
    return row


def criterion(conn: sqlite3.Connection, criterion_id: str, entity: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT c.* FROM acceptance_criteria c
           JOIN tasks t ON t.id = c.task_id
           WHERE c.id = ? AND t.legal_entity_id = ?""", (criterion_id, entity)
    ).fetchone()
    if row is None:
        raise _not_found("CriterionNotFound", criterion_id)
    return row


def decision(conn: sqlite3.Connection, decision_id: str, entity: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM decision_packages WHERE id = ? AND legal_entity_id = ?",
        (decision_id, entity)
    ).fetchone()
    if row is None:
        raise _not_found("DecisionNotFound", decision_id)
    return row


def pack_of(conn: sqlite3.Connection, ident: dict | None) -> str | None:
    """The agent's content pack, via its employee → position chain."""
    if not ident or not ident.get("employee_id"):
        return None
    row = conn.execute(
        """SELECT p.pack_id FROM ai_employees e
           LEFT JOIN positions p ON p.id = e.position_id
           WHERE e.id = ?""", (ident["employee_id"],)
    ).fetchone()
    return row["pack_id"] if row else None
