"""Task endpoints: create from one sentence, inspect, run acceptance, execute a run."""
from __future__ import annotations

import hashlib
import os
import pathlib
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..acceptance import l0_rules, l1_manual, l2_scripts, task_text
from ..audit import new_id, write_audit
from ..db import get_conn
from .. import tenant
from ..identity import require
from ..engine import task_generator
from ..models import ExecuteRunIn, FromSentenceIn, ManualCheckIn, RetryIn
from ..state_machine import IllegalStateTransition, transition
from . import verbs

router = APIRouter(tags=["tasks"])


def _evidence(conn: sqlite3.Connection, criterion_id: str, *, snippet: str | None,
              file_path: str | None, checksum: str | None, line_no: int | None = None) -> None:
    if not (snippet or file_path):
        return
    conn.execute(
        """INSERT INTO evidence_refs
           (id, owner_type, owner_id, file_path, content_snippet, file_checksum, line_no)
           VALUES (?,?,?,?,?,?,?)""",
        (new_id("EV"), "criterion", criterion_id, file_path, snippet, checksum, line_no),
    )


@router.post("/tasks/from_sentence")
def from_sentence(req: FromSentenceIn, _ident: dict = Depends(require("task:create"))):
    with get_conn() as conn:
        entity = tenant.resolve(_ident, req.legal_entity_id)
        try:
            return task_generator.generate(conn, req.text, entity, req.pack_id)
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BadTaskText", "message": str(exc)})


@router.get("/tasks")
def list_tasks(state: str | None = None, actor: str | None = None,
               legal_entity_id: str | None = None,
               limit: int = 50, offset: int = 0,
               _ident: dict = Depends(require("task:read"))):
    """Scheduling view: filter by state and by the agent that ran it."""
    with get_conn() as conn:
        if legal_entity_id and legal_entity_id not in tenant.visible_entities(conn, _ident):
            raise HTTPException(403, detail={"code": "CrossEntityForbidden",
                                             "requested": legal_entity_id})
        ents = ([legal_entity_id] if legal_entity_id
                else tenant.visible_entities(conn, _ident))
        where, args = tenant._in("legal_entity_id", ents)
        scope_args = list(args)
        sql = f"SELECT * FROM tasks WHERE {where}"
        if state:
            sql += " AND state = ?"
            args.append(state)
        if actor:
            sql += " AND id IN (SELECT task_id FROM task_runs WHERE actor_identity = ?)"
            args.append(actor)
        total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
        rows = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                            args + [limit, offset]).fetchall()
        counts = {r[0]: r[1] for r in conn.execute(
            f"SELECT state, COUNT(*) FROM tasks WHERE {where} GROUP BY state", scope_args)}
        return {"total": total, "counts": counts, "tasks": [dict(r) for r in rows]}


@router.get("/locks")
def list_locks(_ident: dict = Depends(require("audit:read"))):
    with get_conn() as conn:
        where, args = tenant._in("t.legal_entity_id", tenant.visible_entities(conn, _ident))
        rows = conn.execute(
            f"""SELECT l.*, r.task_id, r.actor_identity FROM run_locks l
                LEFT JOIN task_runs r ON r.id = l.run_id
                LEFT JOIN tasks t ON t.id = r.task_id
                WHERE {where} OR t.id IS NULL ORDER BY l.acquired_at DESC""",
            args).fetchall()
        return {"count": len(rows), "locks": [dict(r) for r in rows]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, _ident: dict = Depends(require("task:read"))):
    with get_conn() as conn:
        t = tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, _ident))
        entity = t["legal_entity_id"]
        criteria = [dict(r) for r in conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id = ? AND legal_entity_id = ?"
            " ORDER BY seq", (task_id, entity))]
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? AND legal_entity_id = ?"
            " ORDER BY run_seq", (task_id, entity))]
        artifacts = [dict(r) for r in conn.execute(
            """SELECT a.* FROM artifacts a JOIN task_runs r ON r.id = a.run_id
               WHERE r.task_id = ? ORDER BY a.created_at""", (task_id,))]
        evidence = [dict(r) for r in conn.execute(
            """SELECT e.* FROM evidence_refs e JOIN acceptance_criteria c ON c.id = e.owner_id
               WHERE c.task_id = ? ORDER BY e.created_at""", (task_id,))]
        return {"task": dict(t), "criteria": criteria, "runs": runs,
                "artifacts": artifacts, "evidence": evidence}


@router.post("/tasks/{task_id}/rejudge")
def rejudge(task_id: str, _ident: dict = Depends(require("accept:judge"))):
    """Re-run acceptance on the existing artifact (rule/template fix, no new run)."""
    with get_conn() as conn:
        t = tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, _ident))
        if t["state"] != "rejected":
            raise HTTPException(400, detail={"code": "NotRejudgeable", "state": t["state"]})
        transition(conn, task_id, "rejudge", actor=_ident["id"], payload={})
        conn.execute(
            """UPDATE acceptance_criteria SET state='pending', checked_by=NULL, note=NULL
               WHERE task_id = ? AND state != 'na'""", (task_id,))
        write_audit(conn, legal_entity_id=t["legal_entity_id"], verb="rejudge",
                    task_id=task_id, actor=_ident["id"], action="rejudge", target=task_id,
                    payload={}, response={"ok": True})
        return {"task_id": task_id, "state": "pending_review"}


