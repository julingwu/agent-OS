"""The six verbs. `do_*` functions hold the logic so the adapter can call them
in-process; the endpoints are thin wrappers that own the transaction."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import idempotency, tenant
from ..acceptance import l0_rules, l2_scripts, task_text
from ..audit import new_id, write_audit
from ..db import get_conn
from ..identity import require
from ..models import (
    ArbitrationIn,
    AttachArtifactIn,
    EmitEventIn,
    HeartbeatIn,
    ReserveBudgetIn,
    SpawnRunIn,
)
from ..state_machine import IllegalStateTransition, transition

router = APIRouter(prefix="/verbs", tags=["verbs"])
ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- helpers
def budget_limit() -> int:
    return int(os.environ.get("M0_BUDGET_LIMIT_TOKENS", "200000"))


def amount_limit() -> float:
    return float(os.environ.get("M0_BUDGET_LIMIT_AMOUNT", "100000"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | pathlib.Path) -> str | None:
    p = pathlib.Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scoped_run(conn: sqlite3.Connection, run_id: str, ident: dict | None) -> sqlite3.Row:
    """External callers are confined to their own entity; internal calls skip it."""
    if ident is None:
        return get_run(conn, run_id)
    return tenant.run(conn, run_id, tenant.entity_of(ident))


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "RunNotFound", "run_id": run_id})
    return row


def latest_artifact_path(conn: sqlite3.Connection, task_id: str,
                         name: str | None = None) -> str | None:
    sql = """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id = a.run_id
             WHERE r.task_id = ?"""
    args: list = [task_id]
    if name:
        sql += " AND a.file_path LIKE ?"
        args.append(f"%{name}")
    sql += " ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return row["file_path"] if row else None


def entity_pool(conn: sqlite3.Connection, entity: str) -> sqlite3.Row | None:
    """The entity's shared token pool (identity_id NULL, task_id NULL)."""
    return conn.execute(
        """SELECT * FROM budgets WHERE legal_entity_id = ? AND task_id IS NULL
             AND identity_id IS NULL AND state = 'active' AND limit_tokens > 0
           ORDER BY rowid DESC LIMIT 1""",
        (entity,)).fetchone()


def get_or_create_budget(conn: sqlite3.Connection, run: sqlite3.Row) -> sqlite3.Row:
    """One budget row per (agent, task): two agents never share headroom."""
    actor = run["actor_identity"] if "actor_identity" in run.keys() else None
    row = conn.execute(
        "SELECT * FROM budgets WHERE task_id = ? AND COALESCE(identity_id,'') = ?"
        " ORDER BY rowid DESC LIMIT 1",
        (run["task_id"], actor or ""),
    ).fetchone()
    def _limit() -> int:
        base = budget_limit()
        pool = entity_pool(conn, run["legal_entity_id"])
        if pool is not None:                      # a task may not exceed what the pool has left
            left = max(0, pool["limit_tokens"] - pool["used_tokens"] - pool["reserved_tokens"])
            base = min(base, left)
        return base

    if row is None:
        bid = new_id("B")
        limit = _limit()
        conn.execute(
            """INSERT INTO budgets (id, legal_entity_id, identity_id, task_id,
                                    limit_tokens, limit_amount)
               VALUES (?,?,?,?,?,?)""",
            (bid, run["legal_entity_id"], actor, run["task_id"],
             limit, amount_limit()),
        )
        row = conn.execute("SELECT * FROM budgets WHERE id = ?", (bid,)).fetchone()
    return row


# ---------------------------------------------------------------- verbs
def do_spawn_run(conn: sqlite3.Connection, req: SpawnRunIn,
                 ident: dict | None = None) -> dict:
    entity = tenant.resolve(ident, req.legal_entity_id)
    task = tenant.task(conn, req.task_id, entity)
    key = f"{req.task_id}:{req.run_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="spawn_run")

    run_id = new_id("RUN")
    actor = (ident or {}).get("id")
    conn.execute(
        """INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner,
                                  actor_identity)
           VALUES (?,?,?,?, 'spawned', ?, ?)""",
        (run_id, req.task_id, req.run_seq, entity, os.environ.get("RUNNER", "mock"), actor),
    )
    conn.execute(
        "UPDATE tasks SET run_count = COALESCE(run_count, 0) + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (req.task_id,),
    )
    get_or_create_budget(conn, get_run(conn, run_id))
    state = transition(conn, req.task_id, "run_spawned", actor="shell")
    resp = {"run_id": run_id, "state": state}
    write_audit(
        conn,
        legal_entity_id=entity,
        verb="spawn_run",
        task_id=req.task_id,
        run_id=run_id,
        action="spawn_run",
        target=run_id,
        payload=req.model_dump(),
        response=resp,
        idempotency_key=key,
    )
    return resp


