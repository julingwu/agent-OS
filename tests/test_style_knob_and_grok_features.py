"""Tests for Grok benchmark features: Dynamic Style Knob and Grok tools integration."""
from __future__ import annotations

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from app.agent import blackboard, loop, tools


# ==============================================================================
# 1. Dynamic Style Knob: Detection and Text Parsing Tests
# ==============================================================================

class TestStyleKnobDetection:
    """测试动态风格旋钮对用户输入指令、中英文标签的智能检测与清洗。"""

    @pytest.mark.parametrize("text,expected", [
        ("/fun 分析一下这个架构", "fun"),
        ("/strict 审查这段生产代码", "strict"),
        ("/insight 评估低空经济投资可行性", "insight"),
        ("/grok 怎么看待这次技术突破", "fun"),
        ("/general 帮我写个邮件", "general"),
        ("/balanced 保持平衡模式", "general"),
        ("  /fun   前后有空格的输入", "fun"),
        ("/FUN 大写前缀支持", "fun"),
    ])
    def test_prefix_commands(self, text: str, expected: str):
        assert loop.detect_style_mode(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("请评估新能源出海机会 [style:insight]", "insight"),
        ("【style:strict】量化测算估值模型", "strict"),
        ("吐槽一下公关通稿 [style:fun]", "fun"),
        ("怎么看待这个方案 [style:grok]", "fun"),
        ("请处理这个日常事务 [style:general]", "general"),
        ("【style:balanced】日常回复", "general"),
        ("支持等号标签 [style=strict]", "strict"),
    ])
    def test_bracket_tags(self, text: str, expected: str):
        assert loop.detect_style_mode(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("风格:通用 帮忙润色这封通知", "general"),
        ("风格:自然 轻松聊聊", "general"),
        ("风格:平衡 回应日常问询", "general"),
        ("风格:诙谐 评价一下现在的行业黑话", "fun"),
        ("风格：诙谐 中文全角冒号测试", "fun"),
        ("风格:幽默 讲讲这个技术的槽点", "fun"),
        ("风格:犀利 一针见血指出问题", "fun"),
        ("风格:严谨 推导算法时空复杂度", "strict"),
        ("风格：严谨 中文全角严谨", "strict"),
        ("风格:极客 零废话给出最佳实践", "strict"),
        ("风格:洞察 拆解商业模式与护城河", "insight"),
        ("风格：洞察 测算三种市场情景", "insight"),
        ("风格:智囊 给出高管决策建议", "insight"),
        ("【风格:诙谐】带括号的中文风格标记", "fun"),
    ])
    def test_chinese_style_tags(self, text: str, expected: str):
        assert loop.detect_style_mode(text) == expected

    def test_no_style_mode_returns_none(self):
        assert loop.detect_style_mode("你好，请帮我写一段 Python 代码") is None
        assert loop.detect_style_mode("") is None
        assert loop.detect_style_mode(None) is None

    def test_strip_style_command(self):
        cleaned = loop.strip_style_command("/fun 分析一下当前大模型竞争格局")
        assert cleaned == "分析一下当前大模型竞争格局"

        cleaned_tag = loop.strip_style_command("评估宏观经济走势 [style:insight]")
        assert cleaned_tag == "评估宏观经济走势"

        cleaned_cn = loop.strip_style_command("风格:严谨 请给出准确量化数据")
        assert cleaned_cn == "请给出准确量化数据"


# ==============================================================================
# 2. Blackboard Style Mode Persistence Tests
# ==============================================================================

