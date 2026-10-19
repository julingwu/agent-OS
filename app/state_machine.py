"""The state machine. The database trigger mirrors this table (defence in depth)."""
from __future__ import annotations

import sqlite3

from .audit import write_audit

# (current_state, event) -> next_state
TRANSITIONS: dict[tuple[str, str], str] = {
    ("todo", "run_spawned"): "assigned",
    ("assigned", "run_started"): "in_progress",
    ("assigned", "run_failed"): "failed",          # budget denied before spawn
    ("in_progress", "run_succeeded"): "pending_review",
    ("in_progress", "run_failed"): "failed",
    ("in_progress", "run_cancelled"): "cancelled",
    ("pending_review", "review_passed"): "completed",
    ("pending_review", "review_failed"): "rejected",
    ("rejected", "retry"): "in_progress",
    ("rejected", "rejudge"): "pending_review",   # same artifact, fixed judgement
    ("failed", "retry"): "in_progress",          # retry after budget/tool failure
}

TERMINAL = {"completed", "failed", "cancelled"}


class IllegalStateTransition(Exception):
    def __init__(self, current: str, event: str):
        self.current = current
        self.event = event
        super().__init__(f"IllegalStateTransition: no transition from '{current}' on event '{event}'")


def next_state(current: str, event: str) -> str | None:
    return TRANSITIONS.get((current, event))


def transition(
    conn: sqlite3.Connection,
    task_id: str,
    event: str,
    *,
    actor: str = "system",
    payload: dict | None = None,
) -> str:
    row = conn.execute(
        "SELECT id, state, legal_entity_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"task not found: {task_id}")
    current = row["state"]
    target = next_state(current, event)
    if target is None:
        raise IllegalStateTransition(current, event)
    conn.execute(
        "UPDATE tasks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (target, task_id),
    )
    write_audit(
        conn,
        legal_entity_id=row["legal_entity_id"],
        verb="state_transition",
        task_id=task_id,
        layer="system",
        actor=actor,
        action="state_transition",
        target=task_id,
        payload={"from": current, "to": target, "event": event, **(payload or {})},
        response={"state": target},
    )
    return target
