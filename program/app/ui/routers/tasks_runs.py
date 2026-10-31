"""UI Sub-router: Tasks & Runs."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

from fastapi import (APIRouter, Cookie, Depends, File, Header, HTTPException, Query,
                      Response, UploadFile)
from pydantic import BaseModel, Field

from ...audit import write_audit
from ...db import get_conn
from ...engine import dsh_adapter
from ... import tenant, tenancy
from .. import intel, store
from ..common import (
    COOKIE, SESSION_TTL, _secret, _sign, _make_cookie, _read_cookie,
    _token, _require, _tenant_of, _task_actor, _task_write_entities,
    _task_allowed_actions, _task_execution_summary, _budget_for, _get_api_attr,
)

router = APIRouter()

from .chats import _run_turn

class DesignIn(BaseModel):
    note: str | None = None


def _design_message(conn, task_id: str):
    return conn.execute(
        "SELECT * FROM chat_messages WHERE task_id=? AND kind='confirm' ORDER BY id DESC LIMIT 1",
        (task_id,)).fetchone()


@router.post("/designs/{task_id}/approve")
def approve_design(task_id: str, payload: DesignIn | None = None,
                   oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        msg = _design_message(conn, task_id)
        if msg is None:
            raise HTTPException(404, detail={"code": "NoDesignCard"})
        meta = json.loads(msg["meta"] or "{}")
        if meta.get("state") == "approved":
            return {"ok": True, "already": True}
        meta["state"] = "approved"
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), msg["id"]))
        store.add_message(conn, msg["chat_id"], "human", "同意方案，开始开发", author="我")
        members = store.members(conn, msg["chat_id"])
        pm_name = members[0]["name"] if members else "PM"
        store.add_message(conn, msg["chat_id"], "agent",
                          "收到，方案已确认。我这就派开发进入实施阶段。",
                          author=pm_name, kind="text")
        conn.commit()
        chat_id = msg["chat_id"]
        requirement = meta.get("requirement") or ""
    # v5.4.2 §79：后台线程走统一派发口（绑定 owner_db + 登记排空屏障）。
    from ...db import db_path, spawn_bg_thread
    spawn_bg_thread(lambda: _dev_phase(chat_id, task_id, requirement, ident),
                    name=f"oa-dev-{task_id}", db=db_path())
    return {"ok": True}


@router.post("/designs/{task_id}/reject")
def reject_design(task_id: str, payload: DesignIn | None = None,
                  oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        msg = _design_message(conn, task_id)
        if msg is None:
            raise HTTPException(404, detail={"code": "NoDesignCard"})
        meta = json.loads(msg["meta"] or "{}")
        meta["state"] = "rejected"
        if payload and payload.note:
            meta["note"] = payload.note
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), msg["id"]))
        store.add_message(conn, msg["chat_id"], "agent",
                          "方案已驳回，我让设计同学按您的意见重做一版。",
                          author="小文", kind="text")
        conn.commit()
    return {"ok": True}


def _dev_phase(chat_id: str, design_id: str, requirement: str, ident: str) -> None:
    """阶段二：老板点头后才建开发任务。"""
    from ...engine import task_generator
    from .. import pm as pm_mod

    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        mem = store.members(conn, chat_id)
        r = pm_mod.roles(mem)
        gen = task_generator.generate(conn, pm_mod.dev_brief(requirement),
                                      legal_entity_id=r["dev"]["legal_entity_id"],
                                      pack_id="web-v1")
        dev_id = gen["task_id"]
        conn.execute("UPDATE tasks SET owner_employee_id=? WHERE id=?",
                     (r["dev"]["employee_id"], dev_id))
        conn.commit()
        store.add_message(conn, chat_id, "agent",
                          f"开发任务已建：{dev_id}。{r['dev']['name']} 开始实施。",
                          author=r["pm"]["name"], kind="text")
    _run_turn(chat_id, r["dev"]["employee_id"], requirement, ident, ack=False,
              task_id=dev_id, kind="task", requirement=requirement)


@router.get("/tasks")
def task_list(limit: int = 50, offset: int = 0, state: str | None = None,
              q: str | None = None, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        where, args = tenant.clause(conn, actor, "t.legal_entity_id")
        where += " AND t.tenant_id=?"
        args.append(actor["tenant_id"])
        filtered, values = where, list(args)
        if state == "active":
            filtered += " AND t.state IN ('todo','assigned','in_progress')"
        elif state and state != "all":
            if state not in ("todo", "assigned", "in_progress", "pending_review", "rejected", "failed", "completed", "cancelled"):
                raise HTTPException(400, detail={"code": "UnknownTaskState"})
            filtered += " AND t.state=?"
            values.append(state)
        if (q or "").strip():
            filtered += " AND (t.title LIKE ? OR t.id LIKE ? OR e.name LIKE ?)"
            values.extend(["%" + q.strip()[:200] + "%"] * 3)
        limit, offset = max(1, min(200, limit)), max(0, offset)
        total = conn.execute(
            f"SELECT COUNT(*) FROM tasks t LEFT JOIN ai_employees e ON e.id=t.owner_employee_id"
            f" WHERE {filtered}", values).fetchone()[0]
        tasks = [dict(row) for row in conn.execute(
            f"""SELECT t.*, e.name AS owner_name,
                      (SELECT m.chat_id FROM chat_messages m WHERE m.task_id=t.id
                       ORDER BY m.id DESC LIMIT 1) AS chat_id
               FROM tasks t LEFT JOIN ai_employees e ON e.id=t.owner_employee_id
               WHERE {filtered} ORDER BY t.rowid DESC LIMIT ? OFFSET ?""", (*values, limit, offset))]
        for task in tasks:
            task["allowed_actions"] = _task_allowed_actions(task, actor)
            task.update(_task_execution_summary(conn, task, actor))
        counts = {row["state"]: row["count"] for row in conn.execute(
            f"SELECT t.state,COUNT(*) AS count FROM tasks t WHERE {where} GROUP BY t.state", args)}
    return {"tasks": tasks, "counts": counts, "total": total, "limit": limit, "offset": offset}


@router.get("/tasks/{task_id}")
def task_view(task_id: str, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        t = tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, actor))
        execution = _task_execution_summary(conn, t, actor)
        requirement = dsh_adapter.task_requirement(t, conn=conn)
        crit = [dict(r) for r in conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,))]
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY rowid DESC", (task_id,))]
        from ..artifacts import unique_paths
        artifacts = unique_paths(conn.execute(
            """SELECT a.id,a.file_path,a.bytes,a.run_id,a.created_at
               FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               WHERE r.task_id=? AND COALESCE(a.state,'active')='active'
               ORDER BY r.run_seq DESC,a.art_seq DESC,a.rowid DESC""", (task_id,)))
        stream = []
        if runs:
            stream = store.stream(conn, runs[0]["id"], limit=60)
        owner = None
        if t["owner_employee_id"]:
            owner = conn.execute(
                """SELECT e.id, e.name, e.preset FROM ai_employees e
                   WHERE e.id = ?""", (t["owner_employee_id"],)).fetchone()
        if owner is None:
            row = conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=? LIMIT 1",
                               (task_id,)).fetchone()
            if row:
                owner = conn.execute(
                    """SELECT e.id, e.name, e.preset FROM ai_employees e
                       JOIN chats ch ON ch.employee_id = e.id
                       WHERE ch.id = ? AND ch.kind = 'dm'""", (row["chat_id"],)).fetchone()
    allowed_actions = _task_allowed_actions(t, actor)
    return {"task": {**dict(t), **execution, "allowed_actions": allowed_actions, "requirement": requirement},
            **execution,
            "requirement": requirement,
            "allowed_actions": allowed_actions, "criteria": crit, "runs": runs, "stream": stream,
            "artifacts": artifacts,
            "owner": dict(owner) if owner else None}


class ActionIn(BaseModel):
    action: str          # accept | reject | abort
    note: str | None = None


def _stop_run(conn, run, *, ident: str, cancelled: bool) -> bool:
    """Stop an owned worker and invalidate approvals that could restart it."""
    try:
        killed = dsh_adapter.stop_process(
            run["id"], expected_pid=run["pid"] if run["state"] == "running" else None)
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": "RunnerStopFailed", "message": str(exc)}) from exc
    conn.execute("UPDATE task_runs SET state=?, exit_code=-9, ended_at=CURRENT_TIMESTAMP WHERE id=?",
                 ("cancelled" if cancelled else "failed", run["id"]))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_approvals'").fetchone():
        conn.execute("UPDATE tool_approvals SET state='rejected', decided_by=?,"
                     " decided_at=CURRENT_TIMESTAMP WHERE run_id=? AND state='pending'",
                     (ident, run["id"]))
    return killed


@router.post("/runs/{run_id}/kill")
def kill_run(run_id: str, payload: DesignIn | None = None, oa_ui: str | None = Depends(_token)):
    """紧急熔断：直接杀掉底层 DSH 进程树，run 记 failed，写审计。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _task_actor(conn, oa_ui, "verb:*")
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, detail={"code": "RunNotFound"})
        task = tenant.scoped_task(conn, run["task_id"], _task_write_entities(conn, actor))
        if run["state"] not in ("spawned", "running", "awaiting_approval") or task["state"] not in ("assigned", "in_progress"):
            raise HTTPException(409, detail={"code": "RunNotActive", "state": run["state"]})
        pid = run["pid"]
        killed = _stop_run(conn, run, ident=ident, cancelled=False)
        from ... import state_machine
        state_machine.transition(conn, run["task_id"], "run_failed",
                                 actor=ident, payload={"reason": "kill switch"})
        write_audit(conn, legal_entity_id=run["legal_entity_id"], verb="kill",
                    task_id=run["task_id"], run_id=run_id, layer="system",
                    action="run_killed", target=run_id,
                    payload={"pid": pid, "killed": killed}, response={"by": ident})
        conn.commit()
    return {"ok": True, "killed": killed, "pid": pid}


