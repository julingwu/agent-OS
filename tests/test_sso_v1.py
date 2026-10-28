"""工作台免登录（SSO）验收：设备令牌 → 一次性票 → /app/ 的签名 token。"""

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
        return devices.issue(conn, name="pytest-sso", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def test_ticket_requires_device_auth(client):
    assert client.post("/v1/legacy-sso/ticket").status_code == 401


def test_ticket_exchange_grants_workbench_session(client, device):
    ticket = client.post("/v1/legacy-sso/ticket", headers=_auth(device)).json()["ticket"]
    ex = client.get("/v1/legacy-sso/exchange", params={"ticket": ticket})
    assert ex.status_code == 200
    body = ex.json()
    assert body["token"] and body["identity_id"]

    # 用换来的 token 调工作台接口：免登录
    me = client.get("/ui/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "ceo"


def test_ticket_is_single_use(client, device):
    ticket = client.post("/v1/legacy-sso/ticket", headers=_auth(device)).json()["ticket"]
    assert client.get("/v1/legacy-sso/exchange", params={"ticket": ticket}).status_code == 200
    assert client.get("/v1/legacy-sso/exchange", params={"ticket": ticket}).status_code == 409


def test_ticket_expires(client, device):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO legacy_sso_tickets (ticket, identity_id, token, expires_at)"
            " VALUES ('expired-ticket-123','ID-X','t','2020-01-01T00:00:00+08:00')")
    res = client.get("/v1/legacy-sso/exchange", params={"ticket": "expired-ticket-123"})
    assert res.status_code == 410


def test_direct_token_also_works(client, device):
    body = client.post("/v1/legacy-sso", headers=_auth(device)).json()
    me = client.get("/ui/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200


def test_sso_identity_is_reused(client, device):
    a = client.post("/v1/legacy-sso", headers=_auth(device)).json()
    b = client.post("/v1/legacy-sso", headers=_auth(device)).json()
    assert a["identity_id"] == b["identity_id"]
