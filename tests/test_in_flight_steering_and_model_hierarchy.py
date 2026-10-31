"""单测验证：
1. 免 ffmpeg 截屏正常返回 JPEG 字节且不崩溃；
2. get_model_reasoning_levels 正确区分各类模型并返回对应档位；
3. chat() 在 o1/reasoning 模型或指定 reasoning_effort 下自动剔除 temperature；
4. 在途转向检测与 request_abort 状态流与 API 接口；
5. Bot 自定义模型优先于全局默认，空时平滑继承全局默认。
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app import db
from app import devices
from app import events as ev
from app import llm
from app import llm_config
from app.computer import screen
from app.agent import loop as agent_loop
from app.agent import tools
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-steering", kind="exe")


def _auth(d: dict) -> dict:
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 1. 免 ffmpeg 截图与 Windows 保护
def test_screen_no_ffmpeg_capture():
    """验证免 ffmpeg 截图在 Windows / 无头环境下正常返回 JPEG 字节且不崩溃。"""
    # 1. 验证 geometry
    w, h = screen.geometry()
    assert w > 0 and h > 0

    # 2. 验证 grab_jpeg 无论平台均返回有效 JPEG 字节 (SOI \xff\xd8)
    jpg = screen.grab_jpeg(max_w=640)
    assert isinstance(jpg, bytes)
    assert len(jpg) > 100
    assert jpg.startswith(b"\xff\xd8")

    # 3. 验证暗黑科技蓝灰占位帧直接生成
    placeholder = screen._generate_placeholder_frame(1920, 1080, 1280)
    assert isinstance(placeholder, bytes)
    assert placeholder.startswith(b"\xff\xd8")

    # 4. 模拟 ImageGrab 与 ffmpeg 均失败，验证异常降级至占位帧，绝对不抛 ffmpeg 异常
    with patch("PIL.ImageGrab.grab", side_effect=OSError("headless display")):
        fallback_jpg = screen.grab_jpeg(max_w=800)
        assert isinstance(fallback_jpg, bytes)
        assert fallback_jpg.startswith(b"\xff\xd8")

    # 5. 验证 probe() 稳定返回
    status = screen.probe()
    assert status["capture_ok"] is True
    assert status["frame_bytes"] > 0
    assert status["awake"] is True

    # 6. 验证桌面控制在 Windows 保护下平滑返回
    screen.wake()
    screen.sleep()
    screen.xdo("test")


# ---------------------------------------------------------------- 2. 模型推理档位映射
def test_get_model_reasoning_levels():
    """验证 get_model_reasoning_levels 识别模型家族并返回正确档位。"""
    # o1 / o3 / o4 系列
    assert llm.get_model_reasoning_levels("o1") == ["low", "medium", "high"]
    assert llm.get_model_reasoning_levels("o1-mini") == ["low", "medium", "high"]
    assert llm.get_model_reasoning_levels("o1-preview") == ["low", "medium", "high"]
    assert llm.get_model_reasoning_levels("o3") == ["low", "medium", "high"]
    assert llm.get_model_reasoning_levels("o3-mini") == ["low", "medium", "high"]
    assert llm.get_model_reasoning_levels("o4") == ["low", "medium", "high"]

    # deepseek 系列（含 deepseek-reasoner / r1，chat/flash 无思考档位）
    assert llm.get_model_reasoning_levels("deepseek-reasoner") == ["none", "standard", "max"]
    assert llm.get_model_reasoning_levels("deepseek-r1") == ["none", "standard", "max"]
    assert llm.get_model_reasoning_levels("r1") == ["none", "standard", "max"]
    assert llm.get_model_reasoning_levels("deepseek-flash") == []
    assert llm.get_model_reasoning_levels("deepseek-chat") == []

    # claude-3-7 / claude thinking 系列
    assert llm.get_model_reasoning_levels("claude-3-7-sonnet") == ["none", "low", "medium", "high", "max"]
    assert llm.get_model_reasoning_levels("claude-3.7") == ["none", "low", "medium", "high", "max"]
    assert llm.get_model_reasoning_levels("claude-3-5-sonnet-thinking") == ["none", "low", "medium", "high", "max"]

    # 普通无推理档位模型
    assert llm.get_model_reasoning_levels("gpt-4o") == []
    assert llm.get_model_reasoning_levels("qwen-2.5-72b") == []
    assert llm.get_model_reasoning_levels("") == []
    assert llm.get_model_reasoning_levels(None) == []


# ---------------------------------------------------------------- 3. Reasoning 模型 Temperature 互斥
def test_chat_temperature_mutual_exclusion(monkeypatch):
    """验证 chat() 在推理模型或指定 reasoning_effort 时自动剔除 temperature。"""
    captured_bodies = []

    def mock_post(self, url, json=None, headers=None, **kwargs):
        captured_bodies.append(dict(json))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "mock reply"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        return resp

    monkeypatch.setattr(llm, "_key", lambda: "mock_api_key")
    monkeypatch.setattr(llm, "is_offline", lambda: False)
    monkeypatch.setattr(llm_config, "check_global_daily_token_gate", lambda *a: (True, 0, 1000000))
    monkeypatch.setattr("httpx.Client.post", mock_post)

    messages = [{"role": "user", "content": "hi"}]

    # Case A: 普通模型 (deepseek-chat)，不传 reasoning_effort -> temperature 保留
    captured_bodies.clear()
    llm.chat(messages, model="deepseek-chat", temperature=0.7)
    assert len(captured_bodies) == 1
    assert "temperature" in captured_bodies[0]
    assert captured_bodies[0]["temperature"] == 0.7
    assert "reasoning_effort" not in captured_bodies[0]

    # Case B: 推理模型 (o1-mini) -> temperature 必须被剔除
    captured_bodies.clear()
    llm.chat(messages, model="o1-mini", temperature=0.5)
    assert len(captured_bodies) == 1
    assert "temperature" not in captured_bodies[0]
    assert "reasoning_effort" not in captured_bodies[0]

    # Case C: deepseek-reasoner -> temperature 必须被剔除
    captured_bodies.clear()
    llm.chat(messages, model="deepseek-reasoner", temperature=0.5)
    assert len(captured_bodies) == 1
    assert "temperature" not in captured_bodies[0]

    # Case D: 普通模型显式传 reasoning_effort="high" -> temperature 剔除，注入 reasoning_effort
    captured_bodies.clear()
    llm.chat(messages, model="gpt-4o", reasoning_effort="high", temperature=0.3)
    assert len(captured_bodies) == 1
    assert "temperature" not in captured_bodies[0]
    assert captured_bodies[0].get("reasoning_effort") == "high"

    # Case E: reasoning_effort="none" -> 不注入 reasoning_effort
    captured_bodies.clear()
    llm.chat(messages, model="o3", reasoning_effort="none", temperature=0.2)
    assert len(captured_bodies) == 1
    assert "temperature" not in captured_bodies[0]
    assert "reasoning_effort" not in captured_bodies[0]

    # Case F: reasoning_effort="standard" -> 自动映射为 "medium"
    captured_bodies.clear()
    llm.chat(messages, model="SCNet-Max", reasoning_effort="standard")
    assert len(captured_bodies) == 1
    assert captured_bodies[0].get("reasoning_effort") == "medium"
    assert "temperature" not in captured_bodies[0]

    # Case G: reasoning_effort="max" -> 自动映射为 "high"
    captured_bodies.clear()
    llm.chat(messages, model="SCNet-Max", reasoning_effort="max")
    assert len(captured_bodies) == 1
    assert captured_bodies[0].get("reasoning_effort") == "high"
    assert "temperature" not in captured_bodies[0]


def test_reasoning_effort_auto_healing_retry(monkeypatch):
    """验证当上游网关因 reasoning_effort 返回 HTTP 400 (code 10013 / Invalid request parameters) 时，
    chat() 与 chat_stream() 自动剔除 reasoning_effort 并重试成功自愈。
    """
    monkeypatch.setattr(llm, "_key", lambda *a, **kw: "sk-mock-key")
    monkeypatch.setattr(llm, "is_offline", lambda: False)
    monkeypatch.setattr(llm_config, "check_global_daily_token_gate", lambda *a: (True, 0, 1000000))

    messages = [{"role": "user", "content": "hi"}]

    # 1. 测试 chat() 遇 400 10013 自动自愈
    call_records = []

    def mock_post(self, url, json=None, headers=None, **kwargs):
        call_records.append(dict(json or {}))
        resp = MagicMock()
        if len(call_records) == 1:
            resp.status_code = 400
            resp.text = '{"code": 10013, "message": "Invalid request parameters: reasoning_effort not supported"}'
        else:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "healed response"}}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 8},
            }
        return resp

    monkeypatch.setattr("httpx.Client.post", mock_post)

    call_records.clear()
    res = llm.chat(messages, model="SCNet-Max", reasoning_effort="standard")
    assert res is not None
    assert res["message"]["content"] == "healed response"
    assert len(call_records) == 2
    # 第一次尝试带标准映射后的 medium
    assert call_records[0].get("reasoning_effort") == "medium"
    # 第二次自动自愈重试已剔除 reasoning_effort
    assert "reasoning_effort" not in call_records[1]

    # 2. 测试 chat_stream() 遇 400 10013 自动自愈
    stream_calls = []

    class MockStreamResponse:
        def __init__(self, is_first: bool):
            self.is_first = is_first
            self.status_code = 400 if is_first else 200
            self.headers = {"content-type": "text/event-stream"} if not is_first else {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"code": 10013, "message": "Invalid request parameters: unknown reasoning_effort"}'

        def iter_lines(self):
            if not self.is_first:
                yield "data: " + json.dumps({
                    "choices": [{"delta": {"content": "stream healed"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6}
                })
                yield "data: [DONE]"

    def mock_stream(self, method, url, json=None, headers=None, **kwargs):
        stream_calls.append(dict(json or {}))
        return MockStreamResponse(is_first=(len(stream_calls) == 1))

    monkeypatch.setattr("httpx.Client.stream", mock_stream)

    stream_calls.clear()
    events = list(llm.chat_stream(messages, model="SCNet-Max", reasoning_effort="max"))
    assert len(stream_calls) == 2
    assert stream_calls[0].get("reasoning_effort") == "high"
    assert "reasoning_effort" not in stream_calls[1]

    deltas = [val for kind, val in events if kind == "delta"]
    assert "".join(deltas) == "stream healed"
    finals = [val for kind, val in events if kind == "final"]
    assert len(finals) == 1
    assert finals[0]["message"]["content"] == "stream healed"


# ---------------------------------------------------------------- 4. 中止 API 与状态管理
def test_abort_api_and_state(client, device):
    """验证 POST /v1/sessions/{session_id}/abort 接口及状态管理。"""
    session_id = "ses_abort_test_1"

    # 初始状态
    assert not agent_loop.is_aborted(session_id)
    assert not agent_loop.is_session_running(session_id)

    # 调用中止 API
    res = client.post(f"/v1/sessions/{session_id}/abort", headers=_auth(device))
    assert res.status_code == 200
    data = res.json()
    assert data == {"ok": True, "session_id": session_id, "aborted": True}

    # 验证状态置为 True
    assert agent_loop.is_aborted(session_id)

    # 清除中止标记
    agent_loop.clear_abort(session_id)
    assert not agent_loop.is_aborted(session_id)


def test_turn_stops_on_abort(monkeypatch):
    """验证正在运行的 turn 遇到中止标记时，立即安全停止并输出提示信息。"""
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO bots (id, name, created_at) VALUES ('bot_abort_tester', 'Tester', '2026-10-31')")
        conn.execute("INSERT OR IGNORE INTO sessions (id, kind, admin_bot_id, created_at) VALUES ('ses_abort_run', 'direct', 'bot_abort_tester', '2026-10-31')")
        conn.execute("INSERT OR IGNORE INTO session_members (session_id, member_id, role, joined_at) VALUES ('ses_abort_run', 'bot_abort_tester', 'admin', '2026-10-31')")
        ev.emit(conn, bot_id="bot_abort_tester", session_id="ses_abort_run", kind="message.user", payload={"text": "测试任务执行"})
        conn.commit()

    agent_loop.request_abort("ses_abort_run")
    res = agent_loop.run_turn("bot_abort_tester", "ses_abort_run")

    assert res["status"] == "aborted"
    with db.get_conn() as conn:
        events = ev.read(conn, since=0, session_id="ses_abort_run", kinds=["message.bot"])
        bot_texts = [e.payload.get("text") for e in events]
        assert any("⏹️ 任务已由用户手动停止。" in (t or "") for t in bot_texts)


# ---------------------------------------------------------------- 5. 在途实时转向 (In-Flight Steering)
def test_in_flight_steering(monkeypatch, tmp_path):
    """验证在多步工具执行过程中，检测到新 user 消息追加转向指令并发出 thought.delta。"""
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)

    bot_id = "bot_steer_1"
    session_id = "ses_steer_1"

    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO bots (id, name, created_at) VALUES (?, 'SteerBot', '2026-10-31')", (bot_id,))
        conn.execute("INSERT OR IGNORE INTO sessions (id, kind, admin_bot_id, created_at) VALUES (?, 'direct', ?, '2026-10-31')", (session_id, bot_id))
        conn.execute("INSERT OR IGNORE INTO session_members (session_id, member_id, role, joined_at) VALUES (?, ?, 'admin', '2026-10-31')", (session_id, bot_id))
        ev.emit(conn, bot_id=bot_id, session_id=session_id, kind="message.user", payload={"text": "第一步：先探索环境"})
        conn.commit()

    step_counter = 0
    received_messages_in_step_1 = []

    def mock_chat_streamed(conn, messages, *, bot_id, session_id, turn_id, model, tools_specs=None, temperature=0.3, reasoning_effort=None):
        nonlocal step_counter
        if step_counter == 0:
            step_counter += 1
            # 在第 0 步执行完成准备进第 1 步之前，用户追加了在途指令！
            with db.get_conn() as c2:
                ev.emit(c2, bot_id=bot_id, session_id=session_id, kind="message.user",
                        payload={"text": "追加要求：不要探索了，直接写一个总结报告"})
                c2.commit()
            # 返回调用工具
            return {
                "message": {
                    "role": "assistant",
                    "content": "正在执行初次探测...",
                    "tool_calls": [{
                        "id": "call_inspect_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps({"path": "sample.txt"})},
                    }],
                },
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            }
        else:
            # 第 1 步：记录接收到的 messages，并返回最终回答
            received_messages_in_step_1.extend(messages)
            return {
                "message": {
                    "role": "assistant",
                    "content": "已根据最新追加指令调整，总结报告已生成完成。",
                },
                "usage": {"prompt_tokens": 80, "completion_tokens": 40},
            }

    monkeypatch.setattr(agent_loop, "_chat_streamed", mock_chat_streamed)
    res = agent_loop.run_turn(bot_id, session_id)

    assert res["status"] == "done"

    # 验证 messages 中成功追加了在途转向指令
    steering_msgs = [m for m in received_messages_in_step_1 if "【用户在执行过程中追加了最新要求 / 调整方向】" in m.get("content", "")]
    assert len(steering_msgs) >= 1
    assert "追加要求：不要探索了，直接写一个总结报告" in steering_msgs[0]["content"]

    # 验证向前端思考流发射了 thought.delta 事件
    with db.get_conn() as conn:
        thought_events = ev.read(conn, since=0, session_id=session_id, kinds=["thought.delta"])
        delta_texts = [e.payload.get("text", "") for e in thought_events]
        assert any("🔄 收到最新追加指令，正在调整后续执行方向" in t for t in delta_texts)


# ---------------------------------------------------------------- 6. 模型层级继承与 MessageIn 参数传递
def test_model_hierarchy_and_message_in(client, device):
    """验证模型层级继承：Bot 自定义模型优先于全局默认，空时平滑继承全局默认。"""
    global_model = llm_config.resolve_llm_config()["model"]

    # 1. Bot 未设置 model (为 None 或 "") -> 继承全局默认
    bot_default = {"id": "b1", "name": "BotDefault", "model": None}
    assert agent_loop.resolve_turn_model(bot_default) == global_model

    bot_empty = {"id": "b2", "name": "BotEmpty", "model": "  "}
    assert agent_loop.resolve_turn_model(bot_empty) == global_model

    # 2. Bot 明确设置 model -> 自定义模型优先
    bot_custom = {"id": "b3", "name": "BotCustom", "model": "o3-mini"}
    assert agent_loop.resolve_turn_model(bot_custom) == "o3-mini"

    # 3. MessageIn 接收 reasoning_effort 并落入 message.user 事件 payload
    res = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "direct",
        "bot_ids": ["bot_chief"],
    })
    session_id = res.json()["id"]

    post_res = client.post(f"/v1/sessions/{session_id}/messages", headers=_auth(device), json={
        "text": "使用深度思考推理",
        "reasoning_effort": "high",
    })
    assert post_res.status_code == 202

    with db.get_conn() as conn:
        events = ev.read(conn, since=0, session_id=session_id, kinds=["message.user"])
        assert len(events) >= 1
        last_ev = events[-1]
        assert last_ev.payload.get("text") == "使用深度思考推理"
        assert last_ev.payload.get("reasoning_effort") == "high"
