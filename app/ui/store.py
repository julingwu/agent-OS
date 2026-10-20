"""Chat storage for the self-hosted console (replaces the Octo bridge).

Two tables:
  chats          one row per conversation (dm with an AI employee, or a group)
  chat_messages  the transcript; a message_may carry a task_id so the UI can
                 render the live task card inline
"""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'dm',          -- dm | group
  employee_id TEXT,                          -- dm partner (ai_employees.id)
  entity_id TEXT,
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  pinned INTEGER DEFAULT 0,
  hidden INTEGER DEFAULT 0,
  deleted_at TIMESTAMP,
  workdir TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL,
  role TEXT NOT NULL,                        -- human | agent | system
  author TEXT,
  kind TEXT NOT NULL DEFAULT 'text',         -- text | card | thinking | tool
  content TEXT NOT NULL,
  task_id TEXT,
  meta TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, id);

CREATE TABLE IF NOT EXISTS chat_members (
  chat_id TEXT NOT NULL,
  employee_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',      -- pm | member | reviewer
  state TEXT NOT NULL DEFAULT 'active',     -- active | removed
  PRIMARY KEY (chat_id, employee_id)
);


CREATE TABLE IF NOT EXISTS user_preferences (
  identity_id TEXT NOT NULL,
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (identity_id, key)
);

