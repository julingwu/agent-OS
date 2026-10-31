"""Audit log: the only writer of ai_audit_logs."""
from __future__ import annotations

import json
import uuid
from typing import Any

import sqlite3


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _j(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def write_audit(
    conn: sqlite3.Connection,
    *,
    legal_entity_id: str,
    verb: str,
    task_id: str | None = None,
    run_id: str | None = None,
    layer: str = "system",
    actor: str = "system",
    action: str | None = None,
    target: str | None = None,
    payload: Any = None,
    response: Any = None,
    idempotency_key: str | None = None,
    replay_of: str | None = None,
    is_deterministic: int = 1,
) -> str:
    audit_id = new_id("AL")
    conn.execute(
        """INSERT INTO ai_audit_logs
           (id, legal_entity_id, task_id, run_id, verb, layer, actor, action, target,
            payload, response_json, idempotency_key, replay_of, is_deterministic)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            audit_id,
            legal_entity_id,
            task_id,
            run_id,
            verb,
            layer,
            actor,
            action or verb,
            target,
            _j(payload),
            _j(response),
            idempotency_key,
            replay_of,
            is_deterministic,
        ),
    )
    return audit_id
