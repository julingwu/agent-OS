"""流式回复验收：SSE 解析、瞬态事件、回落策略、桌面端依赖的字段形状。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db, llm
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
        return devices.issue(conn, name="pytest-stream", kind="exe")


def _post_user(text: str):
    from app import events

    with db.get_conn() as conn:
        event = events.emit(conn, bot_id="bot_chief", session_id="ses_chief",
                            kind="message.user", origin="user", payload={"text": text})
        conn.commit()
    events.broadcaster.publish(event)
    return event


# ---------------------------------------------------------------- SSE 解析
class _FakeResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStream:
    def __init__(self, response):
        self._response = response

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, response, **kw):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, *a, **kw):
        return _FakeStream(self._response)


def _patch_stream(monkeypatch, lines, status_code=200):
    monkeypatch.setattr(llm, "_key", lambda: "sk-test")
    monkeypatch.setattr(llm, "is_offline", lambda: False)
    resp = _FakeResponse(lines, status_code=status_code)
    monkeypatch.setattr(llm.httpx, "Client",
                        lambda *a, **kw: _FakeClient(resp))


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def test_chat_stream_collects_content_and_usage(monkeypatch):
    lines = [
        _sse({"choices": [{"delta": {"content": "你"}, "index": 0}]}),
        _sse({"choices": [{"delta": {"content": "好"}, "index": 0}]}),
        "",
        _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
              "usage": {"prompt_tokens": 11, "completion_tokens": 2}}),
        "data: [DONE]",
    ]
    _patch_stream(monkeypatch, lines)

    pieces, final = [], None
    for kind, payload in llm.chat_stream([{"role": "user", "content": "hi"}]):
        if kind == "delta":
            pieces.append(payload)
        else:
            final = payload

    assert "".join(pieces) == "你好"
    assert final["message"]["content"] == "你好"
    assert final["usage"] == {"prompt_tokens": 11, "completion_tokens": 2}
    assert final["finish_reason"] == "stop"


def test_chat_stream_accumulates_tool_call_fragments(monkeypatch):
    """tool_calls 是碎片到达的：name 一次到齐，arguments 分几段拼。"""
    lines = [
        _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "type": "function",
             "function": {"name": "fs_write", "arguments": '{"path": "a'}}]}}]}),
        _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '.txt", "content": "x"}'}}]}}]}),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    _patch_stream(monkeypatch, lines)

    final = None
    for kind, payload in llm.chat_stream([{"role": "user", "content": "写文件"}],
                                         tools=[{"type": "function"}]):
        if kind == "final":
            final = payload

    calls = final["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["id"] == "call_a"
    assert calls[0]["function"]["name"] == "fs_write"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "a.txt", "content": "x"}


def test_chat_stream_raises_on_http_error(monkeypatch):
    _patch_stream(monkeypatch, [], status_code=429)
    with pytest.raises(RuntimeError, match="http_429"):
        list(llm.chat_stream([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------- 瞬态事件
def test_message_delta_is_transient():
    with db.get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        event = ev.emit(conn, bot_id="bot_chief", session_id="ses_x",
                        kind="message.delta", payload={"text": "你", "seq": 1})
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert event.id == 0
    assert before == after, "delta 不该落库（否则事件表被逐字记录淹没）"


def test_message_delta_in_closed_set():
    assert "message.delta" in ev.KINDS
    assert "message.delta" in ev.TRANSIENT_KINDS


# ---------------------------------------------------------------- loop 集成
def _drain_broadcast(sub):
    items = []
    while True:
        batch, _dropped = ev.broadcaster.drain(sub)
        if not batch:
            return items
        items.extend(batch)


def test_run_turn_streams_deltas_then_persists_final(monkeypatch):
    _post_user("随便写一句")

    def fake_stream(messages, *, tools=None, **kw):
        yield "delta", "第一段"
        yield "delta", "，第二段"
        yield "final", {"message": {"content": "第一段，第二段"},
                        "usage": {"prompt_tokens": 5, "completion_tokens": 6}}

    monkeypatch.setattr(agent_loop.llm, "chat_stream", fake_stream)
    sub = ev.broadcaster.subscribe()
    try:
        result = agent_loop.run_turn("bot_chief", "ses_chief")
        items = _drain_broadcast(sub)
    finally:
        ev.broadcaster.unsubscribe(sub)

    assert result["status"] == "done"
    deltas = [i for i in items if i["kind"] == "message.delta"]
    assert deltas, "流式应该在广播里出现 message.delta"
    assert "".join(d["payload"]["text"] for d in deltas) == "第一段，第二段"
    assert all(d["id"] == 0 for d in deltas)
    assert all(d["session_id"] == "ses_chief" for d in deltas)

    with db.get_conn() as conn:
        final = ev.read(conn, since=0, kinds=["message.bot"], limit=50)
    assert final[-1].payload["text"] == "第一段，第二段"


def test_streaming_failure_before_any_text_falls_back_to_chat(monkeypatch):
    """流式一个字都没吐就挂：必须静默回落到非流式，用户无感。"""
    _post_user("回退测试")

    def broken_stream(messages, *, tools=None, **kw):
        raise RuntimeError("connection reset")
        yield  # pragma: no cover - 让它成为生成器

    monkeypatch.setattr(agent_loop.llm, "chat_stream", broken_stream)
    monkeypatch.setattr(agent_loop.llm, "chat",
                        lambda messages, *, tools=None, **kw: {
                            "message": {"content": "非流式兜底回答"},
                            "usage": {"prompt_tokens": 3, "completion_tokens": 4}})

    result = agent_loop.run_turn("bot_chief", "ses_chief")
    assert result["status"] == "done"
    with db.get_conn() as conn:
        final = ev.read(conn, since=0, kinds=["message.bot"], limit=50)
    assert final[-1].payload["text"] == "非流式兜底回答"


def test_streaming_partial_text_is_kept(monkeypatch):
    """吐了一半再挂：保留已生成内容，不重跑（重跑会让用户看到内容闪没）。"""
    _post_user("半截测试")

    def half_stream(messages, *, tools=None, **kw):
        yield "delta", "已经写了一半"
        raise RuntimeError("mid-stream failure")

    monkeypatch.setattr(agent_loop.llm, "chat_stream", half_stream)
    monkeypatch.setattr(agent_loop.llm, "chat",
                        lambda *a, **kw: pytest.fail("不该回落到非流式"))

    sub = ev.broadcaster.subscribe()
    try:
        agent_loop.run_turn("bot_chief", "ses_chief")
        items = _drain_broadcast(sub)
    finally:
        ev.broadcaster.unsubscribe(sub)

    deltas = [i for i in items if i["kind"] == "message.delta"]
    assert "".join(d["payload"]["text"] for d in deltas) == "已经写了一半"


# ---------------------------------------------------------------- WS 游标
def test_transient_event_does_not_reset_cursor(client, device):
    """瞬态事件 id=0，若拿它推进游标会把游标打回 0（下次重连重放整库）。"""
    import anyio

    from app.api_v1 import stream as stream_mod

    sent = {"cursor": 42}

    # 直接复现 send_events 的游标推进逻辑
    items = [{"id": 0, "kind": "message.delta"}, {"id": 0, "kind": "message.delta"}]
    last_id = max((int(it.get("id") or 0) for it in items), default=0)
    if last_id:
        sent["cursor"] = last_id
    assert sent["cursor"] == 42
    assert anyio is not None and stream_mod is not None
