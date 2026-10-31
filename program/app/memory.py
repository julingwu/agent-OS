"""F2 memory isolation.

A memory entry lives in exactly one scope:

    global                      readable by every agent
    entity:<entity_id>          readable inside that legal entity only
    pack:<pack_id>              readable by that content pack only
    entity:<entity_id>:pack:<pack_id>   both, at once

Reading is the union of the reader's own entity, its pack, and global. Anything
else is invisible — not "filtered out later", but never selected.
"""
from __future__ import annotations

import sqlite3

from .audit import new_id, write_audit


class ScopeError(ValueError):
    pass


def parse(scope: str) -> tuple[str, str | None, str | None]:
    """'entity:LE-A:pack:content-v1' -> ('entity+pack', 'LE-A', 'content-v1')."""
    parts = (scope or "").split(":")
    if parts == ["global"]:
        return "global", None, None
    if len(parts) == 2 and parts[0] == "entity":
        return "entity", parts[1], None
    if len(parts) == 2 and parts[0] == "pack":
        return "pack", None, parts[1]
    if len(parts) == 4 and parts[0] == "entity" and parts[2] == "pack":
        return "entity+pack", parts[1], parts[3]
    raise ScopeError(f"bad scope {scope!r}")


def can_read(scope: str, entity: str, pack: str | None) -> bool:
    try:
        kind, ent, pk = parse(scope)
    except ScopeError:
        return False
    if kind == "global":
        return True
    if kind == "entity":
        return ent == entity
    if kind == "pack":
        return bool(pack) and pk == pack
    return ent == entity and bool(pack) and pk == pack


def readable_scopes(entity: str, pack: str | None) -> list[str]:
    scopes = ["global", f"entity:{entity}"]
    if pack:
        scopes += [f"pack:{pack}", f"entity:{entity}:pack:{pack}"]
    return scopes


def write(conn: sqlite3.Connection, *, employee_id: str, employee_entity: str,
          employee_pack: str | None, scope: str, content: str) -> dict:
    kind, ent, pk = parse(scope)
    if kind in ("entity", "entity+pack") and ent != employee_entity:
        raise ScopeError("cannot write into another legal entity's memory")
    if kind in ("pack", "entity+pack"):
        if not employee_pack or pk != employee_pack:
            raise ScopeError("cannot write into another pack's memory")
    if not content.strip():
        raise ScopeError("empty content")

    mid = new_id("MEM")
    conn.execute(
        "INSERT INTO memory_entries (id, employee_id, scope, content) VALUES (?,?,?,?)",
        (mid, employee_id, scope, content),
    )
    write_audit(conn, legal_entity_id=employee_entity, verb="memory_write",
                actor=employee_id, action="memory_write", target=mid,
                payload={"scope": scope, "chars": len(content)},
                response={"ok": True})
    return {"id": mid, "scope": scope}


def read(conn: sqlite3.Connection, *, entity: str, pack: str | None,
         scope: str | None = None) -> list[dict]:
    allowed = readable_scopes(entity, pack)
    if scope:
        if scope not in allowed:
            return []
        allowed = [scope]
    marks = ",".join("?" * len(allowed))
    rows = conn.execute(
        f"""SELECT * FROM memory_entries WHERE scope IN ({marks})
            ORDER BY created_at DESC LIMIT 200""", allowed
    ).fetchall()
    return [dict(r) for r in rows]


def get(conn: sqlite3.Connection, memory_id: str, *, entity: str,
        pack: str | None) -> dict | None:
    row = conn.execute("SELECT * FROM memory_entries WHERE id = ?", (memory_id,)).fetchone()
    if row is None or not can_read(row["scope"], entity, pack):
        return None
    return dict(row)
