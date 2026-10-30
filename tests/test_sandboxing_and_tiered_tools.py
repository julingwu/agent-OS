"""Tests for G3 execution sandboxing, workspace jailing, environment sanitization,
and G4 tiered tool schema dynamic lazy loading.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import pytest

from app.agent import tools, ext_tools, loop as agent_loop
from app import db, processes


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    return ws


# ================================================================
# 1. G3: 沙箱安全隔离与工作区防逃逸测试 (Workspace Jailing)
# ================================================================

def test_workspace_jail_valid_paths(workspace):
    """合法路径与子目录应当正常解析。"""
    sub = workspace / "sub" / "child"
    sub.mkdir(parents=True, exist_ok=True)

    assert tools._jail(".") == workspace.resolve()
    assert tools._jail("sub") == (workspace / "sub").resolve()
    assert tools._jail("sub/child") == sub.resolve()
    assert tools._jail(None) == workspace.resolve()
    assert tools._jail("") == workspace.resolve()


def test_workspace_jail_escape_attempts(workspace):
    """试图通过 ..、系统绝对路径或 NUL 字节逃逸应被严格拦截。"""
    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools._jail("..")

    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools._jail("../../")

    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools._jail("../../etc/passwd")

    if sys.platform == "win32":
        with pytest.raises(tools.ToolError, match="escapes workspace"):
            tools._jail("C:\\Windows")
        with pytest.raises(tools.ToolError, match="escapes workspace"):
            tools._jail("C:\\")
    else:
        with pytest.raises(tools.ToolError, match="escapes workspace"):
            tools._jail("/etc/passwd")
        with pytest.raises(tools.ToolError, match="escapes workspace"):
            tools._jail("/var/log")

    with pytest.raises(tools.ToolError, match="NUL byte"):
        tools._jail("sub\x00escape")


def test_exec_cwd_jail_enforcement(workspace):
    """_exec 工具调用如果传入越界 cwd 必须直接拒绝。"""
    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools._exec("echo 1", cwd="../")

    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools.dispatch("exec", {"cmd": "echo 1", "cwd": "../../"})


def test_process_start_cwd_jail_enforcement(workspace):
    """process_start 试图向越界 cwd 启动后台进程必须被拦截。"""
    with pytest.raises(tools.ToolError, match="escapes workspace"):
        tools.dispatch("process_start", {
            "cmd": f'"{sys.executable}" -c "print(1)"',
            "cwd": "../../",
        })


# ================================================================
# 2. G3: 敏感环境变量脱敏与剥离测试 (Environment Sanitization)
# ================================================================

def test_sanitize_env_strips_sensitive_credentials():
    """单元测试 sanitize_env：全面清洗 API 密钥、Tokens 和 Secrets。"""
    raw_env = {
        "DEEPSEEK_API_KEY": "sk-deepseek-123",
        "OPENAI_API_KEY": "sk-openai-456",
        "TAVILY_API_KEY": "tvly-789",
        "BOCHA_API_KEY": "bocha-abc",
        "SERPER_API_KEY": "serper-def",
        "AGENT_SECRET": "sec-000",
        "M0_AGENT_SECRET": "sec-111",
        "ANTHROPIC_API_KEY": "sk-ant-222",
        "GEMINI_API_KEY": "ai-gemini-333",
        "CUSTOM_APP_SECRET": "my-secret-token",
        "SOME_THIRD_PARTY_API_KEY": "tp-999",
        "USER_ACCESS_TOKEN": "uat-888",
        "SAFE_PATH": "/usr/local/bin",
        "PATH": "C:\\Windows;C:\\Python",
        "PROJECT_ENV": "production",
    }

    clean = tools.sanitize_env(raw_env)

    # 验证敏感凭据全量剥离
    for sensitive_key in [
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY",
        "BOCHA_API_KEY", "SERPER_API_KEY", "AGENT_SECRET",
        "M0_AGENT_SECRET", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "CUSTOM_APP_SECRET", "SOME_THIRD_PARTY_API_KEY", "USER_ACCESS_TOKEN",
    ]:
        assert sensitive_key not in clean, f"{sensitive_key} was not sanitized"

    # 验证安全配置与系统路径保留
    assert clean["SAFE_PATH"] == "/usr/local/bin"
    assert clean["PATH"] == "C:\\Windows;C:\\Python"
    assert clean["PROJECT_ENV"] == "production"


def test_exec_child_process_does_not_leak_secrets(workspace, monkeypatch):
    """端到端验证：子进程中执行真实 python 脚本，无法获取宿主服务器敏感凭据。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-sensitive-leak-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-another-secret")
    monkeypatch.setenv("AGENT_SECRET", "top-secret-val")
    monkeypatch.setenv("SAFE_AGENT_MODE", "enabled")

    leak_py = workspace / "check_leak.py"
    leak_py.write_text(
        'import os\n'
        'leaked = []\n'
        'for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AGENT_SECRET"):\n'
        '    if k in os.environ:\n'
        '        leaked.append(k)\n'
        'safe_ok = os.environ.get("SAFE_AGENT_MODE") == "enabled"\n'
        'print("LEAKED:" + ",".join(leaked) + "|SAFE:" + str(safe_ok))\n',
        encoding="utf-8"
    )
    cmd = f'"{sys.executable}" check_leak.py'

    res = tools.dispatch("exec", {"cmd": cmd})
    assert res["ok"] is True
    assert "LEAKED:|SAFE:True" in res["stdout"]


