"""Background watcher: turn shell state changes into WeCom cards, exactly once.

The shell does not know WeCom exists — this polls the database and pushes, so the
API/前端 stay decoupled. `wecom_pushes` is the ledger that makes it idempotent;
a failed send is retried on the next tick.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from ..db import connect
from . import bridge
from .client import WeComClient

log = logging.getLogger("wecom.watcher")


class Watcher(threading.Thread):
    daemon = True

    def __init__(self, interval: float = 3.0) -> None:
        super().__init__(name="wecom-watcher")
        self.interval = interval
        self.stop_event = threading.Event()
        self.client = WeComClient(conn_factory=connect)
        self.last_error: str | None = None

    # ------------------------------------------------------------------ loop
    def run(self) -> None:                       # pragma: no cover - thread body
        log.info("wecom watcher started (interval=%.1fs, mock=%s)",
                 self.interval, os.environ.get("WECOM_MOCK", "0"))
        while not self.stop_event.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:             # noqa: BLE001 - never kill the thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("watcher tick failed: %s", self.last_error)
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()

    # ------------------------------------------------------------------ tick
    def tick(self) -> dict:
        sent = {"task_review": 0, "decision_ceo": 0, "retried": 0}
        with connect() as conn:
            for task in conn.execute(
                    """SELECT * FROM tasks WHERE state = 'pending_review'
                       ORDER BY created_at ASC LIMIT 50""").fetchall():
                for user in bridge.recipients_for(conn, entity=task["legal_entity_id"]):
                    if self._claim(conn, "task_review", task["id"], user):
                        try:
                            criteria, counts = bridge.criteria_counts(conn, task["id"])
                            msg_id = self.client.send_card(
                                user, bridge.task_card(dict(task), criteria, counts, user))
                        except Exception as exc:                  # noqa: BLE001
                            self._fail(conn, "task_review", task["id"], user, exc)
                            raise
                        self._settle(conn, "task_review", task["id"], user, msg_id)
                        sent["task_review"] += 1

            for dec in conn.execute(
                    """SELECT * FROM decision_packages WHERE state = 'escalated_ceo'
                       ORDER BY created_at ASC LIMIT 50""").fetchall():
                for user in bridge.recipients_for(conn, entity=dec["legal_entity_id"],
                                                  need_ceo=True):
                    if self._claim(conn, "decision_ceo", dec["id"], user):
                        try:
                            msg_id = self.client.send_card(user, bridge.decision_card(dict(dec)))
                        except Exception as exc:                  # noqa: BLE001
                            self._fail(conn, "decision_ceo", dec["id"], user, exc)
                            raise
                        self._settle(conn, "decision_ceo", dec["id"], user, msg_id)
                        sent["decision_ceo"] += 1

            retried = self._retry_failed(conn)
            sent["retried"] = retried
        return sent

    # ------------------------------------------------------------------ ledger
    def _claim(self, conn, kind: str, target: str, user: str) -> bool:
        cur = conn.execute(
            """INSERT OR IGNORE INTO wecom_pushes (id, kind, target_id, wecom_userid, status)
               VALUES (?,?,?,?, 'pending')""",
            (f"WP-{kind}-{target}-{user}", kind, target, user))
        conn.commit()
        return cur.rowcount == 1

    def _settle(self, conn, kind: str, target: str, user: str, msg_id: str) -> None:
        conn.execute(
            """UPDATE wecom_pushes SET status='sent', msg_id=?, sent_at=CURRENT_TIMESTAMP,
                                      error=NULL
               WHERE kind=? AND target_id=? AND wecom_userid=?""",
            (msg_id, kind, target, user))
        conn.commit()

    def _fail(self, conn, kind: str, target: str, user: str, exc: Exception) -> None:
        conn.execute(
            """UPDATE wecom_pushes SET status='failed', error=?
               WHERE kind=? AND target_id=? AND wecom_userid=?""",
            (f"{type(exc).__name__}: {exc}"[:400], kind, target, user))
        conn.commit()

    def _retry_failed(self, conn) -> int:
        # 'failed', plus 'pending' rows older than two minutes: a crash between claim
        # and send must not strand a notification forever.
        rows = conn.execute(
            """SELECT * FROM wecom_pushes
               WHERE status='failed'
                  OR (status='pending' AND created_at < datetime('now','-2 minutes'))
               LIMIT 20""").fetchall()
        done = 0
        for r in rows:
            try:
                if r["kind"] == "task_review":
                    task = conn.execute("SELECT * FROM tasks WHERE id=?",
                                        (r["target_id"],)).fetchone()
                    if task is None:
                        continue
                    criteria, counts = bridge.criteria_counts(conn, task["id"])
                    msg_id = self.client.send_card(
                        r["wecom_userid"], bridge.task_card(dict(task), criteria, counts, r["wecom_userid"]))
                else:
                    dec = conn.execute("SELECT * FROM decision_packages WHERE id=?",
                                       (r["target_id"],)).fetchone()
                    if dec is None:
                        continue
                    msg_id = self.client.send_card(
                        r["wecom_userid"], bridge.decision_card(dict(dec)))
                self._settle(conn, r["kind"], r["target_id"], r["wecom_userid"], msg_id)
                done += 1
            except Exception as exc:             # noqa: BLE001
                log.warning("retry failed for %s: %s", r["target_id"], exc)
                conn.execute("UPDATE wecom_pushes SET error=? WHERE id=?", (str(exc), r["id"]))
                conn.commit()
        return done


_watcher: Watcher | None = None


def start(interval: float = 3.0) -> Watcher:
    global _watcher
    if _watcher is None or not _watcher.is_alive():
        _watcher = Watcher(interval=interval)
        _watcher.start()
    return _watcher


def stop() -> None:
    global _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None


def status() -> dict:
    w = _watcher
    return {"running": bool(w and w.is_alive()),
            "interval": getattr(w, "interval", None),
            "last_error": getattr(w, "last_error", None),
            "mock": os.environ.get("WECOM_MOCK", "0") == "1"}
