"""Unit and integration tests for Agent toolchain robustness upgrades.

Covers:
1. Safe type conversions (_safe_int, _safe_bool) avoiding TypeError.
2. Dispatch tolerance with None/string values across tools.
3. Path guards in _fs_write and _fs_list.
4. Long task & checklist tolerance in todo_write and delegate_task.
5. Document parsing robustness for .xls, .doc, and multiline CSV.
6. Web fetch status code and size limit guards.
7. Headless Chrome argument --headless=new.
8. render_diagram architectural, flowchart, sequence, and custom diagram generator.
"""
from __future__ import annotations

import io
import json
import pathlib
import pytest

from app import artifacts, db, llm
from app.agent import doc_tools, ext_tools, tools, web_tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    return ws


# ---------------------------------------------------------------- 1. Safe Type Conversion Tests
def test_safe_int_and_bool():
    # _safe_int
    assert tools._safe_int(None, 42) == 42
    assert tools._safe_int("", 10) == 10
    assert tools._safe_int("   ", 10) == 10
    assert tools._safe_int("100", 0) == 100
    assert tools._safe_int("100.5", 0) == 100
    assert tools._safe_int(123, 0) == 123
    assert tools._safe_int(12.8, 0) == 12
    assert tools._safe_int(True, 0) == 1
    assert tools._safe_int(False, 1) == 0
    assert tools._safe_int("invalid_num", 5) == 5

    # _safe_bool
    assert tools._safe_bool(None, False) is False
    assert tools._safe_bool(None, True) is True
    assert tools._safe_bool(True, False) is True
    assert tools._safe_bool(False, True) is False
    assert tools._safe_bool("true", False) is True
    assert tools._safe_bool("True", False) is True
    assert tools._safe_bool("1", False) is True
    assert tools._safe_bool("yes", False) is True
    assert tools._safe_bool("false", True) is False
    assert tools._safe_bool("0", True) is False
    assert tools._safe_bool("no", True) is False
    assert tools._safe_bool("", False) is False
    assert tools._safe_bool("random", False) is False


def test_dispatch_param_type_robustness(workspace, monkeypatch):
    """Verify dispatch does not raise TypeError when models pass strings/None."""
    # 1. fs_read with string line numbers
    (workspace / "sample.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    res_read = tools.dispatch("fs_read", {
        "path": "sample.txt",
        "start_line": "1",
        "end_line": "2",
        "max_bytes": "5000",
    })
    assert res_read["ok"] is True

    # 2. grep_search with string context_lines and max_matches
    res_grep = tools.dispatch("grep_search", {
        "pattern": "line",
        "path": ".",
        "context_lines": "2",
        "max_matches": "10",
    })
    assert res_grep["ok"] is True

    # 3. doc_read with string max_chars
    res_doc = tools.dispatch("doc_read", {
        "path": "sample.txt",
        "max_chars": "1000",
    })
    assert res_doc["ok"] is True

    # 4. fs_edit with string replace_all
    res_edit = tools.dispatch("fs_edit", {
        "path": "sample.txt",
        "old_text": "line1",
        "new_text": "newline1",
        "replace_all": "false",
    })
    assert res_edit["ok"] is True

    # 5. process_logs with string lines
    res_plogs = tools.dispatch("process_logs", {
        "proc_id": "non_existent",
        "lines": "20",
    })
    assert res_plogs["ok"] is False  # process doesn't exist, but no TypeError


