"""WeCom native approval (审批) as the sign-off channel.

Why: a template-card button needs WeCom's servers to reach our callback URL. A
quick tunnel changes hostname on every restart, so clicks silently vanish. The
approval API is **outbound only** — we create the approval, the chairman
approves in the WeCom 审批 app, and we poll `getapprovaldetail` for the verdict.
No public URL is involved.

Flow:
    POST oa/applyevent        -> sp_no
    GET  oa/getapprovaldetail -> info.sp_status (2 approved / 3 rejected)
    sp_status == 2 -> write checked_by + note, re-run acceptance (state machine
                      emits review_passed and the task completes)

The template itself must exist in the corp (审批 → 模板管理). This module cannot
create it: `oa/addtemplate` is not part of the public API (404).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

import httpx

from ..db import connect
from . import bridge

log = logging.getLogger("wecom.approval")

API = "https://qyapi.weixin.qq.com"
SP_APPROVED = 2
SP_REJECTED = 3


def template_id() -> str:
    return os.environ.get("WECOM_APPROVAL_TEMPLATE_ID", "").strip()


def _ctrl(name: str, default: str) -> str:
    """WeCom requires control ids shaped like `Text-01`, not `task_id`."""
    return os.environ.get(name, default)


def _approver_userid() -> str:
    return os.environ.get("WECOM_APPROVAL_APPROVER", "").strip()


class ApprovalError(RuntimeError):
    pass


def _token(client: httpx.Client) -> str:
    corp = os.environ.get("WECOM_CORP_ID", "")
    secret = os.environ.get("WECOM_SECRET", "")
    r = client.get("/cgi-bin/gettoken", params={"corpid": corp, "corpsecret": secret}).json()
    if r.get("errcode"):
        raise ApprovalError(f"gettoken failed: {r.get('errcode')} {r.get('errmsg')}")
    return r["access_token"]


def _summary(criteria: list[sqlite3.Row]) -> str:
    passed = sum(1 for c in criteria if c["state"] == "passed")
    pending = sum(1 for c in criteria if c["state"] == "pending")
    return f"{passed} 条自动通过 / {pending} 条待人工"


def create(conn: sqlite3.Connection, *, task_id: str, userid: str,
           criterion_id: str | None = None,
           http: httpx.Client | None = None) -> dict:
    """Open one approval for the next pending human criterion of `task_id`."""
    tid = template_id()
    if not tid:
        raise ApprovalError("WECOM_APPROVAL_TEMPLATE_ID 未配置（模板要在企微审批后台先建）")

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ApprovalError(f"任务不存在：{task_id}")
    criteria = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
    if criterion_id:
        target = next((c for c in criteria if c["id"] == criterion_id), None)
    else:
        target = next((c for c in criteria
                       if c["check_type"] == "human_check" and c["state"] == "pending"), None)
    if target is None:
        raise ApprovalError(f"{task_id} 没有待人工签的判据")

    body = {
        "creator_userid": userid,
        "template_id": tid,
        "use_template_approver": 1,
        "apply_data": {"contents": [
            {"control": "Text", "id": _ctrl("WECOM_APPROVAL_CTRL_TASK", "Text-01"),
             "value": {"text": task_id, "lang": "zh_CN"}},
            {"control": "Text", "id": _ctrl("WECOM_APPROVAL_CTRL_CONCLUSION", "Text-02"),
             "value": {"text": (target["criterion"] or "")[:120], "lang": "zh_CN"}},
        ]},
        "summary_list": [{"summary_info": [
            {"text": "任务", "lang": "zh_CN"},
            {"text": f"{task_id} {(task['title'] or '')[:24]}", "lang": "zh_CN"}]}],
    }
    if _approver_userid():
        body["use_template_approver"] = 0
        body["approver"] = [{"attr": 1, "userid": [_approver_userid()]}]

    client = http or httpx.Client(base_url=API, timeout=20, trust_env=False)
    at = _token(client)
    r = client.post("/cgi-bin/oa/applyevent", params={"access_token": at}, json=body).json()
    if r.get("errcode"):
        raise ApprovalError(f"applyevent failed: {r.get('errcode')} {r.get('errmsg')}")

    sp_no = str(r.get("sp_no") or "")
    conn.execute(
        """INSERT INTO wecom_approvals (sp_no, task_id, criterion_id, userid, template_id, state)
           VALUES (?,?,?,?,?, 'pending')""",
        (sp_no, task_id, target["id"], userid, tid))
    conn.commit()
    log.info("approval opened: %s task=%s criterion=%s", sp_no, task_id, target["id"])
    return {"sp_no": sp_no, "task_id": task_id, "criterion_id": target["id"]}


def status(sp_no: str, http: httpx.Client | None = None) -> dict:
    client = http or httpx.Client(base_url=API, timeout=20, trust_env=False)
    at = _token(client)
    # This endpoint takes its parameter in the JSON body; a GET with ?sp_no= returns
    # 301025 "get approval param error" even though the approval exists.
    r = client.post("/cgi-bin/oa/getapprovaldetail",
                    params={"access_token": at}, json={"sp_no": sp_no}).json()
    if r.get("errcode"):
        raise ApprovalError(f"getapprovaldetail failed: {r.get('errcode')} {r.get('errmsg')}")
    info = (r.get("info") or {})
    return {"sp_no": sp_no, "sp_status": info.get("sp_status"),
            "sp_name": info.get("sp_name"), "apply_time": info.get("apply_time"),
            "records": info.get("sp_record") or []}


Signer = "Callable[[sqlite3.Connection, sqlite3.Row, str], str]"


def _sign(conn: sqlite3.Connection, row: sqlite3.Row, note: str) -> str:
    """Write checked_by + note through the same signed path the panel uses."""
    ident = bridge.identity_for(conn, row["userid"])
    if ident is None:
        return f"身份未绑定：{row['userid']}"
    client = bridge.client_for(ident)
    art = conn.execute(
        """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id = a.run_id
           WHERE r.task_id = ? ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1""",
        (row["task_id"],)).fetchone()
    snippet = None
    if art:
        try:
            import pathlib
            snippet = pathlib.Path(art["file_path"]).read_text(encoding="utf-8",
                                                                errors="replace")[:200]
        except OSError:
            snippet = None
    r = client.post(f"/criteria/{row['criterion_id']}/check",
                    json={"passed": True, "note": note, "evidence_snippet": snippet})
    if r.status_code >= 400:
        return f"签字失败：{r.status_code} {r.text[:150]}"
    acc = client.post(f"/tasks/{row['task_id']}/acceptance/run")
    return f"签字完成，验收 {acc.json().get('verdict') if acc.status_code < 400 else acc.status_code}"


def auto_open(conn: sqlite3.Connection, userid: str | None = None) -> dict:
    """Open an approval for every pending human criterion that has none yet.

    This is what makes the channel *stay* running: after a restart nothing needs
    to be clicked — a task that reaches pending_review gets its approval.
    """
    if os.environ.get("WECOM_APPROVAL_AUTO", "1") != "1":
        return {"opened": 0, "skipped": 0}
    who = userid or os.environ.get("WECOM_APPROVAL_APPROVER", "").strip()
    if not who or not template_id():
        return {"opened": 0, "skipped": 0, "reason": "no approver or template"}
    rows = conn.execute(
        """SELECT t.id AS task_id, c.id AS criterion_id
           FROM acceptance_criteria c JOIN tasks t ON t.id = c.task_id
           WHERE c.check_type='human_check' AND c.state='pending'
             AND t.state IN ('pending_review','in_progress')
             AND NOT EXISTS (SELECT 1 FROM wecom_approvals a
                             WHERE a.criterion_id = c.id AND a.state IN ('pending','approved'))
           ORDER BY t.created_at LIMIT 20""").fetchall()
    opened, skipped = 0, 0
    for r in rows:
        try:
            create(conn, task_id=r["task_id"], userid=who, criterion_id=r["criterion_id"])
            opened += 1
        except ApprovalError as exc:
            log.warning("auto_open failed for %s: %s", r["task_id"], exc)
            skipped += 1
    return {"opened": opened, "skipped": skipped}


def poll_once(conn: sqlite3.Connection, http: httpx.Client | None = None,
              signer=None) -> dict:
    """One pass over pending approvals. `signer` is injectable for tests."""
    sign = signer or _sign
    rows = conn.execute("SELECT * FROM wecom_approvals WHERE state='pending'").fetchall()
    done = {"checked": 0, "approved": 0, "rejected": 0, "errors": 0}
    for row in rows:
        done["checked"] += 1
        try:
            st = status(row["sp_no"], http=http)
        except ApprovalError as exc:
            log.warning("poll failed for %s: %s", row["sp_no"], exc)
            done["errors"] += 1
            continue
        code = int(st["sp_status"] or 0)
        if code == SP_APPROVED:
            msg = sign(conn, row, f"企微审批通过 {row['sp_no']}")
            conn.execute("UPDATE wecom_approvals SET state='approved', result=?,"
                         " updated_at=CURRENT_TIMESTAMP WHERE sp_no=?",
                         (msg[:300], row["sp_no"]))
            conn.commit()
            done["approved"] += 1
            log.info("approval %s approved -> %s", row["sp_no"], msg)
        elif code == SP_REJECTED:
            conn.execute("UPDATE wecom_approvals SET state='rejected',"
                         " updated_at=CURRENT_TIMESTAMP WHERE sp_no=?", (row["sp_no"],))
            conn.commit()
            done["rejected"] += 1
    return done


class Poller(threading.Thread):
    daemon = True

    def __init__(self, interval: float = 60.0) -> None:
        super().__init__(name="wecom-approval-poller")
        self.interval = interval
        self.stop_event = threading.Event()
        self.last: dict | None = None
        self.last_error: str | None = None

    def run(self) -> None:                      # pragma: no cover - thread body
        log.info("approval poller started (%.0fs)", self.interval)
        while not self.stop_event.is_set():
            try:
                with connect() as conn:
                    opened = auto_open(conn)
                    self.last = poll_once(conn)
                    self.last["opened"] = opened.get("opened", 0)
                self.last_error = None
            except Exception as exc:            # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("approval poll failed: %s", self.last_error)
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()


_poller: Poller | None = None


def start(interval: float = 60.0) -> Poller:
    global _poller
    if _poller is None or not _poller.is_alive():
        _poller = Poller(interval)
        _poller.start()
    return _poller


def stop() -> None:
    global _poller
    if _poller is not None:
        _poller.stop()
        _poller = None


def poller_status() -> dict:
    p = _poller
    return {"running": bool(p and p.is_alive()),
            "interval": getattr(p, "interval", None),
            "last": getattr(p, "last", None),
            "last_error": getattr(p, "last_error", None)}
