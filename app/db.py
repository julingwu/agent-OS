"""SQLite plumbing: connection, schema bootstrap, per-request transaction."""
from __future__ import annotations

import os
import pathlib
import sqlite3
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parents[1]


def db_path() -> str:
    """Resolved at call time so tests can point DB_PATH at a temp file."""
    return os.environ.get("DB_PATH") or str(ROOT / "data" / "oa.db")


def schema_sql() -> str:
    return (pathlib.Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or db_path()
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


MIGRATIONS = [
    ("artifacts", "state", "ALTER TABLE artifacts ADD COLUMN state TEXT DEFAULT 'active'"),
    ("ai_employees", "api_key", "ALTER TABLE ai_employees ADD COLUMN api_key TEXT"),
    ("ai_employees", "api_base", "ALTER TABLE ai_employees ADD COLUMN api_base TEXT"),
    ("tasks", "owner_employee_id", "ALTER TABLE tasks ADD COLUMN owner_employee_id TEXT"),
    ("chat_members", "state", "ALTER TABLE chat_members ADD COLUMN state TEXT DEFAULT 'active'"),
    ("ai_employees", "preset", "ALTER TABLE ai_employees ADD COLUMN preset TEXT DEFAULT 'standard'"),
    ("ai_employees", "role_id", "ALTER TABLE ai_employees ADD COLUMN role_id TEXT"),
    ("chats", "pinned", "ALTER TABLE chats ADD COLUMN pinned INTEGER DEFAULT 0"),
    ("chats", "hidden", "ALTER TABLE chats ADD COLUMN hidden INTEGER DEFAULT 0"),
    ("chats", "deleted_at", "ALTER TABLE chats ADD COLUMN deleted_at TIMESTAMP"),
    ("chats", "workdir", "ALTER TABLE chats ADD COLUMN workdir TEXT"),

    ("wecom_pushes", "created_at",
     "ALTER TABLE wecom_pushes ADD COLUMN created_at TIMESTAMP"),
    ("task_runs", "usage_tokens", "ALTER TABLE task_runs ADD COLUMN usage_tokens INTEGER DEFAULT 0"),
    ("task_runs", "usage_baseline", "ALTER TABLE task_runs ADD COLUMN usage_baseline INTEGER DEFAULT 0"),
    ("identities", "employee_id", "ALTER TABLE identities ADD COLUMN employee_id TEXT"),
    ("identities", "tenant_id", "ALTER TABLE identities ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("legal_entities", "tenant_id", "ALTER TABLE legal_entities ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("tasks", "tenant_id", "ALTER TABLE tasks ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "tenant_id", "ALTER TABLE task_runs ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("artifacts", "tenant_id", "ALTER TABLE artifacts ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "error", "ALTER TABLE task_runs ADD COLUMN error TEXT"),
    ("task_runs", "mcp_fingerprint", "ALTER TABLE task_runs ADD COLUMN mcp_fingerprint TEXT"),
    ("projects", "tenant_id", "ALTER TABLE projects ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("projects", "veto_note", "ALTER TABLE projects ADD COLUMN veto_note TEXT"),
    ("chats", "tenant_id", "ALTER TABLE chats ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "actor_identity", "ALTER TABLE task_runs ADD COLUMN actor_identity TEXT"),
    ("task_runs", "approval_id", "ALTER TABLE task_runs ADD COLUMN approval_id TEXT"),
    ("task_runs", "prompt_text", "ALTER TABLE task_runs ADD COLUMN prompt_text TEXT"),
    ("decision_packages", "created_by", "ALTER TABLE decision_packages ADD COLUMN created_by TEXT"),
    ("budgets", "identity_id", "ALTER TABLE budgets ADD COLUMN identity_id TEXT"),
    ("budgets", "limit_amount", "ALTER TABLE budgets ADD COLUMN limit_amount REAL DEFAULT 0"),
    ("budgets", "reserved_amount", "ALTER TABLE budgets ADD COLUMN reserved_amount REAL DEFAULT 0"),
    ("budgets", "used_amount", "ALTER TABLE budgets ADD COLUMN used_amount REAL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> set[str]:
    added_tenants = set()
    for table, column, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # 表还没建（如 chat_members 由 store.ensure 建），跳过
        if column not in cols:
            conn.execute(ddl)
            if column == "tenant_id":
                added_tenants.add(table)
    return added_tenants


def _backfill_tenants(conn: sqlite3.Connection, added: set[str]) -> None:
    """Inherit tenant ownership once for old tables, then only repair empty rows."""
    cols = {name: {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
            for name in ("legal_entities", "identities", "projects", "chats",
                         "tasks", "task_runs", "artifacts", "ai_employees")}
    sources = {
        "legal_entities": [],
        "identities": [("legal_entities", "legal_entity_id", "id")],
        "projects": [("legal_entities", "legal_entity_id", "id")],
        "chats": [("legal_entities", "entity_id", "id")],
        "tasks": [("projects", "project_id", "id"),
                  ("legal_entities", "legal_entity_id", "id")],
        "task_runs": [("tasks", "task_id", "id")],
        "artifacts": [("task_runs", "run_id", "id")],
    }
    for table, parents in sources.items():
        if "tenant_id" not in cols[table]:
            continue
        inherited = [f"(SELECT NULLIF(p.tenant_id,'') FROM {parent} p"
                     f" WHERE p.{pk}={table}.{fk})"
                     for parent, fk, pk in parents
                     if fk in cols[table] and "tenant_id" in cols[parent]]
        if table == "chats" and "employee_id" in cols[table] and cols["ai_employees"]:
            inherited.append("(SELECT NULLIF(le.tenant_id,'') FROM ai_employees e"
                             " JOIN legal_entities le ON le.id=e.legal_entity_id"
                             " WHERE e.id=chats.employee_id)")
        value = ", ".join([*inherited, f"NULLIF({table}.tenant_id,'')", "'TENANT-HQ'"])
        where = "1=1" if table in added else "COALESCE(tenant_id,'')=''"
        conn.execute(f"UPDATE {table} SET tenant_id=COALESCE({value}) WHERE {where}")


def init_db(path: str | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(schema_sql())
        added_tenants = _migrate(conn)
        _backfill_tenants(conn, added_tenants)
        conn.execute("UPDATE wecom_pushes SET created_at = CURRENT_TIMESTAMP"
                     " WHERE created_at IS NULL")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
