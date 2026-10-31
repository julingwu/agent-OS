"""Decision packages: the CEO filter, on top of a finance track and an audit track.

Two tracks run on every evaluation, and both are persisted:

  * finance — the amount is checked against the money budget and *reserved* when
    it fits, so two packets can never spend the same headroom.
  * audit   — the filter records which rule matched, with which inputs, and what
    finance said. A decision that never reaches the CEO still has a full trail.

The CEO only sees `escalated_ceo`. Everything else is auto-approved, routed to a
manager, or blocked — all of it explainable after the fact.
"""
from __future__ import annotations

import json
import sqlite3

from .audit import new_id, write_audit

RISK = {"low", "medium", "high"}
REVERSIBILITY = {"reversible", "costly", "irreversible"}
ACTIONS = {"auto_approve", "escalate_manager", "escalate_ceo", "block"}

STATE_OF_ACTION = {
    "auto_approve": "auto_approved",
    "escalate_manager": "escalated_manager",
    "escalate_ceo": "escalated_ceo",
    "block": "blocked",
}
RANK = {"auto_approve": 0, "escalate_manager": 1, "escalate_ceo": 2, "block": 3}


def create(conn: sqlite3.Connection, *, legal_entity_id: str, title: str,
           summary: str | None = None, task_id: str | None = None,
           options: list | None = None, recommendation: str | None = None,
           amount: float = 0.0, currency: str = "CNY", risk_level: str = "low",
           reversibility: str = "reversible", is_compliance: int = 0,
           confidence: float = 1.0, deadline: str | None = None,
           created_by: str | None = None) -> dict:
    if risk_level not in RISK:
        raise ValueError(f"risk_level must be one of {sorted(RISK)}")
    if reversibility not in REVERSIBILITY:
        raise ValueError(f"reversibility must be one of {sorted(REVERSIBILITY)}")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be within 0..1")
    did = new_id("DP")
    conn.execute(
        """INSERT INTO decision_packages
           (id, legal_entity_id, task_id, created_by, title, summary, options, recommendation,
            amount, currency, risk_level, reversibility, is_compliance, confidence, deadline,
            state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'draft')""",
        (did, legal_entity_id, task_id, created_by, title, summary,
         json.dumps(options or [], ensure_ascii=False), recommendation, float(amount),
         currency, risk_level, reversibility, int(is_compliance), float(confidence), deadline),
    )
    write_audit(conn, legal_entity_id=legal_entity_id, verb="decision_created", task_id=task_id,
                action="decision_created", target=did,
                payload={"title": title, "amount": amount, "risk": risk_level,
                         "reversibility": reversibility, "compliance": bool(is_compliance),
                         "confidence": confidence},
                response={"state": "draft"})
    return get(conn, did)


