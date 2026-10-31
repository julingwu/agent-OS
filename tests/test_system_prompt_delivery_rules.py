"""测试 app.agent.loop._system_prompt 的云端执行策略与工业级交付规范。"""

from __future__ import annotations

import pytest

from app.agent import loop as agent_loop


def test_system_prompt_cloud_execution_policy():
    """验证系统提示词中包含自然对话交互准则、云端服务器默认优先、主动执行闭环、沉稳规范的本地电脑边界。"""
    bot = {"id": "bot_test", "name": "分析师", "persona": "你是顶级战略与数据分析师。"}
    prompt = agent_loop._system_prompt(bot)

    # 1. 自然对话与交互准则（正面优雅指引，已去除反向泄露词）
    assert "【自然对话与交互准则】" in prompt
    assert "日常问候与闲聊" in prompt
    assert "保持自然、温和、亲切的回应" in prompt
    assert "实质性任务" in prompt

    # 2. 云端 Linux 服务器运行环境
    assert "专属云端 Linux 服务器" in prompt
    assert "直连全网并配备强大的云端工程能力" in prompt

    # 3. 默认第一优先：云端服务器直接闭环执行
    assert "【默认第一优先：云端服务器直接闭环执行】" in prompt
    assert "web_search/web_fetch" in prompt
    assert "exec/process_*" in prompt
    assert "doc_read" in prompt
    assert "render_chart" in prompt

    # 4. 主动执行闭环
    assert "【主动执行闭环】" in prompt
    assert "在接收到具体任务时，优先自主完成调研与执行闭环" in prompt
    assert "直接交付完整方案与成果，避免不必要的反问推诿" in prompt
    assert "ask_human" in prompt

    # 5. 本地电脑工具执行边界（专业沉稳系统规则，无恐吓口吻）
    assert "【本地电脑工具执行边界】" in prompt
    assert "local_* 系列工具专用于操作用户本人的物理桌面与本地系统" in prompt
    assert "仅在用户显式明确要求在本机执行操作时才调用" in prompt


def test_system_prompt_delivery_standards():
    """验证系统提示词中通用、纯净、优雅的工业交付标准。"""
    bot = {"id": "bot_chief", "name": "总管", "persona": "你是一个可靠的 AI 助手。"}
    prompt = agent_loop._system_prompt(bot)

    # 交付与表达准则
    assert "【交付与表达准则】" in prompt

    # 结构清晰
    assert "结构清晰" in prompt
    assert "清晰工整的 Markdown 组织" in prompt

    # 结论先行
    assert "结论先行" in prompt
    assert "先给明确成果与核心结论" in prompt

    # 辅助可视化
    assert "辅助可视化" in prompt
    assert "render_chart" in prompt
    assert "render_diagram" in prompt

    # 务实闭环
    assert "务实闭环" in prompt
    assert "能动手执行验证的绝不只讲空话" in prompt


def test_system_prompt_with_members_and_memories():
    """验证带有团队成员和长期记忆时，prompt 正确拼接所有部分。"""
    bot = {"id": "bot_1", "name": "小万", "persona": "你是主管"}
    members = [
        {"id": "bot_1", "name": "小万", "persona": "你是主管"},
        {"id": "bot_2", "name": "小图", "persona": "你是制图专家"},
    ]
    memories = [
        {"content": "用户偏好图文并茂的分析报告"},
        {"content": "关注架构设计与系统可靠性"},
    ]
    prompt = agent_loop._system_prompt(bot, members=members, memories=memories)

    # 基础信息
    assert "你是主管" in prompt
    assert "团队（同一会话里的其他 Bot）：小图（你是制图专家）" in prompt
    assert "用户偏好图文并茂的分析报告" in prompt
    assert "关注架构设计与系统可靠性" in prompt

    # 依然具备云端优先和图表交付准则
    assert "云端服务器直接闭环执行" in prompt
    assert "render_chart" in prompt
    assert "local_*" in prompt
