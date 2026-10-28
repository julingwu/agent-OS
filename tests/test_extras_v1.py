"""搜索 / 应用连接 / 设置 / 保险库 / 电脑接管 的验收（v1 extras）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.computer import screen


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import db, devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-extras", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 搜索
def test_search_bots_and_messages(client, device):
    client.post("/v1/sessions/ses_chief/messages", headers=_auth(device),
                json={"text": "搜索测试关键词 alpha", "client_msg_id": "s-1"})
    res = client.get("/v1/search", params={"q": "alpha"}, headers=_auth(device))
    assert res.status_code == 200
    body = res.json()
    assert any("alpha" in m["text"] for m in body["messages"])

    bots = client.get("/v1/search", params={"q": "Chief"}, headers=_auth(device)).json()
    assert any(b["id"] == "bot_chief" for b in bots["bots"])


def test_search_requires_token(client):
    assert client.get("/v1/search", params={"q": "x"}).status_code == 401


# ---------------------------------------------------------------- 应用
def test_apps_seeded_and_toggle(client, device):
    apps = client.get("/v1/apps", headers=_auth(device)).json()["items"]
    ids = {a["id"] for a in apps}
    assert {"wecom", "email", "browser", "mcp"} <= ids
    assert next(a for a in apps if a["id"] == "browser")["connected"] is True

    toggled = client.post("/v1/apps/email/connect", headers=_auth(device),
                          json={"connected": True}).json()
    assert toggled["connected"] is True
    assert client.post("/v1/apps/nope/connect", headers=_auth(device),
                       json={"connected": True}).status_code == 404


# ---------------------------------------------------------------- 设置
def test_settings_defaults_and_update(client, device):
    defaults = client.get("/v1/settings", headers=_auth(device)).json()["values"]
    assert defaults["theme"] == "cyber"
    assert defaults["computer_fps"] == 2

    updated = client.put("/v1/settings", headers=_auth(device),
                         json={"values": {"computer_fps": 3, "view_only": False}}).json()
    assert updated["values"]["computer_fps"] == 3
    assert updated["values"]["view_only"] is False

    bad = client.put("/v1/settings", headers=_auth(device),
                     json={"values": {"hack": 1}})
    assert bad.status_code == 400


# ---------------------------------------------------------------- 保险库
def test_vault_masks_and_reveals(client, device):
    created = client.post("/v1/vault", headers=_auth(device), json={
        "kind": "login", "title": "GitHub",
        "fields": {"username": "alice@example.com", "password": "s3cret!"},
    }).json()
    assert created["masked"]["username"].startswith("al")
    assert "s3cret" not in str(created)

    items = client.get("/v1/vault", headers=_auth(device)).json()["items"]
    assert items and "s3cret" not in str(items)

    revealed = client.post(f"/v1/vault/{created['id']}/reveal",
                           headers=_auth(device)).json()
    assert revealed["fields"]["password"] == "s3cret!"

    assert client.delete(f"/v1/vault/{created['id']}",
                         headers=_auth(device)).json()["deleted"] == created["id"]
    assert client.delete(f"/v1/vault/{created['id']}",
                         headers=_auth(device)).status_code == 404


def test_vault_card_and_contact_masking(client, device):
    card = client.post("/v1/vault", headers=_auth(device), json={
        "kind": "card", "title": "招行卡",
        "fields": {"number": "6225 8800 1234 5678", "holder": "WU", "cvv": "123"},
    }).json()
    assert card["masked"]["last4"] == "5678"
    assert "6225" not in str(card)

    contact = client.post("/v1/vault", headers=_auth(device), json={
        "kind": "contact", "title": "张三",
        "fields": {"phone": "13800138000", "email": "z@x.com"},
    }).json()
    assert contact["masked"]["phone"].startswith("138")
    assert "13800138000" not in str(contact)


def test_vault_rejects_bad_kind(client, device):
    res = client.post("/v1/vault", headers=_auth(device),
                      json={"kind": "nope", "title": "x", "fields": {}})
    assert res.status_code == 422


# ---------------------------------------------------------------- 电脑接管
def test_computer_status_has_state(client, device, monkeypatch):
    monkeypatch.setattr(screen, "probe", lambda: {
        "display": ":0", "geometry": {"w": 1600, "h": 900}, "capture_ok": True,
        "frame_bytes": 10, "error": "", "awake": True,
    })
    body = client.get("/v1/computer", headers=_auth(device)).json()
    assert body["state"] == "awake"


def test_computer_input_dispatch(client, device, monkeypatch):
    calls = []
    monkeypatch.setattr(screen, "input_click", lambda x, y, b: calls.append(("click", x, y, b)))
    monkeypatch.setattr(screen, "input_type", lambda t: calls.append(("type", t)))
    monkeypatch.setattr(screen, "input_key", lambda k: calls.append(("key", k)))

    assert client.post("/v1/computer/input", headers=_auth(device),
                       json={"type": "click", "x": 10, "y": 20}).status_code == 200
    assert client.post("/v1/computer/input", headers=_auth(device),
                       json={"type": "type", "text": "hi"}).status_code == 200
    assert client.post("/v1/computer/input", headers=_auth(device),
                       json={"type": "key", "keys": "Return"}).status_code == 200
    assert calls == [("click", 10, 20, 1), ("type", "hi"), ("key", "Return")]

    assert client.post("/v1/computer/input", headers=_auth(device),
                       json={"type": "click"}).status_code == 400
    assert client.post("/v1/computer/input", headers=_auth(device),
                       json={"type": "nope"}).status_code == 400


def test_computer_wake_sleep(client, device, monkeypatch):
    events = []
    monkeypatch.setattr(screen, "wake", lambda: events.append("wake"))
    monkeypatch.setattr(screen, "sleep", lambda: events.append("sleep"))
    assert client.post("/v1/computer/wake", headers=_auth(device)).json()["state"] == "awake"
    assert client.post("/v1/computer/sleep", headers=_auth(device)).json()["state"] == "sleeping"
    assert events == ["wake", "sleep"]
