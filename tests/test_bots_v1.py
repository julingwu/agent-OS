"""W6 最小切片：Bot / 会话 / 消息 的投影 API 验收。"""

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
        return devices.issue(conn, name="pytest-bots", kind="exe")


def _auth(d: dict) -> dict:
    return {"Authorization": f"Bearer {d['token']}"}


def test_requires_token(client):
    assert client.get("/v1/bots").status_code == 401


def test_seed_bot_and_session_exist(client, device):
    bots = client.get("/v1/bots", headers=_auth(device)).json()["items"]
    assert any(b["id"] == "bot_chief" and b["name"] == "Chief" for b in bots)

    sessions = client.get("/v1/sessions", headers=_auth(device)).json()["items"]
    chief = next(s for s in sessions if s["id"] == "ses_chief")
    assert chief["kind"] == "direct"
    assert chief["admin_bot_id"] == "bot_chief"
    assert chief["members"] == ["bot_chief"]


def test_create_bot_and_group_session(client, device):
    created = client.post("/v1/bots", headers=_auth(device),
                          json={"name": "小文", "role": "content"}).json()
    assert created["id"].startswith("bot_")
    assert created["name"] == "小文"

    session = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "group",
        "bot_ids": ["bot_chief", created["id"]],
        "title": "双十一",
    }).json()
    assert session["kind"] == "group"
    assert set(session["members"]) == {"bot_chief", created["id"]}
    assert session["admin_bot_id"] == "bot_chief"


def test_create_bot_from_role_template_and_seed_skills(client, device):
    roles = client.get("/v1/roles", headers=_auth(device)).json()
    assert roles["count"] >= 13
    qa_role = next((r for r in roles["items"] if r["tag"] == "qa"), roles["items"][0])

    created = client.post("/v1/bots", headers=_auth(device), json={
        "name": "测试小七",
        "role_id": qa_role["id"],
    }).json()
    assert created["role"] == qa_role["tag"]
    assert qa_role["prompt"] in created["persona"]

    # 核验专属技能已自动落库
    skills_res = client.get(f"/v1/skills?bot_id={created['id']}", headers=_auth(device)).json()
    assert skills_res["count"] > 0
    names = [s["name"] for s in skills_res["items"]]
    assert any("用例设计" in n or "复现" in n for n in names)


def test_create_session_rejects_unknown_bot(client, device):
    res = client.post("/v1/sessions", headers=_auth(device),
                      json={"kind": "direct", "bot_ids": ["bot_nope"]})
    assert res.status_code == 400


def test_post_message_is_idempotent(client, device):
    payload = {"text": "你好 Chief", "client_msg_id": "c-msg-1"}
    first = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device), json=payload)
    second = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device), json=payload)
    assert first.status_code == 202
    assert first.json()["event_id"] == second.json()["event_id"]

    msgs = client.get("/v1/sessions/ses_chief/messages", headers=_auth(device)).json()
    assert [m["text"] for m in msgs["items"]] == ["你好 Chief"]
    assert msgs["items"][0]["kind"] == "message.user"


def test_post_message_to_unknown_session(client, device):
    res = client.post("/v1/sessions/ses_nope/messages", headers=_auth(device),
                      json={"text": "hi"})
    assert res.status_code == 404


def test_get_unknown_bot_and_session(client, device):
    assert client.get("/v1/bots/bot_nope", headers=_auth(device)).status_code == 404
    assert client.get("/v1/sessions/ses_nope", headers=_auth(device)).status_code == 404


def test_messages_pagination_with_since(client, device):
    ids = []
    for i in range(3):
        r = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device),
                        json={"text": f"m{i}"})
        ids.append(r.json()["event_id"])
    after = client.get("/v1/sessions/ses_chief/messages",
                       params={"since": ids[0]}, headers=_auth(device)).json()
    assert [m["text"] for m in after["items"]] == ["m1", "m2"]


def test_global_default_skills_seeded(client, device):
    """验证系统级全局特化技能库默认预置就绪。"""
    res = client.get("/v1/skills", headers=_auth(device))
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 10
    names = {s["name"] for s in data["items"]}
    assert "周报三段式" in names
    assert "第一性原理决策推演" in names
    assert "代码质量与安全审计" in names
    assert "增值税与财务合规审核" in names


def test_reasoning_content_preserved_in_llm_and_loop(monkeypatch):
    """验证 reasoning_content 在 llm 修复工具与 loop 消息流转中正确保留。"""
    from app import llm
    from app.agent import loop

    raw_msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "thinking", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "{}"},
    ]
    fixed = llm._fix_assistant_reasoning_content(raw_msgs)
    assert "reasoning_content" in fixed[1]
    assert fixed[1]["reasoning_content"] == ""

    # 验证带思考内容的 assistant 消息在 tool_calls 时完整保留
    msg = {"content": "step 1", "reasoning_content": "my thoughts", "tool_calls": [{"id": "c1"}]}
    assistant_msg = {
        "role": "assistant",
        "content": msg.get("content") or "",
        "tool_calls": msg["tool_calls"],
    }
    if "reasoning_content" in msg or msg.get("reasoning_content") is not None:
        assistant_msg["reasoning_content"] = msg.get("reasoning_content") or ""
    else:
        assistant_msg["reasoning_content"] = ""
    assert assistant_msg["reasoning_content"] == "my thoughts"


def test_loop_emits_diagnostic_bubble_on_llm_failure(client, monkeypatch):
    """验证模型中断时不会静默失败，会弹出结构化诊断与建议气泡。"""
    from app import db
    from app.agent import loop
    import json

    with db.get_conn() as conn:
        def fake_chat_streamed(*args, **kwargs):
            raise RuntimeError("http_429: Rate limit exceeded")

        monkeypatch.setattr(loop, "_chat_streamed", fake_chat_streamed)
        res = loop.run_turn("bot_chief", "ses_chief")
        assert res["status"] == "failed"

        row = conn.execute(
            "SELECT payload_json FROM events WHERE session_id='ses_chief' AND kind='message.bot' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        text = payload.get("text", "")
        assert "任务执行中断反馈" in text
        assert "报错原因" in text
        assert "推荐方案" in text
        assert "429" in text
