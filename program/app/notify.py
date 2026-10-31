"""Business event push (v4.0): project closed 9/9 and red-line block.

Two things matter here:

1. **The row is the evidence.** Every event is written to ``notifications``
   before delivery, so the UI / 验收 can show what was pushed even if the
   webhook was down. A duplicate ``(tenant_id, event, ref)`` is ignored, which
   is what stops a retry loop from spamming the group.
2. **Delivery never blocks work.** The HTTP call runs on a daemon thread with a
   fresh connection; a broken robot must not fail a project or an acceptance
   run. ``NOTIFY_SYNC=1`` runs it inline (tests, CLI).

Red-line pushes are per run, not per task: the same criterion failing again
after a retry is new information and deserves a new message.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading

from .audit import new_id

log = logging.getLogger("notify")

DDL = """
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  event TEXT NOT NULL,
  ref TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'pending',
  detail TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_event
  ON notifications(tenant_id, event, ref);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _base_url() -> str:
    return (os.environ.get("UI_PUBLIC_URL") or "http://127.0.0.1:8077").rstrip("/")


def _record(conn, *, tenant_id: str, entity: str, event: str, ref: str,
            subject: str, body: str) -> str | None:
    """Insert one event; None means it was already recorded (dedupe)."""
    notify_id = new_id("NTF")
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications (id,tenant_id,entity_id,event,ref,subject,body)"
        " VALUES (?,?,?,?,?,?,?)",
        (notify_id, tenant_id, entity, event, ref, subject[:200], body))
    conn.commit()
    return notify_id if cur.rowcount else None


def _dispatch(tenant_id: str, notify_id: str, event: str, body: str) -> None:
    from .db import db_path, open_conn

    # v5.3：投递是异步的，必须绑定「入队时」的库。若等到执行时才读 DB_PATH，
    # 进程内换库场景（测试的下一个用例）会把通知写进别人的库，
    # 并在对方建库时抢锁导致 database is locked。
    owner_db = db_path()

    def deliver() -> None:
        from .wecom import robot
        try:
            with open_conn(owner_db) as conn:
                out = robot.push(conn, tenant_id=tenant_id, event=event, markdown=body)
                conn.execute("UPDATE notifications SET state=?, detail=? WHERE id=?",
                             ("sent" if out.get("ok") else "failed",
                              json.dumps(out, ensure_ascii=False)[:800], notify_id))
                conn.commit()
        except Exception as exc:                 # noqa: BLE001 - 后台线程不能炸
            log.warning("notify delivery failed: %s", exc)
            try:
                with open_conn(owner_db) as conn:
                    conn.execute("UPDATE notifications SET state='failed', detail=? WHERE id=?",
                                 (str(exc)[:500], notify_id))
                    conn.commit()
            except Exception:                    # noqa: BLE001
                pass

    if os.environ.get("NOTIFY_SYNC") == "1":
        deliver()
        return
    # v5.4.2 §79：投递线程走统一派发口——绑定入队时的库，并登记进测试排空屏障。
    from .db import spawn_bg_thread
    spawn_bg_thread(deliver, name="oa-notify", db=owner_db)


def push_event(conn, *, pid: str, event: str, body: str,
               tenant_id: str | None = None, ref: str | None = None) -> str | None:
    """通用事件推送（预算暂停/追加/停摆）。没配 webhook 时只落 notifications。"""
    p = conn.execute("SELECT tenant_id,legal_entity_id,name FROM projects WHERE id=?",
                     (pid,)).fetchone()
    tenant = tenant_id or (p["tenant_id"] if p else "TENANT-HQ")
    entity = p["legal_entity_id"] if p else "LE-GROUP-HQ"
    subject = (p["name"] if p else pid) or pid
    notify_id = _record(conn, tenant_id=tenant, entity=entity, event=event,
                        ref=ref or pid, subject=f"{event} · {subject}", body=body)
    if notify_id:
        _dispatch(tenant, notify_id, event, body)
    return notify_id


