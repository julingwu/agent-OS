"""Tests for app.agent.code_tools: code_outline, code_symbols, and code_references."""
from __future__ import annotations

import json
import os
import pathlib
import pytest

from app.agent import code_tools, tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    return ws


# =============================================================================
# 1. code_outline: Python AST & Docstrings
# =============================================================================

def test_code_outline_python(workspace):
    py_file = workspace / "service.py"
    py_code = '''"""Service layer module."""
from typing import Optional

@dataclass
class SessionBlackboard:
    """会话级黑板状态数据结构。"""
    session_id: str

    @property
    def is_active(self) -> bool:
        """检查会话活跃状态。"""
        return True

    def update(self, key: str, value: str = "") -> None:
        """更新黑板状态条目。"""
        pass

@audit_log
def render_chart(title: str, chart_type: str = "bar") -> dict:
    """渲染研报图表。"""
    return {"title": title, "type": chart_type}

async def _compact_context(messages: list) -> list:
    """压缩历史会话上下文。"""
    return messages[:5]
'''
    py_file.write_text(py_code, encoding="utf-8")

    # Depth 2 (default)
    res = code_tools.code_outline("service.py", max_depth=2)
    assert res["ok"] is True
    assert res["language"] == "python"
    assert res["total_lines"] == len(py_code.splitlines())

    symbols = res["symbols"]
    names = [s["name"] for s in symbols]
    assert "SessionBlackboard" in names
    assert "render_chart" in names
    assert "_compact_context" in names

    # Check Class and nested methods
    cls_sym = next(s for s in symbols if s["name"] == "SessionBlackboard")
    assert cls_sym["kind"] == "class"
    assert cls_sym["docstring"] == "会话级黑板状态数据结构。"
    assert "@dataclass" in cls_sym["decorators"]
    assert cls_sym["start_line"] == 5
    assert cls_sym["end_line"] >= 16

    child_names = [c["name"] for c in cls_sym["children"]]
    assert "is_active" in child_names
    assert "update" in child_names

    is_active_method = next(c for c in cls_sym["children"] if c["name"] == "is_active")
    assert "@property" in is_active_method["decorators"]
    assert is_active_method["docstring"] == "检查会话活跃状态。"

    # Check async function
    compact_sym = next(s for s in symbols if s["name"] == "_compact_context")
    assert compact_sym["kind"] == "async_function"
    assert compact_sym["is_async"] is True
    assert compact_sym["docstring"] == "压缩历史会话上下文。"
    assert "async def _compact_context" in compact_sym["signature"]

    # Check Markdown outline
    md = res["markdown"]
    assert "# Outline: `service.py`" in md
    assert "SessionBlackboard" in md
    assert "render_chart" in md
    assert "*\"会话级黑板状态数据结构。\"*" in md


