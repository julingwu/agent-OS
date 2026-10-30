"""Unit tests for thought streaming and step guard.

Tests cover:
1. `chat_stream` yielding 'thought' and 'delta' with max_tokens=8192 and final structure.
2. `_chat_streamed` distributing 'thought.delta' events and keepalive heartbeat.
3. Empty content at step 0 with or without reasoning_content NEVER false alarms 30-step limit.
4. Genuine step limit triggers only when max_steps is actually reached.
5. Dynamic AGENT_MAX_STEPS configuration.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from app import db
from app import events as ev
from app import llm
from app.agent import loop as agent_loop
from app.agent import tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    return ws


def _post_user(text: str):
    with db.get_conn() as conn:
        event = ev.emit(
            conn,
            bot_id="bot_chief",
            session_id="ses_chief",
            kind="message.user",
            origin="user",
            payload={"text": text},
        )
    ev.broadcaster.publish(event)
    return event


def _events(kind: str) -> list:
    with db.get_conn() as conn:
        return ev.read(conn, since=0, kinds=[kind], limit=ev.MAX_READ_LIMIT)


# ---------------------------------------------------------------- 1. chat_stream Yields Thought & Delta
def test_chat_stream_yields_thought_and_delta(monkeypatch):
    monkeypatch.setattr(llm, "_key", lambda: "mock-key")
    monkeypatch.setattr(llm, "is_offline", lambda: False)
    monkeypatch.setattr(llm.llm_config, "check_global_daily_token_gate", lambda *a, **k: (True, 0, 100000))
    captured_requests = []

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_lines(self):
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "推导步骤1：分析需求"}}]},
                {"choices": [{"delta": {"reasoning_content": "；推导步骤2：架构设计"}}]},
                {"choices": [{"delta": {"content": "这是输出"}}]},
                {"choices": [{"delta": {"content": "正文内容"}}]},
                {"usage": {"prompt_tokens": 15, "completion_tokens": 30}},
            ]
            for c in chunks:
                yield f"data: {json.dumps(c)}"
            yield "data: [DONE]"

    class MockClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, json=None, headers=None):
            captured_requests.append({"json": json, "headers": headers})
            return MockStreamResponse()

    monkeypatch.setattr(llm.httpx, "Client", MockClient)

    gen = llm.chat_stream([{"role": "user", "content": "你好"}])
    items = list(gen)

    # 验证请求参数中的默认 max_tokens 提升到了 8192
    assert len(captured_requests) == 1
    assert captured_requests[0]["json"]["max_tokens"] == 8192

    # 验证逐块 yield 了 thought 与 delta
    thoughts = [payload for kind, payload in items if kind == "thought"]
    deltas = [payload for kind, payload in items if kind == "delta"]
    finals = [payload for kind, payload in items if kind == "final"]

    assert thoughts == ["推导步骤1：分析需求", "；推导步骤2：架构设计"]
    assert deltas == ["这是输出", "正文内容"]
    assert len(finals) == 1

    final_msg = finals[0]["message"]
    assert final_msg["content"] == "这是输出正文内容"
    assert final_msg["reasoning_content"] == "推导步骤1：分析需求；推导步骤2：架构设计"
    assert finals[0]["usage"]["completion_tokens"] == 30


# ---------------------------------------------------------------- 2. _chat_streamed Distributes thought.delta
def test_chat_streamed_distributes_thought_delta(monkeypatch):
    events = []

    def mock_emit(conn, **kw):
        events.append(kw)

    monkeypatch.setattr(agent_loop, "_emit", mock_emit)

    def mock_stream(*a, **kw):
        yield "thought", "第一阶段思考..."
        # 超过 60 字符触发即时 flush
        yield "thought", "第二阶段深度思考：这部分内容比较长，已经超过了六十个字符的阈值以测试即时刷新"
        yield "delta", "你好！"
        yield "final", {
            "message": {
                "content": "你好！",
                "reasoning_content": "第一阶段思考...第二阶段深度思考：这部分内容比较长，已经超过了六十个字符的阈值以测试即时刷新",
            },
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    monkeypatch.setattr(agent_loop.llm, "chat_stream", mock_stream)

    res = agent_loop._chat_streamed(None, [], bot_id="bot1", session_id="ses1", turn_id="t1", model="test-model")
    assert res["message"]["content"] == "你好！"

    thought_events = [e for e in events if e.get("kind") == "thought.delta"]
    assert len(thought_events) >= 1
    combined_thoughts = "".join(e["payload"]["text"] for e in thought_events)
    assert "第一阶段思考" in combined_thoughts
    assert "阈值以测试即时刷新" in combined_thoughts

    delta_events = [e for e in events if e.get("kind") == "message.delta"]
    assert len(delta_events) >= 1
    assert "".join(e["payload"]["text"] for e in delta_events) == "你好！"


def test_chat_streamed_keepalive_heartbeat_on_delay(monkeypatch):
    events = []

    def mock_emit(conn, **kw):
        events.append(kw)

    monkeypatch.setattr(agent_loop, "_emit", mock_emit)

    # 模拟等待首字超过 3 秒的情形
    def slow_stream(*a, **kw):
        time.sleep(3.2)
        yield "delta", "终于吐字了"
        yield "final", {"message": {"content": "终于吐字了"}, "usage": {}}

    monkeypatch.setattr(agent_loop.llm, "chat_stream", slow_stream)

    res = agent_loop._chat_streamed(None, [], bot_id="bot1", session_id="ses1", turn_id="t1", model="test-model")
    assert res["message"]["content"] == "终于吐字了"

    # 验证保活心跳已触发
    keepalive_events = [e for e in events if e.get("kind") == "thought.delta" and "正在深度思考与推导" in e.get("payload", {}).get("text", "")]
    assert len(keepalive_events) == 1


# ---------------------------------------------------------------- 3. Step 0 Empty Content Never False Alarms 30 Steps
def test_step_0_empty_content_with_reasoning_does_not_alarm_30_steps(workspace, monkeypatch):
    _post_user("请深度分析当前行业宏观趋势")

    # 模拟在 step 0，模型消耗 token 仅输出了 reasoning_content，但 content 为空
    monkeypatch.setattr(
        agent_loop.llm,
        "chat_stream",
        lambda *a, **kw: iter([
            ("thought", "深度分析行业宏观趋势：当前产业链处于重构期，推荐关注算力底座与自适应智能体生态。"),
            ("final", {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "深度分析行业宏观趋势：当前产业链处于重构期，推荐关注算力底座与自适应智能体生态。",
                },
                "usage": {"prompt_tokens": 100, "completion_tokens": 200},
            }),
        ]),
    )

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "done"

    bot_msgs = [e.payload["text"] for e in _events("message.bot")]
    assert len(bot_msgs) > 0
    last_text = bot_msgs[-1]

    # 关键断言：绝不能谎称达到 30 步安全上限！
    assert "30 步" not in last_text
    assert "单轮安全上限" not in last_text
    assert "已达到单轮最大执行步数" not in last_text
    # 且能够提取出思考中的结论
    assert "重构期" in last_text or "算力底座" in last_text


def test_step_0_completely_empty_response_does_not_alarm_30_steps(workspace, monkeypatch):
    _post_user("测试异常空响应")

    # 模拟在 step 0，模型返回完全空的结果，且 retry 也为空
    monkeypatch.setattr(
        agent_loop.llm,
        "chat_stream",
        lambda *a, **kw: iter([
            ("final", {
                "message": {"role": "assistant", "content": "", "reasoning_content": ""},
                "usage": {"prompt_tokens": 10, "completion_tokens": 0},
            }),
        ]),
    )
    monkeypatch.setattr(agent_loop.llm, "chat", lambda *a, **kw: None)

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "done"

    bot_msgs = [e.payload["text"] for e in _events("message.bot")]
    assert len(bot_msgs) > 0
    last_text = bot_msgs[-1]

    # 关键断言：绝不能谎称达到 30 步安全上限！
    assert "30 步" not in last_text
    assert "单轮安全上限" not in last_text
    assert "已达到单轮最大执行步数" not in last_text
    assert "重新生成" in last_text or "未返回有效正文" in last_text


# ---------------------------------------------------------------- 4. Genuine Step Limit Triggers Only When Reached
def test_genuine_step_limit_triggers_only_when_really_reached(workspace, monkeypatch):
    # 将步数上限临时调至 2 步方便测试
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    _post_user("不断调用工具的任务")

    step_counter = 0

    def mock_stream(*a, **kw):
        nonlocal step_counter
        step_counter += 1
        # 始终返回工具调用，迫使步数用尽
        return iter([
            ("final", {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"call_{step_counter}",
                        "type": "function",
                        "function": {"name": "fs_write", "arguments": json.dumps({"path": f"test_{step_counter}.txt", "content": "1"})},
                    }],
                },
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }),
        ])

    monkeypatch.setattr(agent_loop.llm, "chat_stream", mock_stream)
    monkeypatch.setattr(agent_loop.llm, "chat", lambda *a, **kw: {
        "message": {"content": "本轮已完成所有工具调用，准备收尾。"}
    })

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "done"

    bot_msgs = [e.payload["text"] for e in _events("message.bot")]
    assert len(bot_msgs) > 0
    last_text = bot_msgs[-1]

    # 此时由于真正跑满了 AGENT_MAX_STEPS (2 步)，因此应当包含 2 步上限收尾提示
    assert "2 步" in last_text
    assert "安全上限" in last_text


# ---------------------------------------------------------------- 5. Dynamic AGENT_MAX_STEPS
def test_dynamic_agent_max_steps_config(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "50")
    assert agent_loop._get_max_steps() == 50

    monkeypatch.setenv("AGENT_MAX_STEPS", "invalid")
    assert agent_loop._get_max_steps() == 30

    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    assert agent_loop._get_max_steps() == 30