def do_report_heartbeat(conn: sqlite3.Connection, req: HeartbeatIn,
                        ident: dict | None = None) -> dict:
    key = f"{req.run_id}:hb:{req.hb_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="report_heartbeat")

    run = scoped_run(conn, req.run_id, ident)
    intercepted = run["state"] not in ("spawned", "running")
    if not intercepted:
        conn.execute(
            """UPDATE task_runs SET hb_seq = ?, last_hb_at = CURRENT_TIMESTAMP,
               est_next_cost = COALESCE(?, est_next_cost) WHERE id = ?""",
            (req.hb_seq, req.estimated_next_cost, req.run_id),
        )
    resp = {"ok": True, "intercepted": intercepted}
    write_audit(
        conn,
        legal_entity_id=run["legal_entity_id"],
        verb="report_heartbeat",
        task_id=run["task_id"],
        run_id=req.run_id,
        action="heartbeat",
        target=req.run_id,
        payload={"hb_seq": req.hb_seq, "estimated_next_cost": req.estimated_next_cost},
        response=resp,
        idempotency_key=key,
        is_deterministic=0,
    )
    return resp


def do_attach_artifact(conn: sqlite3.Connection, req: AttachArtifactIn,
                       ident: dict | None = None) -> dict:
    key = f"{req.run_id}:art:{req.art_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="attach_artifact")

    run = scoped_run(conn, req.run_id, ident)
    p = pathlib.Path(req.file_path)
    if p.is_file():
        data = p.read_bytes()
        content = req.content if req.content is not None else data.decode("utf-8", "replace")
    elif req.content is not None:
        data = req.content.encode("utf-8")
        content = req.content
    else:
        conn.commit()
        raise HTTPException(
            400,
            detail={"code": "ArtifactMissing", "file_path": req.file_path,
                    "message": "file does not exist and no content supplied"},
        )

    checksum = sha256_bytes(data)
    art_id = new_id("ART")
    conn.execute(
        """INSERT INTO artifacts (id, run_id, art_seq, file_path, content, checksum, bytes)
           VALUES (?,?,?,?,?,?,?)""",
        (art_id, req.run_id, req.art_seq, str(p), content, checksum, len(data)),
    )
    resp = {"artifact_id": art_id, "checksum": checksum, "bytes": len(data)}
    write_audit(
        conn,
        legal_entity_id=run["legal_entity_id"],
        verb="attach_artifact",
        task_id=run["task_id"],
        run_id=req.run_id,
        action="attach_artifact",
        target=art_id,
        payload={"art_seq": req.art_seq, "file_path": str(p)},
        response=resp,
        idempotency_key=key,
    )
    return resp


def do_request_arbitration(conn: sqlite3.Connection, req: ArbitrationIn,
                           ident: dict | None = None) -> dict:
    key = f"{req.run_id}:arb:{req.arb_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="request_arbitration")

    c = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE id = ?", (req.criterion_id,)
    ).fetchone()
    if c is None:
        raise HTTPException(404, detail={"code": "CriterionNotFound", "id": req.criterion_id})

    run = scoped_run(conn, req.run_id, ident)
    result, detail = "needs_human", "人工判据，转 L1"
    if c["check_type"] == "rule_auto":
        verdict = l0_rules.evaluate(c["check_ref"], task_text(conn, c["task_id"], check_ref=c["check_ref"]) or "")
        result = "passed" if verdict["ok"] else "failed"
        detail = verdict.get("detail") or ""
    elif c["check_type"] == "script_exec":
        verdict = l2_scripts.evaluate(c["check_ref"], latest_artifact_path(conn, c["task_id"]))
        result = "passed" if verdict.get("ok") else "failed"
        detail = verdict.get("detail") or ""
    elif c["check_type"] != "human_check":
        raise HTTPException(400, detail={"code": "UnsupportedCheckType",
                                         "check_type": c["check_type"]})

    resp = {
        "result": result,
        "layer": c["acceptance_layer"],
        "is_deterministic": bool(c["is_deterministic"]),
        "detail": detail,
    }
    write_audit(
        conn,
        legal_entity_id=run["legal_entity_id"],
        verb="request_arbitration",
        task_id=run["task_id"],
        run_id=req.run_id,
        layer=c["acceptance_layer"] or "system",
        action="arbitration",
        target=req.criterion_id,
        payload={"arb_seq": req.arb_seq},
        response=resp,
        idempotency_key=key,
        is_deterministic=int(c["is_deterministic"]),
    )
    return resp


