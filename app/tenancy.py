"""Tenant context (v3.0): one deployment can carry several tenant workspaces.

SQLite stays single-file; isolation is by `tenant_id` on the rows that matter
(identities / projects / chats) plus a validated `X-Tenant-Id` request header.
Existing installs are backfilled into `TENANT-HQ`; nothing is deleted.
"""
from __future__ import annotations

import sqlite3

DEFAULT_TENANT = "TENANT-HQ"

DDL = """
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  plan TEXT DEFAULT 'internal',          -- internal | team | enterprise
  state TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO tenants (id, name, plan) VALUES ('TENANT-HQ', '集团总部', 'internal');
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def of_identity(conn: sqlite3.Connection, identity_id: str) -> str:
    ensure(conn)
    row = conn.execute("SELECT tenant_id FROM identities WHERE id=?", (identity_id,)).fetchone()
    if row is None:
        return DEFAULT_TENANT
    return row["tenant_id"] or DEFAULT_TENANT


def resolve(conn: sqlite3.Connection, identity_id: str, header: str | None) -> str:
    """Identity is the source of truth; a foreign header is an error, not a hint."""
    own = of_identity(conn, identity_id)
    if header and header != own:
        raise PermissionError("TenantMismatch")
    return own


def of_entity(conn: sqlite3.Connection, entity_id: str | None) -> str:
    ensure(conn)
    if not entity_id:
        return DEFAULT_TENANT
    row = conn.execute("SELECT tenant_id FROM legal_entities WHERE id=?", (entity_id,)).fetchone()
    return (row["tenant_id"] if row and row["tenant_id"] else DEFAULT_TENANT)


def name_of(conn: sqlite3.Connection, tenant_id: str) -> str:
    ensure(conn)
    row = conn.execute("SELECT name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    return row["name"] if row else tenant_id