def project_closed(conn, pid: str) -> dict | None:
    """Push once when every stage of a project is done (9/9 商业闭环)."""
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        return None
    stages = conn.execute("SELECT state FROM project_stages WHERE project_id=?",
                          (pid,)).fetchall()
    if not stages or any(s["state"] != "done" for s in stages):
        return None
    from .wecom import robot as _robot
    if not _robot.resolve(conn, p["tenant_id"])["on_project_done"]:
        return None                          # 管理员关掉了这一类推送
    gates = conn.execute("""SELECT COUNT(*) n FROM stage_gates
        WHERE project_id=? AND superseded=0 AND state='passed'""", (pid,)).fetchone()["n"]
    gate_total = conn.execute("""SELECT COUNT(*) n FROM stage_gates
        WHERE project_id=? AND superseded=0""", (pid,)).fetchone()["n"]
    done, total = len(stages), len(stages)
    entity_row = conn.execute("SELECT name FROM legal_entities WHERE id=?",
                              (p["legal_entity_id"],)).fetchone()
    tenant_row = conn.execute("SELECT name FROM tenants WHERE id=?",
                              (p["tenant_id"],)).fetchone()
    body = "\n".join([
        "### ✅ 商业闭环达成 9/9",
        f"> 项目：**{p['name']}**（{pid}）",
        f"> 阶段：{done}/{total} · 闸门：{gates}/{gate_total}",
        f"> 实体：{entity_row['name'] if entity_row else p['legal_entity_id']}"
        f" · 租户：{tenant_row['name'] if tenant_row else p['tenant_id']}",
        f"> 立项时间：{p['created_at']}",
        f"> [打开工作台]({_base_url()}/app/)",
    ])
    notify_id = _record(conn, tenant_id=p["tenant_id"], entity=p["legal_entity_id"],
                        event="project_closed", ref=pid,
                        subject=f"商业闭环 {done}/{total}：{p['name']}", body=body)
    if notify_id is None:
        return None
    _dispatch(p["tenant_id"], notify_id, "project_closed", body)
    return {"id": notify_id, "event": "project_closed", "ref": pid}


def redline_blocked(conn, *, task_id: str, run_id: str | None, detail: str,
                    hits: list[dict] | None = None) -> dict | None:
    """Push when an L0 red-line check rejects a delivery (no human approval path)."""
    t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if t is None:
        return None
    tenant_id = t["tenant_id"] if "tenant_id" in t.keys() and t["tenant_id"] else "TENANT-HQ"
    entity = t["legal_entity_id"]
    from .wecom import robot as _robot
    if not _robot.resolve(conn, tenant_id)["on_redline"]:
        return None                          # 管理员关掉了红线推送
    terms = sorted({term for hit in (hits or []) for term in (hit.get("terms") or [])})
    body = "\n".join([
        "### ⛔ 企业红线拦截（L0）",
        f"> 任务：**{t['title']}**（{task_id}）",
        f"> 命中：{'、'.join(terms) if terms else '（未记录词）'}",
        f"> 判据：{detail[:200]}",
        f"> 运行：{run_id or '—'}",
        "> 处理：任务已驳回，**没有人工审批入口**；修稿后走【一键重试】。",
        f"> [打开工作台]({_base_url()}/app/)",
    ])
    ref = f"{task_id}:{run_id or 'norun'}"
    notify_id = _record(conn, tenant_id=tenant_id, entity=entity, event="redline_blocked",
                        ref=ref, subject=f"红线拦截：{t['title']}", body=body)
    if notify_id is None:
        return None
    _dispatch(tenant_id, notify_id, "redline_blocked", body)
    return {"id": notify_id, "event": "redline_blocked", "ref": ref}


def recent(conn, *, tenant_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute("""SELECT id,event,ref,subject,state,detail,created_at
                           FROM notifications WHERE tenant_id=?
                           ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                        (tenant_id, max(1, min(int(limit), 100)))).fetchall()
    return [dict(r) for r in rows]