@router.get("/tasks/{task_id}/timeline")
def timeline(task_id: str, _ident: dict = Depends(require("task:read"))):
    with get_conn() as conn:
        tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, _ident))
        where, args = tenant._in("legal_entity_id", tenant.visible_entities(conn, _ident))
        rows = conn.execute(
            f"""SELECT * FROM ai_audit_logs
                WHERE task_id = ? AND {where} ORDER BY created_at, rowid""",
            [task_id] + list(args)).fetchall()
        return {"task_id": task_id, "count": len(rows), "events": [dict(r) for r in rows]}


@router.post("/criteria/{criterion_id}/check")
def manual_check(criterion_id: str, req: ManualCheckIn, _ident: dict = Depends(require("accept:judge"))):
    with get_conn() as conn:
        tenant.scoped_criterion(conn, criterion_id, tenant.visible_entities(conn, _ident))
        try:
            return l1_manual.apply(
                conn, criterion_id,
                passed=req.passed, checked_by=_ident["id"], note=req.note,
                evidence_snippet=req.evidence_snippet, file_path=req.file_path,
                line_no=req.line_no,
            )
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "CriterionNotFound", "message": str(exc)})
        except l1_manual.EvidenceRequired as exc:
            raise HTTPException(400, detail={"code": "EvidenceRequired", "message": str(exc)})


@router.post("/tasks/{task_id}/acceptance/run")
def run_acceptance(task_id: str, _ident: dict = Depends(require("task:*"))):
    from .. import acceptance_run

    with get_conn() as conn:
        return acceptance_run.run(conn, task_id, tenant.visible_entities(conn, _ident))


@router.post("/runs/{run_id}/execute")
def execute_run(run_id: str, req: ExecuteRunIn, _ident: dict = Depends(require("verb:*"))):
    from ..engine import dsh_adapter

    with get_conn() as conn:
        tenant.run(conn, run_id, tenant.entity_of(_ident))
        return dsh_adapter.execute_run(conn, run_id, prompt=req.prompt, runner=req.runner,
                                       budget_tokens=req.budget_tokens)


def _retry_prompt(conn: sqlite3.Connection, task_id: str) -> str:
    """The follow-up turn: same session, but told exactly what failed."""
    rows = conn.execute(
        """SELECT seq, criterion, note FROM acceptance_criteria
           WHERE task_id = ? AND state = 'failed' ORDER BY seq""", (task_id,)).fetchall()
    if not rows:
        return "继续上一次的任务：完成产出并写回同一文件，改完只回复 DONE。"
    lines = "\n".join(
        f"- 判据{int(r['seq'])}：{r['criterion']}｜未通过原因：{r['note'] or '人工判定'}"
        for r in rows)
    return ("上一轮稿件未通过验收。保持同一篇稿件，只针对下列问题修改并写回原文件，"
            "改完只回复 DONE：\n" + lines)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, req: RetryIn, _ident: dict = Depends(require("task:*"))):
    """L1 rejection / failed run -> new run on the same DSH session (context kept)."""
    from ..engine import dsh_adapter

    with get_conn() as conn:
        t = tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, _ident))
        if t["state"] not in ("rejected", "failed"):
            raise HTTPException(400, detail={"code": "NotRetryable", "state": t["state"],
                                             "message": "只有 rejected / failed 可以打回重跑"})

        prev = conn.execute(
            """SELECT * FROM task_runs WHERE task_id = ? AND legal_entity_id = ?
               AND session_id IS NOT NULL ORDER BY run_seq DESC LIMIT 1""",
            (task_id, t["legal_entity_id"])).fetchone()
        session_id = prev["session_id"] if prev else None
        prompt = req.prompt or _retry_prompt(conn, task_id)
        run_seq = conn.execute(
            "SELECT COALESCE(MAX(run_seq), 0) FROM task_runs WHERE task_id = ?"
            " AND legal_entity_id = ?", (task_id, t["legal_entity_id"])).fetchone()[0] + 1
        runner = (req.runner or os.environ.get("RUNNER") or "mock").lower()
        run_id = new_id("RUN")
        conn.execute(
            """INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner,
                                     session_id, actor_identity)
               VALUES (?,?,?,?, 'spawned', ?, ?, ?)""",
            (run_id, task_id, run_seq, t["legal_entity_id"], runner, session_id,
             _ident["id"]))
        transition(conn, task_id, "retry", actor="reviewer",
                   payload={"run_seq": run_seq, "resumed": bool(session_id)})
        # A retry is a new submission: every non-na criterion goes back to pending,
        # otherwise the previous manual verdict would keep the task rejected forever.
        conn.execute(
            """UPDATE acceptance_criteria
               SET state = 'pending', checked_by = NULL, note = NULL
               WHERE task_id = ? AND state != 'na'""", (task_id,))
        write_audit(conn, legal_entity_id=t["legal_entity_id"], verb="retry", task_id=task_id,
                    run_id=run_id, action="retry", target=run_id,
                    payload={"prompt": prompt[:400], "session_id": session_id, "run_seq": run_seq},
                    response={"resumed": bool(session_id)})
        conn.commit()
        return dsh_adapter.execute_run(conn, run_id, prompt=prompt, runner=runner,
                                       resume_session=session_id,
                                       budget_tokens=req.budget_tokens)


@router.get("/audit/by-key/{key}")
def audit_by_key(key: str, _ident: dict = Depends(require("audit:read"))):
    from .. import idempotency

    with get_conn() as conn:
        hist = idempotency.history(conn, key)
        entity = tenant.entity_of(_ident)
        if hist["original"] and hist["original"]["legal_entity_id"] != entity:
            raise HTTPException(404, detail={"code": "AuditNotFound", "key": key})
        return hist