def get(conn: sqlite3.Connection, decision_id: str) -> dict:
    row = conn.execute("SELECT * FROM decision_packages WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        raise KeyError(decision_id)
    d = dict(row)
    d["options"] = json.loads(d["options"]) if d.get("options") else []
    d["finance"] = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_finance WHERE decision_id = ? ORDER BY checked_at", (decision_id,))]
    d["audit"] = [dict(r) for r in conn.execute(
        """SELECT * FROM ai_audit_logs WHERE target = ? AND verb LIKE 'decision%'
           ORDER BY created_at, rowid""", (decision_id,))]
    return d


def active_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM decision_filter_rules WHERE active = 1 ORDER BY priority, id").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["when"] = json.loads(item["when_json"] or "{}")
        out.append(item)
    return out


def _match(when: dict, d: dict) -> bool:
    """Unknown condition keys fail closed (no match) — never silently escalate."""
    for key, value in (when or {}).items():
        amount = float(d.get("amount") or 0)
        if key == "amount_gte" and not amount >= float(value):
            return False
        elif key == "amount_lt" and not amount < float(value):
            return False
        elif key == "risk_in" and d.get("risk_level") not in value:
            return False
        elif key == "reversibility_in" and d.get("reversibility") not in value:
            return False
        elif key == "compliance" and bool(d.get("is_compliance")) != bool(value):
            return False
        elif key == "confidence_lt" and not float(d.get("confidence") or 0) < float(value):
            return False
        elif key not in {"amount_gte", "amount_lt", "risk_in", "reversibility_in",
                         "compliance", "confidence_lt"}:
            return False
    return True


def money_budget(conn: sqlite3.Connection, d: dict) -> sqlite3.Row:
    """The money budget for this decision: its task's, else the entity's.

    Created on first use with the default limit, so a decision is never silently
    "unbudgeted" just because nobody happened to run a task first.
    """
    if d.get("task_id"):
        row = conn.execute(
            "SELECT * FROM budgets WHERE task_id = ? ORDER BY rowid DESC LIMIT 1",
            (d["task_id"],)).fetchone()
        if row:
            return row
    row = None
    if d.get("created_by"):
        row = conn.execute(
            """SELECT * FROM budgets WHERE legal_entity_id = ? AND identity_id = ?
               AND task_id IS NULL ORDER BY rowid DESC LIMIT 1""",
            (d["legal_entity_id"], d["created_by"])).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM budgets WHERE legal_entity_id = ? AND task_id IS NULL"
            " AND identity_id IS NULL ORDER BY rowid DESC LIMIT 1", (d["legal_entity_id"],)
        ).fetchone()
    if row:
        return row
    bid = new_id("B")
    conn.execute(
        """INSERT INTO budgets (id, legal_entity_id, identity_id, task_id, limit_tokens,
                                 limit_amount)
           VALUES (?,?,?,NULL,0,?)""",
        (bid, d["legal_entity_id"], d.get("created_by"),
         float(__import__("os").environ.get("M0_BUDGET_LIMIT_AMOUNT", "100000"))))
    return conn.execute("SELECT * FROM budgets WHERE id = ?", (bid,)).fetchone()


def finance_check(conn: sqlite3.Connection, d: dict) -> dict:
    amount = float(d.get("amount") or 0)
    currency = (d.get("currency") or "CNY").upper()
    if currency != "CNY":
        # token/quota requests are not money: they must not reserve the CNY budget
        # (a 150k-token ask used to eat the 100k money line and show "可用 -50000")
        b, verdict, available, budget_id = None, "non_monetary", 0.0, None
        fid = new_id("DF")
        conn.execute(
            """INSERT INTO decision_finance
               (id, decision_id, budget_id, amount, currency, available_before, reserved_after, verdict)
               VALUES (?,?,?,?,?,?,?,?)""",
            (fid, d["id"], None, amount, currency, 0.0, 0.0, verdict))
        write_audit(conn, legal_entity_id=d["legal_entity_id"], verb="decision_finance",
                    task_id=d.get("task_id"), action="finance_check", target=d["id"],
                    payload={"amount": amount, "currency": currency},
                    response={"verdict": verdict, "reserved_after": 0.0})
        return {"finance_id": fid, "budget_id": None, "verdict": verdict,
                "available_before": 0.0, "reserved_after": 0.0}

    b = money_budget(conn, d)
    if b is None:
        verdict, available, budget_id = "unbudgeted", 0.0, None
    else:
        limit = float(b["limit_amount"] or 0)
        used = float(b["used_amount"] or 0)
        reserved = float(b["reserved_amount"] or 0)
        available = max(0.0, limit - used - reserved)
        budget_id = b["id"]
        verdict = "unbudgeted" if limit <= 0 else ("ok" if amount <= available else "over_budget")

    reserved_after = 0.0
    if verdict == "ok" and amount > 0:
        conn.execute("UPDATE budgets SET reserved_amount = reserved_amount + ? WHERE id = ?",
                     (amount, budget_id))
        reserved_after = amount

    fid = new_id("DF")
    conn.execute(
        """INSERT INTO decision_finance
           (id, decision_id, budget_id, amount, currency, available_before, reserved_after, verdict)
           VALUES (?,?,?,?,?,?,?,?)""",
        (fid, d["id"], budget_id, amount, d.get("currency") or "CNY", available,
         reserved_after, verdict),
    )
    write_audit(conn, legal_entity_id=d["legal_entity_id"], verb="decision_finance",
                task_id=d.get("task_id"), action="finance_check", target=d["id"],
                payload={"amount": amount, "budget_id": budget_id},
                response={"verdict": verdict, "available_before": available,
                          "reserved_after": reserved_after})
    return {"finance_id": fid, "budget_id": budget_id, "verdict": verdict,
            "available_before": available, "reserved_after": reserved_after}


