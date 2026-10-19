"""Acceptance package: L0 rules, L1 manual, L2 scripts."""
from __future__ import annotations

import sqlite3


def task_text(conn: sqlite3.Connection, task_id: str) -> str | None:
    """The text the checks run on: newest artifact of the task, else newest evidence snippet."""
    row = conn.execute(
        """SELECT a.content FROM artifacts a
           JOIN task_runs r ON r.id = a.run_id
           WHERE r.task_id = ? AND a.content IS NOT NULL
           ORDER BY a.created_at DESC LIMIT 1""",
        (task_id,),
    ).fetchone()
    if row and row["content"]:
        return row["content"]
    row = conn.execute(
        """SELECT e.content_snippet FROM evidence_refs e
           JOIN acceptance_criteria c ON c.id = e.owner_id
           WHERE c.task_id = ? AND e.content_snippet IS NOT NULL
           ORDER BY e.created_at DESC LIMIT 1""",
        (task_id,),
    ).fetchone()
    return row["content_snippet"] if row else None
