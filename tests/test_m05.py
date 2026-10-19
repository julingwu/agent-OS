"""M0.5 acceptance: decision packages, CEO filter, finance + audit dual track."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_BUDGET_LIMIT_AMOUNT", "100000")
    (tmp_path / "workspace").mkdir()
    from app.main import app
    from app import identity

    with TestClient(app) as c:
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.row_factory = sqlite3.Row
        ident = identity.issue(con, name="pytest", role="system")
        con.commit()
        con.close()
        yield SignedClient(c, ident["id"], ident["secret"], role="system")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def make(client, **kw) -> dict:
    body = {"title": "季度投放预算", "amount": 1000.0, "risk_level": "low",
            "reversibility": "reversible", "confidence": 0.9}
    body.update(kw)
    r = client.post("/decisions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def set_money_limit(limit: float) -> None:
    """Upsert the entity-level money budget (task_id NULL)."""
    con = db()
    row = con.execute("SELECT id FROM budgets WHERE task_id IS NULL").fetchone()
    if row:
        con.execute("UPDATE budgets SET limit_amount=? WHERE id=?", (limit, row["id"]))
    else:
        con.execute("INSERT INTO budgets (id, legal_entity_id, task_id, limit_tokens, limit_amount)"
                    " VALUES ('B-test','LE-GROUP-HQ',NULL,0,?)", (limit,))
    con.commit()


def run_filter(client, did: str) -> dict:
    r = client.post(f"/decisions/{did}/evaluate")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------- M0.5-1
def test_m05_1_small_reversible_is_auto_approved(client):
    d = make(client, amount=1200.0)
    out = run_filter(client, d["id"])

    assert out["state"] == "auto_approved"
    assert out["matched_rule"] == "FR-08"
    assert out["finance"][0]["verdict"] == "ok"
    assert out["finance"][0]["reserved_after"] == 1200.0

    inbox = client.get("/decisions", params={"state": "escalated_ceo"}).json()
    assert inbox["count"] == 0, "auto-approved packets must never reach the CEO"


# --------------------------------------------------------------- M0.5-2
def test_m05_2_compliance_always_reaches_the_ceo(client):
    d = make(client, amount=800.0, is_compliance=1)
    out = run_filter(client, d["id"])

    assert out["state"] == "escalated_ceo"
    assert out["matched_rule"] == "FR-01"
    inbox = client.get("/decisions", params={"state": "escalated_ceo"}).json()
    assert [x["id"] for x in inbox["decisions"]] == [d["id"]]


# --------------------------------------------------------------- M0.5-3
def test_m05_3_money_outranks_the_rule_table(client):
    set_money_limit(500.0)

    d = make(client, amount=5000.0)          # FR-06 would route to the manager
    out = run_filter(client, d["id"])

    assert out["state"] == "escalated_ceo"
    assert out["finance"][0]["verdict"] == "over_budget"
    assert "超出可用额度" in (out["filter_reason"] or "")

    # already-CEO by rule, but unfunded: the reason must still carry the finance note
    big = make(client, amount=120000.0)
    out2 = run_filter(client, big["id"])
    assert out2["matched_rule"] == "FR-04"
    assert "超出可用额度" in (out2["filter_reason"] or "")


# --------------------------------------------------------------- M0.5-4
def test_m05_4_ceo_approval_settles_the_money(client):
    d = make(client, amount=8000.0, risk_level="medium")
    routed = run_filter(client, d["id"])
    assert routed["state"] == "escalated_manager"

    r = client.post(f"/decisions/{d['id']}/decide",
                    json={"approved": True, "decided_by": "ceo", "note": "批"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["state"] == "approved"
    assert out["decided_by"] == client.ident_id

    con = db()
    b = con.execute("SELECT * FROM budgets").fetchone()
    assert b["used_amount"] == 8000.0
    assert b["reserved_amount"] == 0.0


# --------------------------------------------------------------- M0.5-5
def test_m05_5_dual_track_on_every_decision(client):
    d = make(client, amount=60000.0)          # FR-04 -> CEO
    run_filter(client, d["id"])

    con = db()
    fin = con.execute("SELECT COUNT(*) FROM decision_finance WHERE decision_id=?",
                      (d["id"],)).fetchone()[0]
    assert fin == 1, "finance track row is mandatory"

    verbs = [r[0] for r in con.execute(
        "SELECT verb FROM ai_audit_logs WHERE target=? ORDER BY rowid", (d["id"],))]
    assert verbs == ["decision_created", "decision_finance", "decision_filter"], verbs

    payload = json.loads(con.execute(
        "SELECT payload FROM ai_audit_logs WHERE verb='decision_filter' AND target=?",
        (d["id"],)).fetchone()[0])
    assert payload["rule"] == "FR-04"
    assert payload["inputs"]["amount"] == 60000.0


# --------------------------------------------------------------- M0.5-6
def test_m05_6_cannot_decide_an_unrouted_packet(client):
    d = make(client, amount=100.0)
    run_filter(client, d["id"])
    r = client.post(f"/decisions/{d['id']}/decide", json={"approved": True})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NotDecidable"


# --------------------------------------------------------------- M0.5-7
def test_m05_7_rules_are_data_not_code(client):
    rules = client.get("/decision-rules").json()["rules"]
    assert [r["id"] for r in rules][:2] == ["FR-01", "FR-02"]

    con = db()
    con.execute("UPDATE decision_filter_rules SET active=0 WHERE id='FR-01'")
    con.commit()

    d = make(client, amount=900.0, is_compliance=1)
    out = run_filter(client, d["id"])
    assert out["matched_rule"] == "FR-08", "disabling a rule must change routing without a deploy"
    assert out["state"] == "auto_approved"


# --------------------------------------------------------------- M0.5-8
def test_m05_8_rejection_releases_the_reservation(client):
    d = make(client, amount=30000.0)          # FR-06 -> manager, but reserved on ok
    run_filter(client, d["id"])
    con = db()
    assert con.execute("SELECT reserved_amount FROM budgets").fetchone()[0] == 30000.0

    client.post(f"/decisions/{d['id']}/decide", json={"approved": False, "decided_by": "cfo"})
    con = db()
    row = con.execute("SELECT reserved_amount, used_amount FROM budgets").fetchone()
    assert row["reserved_amount"] == 0.0
    assert row["used_amount"] == 0.0
