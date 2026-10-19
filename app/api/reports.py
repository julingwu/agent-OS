"""Usage/report API: tokens, artifacts, success rate — by entity, agent, pack."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import tenant
from ..db import get_conn
from ..identity import require

router = APIRouter(tags=["reports"])


def _task_filter(ents: list[str], pack: str | None, since: str | None,
                 until: str | None) -> tuple[str, list]:
    cond, args = tenant._in("t.legal_entity_id", ents)
    sql = " WHERE " + cond
    if pack:
        sql += " AND t.pack_id = ?"
        args.append(pack)
    if since:
        sql += " AND t.created_at >= ?"
        args.append(since)
    if until:
        sql += " AND t.created_at < ?"
        args.append(until)
    return sql, args


@router.get("/reports/usage")
def usage(legal_entity_id: str | None = None, identity: str | None = None,
          pack: str | None = None, since: str | None = None, until: str | None = None,
          _ident: dict = Depends(require("audit:read"))):
    with get_conn() as conn:
        ents = tenant.visible_entities(conn, _ident)
        if legal_entity_id:
            if legal_entity_id not in ents:
                raise HTTPException(403, detail={"code": "CrossEntityForbidden",
                                                 "requested": legal_entity_id})
            ents = [legal_entity_id]
        entity = legal_entity_id or tenant.entity_of(_ident)
        where, args = _task_filter(ents, pack, since, until)
        if identity:
            where += " AND t.id IN (SELECT task_id FROM task_runs WHERE actor_identity = ?)"
            args.append(identity)

        tasks = conn.execute(
            f"""SELECT t.state, COUNT(*) AS n FROM tasks t {where} GROUP BY t.state""",
            args).fetchall()
        by_state = {r["state"]: r["n"] for r in tasks}
        total_tasks = sum(by_state.values())

        runs = conn.execute(
            f"""SELECT COUNT(*) AS n,
                       SUM(CASE WHEN r.state='succeeded' THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN r.state IN ('failed','timeout') THEN 1 ELSE 0 END) AS bad,
                       COALESCE(SUM(r.usage_tokens),0) AS tokens
                FROM task_runs r JOIN tasks t ON t.id = r.task_id {where}""", args).fetchone()

        artifacts = conn.execute(
            f"""SELECT COUNT(*) FROM artifacts a
                JOIN task_runs r ON r.id = a.run_id
                JOIN tasks t ON t.id = r.task_id {where}""", args).fetchone()[0]

        by_pack = conn.execute(
            f"""SELECT COALESCE(t.pack_id,'unassigned') AS pack_id, COUNT(DISTINCT t.id) AS tasks,
                       SUM(CASE WHEN t.state='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN t.state IN ('rejected','failed') THEN 1 ELSE 0 END) AS failed
                FROM tasks t {where} GROUP BY t.pack_id ORDER BY tasks DESC""", args).fetchall()

        tokens_by_pack = {r["pack_id"]: r["tokens"] for r in conn.execute(
            f"""SELECT COALESCE(t.pack_id,'unassigned') AS pack_id, COALESCE(SUM(r.usage_tokens),0) AS tokens
                FROM task_runs r JOIN tasks t ON t.id = r.task_id {where}
                GROUP BY t.pack_id""", args)}

        by_identity = conn.execute(
            f"""SELECT COALESCE(r.actor_identity,'system') AS identity, COUNT(*) AS runs,
                       COALESCE(SUM(r.usage_tokens),0) AS tokens,
                       SUM(CASE WHEN r.state='succeeded' THEN 1 ELSE 0 END) AS succeeded
                FROM task_runs r JOIN tasks t ON t.id = r.task_id {where}
                GROUP BY r.actor_identity ORDER BY tokens DESC""", args).fetchall()

        dwhere, dargs = tenant._in("legal_entity_id", ents)
        decisions = conn.execute(
            f"""SELECT state, COUNT(*) AS n, COALESCE(SUM(amount),0) AS amount
                FROM decision_packages WHERE {dwhere} GROUP BY state""", dargs).fetchall()
        dec = {r["state"]: {"count": r["n"], "amount": r["amount"]} for r in decisions}

        done = by_state.get("completed", 0)
        closed = done + by_state.get("rejected", 0) + by_state.get("failed", 0)
        run_total = runs["n"] or 0

        return {
            "entity": entity,
            "entities": ents,
            "filters": {"identity": identity, "pack": pack, "since": since, "until": until},
            "totals": {
                "tasks": total_tasks,
                "states": by_state,
                "task_success_rate": round(done / closed, 4) if closed else None,
                "runs": run_total,
                "run_success_rate": round((runs["ok"] or 0) / run_total, 4) if run_total else None,
                "tokens": runs["tokens"],
                "artifacts": artifacts,
            },
            "by_pack": [{"pack": r["pack_id"], "tasks": r["tasks"], "completed": r["completed"],
                         "failed": r["failed"], "tokens": tokens_by_pack.get(r["pack_id"], 0),
                         "success_rate": round(r["completed"] / max(1, r["completed"] + r["failed"]), 4)}
                        for r in by_pack],
            "by_identity": [{"identity": r["identity"], "runs": r["runs"], "tokens": r["tokens"],
                             "succeeded": r["succeeded"]} for r in by_identity],
            "decisions": dec,
        }


@router.get("/packs")
def list_packs(_ident: dict = Depends(require("task:read"))):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM packs WHERE state='active' ORDER BY id").fetchall()
        return {"packs": [dict(r) for r in rows]}