# ---------------------------------------------------------------- 2. Filesystem Guards (_fs_write, _fs_list)
def test_fs_write_and_list_guards(workspace):
    # Empty path guard
    res_empty = tools.dispatch("fs_write", {"path": "", "content": "hello"})
    assert res_empty["ok"] is False
    assert "path 不能为空" in res_empty["error"]

    res_none = tools.dispatch("fs_write", {"path": None, "content": "hello"})
    assert res_none["ok"] is False
    assert "path 不能为空" in res_none["error"]

    # Writing to a directory guard
    dir_path = workspace / "subfolder"
    dir_path.mkdir(parents=True, exist_ok=True)
    res_dir = tools.dispatch("fs_write", {"path": "subfolder", "content": "data"})
    assert res_dir["ok"] is False
    assert "目标路径是目录，无法写入" in res_dir["error"]

    # Normal write succeeds
    res_ok = tools.dispatch("fs_write", {"path": "subfolder/test.txt", "content": "valid"})
    assert res_ok["ok"] is True
    assert (dir_path / "test.txt").read_text(encoding="utf-8") == "valid"

    # fs_list with None safely defaults to "."
    res_list = tools.dispatch("fs_list", {"path": None})
    assert res_list["ok"] is True
    assert res_list["type"] == "dir"


# ---------------------------------------------------------------- 3. Todo & Delegate Parameter Tolerance
def test_todo_write_string_list_tolerance():
    # Model passes list of strings instead of dicts
    todos_strings = ["第一步：设计系统架构", "第二步：编写核心逻辑", "第三步：完成自动化测试"]
    res = tools.dispatch("todo_write", {"todos": todos_strings}, session_id="ses_robust_todo")
    assert res["ok"] is True
    assert res["count"] == 3
    assert res["todos"][0]["task"] == "第一步：设计系统架构"
    assert res["todos"][0]["status"] == "pending"

    # Read back
    r_res = tools.dispatch("todo_read", {}, session_id="ses_robust_todo")
    assert r_res["ok"] is True
    assert len(r_res["todos"]) == 3
    assert r_res["todos"][1]["task"] == "第二步：编写核心逻辑"


def test_delegate_task_string_list_tolerance(monkeypatch):
    def fake_chat(messages, **kw):
        user_msg = messages[-1]["content"]
        return {"message": {"content": f"Resolved: {user_msg[:25]}"}}

    monkeypatch.setattr(llm, "chat", fake_chat)

    tasks_strings = ["分析竞品价格模型", "梳理技术架构依赖"]
    res = tools.dispatch("delegate_task", {"tasks": tasks_strings})
    assert res["ok"] is True
    assert res["tasks_total"] == 2
    assert len(res["results"]) == 2
    assert res["results"][0]["status"] == "completed"
    assert "竞品价格模型" in res["results"][0]["goal"]


# ---------------------------------------------------------------- 4. Document Parsing Robustness (.xls, .doc, CSV)
def test_doc_read_xls_and_doc_friendly_error(workspace):
    # Old binary .xls mock
    xls_file = workspace / "legacy.xls"
    xls_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)  # OLE CF header
    res_xls = doc_tools.doc_read("legacy.xls")
    assert res_xls["ok"] is False
    assert "老版 Excel 97-2003 (.xls) 二进制格式" in res_xls["error"]
    assert "另存为 .xlsx" in res_xls["error"]

    # Old binary .doc mock
    doc_file = workspace / "legacy.doc"
    doc_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    res_doc = doc_tools.doc_read("legacy.doc")
    assert res_doc["ok"] is False
    assert "老版 Word 97-2003 (.doc) 二进制格式" in res_doc["error"]
    assert "另存为 .docx" in res_doc["error"]


def test_doc_read_csv_multiline_cells(workspace):
    # CSV with newline inside quoted cell
    csv_file = workspace / "multiline.csv"
    csv_content = 'id,title,description\n1,"Task A","This is line 1\nThis is line 2"\n2,"Task B","Single line"'
    csv_file.write_text(csv_content, encoding="utf-8")

    res_csv = doc_tools.doc_read("multiline.csv")
    assert res_csv["ok"] is True
    assert res_csv["rows"] == 3  # header + row 1 + row 2, NOT 4 lines!
    assert "This is line 1 This is line 2" in res_csv["content"]


