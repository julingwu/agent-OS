"""Run-level locks: one run per task, one run per DSH session.

Two agents must never drive the same DSH session at once — the second process
gets `already owned by an active write handle` and the run dies halfway. The
lock makes that a clean 409 before any process is spawned.

Locks live in SQLite so they work across processes. They carry a TTL: a crashed
run cannot block a task forever, and stealing an expired lock is recorded.
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from contextlib import contextmanager

from fastapi import HTTPException

from .audit import write_audit


def ttl_seconds() -> int:
    return int(os.environ.get("M0_LOCK_TTL", "1800"))


class Locked(Exception):
    def __init__(self, resource: str, holder: str):
        self.resource, self.holder = resource, holder
        super().__init__(f"{resource} is held by {holder}")


def _now() -> _dt.datetime:
    # UTC, naive: SQLite's datetime('now') is UTC, mixing in local time made
    # freshly-written locks look already expired (UTC+8 skew).
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _expired(row: sqlite3.Row) -> bool:
    if not row["expires_at"]:
        return False
    try:
        return _dt.datetime.fromisoformat(row["expires_at"]) < _now()
    except ValueError:
        return True


def acquire(conn: sqlite3.Connection, resource: str, run_id: str,
            *, entity: str, task_id: str | None = None) -> bool:
    """True when this run owns the lock; raises Locked when someone else does."""
    row = conn.execute("SELECT * FROM run_locks WHERE resource = ?", (resource,)).fetchone()
    now = _now()
    expires = (now + _dt.timedelta(seconds=ttl_seconds())).isoformat(timespec="seconds")
    if row is None:
        conn.execute(
            "INSERT INTO run_locks (resource, run_id, acquired_at, expires_at) VALUES (?,?,?,?)",
            (resource, run_id, now.isoformat(timespec="seconds"), expires))
        conn.commit()
        return True
    if row["run_id"] == run_id:
        return True
    if _expired(row):
        conn.execute(
            "UPDATE run_locks SET run_id = ?, acquired_at = ?, expires_at = ? WHERE resource = ?",
            (run_id, now.isoformat(timespec="seconds"), expires, resource))
        write_audit(conn, legal_entity_id=entity, verb="lock_stolen", task_id=task_id,
                    run_id=run_id, action="lock_stolen", target=resource,
                    payload={"previous_holder": row["run_id"], "expired_at": row["expires_at"]},
                    response={"ok": True})
        conn.commit()
        return True
    raise Locked(resource, row["run_id"])


def release(conn: sqlite3.Connection, resource: str, run_id: str) -> None:
    conn.execute("DELETE FROM run_locks WHERE resource = ? AND run_id = ?", (resource, run_id))
    conn.commit()


@contextmanager
def hold(conn: sqlite3.Connection, resource: str, run_id: str, *, entity: str,
         task_id: str | None = None):
    acquire(conn, resource, run_id, entity=entity, task_id=task_id)
    try:
        yield
    finally:
        try:
            release(conn, resource, run_id)
        except sqlite3.Error:
            pass


def holder(conn: sqlite3.Connection, resource: str) -> str | None:
    row = conn.execute("SELECT run_id, expires_at FROM run_locks WHERE resource = ?",
                       (resource,)).fetchone()
    if row is None or _expired(row):
        return None
    return row["run_id"]


def conflict(resource: str, run_id: str) -> HTTPException:
    code = "SessionLocked" if resource.startswith("session:") else "TaskLocked"
    return HTTPException(409, detail={"code": code, "resource": resource,
                                      "holder": run_id})
