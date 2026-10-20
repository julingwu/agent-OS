"""A stopped or finished run cannot launch a queued or duplicate model call."""
from __future__ import annotations

import time

import pytest

from app.db import get_conn
from app.engine import dsh_adapter, runner_acp
from tests.test_ui_task_actions import console


def _queued(console):
    _, signed, _ = console
    tid = signed.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()["task_id"]
    rid = signed.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()["run_id"]
    return tid, rid


@pytest.mark.parametrize("runner", ["mock", "mock_acp"])
@pytest.mark.parametrize("stop_state", ["failed", "cancelled"])
def test_killed_queued_run_never_reserves_or_spawns_but_new_retry_is_allowed(console, monkeypatch, runner, stop_state):
    raw, signed, _ = console
    tid, rid = _queued(console)
    if stop_state == "cancelled":
        with get_conn() as conn:
            conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
        stopped = raw.post(f"/ui/tasks/{tid}/action", json={"action": "abort"})
    else:
        stopped = raw.post(f"/ui/runs/{rid}/kill", json={})
    assert stopped.status_code == 200
    with monkeypatch.context() as blocked:
        blocked.setattr(dsh_adapter, "budget_gate", lambda *a: pytest.fail("stopped run must not reserve"))
        blocked.setattr(dsh_adapter.subprocess, "Popen", lambda *a, **kw: pytest.fail("stopped run must not spawn"))
        result = signed.post(f"/runs/{rid}/execute", json={"runner": runner})
        assert result.status_code == 409 and result.json()["detail"]["code"] == "RunNotRunnable"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_locks WHERE run_id=?", (rid,)).fetchone()[0] == 0
        assert conn.execute("SELECT usage_tokens FROM task_runs WHERE id=?", (rid,)).fetchone()[0] == 0
    if stop_state == "cancelled":
        assert raw.post(f"/ui/tasks/{tid}/retry", json={}).status_code == 409
        return
    monkeypatch.setenv("UI_RUNNER", runner)
    retried = raw.post(f"/ui/tasks/{tid}/retry", json={})
    assert retried.status_code == 202 and retried.json()["run_id"] != rid
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        view = raw.get(f"/ui/tasks/{tid}").json()
        if view["task"]["state"] == "pending_review":
            break
        time.sleep(0.05)
    else:
        pytest.fail("new run failed to complete a legitimate retry")
    assert view["runs"][0]["run_seq"] == 2 and view["runs"][0]["usage_tokens"] > 0
    assert view["runs"][1]["state"] == "failed" and view["runs"][1]["usage_tokens"] == 0


@pytest.mark.parametrize("runner", ["mock", "mock_acp"])
def test_stop_during_budget_reservation_is_checked_before_any_process_launch(console, monkeypatch, runner):
    raw, signed, _ = console
    tid, rid = _queued(console)
    original_gate = dsh_adapter.budget_gate

    def stop_after_reservation(conn, run_id, estimate):
        result = original_gate(conn, run_id, estimate)
        assert raw.post(f"/ui/runs/{rid}/kill", json={}).status_code == 200
        return result

    monkeypatch.setattr(dsh_adapter, "budget_gate", stop_after_reservation)
    monkeypatch.setattr(dsh_adapter.subprocess, "Popen", lambda *a, **kw: pytest.fail("stopped before spawn"))
    result = signed.post(f"/runs/{rid}/execute", json={"runner": runner})
    assert result.status_code == 200 and result.json()["spawned"] is False
    assert result.json()["exit_code"] == -9 and result.json()["usage_tokens"] == 0
    with get_conn() as conn:
        assert tuple(conn.execute("SELECT used_tokens,reserved_tokens FROM budgets WHERE task_id=?", (tid,)).fetchone()) == (0, 0)
        assert conn.execute("SELECT COUNT(*) FROM run_locks WHERE run_id=?", (rid,)).fetchone()[0] == 0


@pytest.mark.parametrize("run_state,task_state,exit_code", [
    ("succeeded", "pending_review", 0), ("failed", "failed", 1),
    ("timeout", "failed", -1), ("cancelled", "cancelled", -9),
])
def test_terminal_run_cannot_be_executed_again(console, monkeypatch, run_state, task_state, exit_code):
    _, signed, _ = console
    tid, rid = _queued(console)
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
        conn.execute("UPDATE tasks SET state=? WHERE id=?", (task_state, tid))
        conn.execute("UPDATE task_runs SET state=?,exit_code=? WHERE id=?", (run_state, exit_code, rid))
    monkeypatch.setattr(dsh_adapter, "budget_gate", lambda *a: pytest.fail("terminal run must not reserve"))
    result = signed.post(f"/runs/{rid}/execute", json={"runner": "mock"})
    assert result.status_code == 409 and result.json()["detail"]["code"] == "RunNotRunnable"


class _Process:
    pid = 34567890

    def __init__(self):
        self.stop_called = False
        self.communicated = False

    def poll(self):
        return -9 if self.stop_called else None

    def wait(self, timeout):
        assert self.stop_called
        return -9

    def communicate(self, *args, **kwargs):
        self.communicated = True
        pytest.fail("stopped headless process must not receive the model prompt")


