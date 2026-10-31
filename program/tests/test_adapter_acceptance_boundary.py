"""Signed adapter reports must never act as an acceptance authority."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import acceptance_run, identity
from app.api import verbs
from app.main import app
from app.models import EmitEventIn
from tests.authclient import SignedClient
from tests.test_governance_integration import env, rule, submission


@pytest.mark.parametrize("role", ["agent", "ceo", "system"])
def test_signed_review_event_cannot_complete_forbidden_delivery(env, role):
    conn, people, folder = env
    reviewer = people["ceo"]
    rule(conn, reviewer, terms="绝对")
    submission(conn, folder, text="<p>绝对保证</p>")
    caller = identity.issue(conn, name="Adapter caller", role=role)
    conn.commit()
    with TestClient(app) as transport:
        signed = SignedClient(transport, caller["id"], caller["secret"], role=role)
        response = signed.post("/verbs/emit_event", json={
            "run_id": "R1", "evt_seq": 1, "event_type": "review_passed"})
        assert response.status_code == 422
    assert conn.execute("SELECT state FROM tasks WHERE id='T1'").fetchone()[0] == "pending_review"
    assert acceptance_run.validate_mandatory(conn, "T1")["ok"] is False


def test_in_process_model_bypass_cannot_certify_delivery(env):
    conn, people, folder = env
    rule(conn, people["ceo"], terms="绝对")
    submission(conn, folder, text="绝对保证")
    forged = EmitEventIn.model_construct(run_id="R1", evt_seq=1, event_type="review_passed", payload=None)
    with pytest.raises(HTTPException) as denied:
        verbs.do_emit_event(conn, forged)
    assert denied.value.detail["code"] == "LifecycleEventNotAllowed"
    assert conn.execute("SELECT state FROM tasks WHERE id='T1'").fetchone()[0] == "pending_review"


@pytest.mark.parametrize("event,state", [("run_succeeded", "pending_review"),
                                         ("run_failed", "failed"),
                                         ("run_cancelled", "cancelled")])
def test_signed_adapter_execution_reports_remain_available(env, event, state):
    conn, people, _ = env
    caller = people["ceo"]
    with TestClient(app) as transport:
        signed = SignedClient(transport, caller["id"], caller["secret"], role="ceo")
        task = signed.post("/tasks/from_sentence", json={"text": "写一份使用说明"}).json()
        spawned = signed.post("/verbs/spawn_run", json={"task_id": task["task_id"], "run_seq": 1})
        assert spawned.status_code == 200
        run_id = spawned.json()["run_id"]
        started = signed.post("/verbs/emit_event", json={"run_id": run_id, "evt_seq": 1, "event_type": "run_started"})
        assert started.status_code == 200
        ended = signed.post("/verbs/emit_event", json={"run_id": run_id, "evt_seq": 2, "event_type": event})
        assert ended.status_code == 200
        assert ended.json()["new_task_state"] == state


def test_clean_submission_still_completes_through_acceptance_service(env):
    conn, people, folder = env
    rule(conn, people["ceo"], terms="绝对")
    submission(conn, folder, text="<p>已核对来源的普通文案。</p>")
    result = acceptance_run.run(conn, "T1", "LE-GROUP-HQ")
    assert result["task_state"] == "completed"