# ================================================================
# 3. G4: 工具 Schema 动态分级懒加载测试 (Tiered Lazy Loading)
# ================================================================

def test_default_tool_specs_tiering():
    """默认/缺省状态下只加载核心工具，大幅缩减 Prompt Token 体积。"""
    all_specs = tools.TOOL_SPECS
    effective_specs = tools.get_effective_tool_specs()

    all_names = {s["function"]["name"] for s in all_specs}
    effective_names = {s["function"]["name"] for s in effective_specs}

    # 1. 核心基础工具必须全量包含
    for core_tool in tools.CORE_TOOL_NAMES:
        assert core_tool in effective_names, f"Core tool {core_tool} missing from effective specs"

    # 2. 领域工具（图表、浏览器交互、本机桌面等）在默认状态下绝不加载
    assert "render_chart" not in effective_names
    assert "render_diagram" not in effective_names
    assert "browser_open" not in effective_names
    assert "browser_interact" not in effective_names
    assert "process_start" not in effective_names
    assert "code_references" not in effective_names
    assert "local_screenshot" not in effective_names

    # 3. Schema 数量缩减比例超过 50%
    reduction_ratio = 1.0 - (len(effective_specs) / len(all_specs))
    assert reduction_ratio >= 0.50, f"Expected >= 50% reduction, got {reduction_ratio:.1%}"


def test_keyword_dynamic_activation():
    """用户输入文本中的意图关键词能即时挂载对应领域工具包。"""
    # 命中 VISUAL
    v_specs = tools.get_effective_tool_specs(user_text="请绘制营收走势图表并对比")
    v_names = {s["function"]["name"] for s in v_specs}
    assert "render_chart" in v_names
    assert "render_diagram" in v_names
    assert "publish_artifact" in v_names
    assert "browser_interact" not in v_names

    # 命中 BROWSER
    b_specs = tools.get_effective_tool_specs(user_text="请打开浏览器进行网页登录和交互")
    b_names = {s["function"]["name"] for s in b_specs}
    assert "browser_open" in b_names
    assert "browser_interact" in b_names
    assert "render_chart" not in b_names

    # 命中 PROCESS
    p_specs = tools.get_effective_tool_specs(user_text="启动后台守护进程并查看实时日志")
    p_names = {s["function"]["name"] for s in p_specs}
    assert "process_start" in p_names
    assert "process_list" in p_names
    assert "process_logs" in p_names

    # 命中 CODE
    c_specs = tools.get_effective_tool_specs(user_text="检索符号调用点与引用")
    c_names = {s["function"]["name"] for s in c_specs}
    assert "code_references" in c_names
    assert "glob_search" in c_names
    assert "grep_search" in c_names

    # 命中 LOCAL_DESKTOP
    l_specs = tools.get_effective_tool_specs(user_text="在我电脑上操作本地微信窗口")
    l_names = {s["function"]["name"] for s in l_specs}
    assert "local_screenshot" in l_names
    assert "local_windows" in l_names


def test_bot_role_preset_activation():
    """根据 Bot 角色/设定自动挂载领域工具包。"""
    # 数据分析师 / 架构师 -> 挂载 VISUAL
    quant_bot = {"name": "量化研究员", "tag": "data", "prompt": "量化策略与因子分析"}
    q_specs = tools.get_effective_tool_specs(bot=quant_bot)
    q_names = {s["function"]["name"] for s in q_specs}
    assert "render_chart" in q_names
    assert "render_diagram" in q_names

    # 开发工程师 -> 挂载 CODE 与 PROCESS
    dev_bot = {"name": "后端开发工程师", "tag": "dev", "prompt": "微服务与接口实现"}
    d_specs = tools.get_effective_tool_specs(bot=dev_bot)
    d_names = {s["function"]["name"] for s in d_specs}
    assert "code_references" in d_names
    assert "process_start" in d_names


def test_active_tools_retention():
    """多步交互中，上一轮调用过的工具必须保持常驻。"""
    active = ["browser_interact", "render_chart"]
    specs = tools.get_effective_tool_specs(user_text="好的，接着做下一步", active_tools=active)
    names = {s["function"]["name"] for s in specs}
    assert "browser_interact" in names
    assert "render_chart" in names
    assert "fs_read" in names
