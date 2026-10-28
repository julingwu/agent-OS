"""删除/清理接口的验收（右键菜单用）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-delete", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def test_delete_session_hides_from_list(client, device):
    bot = client.post("/v1/bots", headers=_auth(device),
                      json={"name": "临时 Bot"}).json()
    session = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "direct", "bot_ids": [bot["id"]], "title": "临时会话",
    }).json()

    assert client.delete(f"/v1/sessions/{session['id']}",
                         headers=_auth(device)).status_code == 200
    ids = [s["id"] for s in client.get("/v1/sessions", headers=_auth(device)).json()["items"]]
    assert session["id"] not in ids
    # 幂等：再删一次 404
    assert client.delete(f"/v1/sessions/{session['id']}",
                         headers=_auth(device)).status_code == 404


def test_delete_bot_removes_its_direct_session(client, device):
    bot = client.post("/v1/bots", headers=_auth(device),
                      json={"name": "待删 Bot"}).json()
    session = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "direct", "bot_ids": [bot["id"]], "title": "待删会话",
    }).json()

    assert client.delete(f"/v1/bots/{bot['id']}", headers=_auth(device)).status_code == 200
    bots = [b["id"] for b in client.get("/v1/bots", headers=_auth(device)).json()["items"]]
    sessions = [s["id"] for s in client.get("/v1/sessions", headers=_auth(device)).json()["items"]]
    assert bot["id"] not in bots
    assert session["id"] not in sessions


def test_delete_missing_returns_404(client, device):
    assert client.delete("/v1/sessions/ses_nope", headers=_auth(device)).status_code == 404
    assert client.delete("/v1/bots/bot_nope", headers=_auth(device)).status_code == 404


def test_delete_requires_token(client):
    assert client.delete("/v1/sessions/ses_chief").status_code == 401
