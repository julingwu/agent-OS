"""电脑采集层与电脑页 API 的验收（不需要真桌面，全部 mock）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.computer import screen

FAKE_JPEG = b"\xff\xd8\xff\xe0FAKEJPEG\xff\xd9"


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import db, devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-screen", kind="exe")


@pytest.fixture()
def fake_screen(monkeypatch):
    monkeypatch.setattr(screen, "geometry", lambda refresh=False: (1600, 900))
    monkeypatch.setattr(screen, "grab_jpeg",
                        lambda max_w=1280, quality=5: FAKE_JPEG)
    return screen


def _auth(d: dict) -> dict:
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 单元
def test_parse_geometry_ok():
    assert screen.parse_geometry("  dimensions:    1600x900 pixels") == (1600, 900)


def test_parse_geometry_bad():
    assert screen.parse_geometry("no geometry here") is None


# ---------------------------------------------------------------- REST
def test_computer_status(client, device, fake_screen):
    res = client.get("/v1/computer", headers=_auth(device))
    assert res.status_code == 200
    body = res.json()
    assert body["capture_ok"] is True
    assert body["screen"] == {"w": 1600, "h": 900}
    assert body["stream"]["ws"] == "/v1/computer/screen"


def test_frame_jpeg(client, device, fake_screen):
    res = client.get("/v1/computer/frame.jpg", headers=_auth(device))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.content == FAKE_JPEG


def test_frame_503_when_capture_fails(client, device, monkeypatch):
    monkeypatch.setattr(screen, "geometry", lambda refresh=False: (1600, 900))

    def boom(max_w=1280, quality=5):
        raise screen.CaptureUnavailable("no display")

    monkeypatch.setattr(screen, "grab_jpeg", boom)
    res = client.get("/v1/computer/frame.jpg", headers=_auth(device))
    assert res.status_code == 503


# ---------------------------------------------------------------- WSS
def test_screen_ws_pushes_frames(client, device, fake_screen):
    with client.websocket_connect(
        f"/v1/computer/screen?token={device['token']}"
    ) as ws:
        ws.send_json({"t": "subscribe", "fps": 5, "quality": 6, "max_w": 640})
        meta = ws.receive_json()
        assert meta["t"] == "meta"
        assert meta["geometry"] == {"w": 1600, "h": 900}

        frame = ws.receive_bytes()
        assert frame[4:] == FAKE_JPEG          # 前 4 字节是 seq
        ws.send_json({"t": "unsubscribe"})


def test_screen_ws_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/computer/screen?token=bad") as ws:
            ws.receive_json()
