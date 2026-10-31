"""Release safety regressions. No production DB, models, or backend processes."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import supervisor as gateway
from app import db, events, runtime_control
from app.agent import loop


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def release(monkeypatch, tmp_path):
    state = gateway.SupervisorState()
    state.green_proc = Mock(pid=11)
    state.red_proc = Mock(pid=22)
    state.green_proc.poll.return_value = state.red_proc.poll.return_value = None
    state.prepared = {"track": "red", "pid": 22, "revision": "rev1"}
    monkeypatch.setattr(gateway, "state", state)
    monkeypatch.setattr(gateway, "STATE_PATH", tmp_path / "release.json")
    monkeypatch.setattr(gateway, "fingerprint", lambda: "rev1")
    monkeypatch.setattr(gateway, "_terminate_proc", Mock())
    monkeypatch.setattr(gateway, "spawn", Mock())
    monkeypatch.setattr(gateway, "_wait_until_healthy", AsyncMock(return_value=True))
    info = {"ok": True, "pid": 22, "role": "standby", "busy": False,
            "sessions": 0, "processes": 0, "pending_calls": 0, "workers": []}
    monkeypatch.setattr(gateway, "runtime", AsyncMock(return_value=info))
    return state, info


@pytest.mark.parametrize("failure", ["unhealthy", "changed", "unprepared", "wrong-role"])
def test_switch_rejects_invalid_candidate(release, monkeypatch, failure):
    state, info = release
    if failure == "unhealthy":
        gateway.runtime.side_effect = RuntimeError("not ready")
    elif failure == "changed":
        monkeypatch.setattr(gateway, "fingerprint", lambda: "rev2")
    elif failure == "wrong-role":
        info["role"] = "active"
    else:
        state.prepared = None
    response = run(gateway.supervisor_switch())
    assert response.status_code == 409
    assert state.active_track == "green"
    gateway.spawn.assert_not_called()


def test_busy_work_waits_then_promotes_and_persists(release):
    state, info = release
    response = run(gateway.supervisor_switch())
    assert response.status_code == 202
    assert state.active_track == "green"
    info.update(busy=True, sessions=1)
    run(gateway.release_step())
    assert state.release["phase"] == "draining"
    gateway._terminate_proc.assert_not_called()
    info.update(busy=False, sessions=0)
    state.inflight = 1
    run(gateway.release_step())
    gateway._terminate_proc.assert_not_called()
    state.inflight = 0
    socket = Mock(close=AsyncMock())
    state.sockets.add(socket)
    run(gateway.release_step())
    assert state.active_track == "red"
    assert state.release["phase"] == "completed"
    gateway.spawn.assert_any_call("red", "active")
    gateway.spawn.assert_any_call("green", "standby")
    socket.close.assert_awaited_once_with(code=1012)
    saved = json.loads(gateway.STATE_PATH.read_text(encoding="utf-8"))
    assert saved["active"] == "red"
    assert saved["release"]["phase"] == "completed"


def test_duplicate_switch_does_not_toggle_twice(release):
    state, _ = release
    first = json.loads(run(gateway.supervisor_switch()).body)
    second = json.loads(run(gateway.supervisor_switch()).body)
    assert first["release"]["id"] == second["release"]["id"]
    assert state.active_track == "green"


def test_failed_promotion_restores_source_and_reconnects(release):
    state, _ = release
    run(gateway.supervisor_switch())
    gateway._wait_until_healthy.side_effect = [False, True]
    run(gateway.release_step())
    assert state.active_track == "green"
    assert state.release["phase"] == "rolled_back"
    assert not state.maintenance
    gateway.spawn.assert_any_call("green", "active")


def test_failed_source_recovery_is_explicit(release):
    state, _ = release
    run(gateway.supervisor_switch())
    gateway._wait_until_healthy.return_value = False
    run(gateway.release_step())
    assert state.release["phase"] == "failed"
    assert state.active_track == "green"


def test_state_probe_failure_never_kills_busy_source(release):
    state, _ = release
    run(gateway.supervisor_switch())
    gateway.runtime.side_effect = RuntimeError("probe unavailable")
    with pytest.raises(RuntimeError):
        run(gateway.release_step())
    gateway._terminate_proc.assert_not_called()
    assert state.active_track == "green"


def test_standby_lifespan_does_not_recover_or_schedule(monkeypatch):
    import app.main as main
    from app import processes, routines, pipeline
    monkeypatch.setenv("AGENTOS_TRACK_ROLE", "standby")
    for module, name in [(main, "init_db"), (processes, "recover_interrupted"),
                         (pipeline, "recover_interrupted"), (routines, "start_scheduler")]:
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError("standby mutated execution")))
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/sessions").status_code == 503
        assert client.get("/v1/bots").status_code == 503
        assert client.get("/internal/runtime").status_code == 403
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/device?token=local"):
                pass


def test_drain_rejects_new_work_but_allows_stop(monkeypatch):
    import app.main as main
    monkeypatch.setattr(runtime_control, "draining", True)
    # No lifespan: exercise admission middleware without starting any scheduler.
    client = TestClient(main.app)
    assert client.post("/v1/sessions").status_code == 503
    assert client.post("/v1/sessions/missing/abort").status_code != 503
    assert client.get("/health").status_code == 200


def test_interrupted_turn_is_closed_once_without_reexecution():
    with db.get_conn() as conn:
        events.emit(conn, bot_id="bot", session_id="session", turn_id="turn-1",
                    kind="turn.started", payload={"input_event_id": 0})
        conn.commit()
        assert runtime_control.recover_turns(conn) == 1
        assert runtime_control.recover_turns(conn) == 0
        row = conn.execute("SELECT payload_json FROM events WHERE kind='turn.ended'").fetchone()
        assert json.loads(row[0])["status"] == "interrupted"


def test_steering_does_not_queue_an_extra_turn(monkeypatch):
    captured = []
    monkeypatch.setattr(db, "spawn_bg_thread", lambda target, **kw: captured.append(target))
    sid = "steering-regression"
    try:
        assert loop.run_turn_async("bot", sid) is True
        assert loop.is_running(sid)
        assert loop.run_turn_async("bot", sid) is False
        assert len(captured) == 1
    finally:
        loop._QUEUED_SESSIONS.discard(sid)


def test_gateway_hides_private_control_and_pauses_admission(release):
    state, _ = release
    client = TestClient(gateway.app)
    assert client.get("/internal/runtime").status_code == 404
    state.maintenance = True
    assert client.post("/v1/sessions").status_code == 503
    assert state.inflight == 0


def test_late_message_is_not_lost_when_current_turn_finishes(monkeypatch):
    calls = []
    work = []
    sid = "late-message-regression"
    monkeypatch.setattr(db, "spawn_bg_thread", lambda target, **kw: work.append(target))

    def fake_turn(*args, **kwargs):
        calls.append(1)
        with db.get_conn() as conn:
            if len(calls) == 1:
                events.emit(conn, bot_id="bot", session_id=sid,
                            kind="message.user", payload={"text": "late follow-up"})
                return {"status": "done", "consumed_user_event_id": 0}
            latest = conn.execute("SELECT MAX(id) FROM events").fetchone()[0]
            return {"status": "done", "consumed_user_event_id": latest}

    monkeypatch.setattr(loop, "run_turn", fake_turn)
    assert loop.run_turn_async("bot", sid)
    work[0]()
    assert len(calls) == 2
    assert not loop.is_running(sid)


def test_recovery_spawn_error_is_terminal(release):
    state, _ = release
    run(gateway.supervisor_switch())
    gateway.spawn.side_effect = OSError("cannot start backend")
    run(gateway.release_step())
    assert state.release["phase"] == "failed"
    assert not state.maintenance
    assert not gateway.pending()


def test_status_and_promotion_are_serialized(release, monkeypatch):
    state, info = release
    run(gateway.supervisor_switch())

    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def probe(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                await proceed.wait()
            return info

        monkeypatch.setattr(gateway, "runtime", probe)
        snapshot = asyncio.create_task(gateway.supervisor_status())
        await entered.wait()
        promotion = asyncio.create_task(gateway.release_step())
        await asyncio.sleep(0)
        assert state.active_track == "green"
        assert state.release["phase"] == "queued"
        proceed.set()
        result = await snapshot
        # Copy the phase before allowing the next coroutine to mutate it.
        old_active, old_phase = result["active"], result["release"]["phase"]
        await promotion
        assert old_active == "green"
        assert old_phase == "queued"
        assert state.active_track == "red"

    run(scenario())