@router.post("/tasks/{task_id}/action")
def task_action(task_id: str, payload: ActionIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if payload.action not in ("accept", "reject", "abort"):
            raise HTTPException(400, detail={"code": "UnknownAction"})
        actor = _task_actor(conn, oa_ui, "task:*" if payload.action == "abort" else "accept:judge")
        row = tenant.scoped_task(conn, task_id, _task_write_entities(conn, actor))
        if ((payload.action in ("accept", "reject") and row["state"] != "pending_review") or
                (payload.action == "abort" and row["state"] != "in_progress")):
            raise HTTPException(409, detail={"code": "TaskActionNotAllowed", "state": row["state"],
                                             "action": payload.action})
        if payload.action == "accept":
            from ... import acceptance_run
            mandatory = acceptance_run.validate_mandatory(
                conn, task_id, entity=row["legal_entity_id"], tenant_id=row["tenant_id"],
                commit=False)
            if not mandatory["ok"]:
                acceptance_run.run(conn, task_id, [row["legal_entity_id"]])
                conn.commit()
                raise HTTPException(409, detail={"code": "MandatoryCheckFailed",
                                                 "message": mandatory["detail"]})
            from ...acceptance import l1_manual
            # 人工判据要求证据；老板点「验收通过」时，自动附上产物片段，
            # 免得因为没有 snippet 直接 500（真实业务闭环踩到）
            snippet = None
            art = conn.execute(
                """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                   WHERE r.task_id=? AND a.state='active'
                   ORDER BY r.run_seq DESC, a.art_seq DESC LIMIT 1""", (task_id,)).fetchone()
            if art and art["file_path"]:
                try:
                    with open(art["file_path"], encoding="utf-8", errors="replace") as source:
                        snippet = source.read(200)
                except OSError:
                    snippet = None
            manual = conn.execute(
                    """SELECT * FROM acceptance_criteria WHERE task_id=? AND check_type='human_check'
                       AND state!='na'""", (task_id,)).fetchall()
            snippet = snippet or (payload.note or "").strip() or None
            if not snippet and any(c["evidence_required"] for c in manual):
                raise HTTPException(400, detail={"code": "EvidenceRequired",
                                                "message": "验收需要可读取的产物或验收说明"})
            for c in manual:
                l1_manual.apply(conn, c["id"], passed=True, checked_by=ident,
                                note=payload.note or "UI 验收通过",
                                evidence_snippet=snippet)
            __import__("app.acceptance_run", fromlist=["run"]).run(
                conn, task_id, [row["legal_entity_id"]])
        elif payload.action == "abort":
            from ... import state_machine
            runs = conn.execute("SELECT * FROM task_runs WHERE task_id=?"
                                " AND state IN ('spawned','running','awaiting_approval')", (task_id,)).fetchall()
            for run in runs:
                _stop_run(conn, run, ident=ident, cancelled=True)
            state_machine.transition(conn, task_id, "run_cancelled", actor=ident,
                                     payload={"note": payload.note})
        else:
            from ... import state_machine
            from ...acceptance import l1_manual
            note = (payload.note or "").strip() or "UI 驳回，请修改后重新提交"
            for c in conn.execute("SELECT id FROM acceptance_criteria WHERE task_id=?"
                                  " AND check_type='human_check' AND state!='na'", (task_id,)).fetchall():
                l1_manual.apply(conn, c["id"], passed=False, checked_by=ident,
                                note=note, evidence_snippet=note)
            state_machine.transition(conn, task_id, "review_failed", actor=ident,
                                     payload={"note": note})
        write_audit(conn, legal_entity_id=row["legal_entity_id"], verb="task_action",
                    task_id=task_id, actor=ident, action=f"task_{payload.action}", target=task_id,
                    payload={"note": payload.note}, response={"ok": True})
        conn.commit()
        state = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()["state"]
    if payload.action == "accept" and state != "completed":
        raise HTTPException(409, detail={"code": "AcceptanceNotPassed", "state": state,
                                         "message": "自动验收尚未通过，请检查未通过的判据。"})
    return {"ok": True, "state": state}


class TaskRetryIn(BaseModel):
    note: str | None = None
    prompt: str | None = None
    budget_tokens: int | None = Field(default=None, ge=0)


def _retry_error(retry: dict, exc: Exception) -> None:
    from ...state_machine import transition
    with get_conn() as conn:
        run = conn.execute("SELECT state FROM task_runs WHERE id=?", (retry["run_id"],)).fetchone()
        task = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()
        if run and run["state"] in ("spawned", "running") and task and task["state"] == "in_progress":
            transition(conn, retry["task_id"], "run_failed", actor="retry-worker",
                       payload={"reason": "retry_worker_error"})
            conn.execute("UPDATE task_runs SET state='failed', exit_code=-1,"
                         " ended_at=CURRENT_TIMESTAMP WHERE id=?", (retry["run_id"],))
        write_audit(conn, legal_entity_id=retry["legal_entity_id"], verb="retry",
                    task_id=retry["task_id"], run_id=retry["run_id"], action="retry_worker_failed",
                    target=retry["run_id"], payload={"error": str(exc)[:500]}, response={"ok": False})
        if retry.get("chat_id"):
            store.add_message(conn, retry["chat_id"], "system", f"重试执行失败：{exc}",
                              kind="text", task_id=retry["task_id"])


def _execute_task_retry(retry: dict) -> None:
    """Run a committed submission without holding the console request open."""
    from ... import acceptance_run
    try:
        with get_conn() as conn:
            out = dsh_adapter.execute_run(
                conn, retry["run_id"], prompt=retry["prompt"], runner=retry["runner"],
                resume_session=retry["session_id"], budget_tokens=retry["budget_tokens"])
        with get_conn() as conn:
            task = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()
            if task and task["state"] == "pending_review":
                acceptance_run.run(conn, retry["task_id"], [retry["legal_entity_id"]])
            if out.get("approval_id"):
                from ... import rbac
                from .. import approval_flow
                approval = rbac.get(conn, out["approval_id"])
                if approval is not None:
                    approval_flow.post_card(conn, approval)
            elif retry.get("chat_id"):
                state = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()[0]
                artifact = conn.execute(
                    "SELECT id,file_path,bytes FROM artifacts WHERE run_id=?"
                    " AND COALESCE(state,'active')='active' ORDER BY art_seq DESC LIMIT 1",
                    (retry["run_id"],)).fetchone()
                store.add_message(conn, retry["chat_id"], "agent", f"重试完成，任务状态：{state}",
                                  author="执行助理", kind="card", task_id=retry["task_id"],
                                  meta=json.dumps({"artifact": dict(artifact) if artifact else None,
                                                   "state": state}, ensure_ascii=False))
    except Exception as exc:                        # noqa: BLE001 - preserve worker failure in UI
        _retry_error(retry, exc)


def _launch_task_retry(retry: dict) -> None:
    # v5.4.2 §79：重试也是后台流水线，必须绑定发起时的库并登记排空屏障。
    from ...db import db_path, spawn_bg_thread
    worker = _get_api_attr("_execute_task_retry", _execute_task_retry)
    spawn_bg_thread(lambda: worker(retry), name=f"retry-{retry['run_id']}",
                    db=db_path())


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, payload: TaskRetryIn | None = None,
               oa_ui: str | None = Depends(_token)):
    from ...api.tasks import _retry_prompt, prepare_retry
    from ...models import RetryIn
    payload = payload or TaskRetryIn()
    runner = (os.environ.get("UI_RUNNER") or "acp").strip().lower()
    if runner not in ("acp", "dsh", "mock", "mock_acp"):
        raise HTTPException(503, detail={"code": "RunnerMisconfigured"})
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _task_actor(conn, oa_ui, "task:*")
        task = tenant.scoped_task(conn, task_id, _task_write_entities(conn, actor))
        prompt = payload.prompt
        if not prompt and (payload.note or "").strip():
            prompt = _retry_prompt(conn, task_id) + "\r\n本次补充要求：" + payload.note.strip()
        budget = payload.budget_tokens if payload.budget_tokens is not None else _budget_for(conn, task["legal_entity_id"])
        try:
            retry = prepare_retry(conn, task_id, RetryIn(prompt=prompt, runner=runner, budget_tokens=budget),
                                  actor, entities=_task_write_entities(conn, actor))
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "NotRetryable":
                raise HTTPException(409, detail=exc.detail) from exc
            raise
        chat = conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=? ORDER BY id DESC LIMIT 1",
                            (task_id,)).fetchone()
        retry["chat_id"] = chat["chat_id"] if chat else None
        conn.commit()
    try:
        _get_api_attr("_launch_task_retry", _launch_task_retry)(retry)
    except RuntimeError as exc:
        _retry_error(retry, exc)
        raise HTTPException(503, detail={"code": "RetryWorkerUnavailable"}) from exc
    return {"ok": True, "task_id": task_id, "run_id": retry["run_id"], "run_seq": retry["run_seq"],
            "state": "in_progress", "runner": retry["runner"], "resumed": bool(retry["session_id"])}