CREATE TABLE IF NOT EXISTS alerts (
  id         TEXT PRIMARY KEY,
  level      TEXT NOT NULL DEFAULT 'warning',
  kind       TEXT NOT NULL,
  target     TEXT,
  message    TEXT NOT NULL,
  acked      INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
  id            TEXT PRIMARY KEY,
  chat_id       TEXT NOT NULL,
  title         TEXT NOT NULL,
  prompt        TEXT NOT NULL,
  at_time       TEXT NOT NULL,
  enabled       INTEGER DEFAULT 1,
  last_run_date TEXT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_documents (
  id              TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL,
  name            TEXT NOT NULL,
  path            TEXT NOT NULL,
  bytes           INTEGER DEFAULT 0,
  excerpt         TEXT,
  state           TEXT DEFAULT 'active',
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS group_hops (
  chat_id TEXT PRIMARY KEY,
  window_start TIMESTAMP,
  hops INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_stream (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,                        -- thinking | tool | status
  text TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_run_stream_run ON run_stream(run_id, id);
"""


def ensure(conn: sqlite3.Connection) -> None:
    # DDL is tables/indexes only; execute individually to preserve transactions.
    for statement in DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
    if "tenant_id" not in cols:
        conn.execute("ALTER TABLE chats ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'")
        from ..db import _backfill_tenants
        _backfill_tenants(conn, {"chats"})


def get_or_create_dm(conn: sqlite3.Connection, employee_id: str, title: str,
                     entity_id: str | None, tenant_id: str | None = None) -> sqlite3.Row:
    from ..tenancy import checked_entity_tenant
    emp = conn.execute("SELECT legal_entity_id FROM ai_employees WHERE id=?",
                       (employee_id,)).fetchone()
    if emp is None:
        raise ValueError("UnknownEmployee")
    if entity_id and entity_id != emp["legal_entity_id"]:
        raise ValueError("EmployeeEntityMismatch")
    entity_id = emp["legal_entity_id"]
    tenant_id = checked_entity_tenant(conn, entity_id, tenant_id)
    row = conn.execute("SELECT * FROM chats WHERE employee_id = ? AND kind='dm'"
                       " AND COALESCE(tenant_id,'TENANT-HQ')=?",
                       (employee_id, tenant_id)).fetchone()
    if row:
        return row
    import secrets
    cid = "C-" + secrets.token_hex(8)
    conn.execute("""INSERT INTO chats (id, title, kind, employee_id, entity_id, tenant_id)
                    VALUES (?,?, 'dm', ?, ?, ?)""",
                 (cid, title, employee_id, entity_id, tenant_id))
    conn.commit()
    return conn.execute("SELECT * FROM chats WHERE id = ?", (cid,)).fetchone()


def add_message(conn: sqlite3.Connection, chat_id: str, role: str, content: str,
                *, author: str | None = None, kind: str = "text",
                task_id: str | None = None, meta: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO chat_messages (chat_id, role, author, kind, content, task_id, meta)
           VALUES (?,?,?,?,?,?,?)""",
        (chat_id, role, author, kind, content, task_id, meta))
    conn.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chat_id,))
    conn.commit()
    return int(cur.lastrowid)


def messages(conn: sqlite3.Connection, chat_id: str, after_id: int = 0,
             limit: int = 200) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM chat_messages WHERE chat_id = ? AND id > ?
           ORDER BY id ASC LIMIT ?""", (chat_id, after_id, limit)).fetchall()
    return [dict(r) for r in rows]


def list_chats(conn: sqlite3.Connection, tenant_id: str | None = None) -> list[dict]:
    sql = """SELECT c.*, e.name AS employee_name, e.status AS employee_status,
                  (SELECT content FROM chat_messages m WHERE m.chat_id = c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_message,
                  (SELECT role FROM chat_messages m WHERE m.chat_id = c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_role,
                  (SELECT created_at FROM chat_messages m WHERE m.chat_id = c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_at
           FROM chats c LEFT JOIN ai_employees e ON e.id = c.employee_id
           WHERE COALESCE(c.hidden,0)=0 AND c.deleted_at IS NULL"""
    args = []
    if tenant_id:
        sql += " AND COALESCE(c.tenant_id,'TENANT-HQ')=?"
        args.append(tenant_id)
    sql += " ORDER BY COALESCE(c.pinned,0) DESC, COALESCE(last_at, c.created_at) DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def stream(conn: sqlite3.Connection, run_id: str, after_id: int = 0,
           limit: int = 80) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM run_stream WHERE run_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
        (run_id, after_id, limit)).fetchall()
    return [dict(r) for r in rows]


def add_stream(conn: sqlite3.Connection, run_id: str, kind: str, text: str) -> None:
    conn.execute("INSERT INTO run_stream (run_id, kind, text) VALUES (?,?,?)",
                 (run_id, kind, text[:600]))
    conn.commit()


def create_group(conn: sqlite3.Connection, name: str, employee_ids: list[str],
                 tenant_id: str | None = None) -> sqlite3.Row:
    import secrets
    from ..tenancy import DEFAULT_TENANT, checked_entity_tenant
    for employee_id in employee_ids:
        emp = conn.execute("SELECT legal_entity_id FROM ai_employees WHERE id=?",
                           (employee_id,)).fetchone()
        if emp is None:
            raise ValueError("UnknownEmployee")
        tenant_id = checked_entity_tenant(conn, emp["legal_entity_id"], tenant_id)
    tenant_id = tenant_id or DEFAULT_TENANT
    cid = "G-" + secrets.token_hex(8)
    conn.execute("INSERT INTO chats (id, title, kind, tenant_id) VALUES (?,?, 'group', ?)",
                 (cid, name, tenant_id))
    n = len(employee_ids)
    for i, emp in enumerate(employee_ids):
        role = "pm" if i == 0 else ("reviewer" if (n >= 3 and i == n - 1) else "member")
        conn.execute("INSERT OR IGNORE INTO chat_members (chat_id, employee_id, role)"
                     " VALUES (?,?,?)", (cid, emp, role))
    conn.commit()
    return conn.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()


def members(conn: sqlite3.Connection, chat_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT m.employee_id, m.role, e.name, e.model, e.legal_entity_id, p.title
           FROM chat_members m JOIN ai_employees e ON e.id = m.employee_id
           LEFT JOIN positions p ON p.id = e.position_id
           WHERE m.chat_id = ? AND COALESCE(m.state,'active')='active' ORDER BY CASE m.role WHEN 'pm' THEN 0 WHEN 'reviewer' THEN 2
                                        ELSE 1 END""", (chat_id,)).fetchall()
    return [dict(r) for r in rows]


def hop_allows(conn: sqlite3.Connection, chat_id: str, limit: int = 4,
               window: int = 600) -> bool:
    """Anti-loop: a group answers at most `limit` agent turns per window."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)  # naive UTC, same shape as the rows
    row = conn.execute("SELECT * FROM group_hops WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO group_hops (chat_id, window_start, hops) VALUES (?,?,0)",
                     (chat_id, now.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    started = row["window_start"] or now.strftime("%Y-%m-%d %H:%M:%S")
    age = (now - _dt.datetime.strptime(started, "%Y-%m-%d %H:%M:%S")).total_seconds()
    if age > window:
        conn.execute("UPDATE group_hops SET window_start=?, hops=0 WHERE chat_id=?",
                     (now.strftime("%Y-%m-%d %H:%M:%S"), chat_id))
        conn.commit()
        return True
    return (row["hops"] or 0) < limit


def note_hop(conn: sqlite3.Connection, chat_id: str) -> None:
    conn.execute("UPDATE group_hops SET hops = COALESCE(hops,0) + 1 WHERE chat_id=?", (chat_id,))
    conn.commit()
