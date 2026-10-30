"""Tests for Grok benchmark tools: Social Sentiment Pulse & AI Visual Image Generation."""
from __future__ import annotations

import io
import json
import os
import pathlib
import pytest
from unittest.mock import patch, MagicMock

from PIL import Image

from app.agent import image_tools, social_tools, tools


# ==============================================================================
# 1. Social Pulse Tests (app/agent/social_tools.py)
# ==============================================================================

class TestSocialPulse:
    """测试社交情绪脉搏模块及其弹性容错机制。"""

    @pytest.mark.parametrize("platform", ["all", "weibo", "zhihu", "baidu", "finance"])
    def test_fetch_social_pulse_platforms(self, platform: str):
        """测试各平台参数调用及基础数据结构完备性。"""
        res = social_tools.fetch_social_pulse(platform=platform, limit=4)
        assert res["ok"] is True
        assert res["platform"] == platform
        assert res["count"] > 0
        assert isinstance(res["items"], list)
        assert len(res["items"]) <= 4 or platform == "all"
        assert isinstance(res["markdown_table"], str)
        assert "| 序号 | 平台 |" in res["markdown_table"]
        assert isinstance(res["sentiment_hint"], str)
        assert len(res["sentiment_hint"]) > 10

        # 校验条目结构
        first = res["items"][0]
        assert "platform" in first
        assert "rank" in first
        assert "title" in first
        assert "heat" in first
        assert "url" in first

    def test_fetch_social_pulse_custom_limit(self):
        """测试条数限制与边界值处理。"""
        res_small = social_tools.fetch_social_pulse("weibo", limit=2)
        assert res_small["ok"] is True
        assert len(res_small["items"]) == 2

        # 测试超出边界自动裁剪
        res_bound = social_tools.fetch_social_pulse("weibo", limit=999)
        assert res_bound["ok"] is True
        assert len(res_bound["items"]) <= 50

    def test_fetch_social_pulse_invalid_platform_fallback(self):
        """测试非预期平台参数自动回退到 'all'。"""
        res = social_tools.fetch_social_pulse("unknown_platform_xyz", limit=5)
        assert res["ok"] is True
        assert res["platform"] == "all"
        assert res["count"] > 0

    def test_fetch_social_pulse_offline_fallback(self):
        """测试完全离线/网络故障时的优雅降级与兜底模拟保护。"""
        with patch("httpx.Client.get", side_effect=Exception("Network Unreachable")):
            for p in ["weibo", "zhihu", "baidu", "finance", "all"]:
                res = social_tools.fetch_social_pulse(platform=p, limit=3)
                assert res["ok"] is True
                assert res["count"] > 0
                assert any(it.get("fallback") is True for it in res["items"])
                assert "离线降级" in res["sentiment_hint"] or "基准模式" in res["sentiment_hint"] or "系统容错" in res["sentiment_hint"]

    def test_fetch_social_pulse_http_error_recovery(self):
        """测试上游接口返回 500/403 时的弹性恢复。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch("httpx.Client.get", return_value=mock_resp):
            res = social_tools.fetch_social_pulse("weibo", limit=3)
            assert res["ok"] is True
            assert res["count"] > 0
            assert res["items"][0]["fallback"] is True

    def test_format_markdown_table_empty(self):
        """测试空列表的表格输出。"""
        table = social_tools._format_markdown_table([])
        assert "暂无热点数据" in table


# ==============================================================================
# 2. Image Tools Tests (app/agent/image_tools.py)
# ==============================================================================

class TestImageTools:
    """测试 AI 视觉生图模块（双模管道、高雅降级绘制、分辨率与产物落盘）。"""

    @pytest.mark.parametrize("style", ["cyberpunk", "photorealistic", "illustration", "minimalist_infographic"])
    def test_generate_image_offline_card_styles(self, style: str, tmp_path: pathlib.Path):
        """测试四种核心风格的离线降级高保真卡片绘制与返回结构。"""
        res = image_tools.generate_image_tool(
            prompt="未来量子数据中心与全息流体架构",
            title=f"测试作品_{style}",
            style=style,
            aspect_ratio="16:9",
            workspace_path=str(tmp_path),
        )
        assert res["ok"] is True
        assert res["artifact_id"].startswith("art_")
        assert res["url"].startswith("/v1/artifacts/")
        assert res["style"] == style
        assert f"![测试作品_{style}]" in res["markdown"]
        assert res["title"] == f"测试作品_{style}"

        # 验证临时工作区中文件已落地且可读取
        art_path = tmp_path / "artifacts" / f"{res['artifact_id']}.png"
        assert art_path.exists()
        img = Image.open(art_path)
        assert img.format == "PNG"
        assert img.size == (1200, 675)

    @pytest.mark.parametrize("ratio,expected_dim", [
        ("16:9", (1200, 675)),
        ("1:1", (1024, 1024)),
        ("4:3", (1200, 900)),
        ("9:16", (675, 1200)),
    ])
    def test_generate_image_aspect_ratios(self, ratio: str, expected_dim: tuple[int, int], tmp_path: pathlib.Path):
        """测试多宽高比（16:9, 1:1, 4:3, 9:16）的分辨率精准输出。"""
        res = image_tools.generate_image_tool(
            prompt="高精度几何构图测试",
            style="minimalist_infographic",
            aspect_ratio=ratio,
            workspace_path=str(tmp_path),
        )
        assert res["ok"] is True
        art_path = tmp_path / "artifacts" / f"{res['artifact_id']}.png"
        assert art_path.exists()
        img = Image.open(art_path)
        assert img.size == expected_dim

    def test_generate_image_auto_title_and_prompt_truncation(self, tmp_path: pathlib.Path):
        """测试标题自动推断与超长提示词优雅换行。"""
        long_prompt = "这是一段非常冗长的画面描述，" * 15
        res = image_tools.generate_image_tool(
            prompt=long_prompt,
            title="",  # 留空以触发自提示词推断
            workspace_path=str(tmp_path),
        )
        assert res["ok"] is True
        assert res["title"] != ""
        assert len(res["title"]) <= 30

    def test_generate_image_empty_prompt_rejected(self):
        """测试空 prompt 的参数校验防御。"""
        res = image_tools.generate_image_tool(prompt="")
        assert res["ok"] is False
        assert "prompt 不能为空" in res["error"]

    def test_generate_image_pro_api_mode_success(self, tmp_path: pathlib.Path):
        """测试 Pro API 模式在有 Key 时的请求分发与 Base64 解码。"""
        # 生成一张 64x64 纯色测试图并转为 base64
        dummy_img = Image.new("RGB", (64, 64), (100, 150, 200))
        buf = io.BytesIO()
        dummy_img.save(buf, format="PNG")
        dummy_b64 = buf.getvalue().hex()
        import base64
        dummy_b64_str = base64.b64encode(buf.getvalue()).decode("ascii")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"b64_json": dummy_b64_str}]
        }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-openai-key-12345"}):
            with patch("httpx.Client.post", return_value=mock_resp):
                res = image_tools.generate_image_tool(
                    prompt="宇宙超新星爆发",
                    title="超新星爆发",
                    workspace_path=str(tmp_path),
                )
                assert res["ok"] is True
                assert res["artifact_id"].startswith("art_")
                art_path = tmp_path / "artifacts" / f"{res['artifact_id']}.png"
                assert art_path.exists()
                saved_img = Image.open(art_path)
                assert saved_img.size == (64, 64)

    def test_generate_image_pro_api_failure_fallback_to_offline(self, tmp_path: pathlib.Path):
        """测试 Pro API 报错时无缝退回离线高雅绘制模式。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sk-invalid-silicon-key"}):
            with patch("httpx.Client.post", return_value=mock_resp):
                res = image_tools.generate_image_tool(
                    prompt="深海发光水母生态",
                    title="深海水母",
                    workspace_path=str(tmp_path),
                )
                assert res["ok"] is True
                assert res["artifact_id"].startswith("art_")
                art_path = tmp_path / "artifacts" / f"{res['artifact_id']}.png"
                assert art_path.exists()
                saved_img = Image.open(art_path)
                assert saved_img.size == (1200, 675)


# ==============================================================================
# 3. AgentOS Dispatcher Integration Tests (app/agent/tools.py)
# ==============================================================================

class TestAgentDispatcherIntegration:
    """测试通过 AgentOS 核心 tools.dispatch 调度新工具。"""

    def test_dispatch_fetch_social_pulse(self):
        """验证 tools.dispatch 对 fetch_social_pulse 的调度解析。"""
        res = tools.dispatch("fetch_social_pulse", {"platform": "finance", "limit": 3})
        assert res["ok"] is True
        assert res["platform"] == "finance"
        assert res["count"] > 0

    def test_dispatch_generate_image(self, tmp_path: pathlib.Path):
        """验证 tools.dispatch 对 generate_image 的调度解析。"""
        res = tools.dispatch("generate_image", {
            "prompt": "火星移民基地全息控制台",
            "title": "火星控制台",
            "style": "cyberpunk",
            "aspect_ratio": "16:9",
            "workspace_path": str(tmp_path),
        })
        assert res["ok"] is True
        assert res["artifact_id"].startswith("art_")
        assert "![火星控制台]" in res["markdown"]
