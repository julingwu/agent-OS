"""Idempotency: a key maps to the first response; every later hit replays it.

The first execution stores its response body in ai_audit_logs.response_json
(inside the same transaction as the business write). A replay never touches a
business table: it returns the stored body verbatim and appends exactly one
audit row with verb='replay' and replay_of=<key>.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .audit import write_audit


def lookup(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute(
        "SELECT response_json, task_id, run_id, legal_entity_id FROM ai_audit_logs WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "response": json.loads(row["response_json"]) if row["response_json"] else {},
        "task_id": row["task_id"],
        "run_id": row["run_id"],
        "legal_entity_id": row["legal_entity_id"],
    }


def replay(conn: sqlite3.Connection, key: str, hit: dict, *, verb: str) -> dict:
    write_audit(
        conn,
        legal_entity_id=hit["legal_entity_id"],
        verb="replay",
        task_id=hit["task_id"],
        run_id=hit["run_id"],
        layer="system",
        action="replay",
        target=key,
        payload={"key": key, "original_verb": verb},
        response={"replayed": True},
        replay_of=key,
    )
    return hit["response"]


def history(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    original = conn.execute(
        "SELECT * FROM ai_audit_logs WHERE idempotency_key = ?", (key,)
    ).fetchone()
    replays = conn.execute(
        "SELECT * FROM ai_audit_logs WHERE replay_of = ? ORDER BY created_at", (key,)
    ).fetchall()
    return {
        "found": original is not None,
        "original": dict(original) if original else None,
        "replays": [dict(r) for r in replays],
    }
