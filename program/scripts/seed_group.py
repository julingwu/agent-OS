"""Seed the group drill: two subsidiaries, their agents, identities and budgets.

Idempotent: re-running reuses entities/positions/employees and the existing
identity secrets, so the drill can be repeated without piling up identities.

    python scripts/seed_group.py

Credentials land in .env.drill (gitignored) — never in the repo.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import identity            # noqa: E402
from app.audit import new_id        # noqa: E402
from app.db import connect, init_db  # noqa: E402

ENTITIES = [
    ("LE-CONTENT", "内容科技子公司", "subsidiary"),
    ("LE-QUANT", "量化科技子公司", "subsidiary"),
]
POSITIONS = [
    ("POS-CONTENT-WRITER", "LE-CONTENT", "内容撰稿", "content-v1"),
    ("POS-QUANT-ENG", "LE-QUANT", "量化工程师", "code-v1"),
]
EMPLOYEES = [
    ("AI-CONTENT-01", "LE-CONTENT", "小文", "POS-CONTENT-WRITER"),
    ("AI-QUANT-01", "LE-QUANT", "小码", "POS-QUANT-ENG"),
]
POOL_TOKENS = 100_000


def ensure_entity(conn, eid: str, name: str, kind: str) -> None:
    if conn.execute("SELECT 1 FROM legal_entities WHERE id = ?", (eid,)).fetchone():
        return
    conn.execute(
        """INSERT INTO legal_entities (id, name, type, parent_entity_id, governance_mode,
                                       pack_policy, data_boundary)
           VALUES (?,?,?, 'LE-GROUP-HQ', 'subsidiary', 'follow_group', 'isolated')""",
        (eid, name, kind))


def ensure_position(conn, pid, eid, title, pack) -> None:
    if conn.execute("SELECT 1 FROM positions WHERE id = ?", (pid,)).fetchone():
        return
    conn.execute(
        """INSERT INTO positions (id, legal_entity_id, title, pack_id, state)
           VALUES (?,?,?,?, 'active')""", (pid, eid, title, pack))


def ensure_employee(conn, emp_id, eid, name, pos_id) -> None:
    if conn.execute("SELECT 1 FROM ai_employees WHERE id = ?", (emp_id,)).fetchone():
        return
    conn.execute(
        """INSERT INTO ai_employees (id, legal_entity_id, name, position_id, model, soul,
                                     focus, token_hash, status)
           VALUES (?,?,?,?, 'deepseek-chat', ?, ?, NULL, 'online')""",
        (emp_id, eid, name, pos_id,
         f"你是{name}，只对自己实体的业务负责，不跨实体打探。",
         "按判据交付，不夸大、不越权"))


def ensure_identity(conn, name, role, entity, employee=None) -> dict:
    row = conn.execute("SELECT * FROM identities WHERE name = ?", (name,)).fetchone()
    if row:
        return {"id": row["id"], "secret": row["secret"], "role": row["role"],
                "entity": row["legal_entity_id"], "reused": True}
    out = identity.issue(conn, name=name, role=role, legal_entity_id=entity,
                         employee_id=employee)
    return {"id": out["id"], "secret": out["secret"], "role": role, "entity": entity,
            "reused": False}


def ensure_pool(conn, entity: str) -> str:
    row = conn.execute(
        """SELECT id FROM budgets WHERE legal_entity_id = ? AND task_id IS NULL
             AND identity_id IS NULL""", (entity,)).fetchone()
    if row:
        return row["id"]
    bid = new_id("B")
    conn.execute(
        """INSERT INTO budgets (id, legal_entity_id, identity_id, task_id, limit_tokens,
                                reserved_tokens, used_tokens, state)
           VALUES (?,?,NULL,NULL,?,0,0,'active')""", (bid, entity, POOL_TOKENS))
    return bid


def main() -> int:
    init_db()
    conn = connect()
    try:
        for eid, name, kind in ENTITIES:
            ensure_entity(conn, eid, name, kind)
        for pid, eid, title, pack in POSITIONS:
            ensure_position(conn, pid, eid, title, pack)
        for emp_id, eid, name, pos in EMPLOYEES:
            ensure_employee(conn, emp_id, eid, name, pos)
        pools = {eid: ensure_pool(conn, eid) for eid, _, _ in ENTITIES}

        creds = {
            "ceo": ensure_identity(conn, "ceo-01", "ceo", "LE-GROUP-HQ"),
            "content": ensure_identity(conn, "content-manager", "manager", "LE-CONTENT",
                                       "AI-CONTENT-01"),
            "quant": ensure_identity(conn, "quant-manager", "manager", "LE-QUANT",
                                     "AI-QUANT-01"),
        }
        conn.commit()

        pathlib.Path(ROOT / ".env.drill").write_text(
            "\n".join(f"{k.upper()}_ID={v['id']}\n{k.upper()}_SECRET={v['secret']}"
                      for k, v in creds.items()) + "\n", encoding="utf-8")

        print(json.dumps({
            "entities": [e for e, _, _ in ENTITIES],
            "pools": pools,
            "identities": {k: {"id": v["id"], "role": v["role"], "entity": v["entity"],
                               "reused": v["reused"]} for k, v in creds.items()},
        }, ensure_ascii=False, indent=2))
        print("\ncredentials -> .env.drill (gitignored)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
