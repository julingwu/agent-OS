"""Workspace tenant context, inherited from verified identities and entities.

These helpers never commit the caller's transaction. Entity permissions remain
an additional boundary; tenant membership alone does not authorize an action.
"""
from __future__ import annotations

import sqlite3

DEFAULT_TENANT = "TENANT-HQ"


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT DEFAULT 'internal',
        state TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT OR IGNORE INTO tenants (id,name,plan)"
                 " VALUES (?, '集团总部', 'internal')", (DEFAULT_TENANT,))


def of_identity(conn: sqlite3.Connection, identity_id: str) -> str:
    row = conn.execute("SELECT tenant_id,state FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    if row is None or row["state"] != "active":
        raise PermissionError("UnknownIdentity")
    tenant_id = row["tenant_id"] or DEFAULT_TENANT
    tenant = conn.execute("SELECT state FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if tenant is None or tenant["state"] != "active":
        raise PermissionError("InactiveTenant")
    return tenant_id


def resolve(conn: sqlite3.Connection, identity_id: str, header: str | None = None) -> str:
    own = of_identity(conn, identity_id)
    if header and header != own:
        raise PermissionError("TenantMismatch")
    return own


def of_entity(conn: sqlite3.Connection, entity_id: str | None) -> str:
    if not entity_id:
        return DEFAULT_TENANT
    row = conn.execute("SELECT tenant_id FROM legal_entities WHERE id=?",
                       (entity_id,)).fetchone()
    if row is None:
        raise ValueError("UnknownEntity")
    return row["tenant_id"] or DEFAULT_TENANT


def checked_entity_tenant(conn: sqlite3.Connection, entity_id: str | None,
                          requested: str | None = None) -> str:
    own = of_entity(conn, entity_id)
    if requested and requested != own:
        raise ValueError("TenantMismatch")
    return own


def name_of(conn: sqlite3.Connection, tenant_id: str) -> str:
    row = conn.execute("SELECT name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return row["name"] if row else tenant_id
