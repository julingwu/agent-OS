"""测试 app.agent.loop._system_prompt 的云端执行策略与 Hermes/Codex 交付规范。"""

from __future__ import annotations

import pytest

from app.agent import loop as agent_loop


def test_system_prompt_cloud_execution_policy():
    """验证系统提示词中包含云端服务器默认优先、严禁做选择题、严控本地电脑等准则。"""
    bot = {"id": "bot_test", "name": "分析师", "persona": "你是顶级战略与数据分析师。"}
    prompt = agent_loop._system_prompt(bot)

    # 1. 云端 Linux 服务器运行环境
    assert "专属云端 Linux 服务器" in prompt
    assert "直连全网并配备强大的云端工程能力" in prompt

    # 2. 默认第一优先：云端服务器直接闭环执行
    assert "【默认第一优先：云端服务器直接闭环执行】" in prompt
    assert "web_search/web_fetch" in prompt
    assert "exec/process_*" in prompt
    assert "doc_read" in prompt
    assert "render_chart" in prompt
    assert "100% 默认在云端服务器直接执行并交付" in prompt

    # 3. 严禁推卸与做选择题
    assert "【严禁推卸与做选择题】" in prompt
    assert "严禁在能够自主执行时向用户提问“你选方案 A 还是方案 B”、“你要我怎么做”" in prompt
    assert "绝不把执行责任推给用户" in prompt
    assert "必须先动手跑出完整结果再汇报" in prompt
    assert "ask_human" in prompt

    # 4. 本地个人电脑（local_*）严格管控铁律
    assert "【本地个人电脑（local_*）严格管控铁律】" in prompt
    assert "local_* 系列工具专用于操作用户本人的本地桌面" in prompt
    assert "“在我电脑上”" in prompt
    assert "“在我本机运行”" in prompt
    assert "“打开我本地微信/Excel”" in prompt
    assert "“读取我本机 D 盘”" in prompt
    assert "绝对禁止主动提议“尝试在你本地电脑上执行/搜索”" in prompt
    assert "绝对禁止向用户列出任何本地电脑操作选项" in prompt
    assert "绝对禁止调用任何 local_* 工具！" in prompt


def test_system_prompt_delivery_standards():
    """验证系统提示词中升级后的 Hermes / Codex 工业级交付标准与 render_chart 规范。"""
    bot = {"id": "bot_chief", "name": "总管", "persona": "你是一个可靠的 AI 助手。"}
    prompt = agent_loop._system_prompt(bot)

    # Hermes / Codex 交付标准
    assert "Hermes / Codex 工业级交付标准" in prompt

    # 核心结论卡片
    assert "【核心结论卡片（Executive Summary）】" in prompt
    assert "开篇直接给出清晰、量化的核心结论" in prompt

    # 全景量化对比大表
    assert "【全景量化对比大表（Structured Tables）】" in prompt
    assert "排版工整的多列 Markdown 宽表呈现" in prompt

    # 高分辨率可视化图表交付物
    assert "【高分辨率可视化图表交付物（Visual Chart Artifacts）】" in prompt
    assert "必须主动调用 render_chart 工具生成 1~3 张高分辨率专业图表" in prompt
    assert "![title](url)" in prompt
    assert "严禁只给枯燥干瘪文字！" in prompt

    # 多维穿透与敏感性测算模型
    assert "【多维穿透与敏感性测算模型（Analytical Modeling）】" in prompt
    assert "情景测算" in prompt

    # 决策建议与风险提示
    assert "【决策建议与风险提示（Actionable Next Steps & Risks）】" in prompt


def test_system_prompt_with_members_and_memories():
    """验证带有团队成员和长期记忆时，prompt 正确拼接所有部分。"""
    bot = {"id": "bot_1", "name": "小万", "persona": "你是主管"}
    members = [
        {"id": "bot_1", "name": "小万", "persona": "你是主管"},
        {"id": "bot_2", "name": "小图", "persona": "你是制图专家"},
    ]
    memories = [
        {"content": "用户偏好图文并茂的分析报告"},
        {"content": "关注下行风险与敏感性矩阵"},
    ]
    prompt = agent_loop._system_prompt(bot, members=members, memories=memories)

    # 基础信息
    assert "你是主管" in prompt
    assert "团队（同一会话里的其他 Bot）：小图（你是制图专家）" in prompt
    assert "用户偏好图文并茂的分析报告" in prompt
    assert "关注下行风险与敏感性矩阵" in prompt

    # 依然具备云端优先和图表交付准则
    assert "云端服务器直接闭环执行" in prompt
    assert "render_chart" in prompt
    assert "local_*" in prompt
