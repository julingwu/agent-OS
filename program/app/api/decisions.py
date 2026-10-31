"""Decision package endpoints: create, filter, CEO inbox, decide."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import decisions as engine
from .. import tenant
from ..db import get_conn
from ..identity import require
from ..models import DecideIn, DecisionIn

router = APIRouter(tags=["decisions"])


@router.post("/decisions")
def create_decision(req: DecisionIn, _ident: dict = Depends(require("decision:create"))):
    with get_conn() as conn:
        entity = tenant.resolve(_ident, req.legal_entity_id)
        try:
            return engine.create(
                conn, legal_entity_id=entity, created_by=_ident["id"], title=req.title,
                summary=req.summary, task_id=req.task_id, options=req.options,
                recommendation=req.recommendation, amount=req.amount,
                currency=req.currency, risk_level=req.risk_level,
                reversibility=req.reversibility, is_compliance=req.is_compliance,
                confidence=req.confidence, deadline=req.deadline)
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BadDecision", "message": str(exc)})


@router.get("/decisions")
def list_decisions(state: str | None = Query(default=None),
                   legal_entity_id: str | None = Query(default=None),
                   limit: int = Query(default=50, ge=1, le=200),
                   _ident: dict = Depends(require("decision:read"))):
    """`state=escalated_ceo` is the CEO inbox: nothing else ever reaches it."""
    with get_conn() as conn:
        sql = "SELECT * FROM decision_packages WHERE 1=1"
        args: list = []
        if state:
            sql += " AND state = ?"
            args.append(state)
        if legal_entity_id and legal_entity_id not in tenant.visible_entities(conn, _ident):
            raise HTTPException(403, detail={"code": "CrossEntityForbidden",
                                             "requested": legal_entity_id})
        ents = ([legal_entity_id] if legal_entity_id
                else tenant.visible_entities(conn, _ident))
        where, eargs = tenant._in("legal_entity_id", ents)
        sql += " AND " + where
        args.extend(eargs)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            r["options"] = []
        return {"count": len(rows), "decisions": rows}


@router.get("/decision-rules")
def list_rules(_ident: dict = Depends(require("decision:read"))):
    with get_conn() as conn:
        return {"rules": engine.active_rules(conn)}


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, _ident: dict = Depends(require("decision:read"))):
    with get_conn() as conn:
        try:
            tenant.scoped_decision(conn, decision_id, tenant.visible_entities(conn, _ident))
            return engine.get(conn, decision_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "DecisionNotFound", "id": decision_id})


@router.post("/decisions/{decision_id}/evaluate")
def evaluate_decision(decision_id: str, _ident: dict = Depends(require("decision:evaluate"))):
    with get_conn() as conn:
        try:
            tenant.scoped_decision(conn, decision_id, tenant.visible_entities(conn, _ident))
            return engine.evaluate(conn, decision_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "DecisionNotFound", "id": decision_id})
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "NotEvaluable", "message": str(exc)})


@router.post("/decisions/{decision_id}/decide")
def decide_decision(decision_id: str, req: DecideIn, _ident: dict = Depends(require("decision:read"))):
    with get_conn() as conn:
        row = tenant.scoped_decision(conn, decision_id, tenant.visible_entities(conn, _ident))
        state, role = row["state"], _ident["role"]
        if role == "system":
            pass                                   # operator/superuser
        elif state == "escalated_ceo" and role != "ceo":
            raise HTTPException(403, detail={"code": "NeedsCEO", "role": role})
        elif state == "escalated_manager" and role not in ("manager", "ceo"):
            raise HTTPException(403, detail={"code": "NeedsManager", "role": role})
        try:
            # decided_by comes from the verified signature, never from the body
            return engine.decide(conn, decision_id, approved=req.approved,
                                 decided_by=_ident["id"], role=role, note=req.note)
        except KeyError:
            raise HTTPException(404, detail={"code": "DecisionNotFound", "id": decision_id})
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "NotDecidable", "message": str(exc)})
