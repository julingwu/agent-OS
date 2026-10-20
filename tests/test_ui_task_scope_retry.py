"""Tenant boundaries and asynchronous console retries share the real lifecycle."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app import identity
from app.db import get_conn
from app.engine import dsh_adapter, task_generator
from app.ui import api as ui_api
from tests.test_ui_task_actions import console, _submission


def _as(raw, ident):
    raw.cookies.set("oa_ui", ui_api._make_cookie(ident["id"]))


def _tenant_data():
    with get_conn() as conn:
        conn.execute("UPDATE legal_entities SET governance_mode='group_direct' WHERE id='LE-GROUP-HQ'")
        conn.execute("INSERT INTO legal_entities (id,name,type,parent_entity_id)"
                     " VALUES ('LE-SUB','子公司','subsidiary','LE-GROUP-HQ'),"
                     " ('LE-OTHER','其他公司','subsidiary',NULL)")
        sub = identity.issue(conn, name="sub-manager", role="manager", legal_entity_id="LE-SUB")
        agent = identity.issue(conn, name="hq-agent", role="agent", legal_entity_id="LE-GROUP-HQ")
        system = identity.issue(conn, name="operator", role="system")
        sub_tid = task_generator.generate(conn, "写一篇子公司短文", "LE-SUB")["task_id"]
        other_tid = task_generator.generate(conn, "写一篇其他公司短文", "LE-OTHER")["task_id"]
        for tid in (sub_tid, other_tid):
            for state in ("assigned", "in_progress", "pending_review"):
                conn.execute("UPDATE tasks SET state=? WHERE id=?", (state, tid))
        return sub, agent, system, sub_tid, other_tid


def test_catalog_counts_detail_and_actions_use_identity_tenant(console):
    raw, _, boss = console
    hq_tid, _, _ = _submission(console)
    sub, agent, system, sub_tid, other_tid = _tenant_data()

    # A group CEO may read a subsidiary but cannot write into it.
    catalog = raw.get("/ui/tasks").json()
    assert {t["id"] for t in catalog["tasks"]} == {hq_tid, sub_tid}
    assert catalog["counts"] == {"pending_review": 2}
    assert raw.get(f"/ui/tasks/{sub_tid}").json()["task"]["allowed_actions"] == []
    assert raw.get(f"/ui/tasks/{other_tid}").status_code == 404
    assert raw.post(f"/ui/tasks/{sub_tid}/action", json={"action": "reject"}).status_code == 404
    assert raw.post(f"/ui/tasks/{sub_tid}/retry", json={}).status_code == 404

    _as(raw, sub)
    catalog = raw.get("/ui/tasks", params={"limit": 1}).json()
    assert [t["id"] for t in catalog["tasks"]] == [sub_tid]
    assert catalog["counts"] == {"pending_review": 1}
    assert raw.get(f"/ui/tasks/{hq_tid}").status_code == 404
    assert raw.post(f"/ui/tasks/{hq_tid}/action", json={"action": "reject"}).status_code == 404
    assert raw.post(f"/ui/tasks/{hq_tid}/retry", json={}).status_code == 404

    _as(raw, agent)
    assert [t["id"] for t in raw.get("/ui/tasks").json()["tasks"]] == [hq_tid]
    assert raw.get(f"/ui/tasks/{hq_tid}").json()["allowed_actions"] == []
    assert raw.post(f"/ui/tasks/{hq_tid}/action", json={"action": "accept"}).status_code == 403
    assert raw.post(f"/ui/tasks/{hq_tid}/action", json={"action": "abort"}).status_code == 403
    assert raw.post(f"/ui/tasks/{hq_tid}/retry", json={}).status_code == 403

    _as(raw, system)
    assert len(raw.get("/ui/tasks").json()["tasks"]) == 3
    assert raw.get(f"/ui/tasks/{other_tid}").json()["task"]["allowed_actions"] == ["accept", "reject"]
    _as(raw, boss)


def test_revoked_cookie_is_rejected_by_all_task_endpoints(console):
    raw, _, boss = console
    tid, _, _ = _submission(console)
    with get_conn() as conn:
        identity.revoke(conn, boss["id"])
    assert raw.get("/ui/tasks").status_code == 401
    assert raw.get(f"/ui/tasks/{tid}").status_code == 401
    assert raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept"}).status_code == 401
    assert raw.post(f"/ui/tasks/{tid}/retry", json={}).status_code == 401


def test_catalog_paginates_filtered_tasks_without_changing_scoped_counts(console):
    raw, _, _ = console
    _, agent, _, sub_tid, _ = _tenant_data()
    with get_conn() as conn:
        conn.execute("INSERT INTO ai_employees (id,legal_entity_id,name,model,status)"
                     " VALUES ('AI-CATALOG','LE-GROUP-HQ','小鹿','test-model','online')")
        tids = []
        for state in ("todo", "assigned", "in_progress", "pending_review"):
            tid = task_generator.generate(conn, f"报告 {state}", "LE-GROUP-HQ")["task_id"]
            tids.append(tid)
            conn.execute("UPDATE tasks SET owner_employee_id='AI-CATALOG' WHERE id=?", (tid,))
            if state != "todo":
                conn.execute("UPDATE tasks SET state='assigned' WHERE id=?", (tid,))
            if state in ("in_progress", "pending_review"):
                conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
            if state == "pending_review":
                conn.execute("UPDATE tasks SET state='pending_review' WHERE id=?", (tid,))
    _as(raw, agent)
    page1 = raw.get("/ui/tasks", params={"state": "active", "q": "小鹿", "limit": 2}).json()
    page2 = raw.get("/ui/tasks", params={"state": "active", "q": "小鹿", "limit": 2, "offset": 2}).json()
    assert page1["total"] == page2["total"] == 3
    assert [t["id"] for t in page1["tasks"] + page2["tasks"]] == list(reversed(tids[:3]))
    assert page1["counts"] == {"todo": 1, "assigned": 1, "in_progress": 1, "pending_review": 1}
    assert page2["counts"] == page1["counts"]
    assert raw.get("/ui/tasks", params={"q": sub_tid}).json()["total"] == 0
    exact = raw.get("/ui/tasks", params={"q": tids[1]}).json()
    assert [t["id"] for t in exact["tasks"]] == [tids[1]]
    assert raw.get("/ui/tasks", params={"q": "报告", "state": "pending_review"}).json()["total"] == 1
    assert raw.get("/ui/tasks", params={"state": "unknown"}).status_code == 400


def test_retry_uses_ui_runner_preserves_session_and_resets_criteria(console, monkeypatch):
    raw, _, _ = console
    tid, _, previous = _submission(console, runner="mock_acp")
    assert raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject", "note": "改善语气"}).json()["state"] == "rejected"
    captured = []
    monkeypatch.setenv("UI_RUNNER", "acp")
    monkeypatch.setenv("RUNNER", "mock")
    monkeypatch.setenv("UI_TASK_BUDGET", "80000")
    monkeypatch.setattr(ui_api, "_launch_task_retry", captured.append)
    response = raw.post(f"/ui/tasks/{tid}/retry", json={"note": "请保留原有段落"})
    assert response.status_code == 202 and response.json()["runner"] == "acp", response.text
    retry = captured[0]
    assert retry["runner"] == "acp" and retry["budget_tokens"] == 80000
    assert retry["session_id"] == previous["session_id"]
    assert "改善语气" in retry["prompt"] and "保留原有段落" in retry["prompt"]
    view = raw.get(f"/ui/tasks/{tid}").json()
    assert view["task"]["allowed_actions"] == ["abort"]
    assert [run["run_seq"] for run in view["runs"]] == [2, 1]
    assert view["runs"][0]["session_id"] == previous["session_id"]
    assert all(c["state"] in ("pending", "na") for c in view["criteria"])
    assert any(c["state"] == "na" for c in view["criteria"])


def test_concurrent_retry_clicks_create_one_submission(console, monkeypatch):
    raw, _, _ = console
    tid, _, _ = _submission(console)
    assert raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject"}).status_code == 200
    captured = []
    monkeypatch.setattr(ui_api, "_launch_task_retry", captured.append)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(raw.post, f"/ui/tasks/{tid}/retry", json={}) for _ in range(2)]
        responses = [future.result(timeout=10) for future in futures]
    assert sorted(r.status_code for r in responses) == [202, 409]
    assert len(captured) == 1
    with get_conn() as conn:
        runs = conn.execute("SELECT run_seq FROM task_runs WHERE task_id=? ORDER BY run_seq", (tid,)).fetchall()
        assert [r[0] for r in runs] == [1, 2]


def test_retry_response_does_not_wait_for_model_worker(console, monkeypatch):
    raw, _, _ = console
    tid, _, _ = _submission(console)
    raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject"})
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_worker(retry):
        entered.set()
        release.wait(timeout=10)
        finished.set()

    monkeypatch.setattr(ui_api, "_execute_task_retry", blocked_worker)
    try:
        response = raw.post(f"/ui/tasks/{tid}/retry", json={})
        assert response.status_code == 202
        assert entered.wait(timeout=2)
        assert not finished.is_set(), "response must return while worker remains blocked"
    finally:
        release.set()
        assert finished.wait(timeout=2)


def test_retry_worker_failure_is_visible_and_retryable(console, monkeypatch):
    raw, _, _ = console
    tid, _, _ = _submission(console)
    raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject"})

    def fail(*args, **kwargs):
        raise RuntimeError("simulated runner startup failure")

    monkeypatch.setattr(dsh_adapter, "execute_run", fail)
    response = raw.post(f"/ui/tasks/{tid}/retry", json={})
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        view = raw.get(f"/ui/tasks/{tid}").json()
        if view["task"]["state"] == "failed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("failed retry worker left task stuck in progress")
    assert view["runs"][0]["state"] == "failed"
    assert view["task"]["allowed_actions"] == ["retry"]