def test_code_outline_max_depth(workspace):
    py_file = workspace / "nested.py"
    py_file.write_text(
        "class Outer:\n"
        "    def method(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    # At depth 1, class methods should not be parsed into children
    res1 = code_tools.code_outline("nested.py", max_depth=1)
    assert res1["ok"] is True
    assert len(res1["symbols"][0]["children"]) == 0

    # At depth 2, class methods are included
    res2 = code_tools.code_outline("nested.py", max_depth=2)
    assert res2["ok"] is True
    assert len(res2["symbols"][0]["children"]) == 1
    assert res2["symbols"][0]["children"][0]["name"] == "method"


def test_code_outline_syntax_error_fallback(workspace):
    broken_file = workspace / "broken.py"
    broken_file.write_text(
        "# Broken Python syntax\n"
        "class ResilientParser:\n"
        "    def calculate(self, x):\n"
        "        return (x + 1   # unclosed paren\n"
        "def helper_func():\n"
        "    pass\n",
        encoding="utf-8",
    )

    res = code_tools.code_outline("broken.py")
    assert res["ok"] is True
    assert res["language"] == "python"
    names = [s["name"] for s in res["symbols"]]
    assert "ResilientParser" in names
    assert "helper_func" in names


# =============================================================================
# 2. code_outline: Other Languages (JS/TS, Go, Java, Rust)
# =============================================================================

def test_code_outline_typescript(workspace):
    ts_file = workspace / "user.ts"
    ts_code = '''/**
 * User interface configuration.
 */
export interface UserConfig {
    id: string;
    name: string;
}

export type UserId = string;

/**
 * Main User manager service.
 */
export class UserManager {
    private cache: Map<string, any>;

    /**
     * Find user by id.
     */
    async getUser(id: string): Promise<UserConfig> {
        return { id, name: "test" };
    }
}

/**
 * Format user display label.
 */
export async function formatUser(config: UserConfig): Promise<string> {
    return config.name;
}

export const helper = (x: number) => x * 2;
'''
    ts_file.write_text(ts_code, encoding="utf-8")

    res = code_tools.code_outline("user.ts")
    assert res["ok"] is True
    assert res["language"] == "typescript"

    syms = {s["name"]: s for s in res["symbols"]}
    assert "UserConfig" in syms
    assert syms["UserConfig"]["kind"] == "interface"
    assert syms["UserConfig"]["docstring"] == "User interface configuration."

    assert "UserId" in syms
    assert syms["UserId"]["kind"] == "type"

    assert "UserManager" in syms
    assert syms["UserManager"]["kind"] == "class"
    assert syms["UserManager"]["docstring"] == "Main User manager service."
    # Check method inside UserManager
    assert len(syms["UserManager"]["children"]) >= 1
    m = syms["UserManager"]["children"][0]
    assert m["name"] == "getUser"
    assert m["is_async"] is True
    assert m["docstring"] == "Find user by id."

    assert "formatUser" in syms
    assert syms["formatUser"]["is_async"] is True
    assert syms["formatUser"]["docstring"] == "Format user display label."


def test_code_outline_go_and_rust(workspace):
    go_file = workspace / "server.go"
    go_file.write_text(
        "package main\n\n"
        "// Server configuration\n"
        "type Server struct {\n"
        "    Port int\n"
        "}\n\n"
        "// Start initiates listening\n"
        "func (s *Server) Start() error {\n"
        "    return nil\n"
        "}\n\n"
        "func NewServer() *Server {\n"
        "    return &Server{}\n"
        "}\n",
        encoding="utf-8",
    )

    res_go = code_tools.code_outline("server.go")
    assert res_go["ok"] is True
    assert res_go["language"] == "go"
    go_names = [s["name"] for s in res_go["symbols"]]
    assert "Server" in go_names
    assert "Start" in go_names
    assert "NewServer" in go_names

    # Rust
    rs_file = workspace / "lib.rs"
    rs_file.write_text(
        "/// Client pool struct\n"
        "pub struct ClientPool {\n"
        "    size: usize,\n"
        "}\n\n"
        "/// Connect to remote service\n"
        "pub async fn connect() -> Result<(), ()> {\n"
        "    Ok(())\n"
        "}\n",
        encoding="utf-8",
    )

    res_rs = code_tools.code_outline("lib.rs")
    assert res_rs["ok"] is True
    assert res_rs["language"] == "rust"
    rs_names = [s["name"] for s in res_rs["symbols"]]
    assert "ClientPool" in rs_names
    assert "connect" in rs_names


# =============================================================================
# 3. code_symbols: Multi-file Indexing & Fuzzy Search
# =============================================================================

def test_code_symbols_multi_file_and_fuzzy(workspace):
    # File 1: Python blackboard
    (workspace / "app").mkdir(parents=True, exist_ok=True)
    (workspace / "app" / "blackboard.py").write_text(
        "class SessionBlackboard:\n"
        "    def update(self, key: str, value: str) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )

    # File 2: Python loop
    (workspace / "app" / "loop.py").write_text(
        "def _compact_context(messages: list) -> list:\n"
        "    return messages\n"
        "def agent_loop():\n"
        "    pass\n",
        encoding="utf-8",
    )

    # File 3: Python tools
    (workspace / "app" / "tools.py").write_text(
        "def render_chart(title: str) -> dict:\n"
        "    return {}\n"
        "def render_diagram(title: str) -> dict:\n"
        "    return {}\n",
        encoding="utf-8",
    )

    # File 4: TS file
    (workspace / "app" / "ui.ts").write_text(
        "export class ChartWidget {\n"
        "    render() {}\n"
        "}\n",
        encoding="utf-8",
    )

    # Search exact symbol: SessionBlackboard
    res1 = code_tools.code_symbols(query="SessionBlackboard", path="app")
    assert res1["ok"] is True
    assert res1["count"] >= 1
    top1 = res1["symbols"][0]
    assert top1["name"] == "SessionBlackboard"
    assert top1["kind"] == "class"
    assert "app/blackboard.py" in top1["path"]

    # Fuzzy search: _compact_context
    res2 = code_tools.code_symbols(query="_compact_context", path="app")
    assert res2["ok"] is True
    assert res2["count"] >= 1
    assert res2["symbols"][0]["name"] == "_compact_context"
    assert "app/loop.py" in res2["symbols"][0]["path"]

    # Fuzzy search: render_chart
    res3 = code_tools.code_symbols(query="render_chart", path="app")
    assert res3["ok"] is True
    assert any(s["name"] == "render_chart" for s in res3["symbols"])

    # Prefix / Substring search: "render"
    res4 = code_tools.code_symbols(query="render", path="app")
    assert res4["ok"] is True
    names4 = [s["name"] for s in res4["symbols"]]
    assert "render_chart" in names4
    assert "render_diagram" in names4

    # Filter by symbol_type="class"
    res_cls = code_tools.code_symbols(query="", path="app", symbol_type="class")
    assert res_cls["ok"] is True
    kinds = [s["kind"] for s in res_cls["symbols"]]
    assert all(k == "class" for k in kinds)
    assert any(s["name"] == "SessionBlackboard" for s in res_cls["symbols"])


# =============================================================================
# 4. .code_index.json Cache Generation and Incremental Loading
# =============================================================================

def test_code_index_cache(workspace):
    cache_path = workspace / ".code_index.json"
    assert not cache_path.exists()

    mod_file = workspace / "math_utils.py"
    mod_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    # Initial scan generates cache
    res1 = code_tools.code_symbols(query="add")
    assert res1["ok"] is True
    assert cache_path.exists()

    cache_content = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "files" in cache_content
    rel_path = "math_utils.py"
    assert rel_path in cache_content["files"]
    first_mtime = cache_content["files"][rel_path]["mtime"]

    # Second scan without modification reuses cache
    res2 = code_tools.code_symbols(query="add")
    assert res2["ok"] is True
    cache_content_2 = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache_content_2["files"][rel_path]["mtime"] == first_mtime

    # Modify file, mtime changes -> index updates
    import time
    time.sleep(0.05)
    mod_file.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
        "def multiply(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )
    # Ensure mtime is strictly greater
    new_mtime = first_mtime + 2.0
    os.utime(str(mod_file), (new_mtime, new_mtime))

    res3 = code_tools.code_symbols(query="multiply")
    assert res3["ok"] is True
    assert any(s["name"] == "multiply" for s in res3["symbols"])

    cache_content_3 = json.loads(cache_path.read_text(encoding="utf-8"))
    assert abs(cache_content_3["files"][rel_path]["mtime"] - new_mtime) < 0.01


# =============================================================================
# 5. code_references
# =============================================================================

def test_code_references(workspace):
    (workspace / "pkg").mkdir(parents=True, exist_ok=True)
    def_file = workspace / "pkg" / "blackboard.py"
    def_file.write_text(
        "class SessionBlackboard:\n"
        "    def __init__(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    caller_file = workspace / "pkg" / "caller.py"
    caller_file.write_text(
        "from .blackboard import SessionBlackboard\n\n"
        "def init_system():\n"
        "    board = SessionBlackboard()\n"
        "    return board\n",
        encoding="utf-8",
    )

    res = code_tools.code_references("SessionBlackboard", path="pkg")
    assert res["ok"] is True
    assert res["count"] >= 3

    kinds = [r["kind"] for r in res["references"]]
    assert "def" in kinds
    assert "import" in kinds
    assert "call" in kinds

    imp_ref = next(r for r in res["references"] if r["kind"] == "import")
    assert imp_ref["path"] == "pkg/caller.py"
    assert imp_ref["line"] == 1

    call_ref = next(r for r in res["references"] if r["kind"] == "call")
    assert call_ref["path"] == "pkg/caller.py"
    assert call_ref["line"] == 4

    def_ref = next(r for r in res["references"] if r["kind"] == "def")
    assert def_ref["path"] == "pkg/blackboard.py"
    assert def_ref["line"] == 1


# =============================================================================
# 6. tools.py Dispatch Integration
# =============================================================================

def test_tools_dispatch_integration(workspace):
    test_file = workspace / "integration.py"
    test_file.write_text(
        "class AgentTester:\n"
        "    def run_check(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    # 1. code_outline via dispatch
    out = tools.dispatch("code_outline", {"path": "integration.py"})
    assert out["ok"] is True
    assert out["symbols"][0]["name"] == "AgentTester"

    # 2. code_symbols via dispatch
    syms = tools.dispatch("code_symbols", {"query": "AgentTester"})
    assert syms["ok"] is True
    assert any(s["name"] == "AgentTester" for s in syms["symbols"])

    # 3. code_references via dispatch
    refs = tools.dispatch("code_references", {"symbol": "AgentTester"})
    assert refs["ok"] is True
    assert len(refs["references"]) >= 1
