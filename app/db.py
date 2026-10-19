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
    ("task_runs", "actor_identity", "ALTER TABLE task_runs ADD COLUMN actor_identity TEXT"),
    ("task_runs", "approval_id", "ALTER TABLE task_runs ADD COLUMN approval_id TEXT"),
    ("task_runs", "prompt_text", "ALTER TABLE task_runs ADD COLUMN prompt_text TEXT"),
    ("decision_packages", "created_by", "ALTER TABLE decision_packages ADD COLUMN created_by TEXT"),
    ("budgets", "identity_id", "ALTER TABLE budgets ADD COLUMN identity_id TEXT"),
    ("budgets", "limit_amount", "ALTER TABLE budgets ADD COLUMN limit_amount REAL DEFAULT 0"),
    ("budgets", "reserved_amount", "ALTER TABLE budgets ADD COLUMN reserved_amount REAL DEFAULT 0"),
    ("budgets", "used_amount", "ALTER TABLE budgets ADD COLUMN used_amount REAL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # 表还没建（如 chat_members 由 store.ensure 建），跳过
        if column not in cols:
            conn.execute(ddl)


def init_db(path: str | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(schema_sql())
        _migrate(conn)
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
