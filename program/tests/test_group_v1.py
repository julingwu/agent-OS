"""群聊路由 + Bot 间交接（@ 交接、跳数上限）。

覆盖 build-spec 的 E2 验收：
  * 群里 @ 点名 → 只叫被点名的人；没点名 → 管理员
  * 单聊不受 @ 影响
  * Bot 回复里 @ 同群其他 Bot → handoff.created 事件 + 异步接手
  * 跳数超上限不再传染
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app import events as ev
from app.agent import loop as agent_loop


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-group", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


@pytest.fixture()
def group(client, device):
    """一个双 Bot 群：Chief（admin）+ 小文。"""
    writ = client.post("/v1/bots", headers=_auth(device),
                       json={"name": "小文", "role": "content"}).json()
    session = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "group", "bot_ids": ["bot_chief", writ["id"]], "title": "测试群",
    }).json()
    return {"writ": writ, "session": session}


@pytest.fixture()
def recorder(monkeypatch):
    calls = []

    def fake_async(bot_id, session_id, *, hops=0, task_hint=None):
        calls.append({"bot_id": bot_id, "session_id": session_id,
                      "hops": hops, "task_hint": task_hint})

    monkeypatch.setattr(agent_loop, "run_turn_async", fake_async)
    monkeypatch.setenv("AGENT_AUTORUN", "1")
    return calls


# ---------------------------------------------------------------- 路由
def test_group_mention_routes_to_mentioned_bot(client, device, group, recorder):
    res = client.post(f"/v1/sessions/{group['session']['id']}/messages",
                      headers=_auth(device),
                      json={"text": "@小文 写一段文案", "client_msg_id": "g-1"})
    body = res.json()
    assert body["reply_scheduled"] is True
    assert body["targets"] == [group["writ"]["id"]]
    assert any(c["bot_id"] == group["writ"]["id"] for c in recorder)


def test_group_without_mention_only_admin(client, device, group, recorder):
    res = client.post(f"/v1/sessions/{group['session']['id']}/messages",
                      headers=_auth(device),
                      json={"text": "大家早", "client_msg_id": "g-2"})
    assert res.json()["targets"] == ["bot_chief"]
    assert all(c["bot_id"] == "bot_chief" for c in recorder)


def test_direct_session_ignores_mention(client, device, recorder):
    res = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device),
                      json={"text": "@小文 你好", "client_msg_id": "g-3"})
    assert res.json()["targets"] == ["bot_chief"]


def test_autorun_off_schedules_nothing(client, device, group, monkeypatch):
    monkeypatch.setenv("AGENT_AUTORUN", "0")
    called = []
    monkeypatch.setattr(agent_loop, "run_turn_async",
                        lambda *a, **k: called.append(a))
    res = client.post(f"/v1/sessions/{group['session']['id']}/messages",
                      headers=_auth(device),
                      json={"text": "@小文 你好", "client_msg_id": "g-4"})
    assert res.json()["reply_scheduled"] is False
    assert called == []


# ---------------------------------------------------------------- 交接
def _fake_llm(monkeypatch, text):
    monkeypatch.setattr(agent_loop.llm, "chat",
                        lambda messages, **kw: {
                            "message": {"role": "assistant", "content": text},
                            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                        })


def test_bot_handoff_creates_event_and_spawns(client, device, group, recorder, monkeypatch):
    session_id = group["session"]["id"]
    _fake_llm(monkeypatch, "这部分交给 @小文 处理。")

    result = agent_loop.run_turn("bot_chief", session_id, hops=0)
    assert result["status"] == "done"

    with db.get_conn() as conn:
        handoffs = ev.read(conn, since=0, session_id=session_id,
                           kinds=["handoff.created"], limit=10)
    assert len(handoffs) == 1
    payload = handoffs[0].payload
    assert payload["from_bot"] == "bot_chief"
    assert payload["to_bot"] == group["writ"]["id"]
    assert payload["hops"] == 1

    assert any(c["bot_id"] == group["writ"]["id"] and c["hops"] == 1 for c in recorder)
    assert recorder[0]["task_hint"] and "交接" in recorder[0]["task_hint"]


def test_handoff_stops_at_hop_limit(client, device, group, recorder, monkeypatch):
    session_id = group["session"]["id"]
    _fake_llm(monkeypatch, "还是交给 @小文。")

    agent_loop.run_turn("bot_chief", session_id, hops=3)

    with db.get_conn() as conn:
        handoffs = ev.read(conn, since=0, session_id=session_id,
                           kinds=["handoff.created"], limit=10)
    assert handoffs == []
    assert recorder == []


def test_mention_without_match_falls_back_to_admin(client, device, group, recorder):
    res = client.post(f"/v1/sessions/{group['session']['id']}/messages",
                      headers=_auth(device),
                      json={"text": "@不存在的人 你好", "client_msg_id": "g-5"})
    assert res.json()["targets"] == ["bot_chief"]
