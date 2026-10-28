"""W5 Agent 内核验收：工具循环、事件产出、需要人时中断、离线兜底、路径越狱。"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from app import db
from app import events as ev
from app.agent import loop as agent_loop
from app.agent import tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    return ws


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-agent", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def _fake_llm(monkeypatch, responses):
    """按顺序返回预设的模型响应。"""
    seq = iter(responses)

    def fake_chat(messages, *, tools=None, **kw):
        return next(seq)

    monkeypatch.setattr(agent_loop.llm, "chat", fake_chat)


def _events(kind: str) -> list:
    with db.get_conn() as conn:
        return ev.read(conn, since=0, kinds=[kind], limit=ev.MAX_READ_LIMIT)


def _post_user(text: str):
    with db.get_conn() as conn:
        event = ev.emit(conn, bot_id="bot_chief", session_id="ses_chief",
                        kind="message.user", origin="user", payload={"text": text})
    ev.broadcaster.publish(event)
    return event


# ---------------------------------------------------------------- 工具层
def test_tool_path_jail_blocks_escape(workspace):
    with pytest.raises(tools.ToolError):
        tools.dispatch("fs_read", {"path": "../../etc/passwd"})
    with pytest.raises(tools.ToolError):
        tools.dispatch("fs_write", {"path": "/etc/evil", "content": "x"})


def test_tool_write_read_roundtrip(workspace):
    assert tools.dispatch("fs_write", {"path": "a/b.txt", "content": "你好"})["ok"]
    got = tools.dispatch("fs_read", {"path": "a/b.txt"})
    assert got["content"] == "你好"
    assert (workspace / "a" / "b.txt").exists()


def test_tool_exec_blocks_dangerous(workspace):
    with pytest.raises(tools.ToolError, match="blocked"):
        tools.dispatch("exec", {"cmd": "rm -rf / --no-preserve-root"})


def test_tool_exec_runs(workspace):
    got = tools.dispatch("exec", {"cmd": "echo hello"})
    assert got["ok"] is True
    assert "hello" in got["stdout"]


# ---------------------------------------------------------------- 一轮对话
def test_run_turn_executes_tool_then_replies(workspace, monkeypatch):
    _post_user("帮我写个文件")
    _fake_llm(monkeypatch, [
        {"message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "fs_write",
                         "arguments": json.dumps({"path": "note.txt", "content": "hello"})},
        }]}, "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"message": {"role": "assistant", "content": "已写好 note.txt"},
         "usage": {"prompt_tokens": 12, "completion_tokens": 4}},
    ])

    result = agent_loop.run_turn("bot_chief", "ses_chief")

    assert result["status"] == "done"
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello"

    kinds = [e.kind for e in _all_events()]
    i_start = kinds.index("turn.started")
    assert i_start < kinds.index("tool.call") < kinds.index("tool.result") \
        < kinds.index("message.bot") < kinds.index("turn.ended")

    bot_texts = [e.payload["text"] for e in _events("message.bot")]
    assert bot_texts[-1] == "已写好 note.txt"


def _all_events():
    with db.get_conn() as conn:
        return ev.read(conn, since=0, limit=ev.MAX_READ_LIMIT)


def test_run_turn_needs_human(workspace, monkeypatch):
    _post_user("帮我转账")
    _fake_llm(monkeypatch, [
        {"message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-2", "type": "function",
            "function": {"name": "ask_human",
                         "arguments": json.dumps({"question": "确认转账 1000 元？",
                                                  "options": ["确认", "取消"]})},
        }]}, "usage": {"prompt_tokens": 8, "completion_tokens": 3}},
    ])

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "needs_human"
    human = _events("needs_human")
    assert human[-1].payload["question"] == "确认转账 1000 元？"
    assert human[-1].payload["options"] == ["确认", "取消"]


def test_run_turn_offline_fallback(workspace, monkeypatch):
    _post_user("在吗")
    monkeypatch.setattr(agent_loop.llm, "chat", lambda *a, **k: None)

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "failed"
    assert "模型未配置" in _events("message.bot")[-1].payload["text"]


def test_run_turn_tool_error_is_reported_not_raised(workspace, monkeypatch):
    _post_user("读一下外面的文件")
    _fake_llm(monkeypatch, [
        {"message": {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-3", "type": "function",
            "function": {"name": "fs_read",
                         "arguments": json.dumps({"path": "../../etc/passwd"})},
        }]}, "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        {"message": {"role": "assistant", "content": "那个路径不让读"},
         "usage": {"prompt_tokens": 6, "completion_tokens": 3}},
    ])

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "done"
    tool_results = _events("tool.result")
    assert tool_results[-1].payload["ok"] is False


# ---------------------------------------------------------------- API 触发
def test_post_message_schedules_agent_turn(workspace, client, device, monkeypatch):
    monkeypatch.setenv("AGENT_AUTORUN", "1")
    called = []

    def fake_run(bot_id, session_id):
        called.append((bot_id, session_id))

    monkeypatch.setattr(agent_loop, "run_turn_async", fake_run)

    res = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device),
                      json={"text": "你好", "client_msg_id": "agent-trigger-1"})
    assert res.status_code == 202
    assert res.json()["reply_scheduled"] is True
    assert called == [("bot_chief", "ses_chief")]


def test_post_message_does_not_schedule_when_disabled(client, device, monkeypatch):
    monkeypatch.setenv("AGENT_AUTORUN", "0")
    res = client.post("/v1/sessions/ses_chief/messages", headers=_auth(device),
                      json={"text": "安静", "client_msg_id": "agent-trigger-2"})
    assert res.json()["reply_scheduled"] is False