@pytest.mark.parametrize("phase", ["headless_popen", "acp_pre_register", "acp_post_start", "acp_post_session"])
@pytest.mark.parametrize("stop_state", ["failed", "cancelled"])
def test_kill_during_spawn_registration_terminates_owned_process_without_prompt(console, monkeypatch, phase, stop_state):
    raw, signed, _ = console
    tid, rid = _queued(console)
    process = _Process()
    seen = {"initialize": False, "prompt": False}
    monkeypatch.setattr(dsh_adapter, "mount_shared", lambda ws: None)
    monkeypatch.setattr(dsh_adapter, "kill_tree", lambda pid: setattr(process, "stop_called", pid == process.pid))

    def kill():
        path = f"/ui/runs/{rid}/kill" if stop_state == "failed" else f"/ui/tasks/{tid}/action"
        body = {} if stop_state == "failed" else {"action": "abort"}
        stopped = raw.post(path, json=body)
        assert stopped.status_code == 200, stopped.text
        assert raw.post(path, json=body).status_code == 409

    if phase == "headless_popen":
        def popen(*args, **kwargs):
            kill()  # worker has spawned, but has not registered its process yet
            return process
        monkeypatch.setattr(dsh_adapter.subprocess, "Popen", popen)
        runner = "mock"
    else:
        class FakeAcp:
            pid = process.pid

            def __init__(self, *args, **kwargs):
                self.log, self.stderr = [], []

            def start(self):
                if phase == "acp_pre_register":
                    kill()
                self.on_started(process)
                seen["initialize"] = True
                if phase == "acp_post_start":
                    kill()

            def new_session(self):
                if phase == "acp_post_session":
                    kill()
                return {"sessionId": "session-race"}

            def prompt(self, *args, **kwargs):
                seen["prompt"] = True
                pytest.fail("stopped ACP client must not receive model prompt")

            def close(self, **kwargs):
                pass
        monkeypatch.setattr(runner_acp, "AcpClient", FakeAcp)
        runner = "mock_acp"
    result = signed.post(f"/runs/{rid}/execute", json={"runner": runner})
    assert result.status_code == 200, result.text
    assert result.json()["exit_code"] == -9 and result.json()["usage_tokens"] == 0
    assert result.json()["spawned"] is True
    assert process.stop_called and not process.communicated and not seen["prompt"]
    if phase == "acp_pre_register":
        assert not seen["initialize"], "registration guard must run before initialize"
    with get_conn() as conn:
        run = conn.execute("SELECT state,exit_code,usage_tokens FROM task_runs WHERE id=?", (rid,)).fetchone()
        budget = conn.execute("SELECT used_tokens,reserved_tokens FROM budgets WHERE task_id=?", (tid,)).fetchone()
        assert tuple(run) == (stop_state, -9, 0)
        assert tuple(budget) == (0, 0)
        assert conn.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (rid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM run_locks WHERE run_id=?", (rid,)).fetchone()[0] == 0


def test_stop_during_parked_run_initialize_preserves_previous_usage(console, monkeypatch):
    raw, _, _ = console
    tid, rid = _queued(console)
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET state='in_progress' WHERE id=?", (tid,))
        conn.execute("UPDATE task_runs SET state='awaiting_approval',session_id='parked-session',"
                     "usage_tokens=321 WHERE id=?", (rid,))
        conn.execute("UPDATE budgets SET used_tokens=321 WHERE task_id=?", (tid,))
    process = _Process()
    monkeypatch.setattr(dsh_adapter, "mount_shared", lambda ws: None)
    monkeypatch.setattr(dsh_adapter, "kill_tree", lambda pid: setattr(process, "stop_called", True))

    class StoppedDuringInitialize:
        pid = process.pid

        def __init__(self, *args, **kwargs):
            self.log, self.stderr = [], []

        def start(self):
            self.on_started(process)
            assert raw.post(f"/ui/runs/{rid}/kill", json={}).status_code == 200
            raise runner_acp.AcpError("agent exited before answering initialize")

        def prompt(self, *args, **kwargs):
            pytest.fail("stopped parked run must not receive a new model prompt")

        def close(self, **kwargs):
            pass

    monkeypatch.setattr(runner_acp, "AcpClient", StoppedDuringInitialize)
    with get_conn() as conn:
        out = dsh_adapter.execute_run(conn, rid, runner="mock_acp", resume_session="parked-session")
        run = conn.execute("SELECT state,exit_code,usage_tokens FROM task_runs WHERE id=?", (rid,)).fetchone()
        budget = conn.execute("SELECT used_tokens,reserved_tokens FROM budgets WHERE task_id=?", (tid,)).fetchone()
        assert tuple(run) == ("failed", -9, 321)
        assert tuple(budget) == (321, 0)
    assert out["usage_tokens"] == 0 and out["usage_total"] == 321
    assert process.stop_called