# ---------------------------------------------------------------- 5. Web Fetch Robustness
def test_web_fetch_status_code_guard(monkeypatch):
    class MockStreamResponse:
        status_code = 404
        headers = {"content-type": "text/html"}
        encoding = "utf-8"
        url = "https://example.com/not-found"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_bytes(self):
            yield b"<html><body>404 Not Found</body></html>"

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return MockStreamResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", MockClient)

    res = web_tools.web_fetch("https://example.com/not-found")
    assert res["ok"] is False
    assert "网页请求失败 HTTP 404" in res["error"]
    assert "404 Not Found" not in res.get("content", "")


def test_web_fetch_content_length_guard(monkeypatch):
    class MockLargeResponse:
        status_code = 200
        headers = {"content-type": "application/octet-stream", "content-length": "20000000"}  # 20MB
        encoding = "utf-8"
        url = "https://example.com/big-file"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_bytes(self):
            yield b"A" * 1000

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return MockLargeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", MockClient)

    res = web_tools.web_fetch("https://example.com/big-file")
    assert res["ok"] is False
    assert "超过 15MB 限制" in res["error"]


# ---------------------------------------------------------------- 6. Headless Browser Flag
def test_headless_new_flag():
    import inspect
    browser_open_code = inspect.getsource(web_tools.browser_open)
    fallback_code = inspect.getsource(web_tools._search_chromium_fallback)
    assert "--headless=new" in browser_open_code
    assert "--headless=new" in fallback_code


# ---------------------------------------------------------------- 7. render_diagram Tool
def test_render_diagram_tool_specs():
    specs = {s["function"]["name"]: s["function"] for s in tools.TOOL_SPECS}
    assert "render_diagram" in specs
    spec = specs["render_diagram"]
    assert "architecture" in spec["parameters"]["properties"]["diagram_type"]["enum"]
    assert "flowchart" in spec["parameters"]["properties"]["diagram_type"]["enum"]
    assert "sequence" in spec["parameters"]["properties"]["diagram_type"]["enum"]


def test_render_diagram_architecture(workspace):
    code = """
    Layer: 接入端 [Web端, 移动端, 微信小程序]
    Layer: API网关层 [Kong网关, WAF防火墙]
    Layer: 核心业务微服务 [用户中心, 订单服务, 结算服务]
    Layer: 基础设施 [MySQL集群, Redis缓存, Kafka]
    """
    res = tools.dispatch("render_diagram", {
        "title": "测试系统架构图",
        "diagram_type": "architecture",
        "code": code,
        "theme": "modern",
    })
    assert res["ok"] is True
    assert "artifact_id" in res
    assert "![测试系统架构图]" in res["markdown"]
    art_path = artifacts.root() / f"{res['artifact_id']}.png"
    assert art_path.exists()
    assert art_path.stat().st_size > 10000


def test_render_diagram_flowchart(workspace):
    code = """
    A[用户提交订单] --> B{库存是否充足}
    B -->|充足| C[生成支付单]
    B -->|缺货| D[触发补货通知]
    C --> E[调用支付接口]
    """
    res = tools.dispatch("render_diagram", {
        "title": "测试业务流程图",
        "diagram_type": "flowchart",
        "code": code,
        "theme": "financial",
    })
    assert res["ok"] is True
    assert "artifact_id" in res
    assert "![测试业务流程图]" in res["markdown"]
    art_path = artifacts.root() / f"{res['artifact_id']}.png"
    assert art_path.exists()
    assert art_path.stat().st_size > 10000


def test_render_diagram_sequence(workspace):
    code = """
    participant Client as 客户端
    participant Gateway as 网关
    participant Service as 认证服务

    Client -> Gateway: GET /api/user/info
    Gateway -> Service: 验证 Token 有效性
    Service -->> Gateway: 返回用户上下文
    Gateway -->> Client: 200 OK
    """
    res = tools.dispatch("render_diagram", {
        "title": "测试认证时序图",
        "diagram_type": "sequence",
        "code": code,
        "theme": "academic",
    })
    assert res["ok"] is True
    assert "artifact_id" in res
    assert "![测试认证时序图]" in res["markdown"]
    art_path = artifacts.root() / f"{res['artifact_id']}.png"
    assert art_path.exists()
    assert art_path.stat().st_size > 10000