def do_emit_event(conn: sqlite3.Connection, req: EmitEventIn,
                  ident: dict | None = None) -> dict:
    key = f"{req.run_id}:evt:{req.evt_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="emit_event")

    run = scoped_run(conn, req.run_id, ident)
    try:
        state = transition(conn, run["task_id"], req.event_type,
                           actor="adapter", payload={"event_payload": req.payload or {}})
    except IllegalStateTransition as exc:
        write_audit(
            conn,
            legal_entity_id=run["legal_entity_id"],
            verb="emit_event",
            task_id=run["task_id"],
            run_id=req.run_id,
            action="rejected",
            target=req.event_type,
            payload={"evt_seq": req.evt_seq, "event_type": req.event_type},
            response={"error": "IllegalStateTransition", "current": exc.current},
        )
        conn.commit()
        raise HTTPException(
            400,
            detail={"code": "IllegalStateTransition", "current": exc.current,
                    "event": req.event_type, "message": str(exc)},
        )

    resp = {"ok": True, "new_task_state": state}
    write_audit(
        conn,
        legal_entity_id=run["legal_entity_id"],
        verb="emit_event",
        task_id=run["task_id"],
        run_id=req.run_id,
        action="emit_event",
        target=req.event_type,
        payload={"evt_seq": req.evt_seq, "payload": req.payload},
        response=resp,
        idempotency_key=key,
    )
    return resp


def do_reserve_budget(conn: sqlite3.Connection, req: ReserveBudgetIn,
                      ident: dict | None = None) -> dict:
    key = f"{req.run_id}:bud:{req.call_seq}"
    hit = idempotency.lookup(conn, key)
    if hit:
        return idempotency.replay(conn, key, hit, verb="reserve_budget")

    run = scoped_run(conn, req.run_id, ident)
    b = get_or_create_budget(conn, run)
    if b["reserved_tokens"] + req.estimated_tokens > b["limit_tokens"]:
        resp = {"result": "denied", "reason": "budget_exhausted",
                "limit": b["limit_tokens"], "reserved": b["reserved_tokens"]}
    else:
        conn.execute(
            "UPDATE budgets SET reserved_tokens = reserved_tokens + ? WHERE id = ?",
            (req.estimated_tokens, b["id"]),
        )
        resp = {"result": "approved", "reason": None,
                "limit": b["limit_tokens"],
                "reserved": b["reserved_tokens"] + req.estimated_tokens}
    write_audit(
        conn,
        legal_entity_id=run["legal_entity_id"],
        verb="reserve_budget",
        task_id=run["task_id"],
        run_id=req.run_id,
        action="reserve_budget",
        target=b["id"],
        payload={"call_seq": req.call_seq, "estimated_tokens": req.estimated_tokens},
        response=resp,
        idempotency_key=key,
    )
    return resp


# ---------------------------------------------------------------- endpoints
@router.post("/spawn_run")
def spawn_run(req: SpawnRunIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        try:
            return do_spawn_run(conn, req, ident=_ident)
        except IllegalStateTransition as exc:
            raise HTTPException(400, detail={"code": "IllegalStateTransition",
                                             "current": exc.current,
                                             "task_id": req.task_id})


@router.post("/report_heartbeat")
def report_heartbeat(req: HeartbeatIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        return do_report_heartbeat(conn, req, ident=_ident)


@router.post("/attach_artifact")
def attach_artifact(req: AttachArtifactIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        return do_attach_artifact(conn, req, ident=_ident)


@router.post("/request_arbitration")
def request_arbitration(req: ArbitrationIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        return do_request_arbitration(conn, req, ident=_ident)


@router.post("/emit_event")
def emit_event(req: EmitEventIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        return do_emit_event(conn, req, ident=_ident)


@router.post("/reserve_budget")
def reserve_budget(req: ReserveBudgetIn, _ident: dict = Depends(require("verb:*"))):
    with get_conn() as conn:
        return do_reserve_budget(conn, req, ident=_ident)
