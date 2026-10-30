"""测试对标 Codex + Claude + Hermes 的自适应上下文管理、双模载荷与会话黑板引擎。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app.agent import blackboard, loop as agent_loop, tools


# ---------------------------------------------------------------- 1. Token 估算器精准性测试
def test_token_estimator_accuracy_and_bounds():
    """验证 Token 估算器精确兼顾 CJK 中日韩字符、代码/JSON/英文字符及协议元数据。"""
    # 纯英文
    eng_text = "Hello world! This is a test of token estimation."
    eng_tokens = agent_loop._estimate_text_tokens(eng_text)
    assert 5 <= eng_tokens <= 25

    # 纯中文
    cjk_text = "自适应上下文管理与微压缩引擎"  # 15 个汉字
    cjk_tokens = agent_loop._estimate_text_tokens(cjk_text)
    # 15 * 1.3 ≈ 19 tokens
    assert 15 <= cjk_tokens <= 30

    # 结构化 JSON
    json_text = json.dumps({"role": "assistant", "metrics": {"revenue": 1000, "growth": "15%"}})
    json_tokens = agent_loop._estimate_text_tokens(json_text)
    assert 10 <= json_tokens <= 40

    # 完整消息流估算
    messages = [
        {"role": "system", "content": "你是一个可靠的 AI 助手。"},
        {"role": "user", "content": "请分析黄金与美债收益率相关性。"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "web_search", "arguments": '{"query": "gold price"}'},
                }
            ],
            "reasoning_content": "正在思考检索关键词...",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true, "results": ["data1", "data2"]}'},
    ]
    total_tokens = agent_loop._estimate_tokens(messages)
    # 4 条消息的基础 framing (16) + 中文字符 + tool_calls + reasoning
    assert 40 <= total_tokens <= 200


# ---------------------------------------------------------------- 2. Codex 式双模载荷自动落盘与紧凑视图
def test_large_tool_output_dual_representation_offloading(tmp_path, monkeypatch):
    """测试超大工具输出自动持久化至 .tool_outputs/ 并返回包含 summary 与 output_file 的紧凑视图。"""
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path)
    outputs_dir = tmp_path / ".tool_outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "TOOL_OUTPUTS_DIR", outputs_dir)

    # 1. 小输出：保持原生返回，绝不额外落盘
    res_small = tools.dispatch("fs_write", {"path": "small.txt", "content": "short payload"})
    assert res_small["ok"] is True
    assert "output_file" not in res_small
    assert (tmp_path / "small.txt").read_text(encoding="utf-8") == "short payload"

    # 2. 超大输出（生成 4,000 字符的大文件）
    large_payload = "A" * 4000
    (tmp_path / "large.txt").write_text(large_payload, encoding="utf-8")

    # 读取超大文件
    call_id = "call_abc123456789"
    res_large = tools.dispatch("fs_read", {"path": "large.txt"}, call_id=call_id)

    # 验证紧凑双模载荷格式
    assert res_large["ok"] is True
    assert "output_file" in res_large
    assert res_large["output_file"].startswith(".tool_outputs/step_call_abc1234_fs_read.log")
    assert res_large["total_chars"] == 4000
    assert "_hint" in res_large
    assert "fs_read" in res_large["_hint"] or "grep_search" in res_large["_hint"]
    assert "summary" in res_large
    assert len(res_large["summary"]) <= 650

    # 验证完整原始载荷真实写入工作区落盘文件
    persisted_file = tmp_path / res_large["output_file"]
    assert persisted_file.exists()
    assert persisted_file.read_text(encoding="utf-8") == large_payload


# ---------------------------------------------------------------- 3. Claude Code 式活跃窗口全保真与微剪枝
def test_adaptive_compact_context_active_window_preservation():
    """测试活跃窗口（最近 4 步工具往返）100% 原样全保真，4 步之前历史步骤执行规则微剪枝。"""
    messages: list[dict] = [
        {"role": "system", "content": "系统静态指令"},
        {"role": "user", "content": "请完成综合任务"},
    ]

    # 构建 6 步工具调用链
    for step in range(1, 7):
        call_id = f"call_{step}"
        messages.append({
            "role": "assistant",
            "content": f"准备执行第 {step} 步操作",
            "tool_calls": [{"id": call_id, "function": {"name": "fs_read", "arguments": f'{{"step": {step}}}'}}],
        })
        # 前 2 步放长工具返回，后 4 步放长工具返回
        long_content = f"Step {step} detailed data: " + ("X" * 1200)
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({"ok": True, "step": step, "data": long_content, "path": f"data_{step}.csv"}),
        })

    # 设置 target_tokens 触发微剪枝（约 2280 > 2000 触发 Phase 1；微剪枝后降至约 1778 <= 2000，精准保留微剪枝状态）
    # keep_recent_steps=4：保护 Step 3, 4, 5, 6
    compacted = agent_loop.adaptive_compact_context(messages, keep_recent_steps=4, target_tokens=2000)

    # 活跃窗口（最近 4 步，即 Step 3, 4, 5, 6）必须 100% 全保真未被剪枝
    # Step 6 tool
    step6_tool = compacted[-1]
    assert step6_tool["role"] == "tool"
    assert "Step 6 detailed data: XXX" in step6_tool["content"]

    # Step 3 tool
    step3_tool = compacted[-7]
    assert step3_tool["role"] == "tool"
    assert "Step 3 detailed data: XXX" in step3_tool["content"]

    # 4 步之前（Step 1, Step 2）的旧工具输出已做微剪枝
    # 消息[3] 为 Step 1 tool
    step1_tool = compacted[3]
    assert step1_tool["role"] == "tool"
    assert "[早期工具输出已做微剪枝保留核心结论" in step1_tool["content"]
    assert "ok=True" in step1_tool["content"]
    assert "path=data_1.csv" in step1_tool["content"]
    # 绝未抹杀为空壳，且长度远低于原长
    assert len(step1_tool["content"]) < 600


# ---------------------------------------------------------------- 4. Phase 2: 宏观阶段折叠与语法对齐
def test_adaptive_compact_macro_compaction_phase2():
    """测试当微剪枝后仍超预算时，宏观折叠早期步骤为成果摘要，且严密维持语法对齐。"""
    messages: list[dict] = [
        {"role": "system", "content": "系统静态指令"},
        {"role": "user", "content": "请从第 1 步做到第 5 步"},
    ]

    for step in range(1, 6):
        call_id = f"call_{step}"
        messages.append({
            "role": "assistant",
            "content": f"执行步 {step}",
            "tool_calls": [{"id": call_id, "function": {"name": "fs_read", "arguments": "{}"}}],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({"ok": True, "step": step, "output": "Y" * 400}),
        })

    # 强制极低 target_tokens 触发 Phase 2 宏观阶段折叠
    compacted = agent_loop.adaptive_compact_context(messages, keep_recent_steps=2, target_tokens=100)

    # 验证语法结构与对齐合法性
    assert compacted[0]["role"] == "system"
    assert compacted[1]["role"] == "user"

    # 验证活跃窗口包含 Step 4 和 Step 5
    # 且折叠摘要已优雅附加在活跃窗口第一条 assistant 消息中
    assert "【历史探索阶段性成果摘要】" in compacted[2]["content"]
    assert compacted[2]["role"] == "assistant"
    assert len(compacted[2]["tool_calls"]) == 1

    # 检查所有 tool 消息之前均有合法配对的 assistant 消息
    for idx, m in enumerate(compacted):
        if m["role"] == "tool":
            prev = compacted[idx - 1]
            assert prev["role"] in ("assistant", "tool")


# ---------------------------------------------------------------- 5. Hermes 式会话黑板（Session Blackboard）
def test_session_blackboard_state_and_breakpoint_inheritance():
    """测试 Hermes 会话黑板状态记录、产物自动嗅探与跨轮断点无缝接力。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    blackboard.init_blackboard_schema(conn)

    session_id = "sess_hermes_test_01"

    # 1. 记录初始目标
    blackboard.save_blackboard(conn, session_id, last_goal="生成 2026 年宏观资产配置研报与相关图表")

    # 2. 模拟工具调用并记录产物
    # fs_write 生成 csv
    blackboard.record_tool_artifact_and_step(
        conn, session_id, "fs_write",
        {"path": "data/gold_price.csv"},
        {"ok": True, "path": "data/gold_price.csv"},
    )
    # render_chart 生成趋势图
    blackboard.record_tool_artifact_and_step(
        conn, session_id, "render_chart",
        {"title": "黄金十年走势图"},
        {"ok": True, "chart_path": "charts/gold_10y_trend.png"},
    )
    # publish_artifact 发布 html 研报
    blackboard.record_tool_artifact_and_step(
        conn, session_id, "publish_artifact",
        {"path": "reports/macro_2026.html", "name": "宏观大类资产配置研报.html"},
        {"ok": True, "path": "reports/macro_2026.html"},
    )

    # 3. 读取黑板状态
    board = blackboard.get_blackboard(conn, session_id)
    assert board["last_goal"] == "生成 2026 年宏观资产配置研报与相关图表"
    assert len(board["artifacts"]) == 3

    artifact_paths = [a["path"] for a in board["artifacts"]]
    assert "data/gold_price.csv" in artifact_paths
    assert "charts/gold_10y_trend.png" in artifact_paths
    assert "reports/macro_2026.html" in artifact_paths

    assert len(board["completed_steps"]) == 3
    assert any("fs_write" in s for s in board["completed_steps"])
    assert any("render_chart" in s for s in board["completed_steps"])

    # 4. 判定用户断点接力意图
    assert blackboard.is_continuation_intent("继续") is True
    assert blackboard.is_continuation_intent("接着做") is True
    assert blackboard.is_continuation_intent("往下") is True
    assert blackboard.is_continuation_intent("continue") is True
    assert blackboard.is_continuation_intent("请重新分析原油价格走势") is False

    # 5. 格式化黑板断点看板 Prompt 注入
    prompt_block = blackboard.format_blackboard_prompt(board)
    assert "【当前任务黑板 / 断点进度看板" in prompt_block
    assert "核心任务目标：生成 2026 年宏观资产配置研报与相关图表" in prompt_block
    assert "charts/gold_10y_trend.png" in prompt_block
    assert "reports/macro_2026.html" in prompt_block
    assert "严禁重复生成" in prompt_block
