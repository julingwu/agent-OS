"""Execution summaries distinguish parked tasks without exposing approval data."""
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import identity, rbac, trace
from app.db import get_conn, init_db


@pytest.fixture()
def status_console(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "status.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    init_db()
    from app.ui import api, store
    app = FastAPI()
    app.include_router(api.router)
    with get_conn() as conn:
        store.ensure(conn)
        rbac.ensure(conn)
        trace.ensure(conn)
        conn.execute("UPDATE legal_entities SET governance_mode='group_direct' WHERE id='LE-GROUP-HQ'")
        conn.execute("INSERT INTO legal_entities(id,name,type,parent_entity_id)"
                     " VALUES('LE-STATUS-CHILD','child','subsidiary','LE-GROUP-HQ')")
        tokens = {}
        for name, role, entity in (("ceo", "ceo", "LE-GROUP-HQ"), ("manager", "manager", "LE-GROUP-HQ"),
                                   ("agent", "agent", "LE-GROUP-HQ"), ("system", "system", "LE-GROUP-HQ"),
                                   ("child", "ceo", "LE-STATUS-CHILD")):
            who = identity.issue(conn, name=name, role=role, legal_entity_id=entity)
            tokens[name] = api._make_cookie(who["id"])
        conn.execute("INSERT INTO tasks(id,legal_entity_id,title,state)"
                     " VALUES('T-status','LE-GROUP-HQ','paused task','in_progress')")
        conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,state,runner,
                        usage_tokens,session_id,pid) VALUES('RUN-status','T-status',1,
                        'LE-GROUP-HQ','awaiting_approval','mock_acp',420,'private-session',987654)""")
        conn.execute("""INSERT INTO tool_approvals(id,task_id,run_id,tool,args,target,session_id)
                        VALUES('AP-status','T-status','RUN-status','file_delete',
                        '{"content":"private-approval"}','private-target','private-session')""")
    with TestClient(app) as client:
        def login(name):
            client.cookies.set("oa_ui", tokens[name])
            return client
        yield login


@pytest.mark.parametrize("actor,can_approve", [("ceo", True), ("manager", True), ("system", True), ("agent", False)])
def test_parked_summary_keeps_task_state_and_limits_approval_metadata(status_console, actor, can_approve):
    client = status_console(actor)
    listing = client.get("/ui/tasks").json()
    detail = client.get("/ui/tasks/T-status").json()
    for task in (listing["tasks"][0], detail["task"]):
        assert task["state"] == "in_progress"
        assert task["awaiting_approval"] is True
        assert set(task["latest_run"]) == {"id", "run_seq", "state", "runner", "usage_tokens", "started_at", "ended_at"}
        assert task["latest_run"]["state"] == "awaiting_approval"
        assert task["pending_approval"] == ({"id": "AP-status", "state": "pending"} if can_approve else None)
    assert listing["counts"] == {"in_progress": 1}
    assert "private-approval" not in str(listing) and "private-approval" not in str(detail)


def test_group_read_access_does_not_disclose_child_approval_metadata(status_console):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET legal_entity_id='LE-STATUS-CHILD' WHERE id='T-status'")
        conn.execute("UPDATE task_runs SET legal_entity_id='LE-STATUS-CHILD' WHERE id='RUN-status'")
    client = status_console("ceo")
    task = client.get("/ui/tasks/T-status").json()["task"]
    assert task["awaiting_approval"] is True and task["pending_approval"] is None
    assert status_console("child").get("/ui/tasks/T-status").json()["task"]["pending_approval"]["id"] == "AP-status"


def test_latest_run_does_not_reuse_an_old_runs_pending_approval(status_console):
    with get_conn() as conn:
        conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,state,runner)
                        VALUES('RUN-new','T-status',2,'LE-GROUP-HQ','running','mock_acp')""")
    client = status_console("ceo")
    for task in (client.get("/ui/tasks").json()["tasks"][0], client.get("/ui/tasks/T-status").json()["task"]):
        assert task["latest_run"]["id"] == "RUN-new"
        assert task["awaiting_approval"] is False and task["pending_approval"] is None


@pytest.mark.parametrize("approve", [True, False])
def test_summary_updates_after_approval_resume_or_rejection(status_console, monkeypatch, approve):
    from app.ui import approval_flow
    resumed = threading.Event()
    def fake_resume(_approval_id):
        with get_conn() as conn:
            conn.execute("UPDATE task_runs SET state='running' WHERE id='RUN-status'")
        resumed.set()
    monkeypatch.setattr(approval_flow, "resume", fake_resume)
    client = status_console("ceo")
    response = client.post("/ui/approvals/AP-status", json={"approve": approve})
    assert response.status_code == 200, response.text
    if approve:
        assert resumed.wait(2), "approved run should resume without executing a real model"
    task = client.get("/ui/tasks/T-status").json()["task"]
    assert task["awaiting_approval"] is False and task["pending_approval"] is None
    assert task["state"] == ("in_progress" if approve else "failed")
    assert task["latest_run"]["state"] == ("running" if approve else "failed")