class TestBlackboardStylePersistence:
    """测试风格模式在 Hermes Session Blackboard 中的跨轮次持久化与会话记忆。"""

    @pytest.fixture
    def db_conn(self, tmp_path, monkeypatch):
        bb_dir = tmp_path / "blackboard"
        bb_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(blackboard, "BLACKBOARD_DIR", bb_dir)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        blackboard.init_blackboard_schema(conn)
        yield conn
        conn.close()

    def test_blackboard_save_and_get_style_mode(self, db_conn):
        session_id = "test_sess_001"

        # 初始状态
        board0 = blackboard.get_blackboard(db_conn, session_id)
        assert board0["style_mode"] == ""

        # 持久化为 fun 模式
        blackboard.save_blackboard(db_conn, session_id, style_mode="fun")
        board1 = blackboard.get_blackboard(db_conn, session_id)
        assert board1["style_mode"] == "fun"

        # 切换持久化为 strict 模式
        blackboard.save_blackboard(db_conn, session_id, style_mode="strict")
        board2 = blackboard.get_blackboard(db_conn, session_id)
        assert board2["style_mode"] == "strict"

    def test_session_style_inheritance_across_turns(self, db_conn):
        """测试在无新指令时自动沿用 Blackboard 中已持久化的会话风格。"""
        session_id = "test_sess_inherit"

        # 第一轮用户显式发送 /fun 指令
        user_text_round1 = "/fun 帮我写个段子"
        style_r1 = loop.detect_style_mode(user_text_round1)
        assert style_r1 == "fun"
        blackboard.save_blackboard(db_conn, session_id, style_mode=style_r1)

        # 第二轮用户普通提问，未包含任何风格指令
        user_text_round2 = "然后再给配个标题"
        detected_r2 = loop.detect_style_mode(user_text_round2)
        assert detected_r2 is None

        # 从 blackboard 读取上一轮风格，确保风格状态连贯一致
        board_r2 = blackboard.get_blackboard(db_conn, session_id)
        effective_style_r2 = detected_r2 or board_r2.get("style_mode") or "insight"
        assert effective_style_r2 == "fun"


# ==============================================================================
# 3. System Prompt Style Injection & Temperature Linking Tests
# ==============================================================================

class TestPromptInjectionAndTemperature:
    """测试系统提示词动态风格指南注入与模型温度参数联动。"""

    def test_system_prompt_injection_general(self):
        bot = {"id": "b0", "name": "General", "persona": "日常助手"}
        prompt = loop._system_prompt(bot, style_mode="general")
        assert "自然通用" in prompt
        assert "保持自然友好、务实清晰、重点突出" in prompt

    def test_system_prompt_injection_strict(self):
        bot = {"id": "b1", "name": "Geek", "persona": "极客工程师"}
        prompt = loop._system_prompt(bot, style_mode="strict")
        assert "极客严谨模式" in prompt
        assert "第一性原理与零套话废话" in prompt
        assert "量化精确口径与直面不确定性" in prompt
        assert "代码生产级标准" in prompt

    def test_system_prompt_injection_insight(self):
        bot = {"id": "b2", "name": "Partner", "persona": "战略智囊"}
        prompt = loop._system_prompt(bot, style_mode="insight")
        assert "洞察智囊模式" in prompt
        assert "合伙人/CEO/首席研究员视角" in prompt
        assert "深度推演与权衡分析" in prompt
        assert "风险对冲与落地动作" in prompt

    def test_system_prompt_injection_fun(self):
        bot = {"id": "b3", "name": "Grok", "persona": "幽默伙伴"}
        prompt = loop._system_prompt(bot, style_mode="fun")
        assert "诙谐犀利模式" in prompt
        assert "机智幽默与犀利表达" in prompt
        assert "反讽戏谑" in prompt
        assert "公关黑话" in prompt
        assert "金句" in prompt
        assert "底层逻辑与技术事实 100% 硬核严谨" in prompt

    def test_system_prompt_none_style_mode(self):
        bot = {"id": "b4", "name": "Normal", "persona": "助手"}
        prompt = loop._system_prompt(bot, style_mode=None)
        assert "当前交互风格" not in prompt

    def test_temperature_linking(self):
        """测试采样温度联动匹配：strict 0.2 / insight 0.4 / fun 0.7 / general 0.4。"""
        assert loop.get_style_temperature("strict") == 0.2
        assert loop.get_style_temperature("insight") == 0.4
        assert loop.get_style_temperature("fun") == 0.7
        assert loop.get_style_temperature("grok") == 0.7
        assert loop.get_style_temperature("general") == 0.4
        assert loop.get_style_temperature("balanced") == 0.4
        assert loop.get_style_temperature(None) == 0.4


