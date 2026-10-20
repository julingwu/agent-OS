"""v1.1 运维面：告警、定时任务、关于。"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
import uuid

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from ..audit import write_audit
from ..db import get_conn
from . import store

log = logging.getLogger("ui.ops")
router = APIRouter(prefix="/ui", tags=["ui-ops"])

VERSION = "3.2.1"


def _ident(oa_ui: str | None = Cookie(default=None),
           authorization: str | None = Header(default=None),
           t: str | None = Query(default=None)) -> str | None:
    from .api import _token
    return _token(oa_ui, authorization, t)


def _require(token: str | None) -> str:
    from .api import _require as _r
    return _r(token)


Auth = Depends(_ident)


# ------------------------------------------------------------------ 告警

def raise_alert(conn, *, level: str, kind: str, message: str, target: str | None = None) -> str:
    aid = "AL-" + uuid.uuid4().hex[:10]
    conn.execute("INSERT INTO alerts (id, level, kind, target, message) VALUES (?,?,?,?,?)",
                 (aid, level, kind, target, message))
    conn.commit()
    try:
        _push_wecom(message)
    except Exception as exc:                     # noqa: BLE001
        log.warning("wecom alert push failed: %s", exc)
    return aid


def _push_wecom(message: str) -> None:
    """强提醒走企微；没配用户或 MOCK 时静默失败，不影响主流程。"""
    from ..wecom import client as wc
    to = os.environ.get("WECOM_ALERT_USER") or os.environ.get("WECOM_APPROVAL_APPROVER")
    if not to:
        return
    wc.send_text(to, f"【Agent OS 告警】{message}")


def check_reject_storm(conn, task_id: str, threshold: int = 3) -> bool:
    rows = conn.execute(
        "SELECT state FROM task_runs WHERE task_id=? ORDER BY run_seq DESC LIMIT ?",
        (task_id, threshold)).fetchall()
    if len(rows) < threshold:
        return False
    if all((r["state"] or "") in ("rejected", "failed", "timeout") for r in rows):
        raise_alert(conn, level="critical", kind="reject_storm",
                    message=f"任务 {task_id} 连续 {threshold} 次未通过，已停手等人工。",
                    target=task_id)
        return True
    return False


def check_budgets(conn, threshold: float = 0.10) -> list[str]:
    hits = []
    rows = conn.execute(
        """SELECT legal_entity_id, limit_tokens, used_tokens FROM budgets
           WHERE task_id IS NULL AND identity_id IS NULL AND limit_tokens > 0""").fetchall()
    for r in rows:
        left = int(r["limit_tokens"]) - int(r["used_tokens"] or 0)
        if left / max(1, int(r["limit_tokens"])) < threshold:
            aid = raise_alert(conn, level="critical", kind="budget_low",
                              message=f"{r['legal_entity_id']} 预算仅剩 {left:,} tokens"
                                      f"（低于 {int(threshold*100)}%）。",
                              target=r["legal_entity_id"])
            hits.append(aid)
    return hits


@router.get("/alerts")
def list_alerts(token: str | None = Auth, unacked: int = 0):
    _require(token)
    with get_conn() as conn:
        sql = "SELECT * FROM alerts"
        if unacked:
            sql += " WHERE acked=0"
        sql += " ORDER BY created_at DESC LIMIT 50"
        return {"alerts": [dict(r) for r in conn.execute(sql)]}


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        conn.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 定时任务

class JobIn(BaseModel):
    chat_id: str
    title: str
    at_time: str                      # "09:00"
    prompt: str = "昨天的任务产出报告"


@router.get("/jobs")
def list_jobs(token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at DESC")]
    return {"jobs": rows}


@router.post("/jobs")
def create_job(payload: JobIn, token: str | None = Auth):
    who = _require(token)
    if len(payload.at_time) != 5 or ":" not in payload.at_time:
        raise HTTPException(400, detail={"code": "BadTime"})
    jid = "JOB-" + uuid.uuid4().hex[:10]
    with get_conn() as conn:
        conn.execute("""INSERT INTO scheduled_jobs (id, chat_id, title, prompt, at_time)
                        VALUES (?,?,?,?,?)""",
                     (jid, payload.chat_id, payload.title, payload.prompt, payload.at_time))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="job_created",
                    target=jid, layer="system", payload=payload.model_dump(),
                    response={"by": who})
        conn.commit()
    return {"ok": True, "id": jid}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        conn.execute("UPDATE scheduled_jobs SET enabled=0 WHERE id=?", (job_id,))
        conn.commit()
    return {"ok": True}


def daily_digest(conn, chat_id: str) -> str:
    since = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT state, COUNT(*) n FROM tasks WHERE created_at >= ? GROUP BY state""",
        (since,)).fetchall()
    if not rows:
        return "昨天没有新任务。"
    total = sum(r["n"] for r in rows)
    bits = "、".join(f"{r['state']} {r['n']}" for r in rows)
    files = conn.execute(
        """SELECT COUNT(*) n FROM artifacts a JOIN task_runs r ON r.id=a.run_id
           WHERE a.created_at >= ? AND COALESCE(a.state,'active')='active'""", (since,)).fetchone()["n"]
    return f"昨日任务报告：共 {total} 个任务（{bits}），产出文件 {files} 个。"


@router.post("/jobs/{job_id}/run")
def run_job(job_id: str, token: str | None = Auth):
    _require(token)
    return {"ok": True, "message": run_job_now(job_id)}


def run_job_now(job_id: str) -> str:
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            return "job not found"
        text = daily_digest(conn, job["chat_id"])
        store.add_message(conn, job["chat_id"], "agent", text, author="值班助理", kind="text")
        conn.execute("UPDATE scheduled_jobs SET last_run_date=? WHERE id=?",
                     (dt.date.today().isoformat(), job_id))
        conn.commit()
    return text


class _Ticker(threading.Thread):
    def __init__(self, interval: int = 60) -> None:
        super().__init__(daemon=True, name="ui-cron")
        self.interval = interval

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:              # noqa: BLE001
                log.warning("cron tick failed: %s", exc)
            time.sleep(self.interval)

    def tick(self) -> None:
        now = dt.datetime.now()
        hhmm = now.strftime("%H:%M")
        with get_conn() as conn:
            due = conn.execute(
                """SELECT * FROM scheduled_jobs
                   WHERE enabled=1 AND at_time=?
                     AND (last_run_date IS NULL OR last_run_date != ?)""",
                (hhmm, now.date().isoformat())).fetchall()
            for job in due:
                run_job_now(job["id"])
            check_budgets(conn)


_cron: _Ticker | None = None


def start_cron(interval: int | None = None) -> None:
    global _cron
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if _cron and _cron.is_alive():
        return
    _cron = _Ticker(int(interval or os.environ.get("UI_CRON_TICK", "60")))
    _cron.start()


@router.get("/about")
def about(token: str | None = Auth):
    _require(token)
    return {"version": VERSION, "engine": "DSH (ACP) + FastAPI + SQLite",
            "shell": "Tauri 2 / React 18", "frozen_baseline": "v1.0.0"}
