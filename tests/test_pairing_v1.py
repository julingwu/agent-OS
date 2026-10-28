"""配对码 + 模型列表/切换 的验收。

对应两个真实问题：
  * 别人下载 exe 后怎么绑定这台服务器 —— 配对码换设备令牌；
  * 每个 Bot / 全局默认能不能自由换模型。
"""

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
        return devices.issue(conn, name="pytest-pairing", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 配对码
def test_server_info_is_public(client):
    body = client.get("/v1/server-info").json()
    assert body["name"] == "AgentOS" and body["pairing"] is True


def test_pairing_requires_auth_to_create(client):
    assert client.post("/v1/pairing/codes").status_code == 401


def test_pairing_claim_flow(client, device):
    code = client.post("/v1/pairing/codes", headers=_auth(device)).json()["code"]
    assert len(code) == 6 and code.isdigit()

    # 新设备没有 token，只有配对码
    claimed = client.post("/v1/pairing/claim", json={
        "code": code, "name": "新电脑", "kind": "exe",
    })
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["device_id"].startswith("dev_")
    assert body["token"]

    # 新令牌能正常访问
    res = client.get("/v1/bots", headers={"Authorization": f"Bearer {body['token']}"})
    assert res.status_code == 200


def test_pairing_code_is_single_use(client, device):
    code = client.post("/v1/pairing/codes", headers=_auth(device)).json()["code"]
    assert client.post("/v1/pairing/claim", json={"code": code}).status_code == 200
    second = client.post("/v1/pairing/claim", json={"code": code})
    assert second.status_code == 409


def test_pairing_code_expires(client, device):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO pairing_codes (code, created_at, expires_at)"
            " VALUES ('000001','2020-01-01T00:00:00+08:00','2020-01-01T00:05:00+08:00')"
        )
    res = client.post("/v1/pairing/claim", json={"code": "000001"})
    assert res.status_code == 410


def test_pairing_unknown_code(client):
    assert client.post("/v1/pairing/claim", json={"code": "999999"}).status_code == 404


def test_pairing_rejects_bad_code_format(client):
    assert client.post("/v1/pairing/claim", json={"code": "abc"}).status_code == 422


# ---------------------------------------------------------------- 模型
def test_models_list(client, device):
    body = client.get("/v1/models", headers=_auth(device)).json()
    ids = [m["id"] for m in body["items"]]
    assert "deepseek-flash" in ids and "deepseek-v4-pro" in ids
    assert body["default"] in ids


def test_patch_bot_model(client, device):
    res = client.patch("/v1/bots/bot_chief", headers=_auth(device),
                       json={"model": "deepseek-v4-pro"})
    assert res.status_code == 200
    assert res.json()["model"] == "deepseek-v4-pro"

    detail = client.get("/v1/bots/bot_chief", headers=_auth(device)).json()
    assert detail["model"] == "deepseek-v4-pro"


def test_patch_bot_rejects_empty(client, device):
    assert client.patch("/v1/bots/bot_chief", headers=_auth(device),
                        json={}).status_code == 400


def test_agent_uses_bot_model(monkeypatch):
    """loop 调用模型时必须把 bot.model 传下去。"""
    from app.agent import loop as agent_loop

    captured = {}

    def fake_chat(messages, *, tools=None, temperature=None, model=None, **kw):
        captured["model"] = model
        return {"message": {"role": "assistant", "content": "ok"},
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(agent_loop.llm, "chat", fake_chat)

    from app import db as _db

    with _db.get_conn() as conn:
        conn.execute("UPDATE bots SET model='deepseek-v4-pro' WHERE id='bot_chief'")

    agent_loop.run_turn("bot_chief", "ses_chief")
    assert captured["model"] == "deepseek-v4-pro"

    with _db.get_conn() as conn:
        conn.execute("UPDATE bots SET model=NULL WHERE id='bot_chief'")