# ==============================================================================
# 4. Tool Mounting by Intent Keywords & Bot Roles Tests
# ==============================================================================

class TestToolMountingAndEffectiveSpecs:
    """测试 tools.get_effective_tool_specs 在意图关键词与角色触发下的精准挂载。"""

    @pytest.mark.parametrize("kw", [
        "调研", "研报", "深研", "尽调", "研究", "deep research", "deep search"
    ])
    def test_deep_research_keyword_activation(self, kw: str):
        user_input = f"请针对人形机器人执行深度{kw}"
        specs = tools.get_effective_tool_specs(user_text=user_input)
        names = {s["function"]["name"] for s in specs}
        assert "deep_research" in names

    @pytest.mark.parametrize("kw", [
        "热搜", "热榜", "热点", "微博", "知乎", "趋势", "社交情绪", "trending"
    ])
    def test_social_pulse_keyword_activation(self, kw: str):
        user_input = f"帮我看看全网现在的{kw}情况"
        specs = tools.get_effective_tool_specs(user_text=user_input)
        names = {s["function"]["name"] for s in specs}
        assert "social_pulse" in names or "fetch_social_pulse" in names

    @pytest.mark.parametrize("kw", [
        "生图", "画图", "作图", "插画", "配图", "海报", "generate image", "flux", "dall-e"
    ])
    def test_generate_image_keyword_activation(self, kw: str):
        user_input = f"帮我用{kw}绘制一张科技感封面"
        specs = tools.get_effective_tool_specs(user_text=user_input)
        names = {s["function"]["name"] for s in specs}
        assert "generate_image" in names

    def test_bot_role_analyst_mounts_grok_tools(self):
        """分析师角色自动挂载 deep_research、social_pulse 和 generate_image。"""
        bot = {"id": "bot_analyst", "name": "资深行业分析师", "persona": "负责宏观与产业分析"}
        specs = tools.get_effective_tool_specs(bot=bot)
        names = {s["function"]["name"] for s in specs}
        assert "deep_research" in names
        assert "social_pulse" in names
        assert "generate_image" in names

    def test_bot_role_director_mounts_visual_tools(self):
        """导演角色自动挂载 generate_image。"""
        bot = {"id": "bot_director", "name": "短剧导演", "persona": "负责视觉分镜与分镜生图"}
        specs = tools.get_effective_tool_specs(bot=bot)
        names = {s["function"]["name"] for s in specs}
        assert "generate_image" in names

    def test_bot_role_media_creator_mounts_research_and_visual(self):
        """自媒体角色自动挂载研报、热搜热点与生图工具。"""
        bot = {"id": "bot_media", "name": "科技自媒体主理人", "role": "自媒体创作者"}
        specs = tools.get_effective_tool_specs(bot=bot)
        names = {s["function"]["name"] for s in specs}
        assert "deep_research" in names
        assert "social_pulse" in names
        assert "generate_image" in names


# ==============================================================================
# 5. Tool Dispatcher Aliases Verification
# ==============================================================================

class TestToolDispatchAliases:
    """测试 tools.dispatch 对工具别名的完整支持。"""

    def test_dispatch_deep_search_alias(self):
        with patch("app.agent.deep_research.run_deep_research", return_value={"ok": True, "report": "test"}):
            res1 = tools.dispatch("deep_research", {"topic": "AI芯片"})
            res2 = tools.dispatch("deep_search", {"topic": "AI芯片"})
            assert res1["ok"] is True
            assert res2["ok"] is True

    def test_dispatch_social_pulse_alias(self):
        with patch("app.agent.social_tools.fetch_social_pulse", return_value={"ok": True, "items": []}):
            res1 = tools.dispatch("fetch_social_pulse", {"platform": "all"})
            res2 = tools.dispatch("social_pulse", {"platform": "all"})
            assert res1["ok"] is True
            assert res2["ok"] is True