# ------------------------------------------------------------------ live feed
@router.get("/live/{run_id}")
def live(run_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if run is not None:
            tenant.scoped_task(conn, run["task_id"], tenant.visible_entities(conn, actor))
        ev = store.stream(conn, run_id, after_id=after) if run else []
    return {"run": dict(run) if run else None, "stream": ev}



@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, oa_ui: str | None = Depends(_token)):
    """彻底删除任务：级联清理 runs、stream、traces、spans、criteria、evidence、artifacts 及物理磁盘文件。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:write")
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise HTTPException(404, detail={"code": "TaskNotFound"})

        role = actor.get("role")
        if role not in {"ceo", "system", "operator", "admin"} and not actor.get("can_manage_deployment"):
            if task.get("creator_id") != actor["id"] and task.get("owner_employee_id") != actor["id"]:
                raise HTTPException(403, detail={"code": "Forbidden"})

        def drop_safe(sql: str, params=()):
            try:
                conn.execute(sql, params)
            except sqlite3.OperationalError:
                pass

        runs = [r["id"] for r in conn.execute("SELECT id FROM task_runs WHERE task_id=?", (task_id,)).fetchall()]
        files_removed = 0
        if runs:
            placeholders = ",".join("?" for _ in runs)
            try:
                artifacts = conn.execute(f"SELECT file_path FROM artifacts WHERE run_id IN ({placeholders})", runs).fetchall()
                for art in artifacts:
                    fp = art["file_path"]
                    if fp and os.path.exists(fp):
                        try:
                            os.remove(fp)
                            files_removed += 1
                        except OSError:
                            pass
            except sqlite3.OperationalError:
                pass

            drop_safe(f"DELETE FROM artifacts WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM run_stream WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM run_diagnoses WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM redline_scans WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM tool_approvals WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM eval_results WHERE run_id IN ({placeholders})", runs)
            drop_safe(f"DELETE FROM task_runs WHERE task_id=?", (task_id,))

        try:
            traces = [t["trace_id"] for t in conn.execute("SELECT trace_id FROM agent_traces WHERE task_id=?", (task_id,)).fetchall()]
            if traces:
                tp = ",".join("?" for _ in traces)
                drop_safe(f"DELETE FROM trace_spans WHERE trace_id IN ({tp})", traces)
                drop_safe(f"DELETE FROM agent_traces WHERE trace_id IN ({tp})", traces)
        except sqlite3.OperationalError:
            pass

        try:
            crit_ids = [c["id"] for c in conn.execute("SELECT id FROM acceptance_criteria WHERE task_id=?", (task_id,)).fetchall()]
            if crit_ids:
                cp = ",".join("?" for _ in crit_ids)
                drop_safe(f"DELETE FROM evidence_refs WHERE owner_type='criterion' AND owner_id IN ({cp})", crit_ids)
                drop_safe("DELETE FROM acceptance_criteria WHERE task_id=?", (task_id,))
        except sqlite3.OperationalError:
            pass

        for t in ("budgets", "wbs_items", "chat_messages", "decision_packages", "wecom_approvals"):
            drop_safe(f"DELETE FROM {t} WHERE task_id=?", (task_id,))

        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

        from ...audit import write_audit
        write_audit(conn, legal_entity_id=task["legal_entity_id"] or "LE-GROUP-HQ",
                    verb="task:delete", actor=actor["id"], action="task_deleted",
                    target=task_id, payload={"title": task["title"]},
                    response={"ok": True, "files_removed": files_removed})
        conn.commit()
    return {"ok": True, "task_id": task_id, "files_removed": files_removed}

# ------------------------------------------------------------------ 角色库（M15）
class KernelModeIn(BaseModel):
    mode: str = Field(..., pattern="^(auto_failover|native|dsh)$")


@router.get("/kernel/status")
def get_kernel_status_route(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    from ...engine import kernel_router
    return kernel_router.get_kernel_status()


@router.post("/kernel/mode")
def set_kernel_mode_route(payload: KernelModeIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    from ...engine import kernel_router
    kernel_router.set_kernel_mode(payload.mode)
    return kernel_router.get_kernel_status()


# ------------------------------------------------------------------ 项目（v1.5）
class ToolCallIn(BaseModel):
    role_tag: str
    tool: str
    sql: str | None = None


@router.post("/tools/call")
def tool_call(payload: ToolCallIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    from ... import tools_acl
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from .. import roles as roles_mod
        roles_mod.ensure(conn)
        role = conn.execute(
            """SELECT rt.tag FROM ai_employees e JOIN role_templates rt ON rt.id=e.role_id
               WHERE e.id=? AND e.legal_entity_id=?""",
            (actor.get("employee_id"), tenant.entity_of(actor))).fetchone()
        role_tag = role["tag"] if role else None
        if actor["role"] not in {"ceo", "system"} and payload.role_tag != role_tag:
            raise HTTPException(403, detail={"code": "ToolDenied"})
        role_tag = role_tag or (payload.role_tag if actor["role"] in {"ceo", "system"} else None)
        try:
            tools_acl.check(conn, role_tag=role_tag, tool=payload.tool,
                            sql=payload.sql, target=payload.tool, entity=tenant.entity_of(actor))
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "ToolDenied", "message": str(exc)})
    return {"ok": True, "role": role_tag, "tool": payload.tool, "executed": False}


# ------------------------------------------------------- 会话管理（v2.0 深度删除）
@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import trace as tr
        data = tr.load(conn, run_id)
    if data is None:
        raise HTTPException(404, detail={"code": "TraceNotFound"})
    return data


@router.get("/traces/spans/{span_id}/audit")
def span_audit(span_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import trace as tr
        rows = tr.audit_for_span(conn, span_id)
    return {"audit": rows}


# ------------------------------------- 实时执行流 / 失败诊断（v4.1）
_TERMINAL_RUN = {"succeeded", "failed", "cancelled", "rejected", "done"}


@router.get("/runs/{run_id}/live-stream")
async def run_live_stream(run_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    """SSE：把 trace_spans 逐条推给前端（thinking / tool_call / tool_result / 模型）。

    span 在关闭时即落库并 commit（`trace.span()`），所以这里是**真实时**，不是跑完补发。
    没有新 span 时也每 0.8s 发一次 status，前端据此判断还在跑还是已收尾。
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    _require(oa_ui)

    async def gen():
        last = int(after or 0)
        idle = 0
        yield "retry: 3000\r\n\r\n"
        for _ in range(900):                    # 上限 12 分钟，前端断开即结束
            rows, state = [], None
            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT s.rowid,s.kind,s.name,s.status,s.started_ms,s.ended_ms,"
                        "s.tokens,s.detail FROM trace_spans s JOIN agent_traces t"
                        " ON t.trace_id=s.trace_id WHERE t.run_id=? AND s.rowid>?"
                        " ORDER BY s.rowid LIMIT 50", (run_id, last)).fetchall()
                    run = conn.execute("SELECT state FROM task_runs WHERE id=?",
                                       (run_id,)).fetchone()
                    state = run["state"] if run else None
            except Exception:                   # noqa: BLE001 - 流不能因为一次读失败就断
                rows, state = [], None
            for row in rows:
                last = int(row["rowid"])
                kind = row["kind"] or "span"
                if kind == "tool":
                    event = "tool_call"
                elif kind == "thinking":
                    event = "thinking"
                elif kind == "model":
                    event = "model"
                else:
                    event = "span"
                payload = {"kind": kind, "name": row["name"], "status": row["status"],
                           "start_ms": row["started_ms"], "end_ms": row["ended_ms"],
                           "tokens": row["tokens"], "detail": row["detail"] or ""}
                yield "event: " + event + "\r\ndata: " + json.dumps(
                    payload, ensure_ascii=False) + "\r\n\r\n"
                if kind == "tool" and (row["detail"] or ""):
                    yield "event: tool_result\r\ndata: " + json.dumps(
                        {"name": row["name"], "detail": row["detail"],
                         "status": row["status"]}, ensure_ascii=False) + "\r\n\r\n"
                idle = 0
            yield "event: status\r\ndata: " + json.dumps(
                {"run_id": run_id, "state": state}, ensure_ascii=False) + "\r\n\r\n"
            if state in _TERMINAL_RUN:
                idle += 1
                if idle >= 2 and not rows:
                    yield "event: done\r\ndata: {}\r\n\r\n"
                    return
            await asyncio.sleep(0.8)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/diagnosis")
def run_diagnosis(run_id: str, oa_ui: str | None = Depends(_token)):
    """失败诊断：规则结论优先，模型可用时补一句根因与修复建议（中文）。"""
    _require(oa_ui)
    from ... import finance, flows
    with get_conn() as conn:
        try:
            # v5.4.2 TASK-2：诊断里的 llm.complete 也要入账。它没有阶段上下文，
            # 就记到这次 run 所属项目的项目级 overhead 上；查不到项目就只记本地计数。
            row = conn.execute("SELECT t.project_id FROM task_runs r"
                               " JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
                               (run_id,)).fetchone()
            pid = row["project_id"] if row is not None else None
            with finance.overhead_scope(conn, pid):
                return flows.diagnose(conn, run_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "RunNotFound"}) from None


# ------------------------------------------------------------ RBAC（v2.3）