def evaluate(conn: sqlite3.Connection, decision_id: str) -> dict:
    d = dict(conn.execute("SELECT * FROM decision_packages WHERE id = ?",
                          (decision_id,)).fetchone() or {})
    if not d:
        raise KeyError(decision_id)
    if d["state"] != "draft":
        raise ValueError(f"only draft decisions can be evaluated (state={d['state']})")

    fin = finance_check(conn, d)

    matched, action, reason = None, None, None
    for rule in active_rules(conn):
        if _match(rule["when"], d):
            matched, action, reason = rule, rule["action"], rule["reason"]
            break

    # Money outranks the rule table: an unfunded decision never gets auto-approved,
    # and the CEO always sees why it is unfunded — even when a rule already sent it up.
    if fin["verdict"] == "over_budget":
        if RANK.get(action, 0) < RANK["escalate_ceo"]:
            action = "escalate_ceo"
        reason = f"{reason}；财务：超出可用额度（可用 {fin['available_before']:.2f}）"
    elif fin["verdict"] == "unbudgeted" and action == "auto_approve":
        action = "escalate_manager"
        reason = f"{reason}；财务：没有可用额度，降级人工"

    state = STATE_OF_ACTION[action]
    if action == "block" and fin["reserved_after"]:
        conn.execute("UPDATE budgets SET reserved_amount = reserved_amount - ? WHERE id = ?",
                     (fin["reserved_after"], fin["budget_id"]))

    conn.execute(
        """UPDATE decision_packages SET state = ?, route = ?, matched_rule = ?,
             filter_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (state, action, matched["id"] if matched else None, reason, decision_id))
    write_audit(
        conn, legal_entity_id=d["legal_entity_id"], verb="decision_filter",
        task_id=d.get("task_id"), action="filter", target=decision_id,
        payload={"rule": matched["id"] if matched else None,
                 "when": matched["when"] if matched else None,
                 "inputs": {"amount": d["amount"], "risk": d["risk_level"],
                            "reversibility": d["reversibility"],
                            "compliance": bool(d["is_compliance"]),
                            "confidence": d["confidence"]}},
        response={"state": state, "route": action, "reason": reason,
                  "finance": fin["verdict"]})
    return get(conn, decision_id)


def decide(conn: sqlite3.Connection, decision_id: str, *, approved: bool,
           decided_by: str, role: str | None = None, note: str | None = None) -> dict:
    d = dict(conn.execute("SELECT * FROM decision_packages WHERE id = ?",
                          (decision_id,)).fetchone() or {})
    if not d:
        raise KeyError(decision_id)
    if d["state"] not in ("escalated_manager", "escalated_ceo"):
        raise ValueError(f"decision is not awaiting a human (state={d['state']})")

    amount = float(d["amount"] or 0)
    b = money_budget(conn, d)
    if b is not None and amount > 0:
        if approved:
            conn.execute(
                "UPDATE budgets SET reserved_amount = MAX(0, reserved_amount - ?),"
                " used_amount = used_amount + ? WHERE id = ?", (amount, amount, b["id"]))
        else:
            conn.execute("UPDATE budgets SET reserved_amount = MAX(0, reserved_amount - ?)"
                         " WHERE id = ?", (amount, b["id"]))

    state = "approved" if approved else "rejected"
    conn.execute(
        """UPDATE decision_packages SET state = ?, decided_by = ?, decision_note = ?,
             decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (state, decided_by, note, decision_id))
    write_audit(conn, legal_entity_id=d["legal_entity_id"], verb="decision_decided",
                task_id=d.get("task_id"), actor=decided_by, action="decision_decided",
                target=decision_id, payload={"approved": approved, "note": note, "role": role},
                response={"state": state, "amount": amount})
    if not approved:
        try:
            from . import experience
            experience.capture_decision_rejection(conn, decision_id, note=note)
        except Exception:                    # noqa: BLE001
            pass
    conn.commit()
    return get(conn, decision_id)
