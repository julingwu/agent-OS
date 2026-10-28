"""契约 01/03 的流式验收：REST 补拉 + WSS 先 backlog 再实时 + 断线续传。

这些用例直接对应 build-spec 的验收清单第 3/11 条（关闭客户端后事件补全、断线重连）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import db, devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-exe", kind="exe")


def _auth(device: dict) -> dict:
    return {"Authorization": f"Bearer {device['token']}"}


def _emit(text: str, *, session_id: str = "ses_1", bot_id: str = "bot_1"):
    from app import db
    from app import events

    with db.get_conn() as conn:
        event = events.emit(conn, bot_id=bot_id, session_id=session_id,
                            kind="message.bot", payload={"text": text})
    events.broadcaster.publish(event)
    return event


def _recv_events(ws, *, skip_hello: bool = False, max_frames: int = 20):
    """收帧直到拿到 events 帧；返回该帧的 items。"""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if frame["t"] == "events":
            return frame
    raise AssertionError("no events frame received")


# ---------------------------------------------------------------- REST
def test_health_is_public(client):
    res = client.get("/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and "cursor" in body


def test_events_requires_device_token(client):
    assert client.get("/v1/events").status_code == 401
    assert client.get("/v1/events", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_events_cursor_and_filters(client, device):
    a = _emit("一")
    b = _emit("二")
    _emit("别的会话", session_id="ses_2")

    res = client.get("/v1/events", params={"since": a.id}, headers=_auth(device))
    assert res.status_code == 200
    items = res.json()["items"]
    assert [it["payload"]["text"] for it in items] == ["二", "别的会话"]
    assert items[0]["id"] == b.id

    res = client.get("/v1/events", params={"session_id": "ses_1"},
                     headers=_auth(device))
    assert [it["payload"]["text"] for it in res.json()["items"]] == ["一", "二"]


def test_snapshot_returns_ascending_and_cursor(client, device):
    ids = [_emit(str(i)).id for i in range(5)]

    res = client.get("/v1/sessions/ses_1/snapshot", params={"limit": 3},
                     headers=_auth(device))
    assert res.status_code == 200
    body = res.json()
    assert [e["id"] for e in body["events"]] == ids[2:]
    assert body["cursor"] == ids[-1]
    assert body["has_more"] is True


# ---------------------------------------------------------------- WSS
def test_stream_replays_backlog_then_hello(client, device):
    events = [_emit("历史1"), _emit("历史2")]

    with client.websocket_connect(
        f"/v1/stream?since=0&token={device['token']}"
    ) as ws:
        hello = ws.receive_json()
        assert hello["t"] == "hello"
        assert hello["device_id"] == device["device_id"]

        frame = _recv_events(ws)
        assert frame["replay"] is True
        assert [it["payload"]["text"] for it in frame["items"]] == ["历史1", "历史2"]
        assert frame["cursor"] == events[-1].id


def test_stream_reconnect_only_sends_missed_events(client, device):
    first = _emit("旧")
    _emit("中")
    last = _emit("新")

    with client.websocket_connect(
        f"/v1/stream?since={first.id}&token={device['token']}"
    ) as ws:
        assert ws.receive_json()["t"] == "hello"
        frame = _recv_events(ws)
        assert frame["replay"] is True
        assert [it["payload"]["text"] for it in frame["items"]] == ["中", "新"]
        assert frame["cursor"] == last.id


def test_stream_pushes_realtime_events(client, device):
    with client.websocket_connect(
        f"/v1/stream?since=0&token={device['token']}"
    ) as ws:
        assert ws.receive_json()["t"] == "hello"

        live = _emit("实时推送", session_id="ses_live")
        frame = _recv_events(ws)
        assert frame["replay"] is False
        assert frame["items"][0]["id"] == live.id
        assert frame["items"][0]["payload"]["text"] == "实时推送"


def test_stream_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/stream?since=0&token=bad") as ws:
            ws.receive_json()
