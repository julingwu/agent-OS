"""Console review and stop actions must change the real task lifecycle."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import identity, rbac
from app.db import get_conn
from app.engine import dsh_adapter, task_generator
from app.ui import api as ui_api, approval_flow
from tests.authclient import SignedClient


@pytest.fixture()
def console(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "actions.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
        "UI_CRON": "0", "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0",
        "OCTO_RUNNER": "0", "RUNNER": "mock", "UI_RUNNER": "mock",
        "M0_SANDBOX": "none", "M0_SANDBOX_ACP": "0", "M0_HEARTBEAT_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    from app.main import app
    with TestClient(app) as raw:
        with get_conn() as conn:
            who = identity.issue(conn, name="reviewer", role="ceo", legal_entity_id="LE-GROUP-HQ")
        raw.cookies.set("oa_ui", ui_api._make_cookie(who["id"]))
        yield raw, SignedClient(raw, who["id"], who["secret"]), who


def _submission(console, runner="mock"):
    raw, signed, _ = console
    task = signed.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()
    tid = task["task_id"]
    run = signed.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    result = signed.post(f"/runs/{run['run_id']}/execute", json={"runner": runner})
    assert result.status_code == 200, result.text
    assert result.json()["task_state"] == "pending_review"
    return tid, run["run_id"], result.json()


def test_reject_retry_then_accept_updates_criteria_and_delivers_files(console):
    raw, signed, _ = console
    tid, _, _ = _submission(console)
    rejected = raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject", "note": "语气过于夸张，请改为自然表达"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    with get_conn() as conn:
        failed = conn.execute("SELECT state,note FROM acceptance_criteria WHERE task_id=?"
                              " AND check_type='human_check' AND is_blocking=1", (tid,)).fetchone()
        assert failed["state"] == "failed" and "自然表达" in failed["note"]
    retried = raw.post(f"/ui/tasks/{tid}/retry", json={"note": "注意自然表达"})
    assert retried.status_code == 202 and retried.json()["state"] == "in_progress", retried.text
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if raw.get(f"/ui/tasks/{tid}").json()["task"]["state"] == "pending_review":
            break
        time.sleep(0.05)
    else:
        pytest.fail("retry did not finish")
    accepted = raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept"})
    assert accepted.status_code == 200 and accepted.json()["state"] == "completed", accepted.text
    files = raw.get("/ui/files").json()["files"]
    assert files
    artifact_id = next(f["id"] for f in files if f["task_id"] == tid)
    assert raw.get(f"/ui/files/{artifact_id}/preview").status_code == 200
    assert raw.get(f"/ui/files/{artifact_id}/download").status_code == 200
    with get_conn() as conn:
        actions = {r[0] for r in conn.execute("SELECT action FROM ai_audit_logs WHERE task_id=?", (tid,))}
        assert {"task_reject", "task_accept", "retry"} <= actions


def test_accept_without_readable_evidence_returns_actionable_error(console):
    raw, _, _ = console
    with get_conn() as conn:
        tid = task_generator.generate(conn, "写一篇短文", "LE-GROUP-HQ")["task_id"]
        # Direct insertion is used only to arrange a submission with no output.
        conn.execute("DELETE FROM acceptance_criteria WHERE task_id=? AND check_type!='human_check'", (tid,))
        conn.execute("UPDATE tasks SET state='assigned' WHERE id=?", (tid,))
        conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
        conn.execute("UPDATE tasks SET state='pending_review' WHERE id=?", (tid,))
    result = raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept"})
    assert result.status_code == 400 and result.json()["detail"]["code"] == "EvidenceRequired"
    with get_conn() as conn:
        assert conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "pending_review"
        assert conn.execute("SELECT state FROM acceptance_criteria WHERE task_id=? AND is_blocking=1", (tid,)).fetchone()[0] == "pending"
    reviewed = raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept", "note": "已核对完整内容，表达准确"})
    assert reviewed.status_code == 200 and reviewed.json()["state"] == "completed"


def test_task_list_counts_and_detail_exclude_deleted_artifacts(console):
    raw, _, _ = console
    tid, _, output = _submission(console)
    artifact_id = output["artifacts"][0]["artifact_id"]
    before = raw.get(f"/ui/tasks/{tid}").json()
    assert artifact_id in {a["id"] for a in before["artifacts"]}
    assert raw.delete(f"/ui/files/{artifact_id}").status_code == 200
    assert raw.get(f"/ui/tasks/{tid}").json()["artifacts"] == []
    listing = raw.get("/ui/tasks", params={"limit": 1}).json()
    assert [t["id"] for t in listing["tasks"]] == [tid]
    assert listing["counts"]["pending_review"] == 1
    assert {"owner_name", "chat_id"} <= listing["tasks"][0].keys()


class _OwnedProcess:
    pid = 12345678
    stopped = False

    def poll(self):
        return -9 if self.stopped else None

    def wait(self, timeout):
        assert self.stopped
        return -9


def _running(console, monkeypatch):
    _, signed, _ = console
    tid = signed.post("/tasks/from_sentence", json={"text": "写一篇短文"}).json()["task_id"]
    rid = signed.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()["run_id"]
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
        conn.execute("UPDATE task_runs SET state='running',pid=? WHERE id=?", (_OwnedProcess.pid, rid))
    process = _OwnedProcess()
    monkeypatch.setattr(dsh_adapter, "kill_tree", lambda pid: setattr(process, "stopped", pid == process.pid))
    dsh_adapter.register_process(rid, process)
    return tid, rid, process


def _finish_after_stop(tid, rid):
    with get_conn() as conn:
        return dsh_adapter.finish(conn, run_id=rid, task_id=tid, entity="LE-GROUP-HQ", runner="mock",
                                  ok=True, reason="completed", exit_code=0, session_id=None,
                                  used=10, event_count=1, artifacts=[], final="late success")


def test_abort_stops_owned_worker_and_late_success_cannot_overwrite_it(console, monkeypatch):
    raw, _, _ = console
    tid, rid, process = _running(console, monkeypatch)
    try:
        with get_conn() as conn:
            rbac.ensure(conn)
            conn.execute("INSERT INTO tool_approvals (id,tool,state,run_id,task_id,session_id)"
                         " VALUES ('AP-pending','write','pending',?,?, 'session-1')", (rid, tid))
            conn.execute("INSERT INTO tool_approvals (id,tool,state,run_id,task_id,session_id)"
                         " VALUES ('AP-approved','write','approved',?,?, 'session-1')", (rid, tid))
        response = raw.post(f"/ui/tasks/{tid}/action", json={"action": "abort"})
        assert response.status_code == 200 and response.json()["state"] == "cancelled", response.text
        assert process.stopped
        with get_conn() as conn:
            assert conn.execute("SELECT state FROM tool_approvals WHERE id='AP-pending'").fetchone()[0] == "rejected"
        monkeypatch.setattr(rbac, "apply_approval", lambda *args: pytest.fail("cancelled task must not write approved content"))
        assert approval_flow.resume("AP-approved")["ok"] is False
        late = _finish_after_stop(tid, rid)
        assert late["task_state"] == "cancelled" and late["exit_code"] == -9
        with get_conn() as conn:
            assert conn.execute("SELECT state FROM task_runs WHERE id=?", (rid,)).fetchone()[0] == "cancelled"
    finally:
        dsh_adapter.unregister_process(rid)


def test_kill_switch_marks_task_failed_and_preserves_stop_against_late_result(console, monkeypatch):
    raw, _, _ = console
    tid, rid, process = _running(console, monkeypatch)
    try:
        response = raw.post(f"/ui/runs/{rid}/kill", json={})
        assert response.status_code == 200 and response.json()["killed"] is True, response.text
        assert process.stopped
        late = _finish_after_stop(tid, rid)
        assert late["task_state"] == "failed" and late["exit_code"] == -9
        assert raw.get(f"/ui/tasks/{tid}").json()["task"]["state"] == "failed"
    finally:
        dsh_adapter.unregister_process(rid)


def test_stale_pid_cannot_kill_an_unrelated_process(console, monkeypatch):
    raw, _, _ = console
    tid, rid, process = _running(console, monkeypatch)
    dsh_adapter.unregister_process(rid)
    response = raw.post(f"/ui/runs/{rid}/kill", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RunnerStopFailed"
    assert not process.stopped
    assert raw.get(f"/ui/tasks/{tid}").json()["task"]["state"] == "in_progress"


@pytest.mark.parametrize("runner", ["mock", "mock_acp"])
def test_abort_terminates_a_real_owned_subprocess(console, tmp_path, monkeypatch, runner):
    """Exercise the actual process handle and both runner completion paths."""
    raw, signed, _ = console
    sleeper = tmp_path / "slow_agent.py"
    if runner == "mock":
        sleeper.write_text("import sys,time\nsys.stdin.read()\ntime.sleep(30)\n", encoding="utf-8")
        monkeypatch.setattr(dsh_adapter, "MOCK_AGENT", sleeper)
    else:
        from app.engine import runner_acp
        sleeper.write_text(
            "import json,sys,time\n"
            "for line in sys.stdin:\n"
            " m=json.loads(line); method=m.get('method'); result={}\n"
            " if method=='session/new': result={'sessionId':'sess-stop-test'}\n"
            " if method=='session/prompt': time.sleep(30)\n"
            " if 'id' in m:\n"
            "  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)\n",
            encoding="utf-8")
        monkeypatch.setattr(runner_acp, "MOCK_ACP_AGENT", sleeper)
    tid = signed.post("/tasks/from_sentence", json={"text": "写一篇短文"}).json()["task_id"]
    rid = signed.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()["run_id"]
    response = {}

    def execute():
        response["result"] = signed.post(f"/runs/{rid}/execute", json={"runner": runner})

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            runs = raw.get(f"/ui/tasks/{tid}").json()["runs"]
            if runs and runs[0]["state"] == "running" and runs[0]["pid"]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("test runner never reached its running state")
        aborted = raw.post(f"/ui/tasks/{tid}/action", json={"action": "abort"})
        assert aborted.status_code == 200 and aborted.json()["state"] == "cancelled", aborted.text
        worker.join(timeout=10)
        assert not worker.is_alive(), "cancel must terminate the runner before its 30s sleep completes"
        assert response["result"].status_code == 200, response["result"].text
        assert response["result"].json()["task_state"] == "cancelled"
        assert raw.get(f"/ui/tasks/{tid}").json()["runs"][0]["state"] == "cancelled"
    finally:
        if worker.is_alive():
            dsh_adapter.stop_process(rid)
            worker.join(timeout=10)


@pytest.mark.parametrize("action", ["accept", "reject", "abort"])
def test_terminal_tasks_reject_actions_without_mutation(console, monkeypatch, action):
    raw, _, _ = console
    tid, rid, _ = _submission(console)
    assert raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept"}).json()["state"] == "completed"
    monkeypatch.setattr(dsh_adapter, "stop_process", lambda *args, **kwargs: pytest.fail("terminal run must not terminate a PID"))
    result = raw.post(f"/ui/tasks/{tid}/action", json={"action": action})
    assert result.status_code == 409
    assert raw.post(f"/ui/runs/{rid}/kill", json={}).status_code == 409
    assert raw.get(f"/ui/tasks/{tid}").json()["task"]["state"] == "completed"
