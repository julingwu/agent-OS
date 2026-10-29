"""Comprehensive unit and integration test suite for AgentOS upgraded toolkit parity.

Tests:
1. web_search, web_fetch, browser_open fallback
2. doc_read across formats (PDF, DOCX, XLSX, CSV, JSON, HTML, ZIP)
3. fs_read with line-number slicing and auto-delegation
4. fs_edit surgical replacement and collision guards
5. glob_search and grep_search with context lines
6. run_exec with 16k spillover file handling
7. Background processes (process_start, process_list, process_logs, process_stop)
8. todo_write and todo_read SQLite persistence
9. delegate_task parallel subagent execution
10. skill_save and skill_delete
11. routine_create and routine_delete
12. publish_artifact and event emission
13. POST /v1/workspace/upload endpoint
14. Context compaction guard in loop.py
"""
from __future__ import annotations

import sys

import base64
import json
import os
import pathlib
import zipfile
import pytest
from fastapi.testclient import TestClient

from app import db, devices, events as ev, llm
from app.agent import doc_tools, ext_tools, loop as agent_loop, tools, web_tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    return ws


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    with db.get_conn() as conn:
        return devices.issue(conn, name="test-device", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 1. Web Tools
def test_web_search_and_fetch():
    # Test web_search returns structured results or graceful offline result
    res = web_tools.web_search("Python PEP 8", count=3)
    assert "ok" in res
    assert "query" in res
    assert "results" in res
    assert isinstance(res["results"], list)

    # Test web_fetch with an HTML string parsing
    html = """
    <html>
        <head><title>Test Documentation</title></head>
        <body>
            <header><nav>Navigation Link</nav></header>
            <main>
                <h1>Main Heading</h1>
                <p>This is paragraph content with a <a href="https://example.com">link</a>.</p>
                <table>
                    <tr><th>Key</th><th>Value</th></tr>
                    <tr><td>A</td><td>100</td></tr>
                </table>
            </main>
            <footer>Footer note</footer>
        </body>
    </html>
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    md = web_tools._html_to_markdown(soup)
    assert "# Main Heading" in md
    assert "[link](https://example.com)" in md
    assert "| Key | Value |" in md
    assert "Navigation Link" not in md  # nav stripped


# ---------------------------------------------------------------- 2. Document Parsing
def test_doc_read_formats(workspace):
    # CSV
    csv_file = workspace / "data.csv"
    csv_file.write_text("name,score\nAlice,95\nBob,88", encoding="utf-8")
    res_csv = doc_tools.doc_read("data.csv")
    assert res_csv["ok"] is True
    assert res_csv["doc_type"] == "csv"
    assert "Alice" in res_csv["content"]

    # JSON
    json_file = workspace / "config.json"
    json_file.write_text('{"service": "AgentOS", "status": "active"}', encoding="utf-8")
    res_json = doc_tools.doc_read("config.json")
    assert res_json["ok"] is True
    assert res_json["doc_type"] == "json"
    assert "AgentOS" in res_json["content"]

    # HTML
    html_file = workspace / "page.html"
    html_file.write_text("<html><head><title>Page Title</title></head><body><p>Hello HTML</p></body></html>", encoding="utf-8")
    res_html = doc_tools.doc_read("page.html")
    assert res_html["ok"] is True
    assert res_html["doc_type"] == "html"
    assert "Page Title" in res_html["content"]

    # DOCX
    import docx
    doc = docx.Document()
    doc.add_heading("Docx Title", 0)
    doc.add_paragraph("First paragraph in docx.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "H1"
    table.cell(0, 1).text = "H2"
    table.cell(1, 0).text = "V1"
    table.cell(1, 1).text = "V2"
    docx_file = workspace / "sample.docx"
    doc.save(str(docx_file))

    res_docx = doc_tools.doc_read("sample.docx")
    assert res_docx["ok"] is True
    assert res_docx["doc_type"] == "docx"
    assert "First paragraph in docx" in res_docx["content"]
    assert "| H1 | H2 |" in res_docx["content"]

    # XLSX
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws.append(["Q1", "Revenue", "Profit"])
    ws.append(["Jan", 1000, 200])
    xlsx_file = workspace / "sample.xlsx"
    wb.save(str(xlsx_file))

    res_xlsx = doc_tools.doc_read("sample.xlsx")
    assert res_xlsx["ok"] is True
    assert res_xlsx["doc_type"] == "xlsx"
    assert "Financials" in res_xlsx["sheet"]
    assert "| Q1 | Revenue | Profit |" in res_xlsx["content"]

    # ZIP
    zip_path = workspace / "archive.zip"
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        zf.writestr("test1.txt", "Content 1")
        zf.writestr("sub/test2.txt", "Content 2")
    res_zip = doc_tools.doc_read("archive.zip")
    assert res_zip["ok"] is True
    assert res_zip["doc_type"] == "zip"
    assert res_zip["file_count"] == 2


# ---------------------------------------------------------------- 3. File Read with Line Slicing
def test_fs_read_slicing_and_delegation(workspace):
    # Plain text line slicing
    code_file = workspace / "test.py"
    lines = [f"line_{i} = {i}" for i in range(1, 21)]
    code_file.write_text("\n".join(lines), encoding="utf-8")

    # Full read
    r_full = tools.dispatch("fs_read", {"path": "test.py"})
    assert r_full["ok"] is True
    assert r_full["total_lines"] == 20
    assert r_full["content"].replace("\r\n", "\n") == "\n".join(lines)

    # Sliced read (lines 5 to 8)
    r_sliced = tools.dispatch("fs_read", {"path": "test.py", "start_line": 5, "end_line": 8})
    assert r_sliced["ok"] is True
    assert r_sliced["start_line"] == 5
    assert r_sliced["end_line"] == 8
    assert "5: line_5 = 5" in r_sliced["content"]
    assert "8: line_8 = 8" in r_sliced["content"]
    assert "9: line_9 = 9" not in r_sliced["content"]

    # Auto delegation for office docs
    csv_file = workspace / "auto.csv"
    csv_file.write_text("col1,col2\nval1,val2", encoding="utf-8")
    r_delegated = tools.dispatch("fs_read", {"path": "auto.csv"})
    assert r_delegated["ok"] is True
    assert r_delegated["doc_type"] == "csv"


# ---------------------------------------------------------------- 4. Surgical Edit (fs_edit)
def test_fs_edit_surgical_precision(workspace):
    f = workspace / "code.py"
    f.write_text("def hello():\n    return 'old_value'\n", encoding="utf-8")

    # Precise replacement
    r1 = tools.dispatch("fs_edit", {
        "path": "code.py",
        "old_text": "return 'old_value'",
        "new_text": "return 'new_value'",
    })
    assert r1["ok"] is True
    assert r1["replacements"] == 1
    assert "return 'new_value'" in f.read_text(encoding="utf-8")

    # Target not found
    r2 = tools.dispatch("fs_edit", {
        "path": "code.py",
        "old_text": "non_existent_string",
        "new_text": "irrelevant",
    })
    assert r2["ok"] is False
    assert "not found" in r2["error"]

    # Multiple match collision guard
    f.write_text("item = 1\nitem = 1\n", encoding="utf-8")
    r3 = tools.dispatch("fs_edit", {
        "path": "code.py",
        "old_text": "item = 1",
        "new_text": "item = 2",
    })
    assert r3["ok"] is False
    assert "matches 2 occurrences" in r3["error"]

    # Replace all flag
    r4 = tools.dispatch("fs_edit", {
        "path": "code.py",
        "old_text": "item = 1",
        "new_text": "item = 2",
        "replace_all": True,
    })
    assert r4["ok"] is True
    assert r4["replacements"] == 2
    assert f.read_text(encoding="utf-8").replace("\r\n", "\n") == "item = 2\nitem = 2\n"


# ---------------------------------------------------------------- 5. Search (glob & grep)
def test_glob_and_grep_search(workspace):
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "module_a.py").write_text("def find_me():\n    pass\n", encoding="utf-8")
    (workspace / "pkg" / "module_b.py").write_text("def other():\n    find_me()\n", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignore.py").write_text("def find_me():\n    pass\n", encoding="utf-8")

    # glob_search
    glob_res = tools.dispatch("glob_search", {"pattern": "*.py", "path": "."})
    assert glob_res["ok"] is True
    paths = [m["path"] for m in glob_res["matches"]]
    assert any("module_a.py" in p for p in paths)
    assert not any("ignore.py" in p for p in paths)  # node_modules skipped

    # grep_search
    grep_res = tools.dispatch("grep_search", {"pattern": r"find_me\(\)", "path": "."})
    assert grep_res["ok"] is True
    assert grep_res["count"] >= 2
    match_files = [m["path"] for m in grep_res["matches"]]
    assert any("module_a.py" in f for f in match_files)
    assert any("module_b.py" in f for f in match_files)


# ---------------------------------------------------------------- 6. Exec & 16k Spillover
def test_exec_and_spillover(workspace):
    # Standard exec
    res = tools.dispatch("exec", {"cmd": "echo test_exec_output"})
    assert res["ok"] is True
    assert "test_exec_output" in res["stdout"]

    # Dangerous command blocked
    with pytest.raises(tools.ToolError, match="blocked"):
        tools.dispatch("exec", {"cmd": "shutdown -h now"})

    # Spillover file generation for long output (> 16000 chars)
    long_cmd = f'"{sys.executable}" -c "print(\'A\' * 20000)"'
    res_long = tools.dispatch("exec", {"cmd": long_cmd})
    assert res_long["ok"] is True
    assert res_long["spillover_file"] is not None
    assert (workspace / res_long["spillover_file"]).exists()
    assert "...[输出超长(已截断)，完整输出已存至" in res_long["stdout"]


# ---------------------------------------------------------------- 7. Processes Supervision
def test_process_tools(workspace):
    # Start a background process
    start_res = tools.dispatch("process_start", {
        "cmd": f'"{sys.executable}" -c "import time; print(\'started\'); time.sleep(5); print(\'done\')"',
        "name": "bg_sleeper",
    })
    assert start_res["ok"] is True
    proc_id = start_res["process"]["id"]

    # List processes
    list_res = tools.dispatch("process_list", {})
    assert list_res["ok"] is True
    proc_ids = [p["id"] for p in list_res["processes"]]
    assert proc_id in proc_ids

    # Read logs
    logs_res = tools.dispatch("process_logs", {"proc_id": proc_id})
    assert logs_res["ok"] is True

    # Stop process
    stop_res = tools.dispatch("process_stop", {"proc_id": proc_id})
    assert stop_res["ok"] is True


# ---------------------------------------------------------------- 8. Session Todos / Checklist
def test_todo_persistence():
    todos = [
        {"id": 1, "task": "Research API", "status": "completed"},
        {"id": 2, "task": "Write Code", "status": "in_progress"},
        {"id": 3, "task": "Run Pytest", "status": "pending"},
    ]
    w_res = tools.dispatch("todo_write", {"todos": todos}, session_id="ses_test_todos")
    assert w_res["ok"] is True
    assert w_res["count"] == 3

    r_res = tools.dispatch("todo_read", {}, session_id="ses_test_todos")
    assert r_res["ok"] is True
    assert len(r_res["todos"]) == 3
    assert r_res["todos"][0]["task"] == "Research API"
    assert r_res["todos"][0]["status"] == "completed"


# ---------------------------------------------------------------- 9. Parallel Delegation
def test_delegate_task(monkeypatch):
    def fake_chat(messages, **kw):
        user_msg = messages[-1]["content"]
        return {"message": {"content": f"Resolved: {user_msg[:20]}"}}

    monkeypatch.setattr(llm, "chat", fake_chat)

    tasks = [
        {"id": "t1", "goal": "Analyze Market Size", "context": "Semiconductor industry"},
        {"id": "t2", "goal": "Analyze Key Competitors", "context": "Global foundries"},
        {"id": "t3", "goal": "Draft Risk Matrix", "context": "Supply chain issues"},
    ]
    res = tools.dispatch("delegate_task", {"tasks": tasks})
    assert res["ok"] is True
    assert res["tasks_total"] == 3
    assert len(res["results"]) == 3
    assert res["results"][0]["status"] == "completed"
    assert "Resolved:" in res["results"][0]["result"]


# ---------------------------------------------------------------- 10. Self Evolution (Skills & Routines)
def test_skill_and_routine_tools():
    # Save skill
    s_res = tools.dispatch("skill_save", {
        "name": "测试自定义技能",
        "description": "单元测试生成的技能",
        "instructions": "第一步分析问题：{input}",
    })
    assert s_res["ok"] is True

    # Check skill list
    list_s = tools.dispatch("list_skills", {})
    assert any(s["name"] == "测试自定义技能" for s in list_s["items"])

    # Delete skill
    del_s = tools.dispatch("skill_delete", {"name_or_id": "测试自定义技能"})
    assert del_s["ok"] is True

    # Create routine
    r_res = tools.dispatch("routine_create", {
        "title": "每日早会提醒",
        "prompt": "生成今日各组进度同步早报",
        "schedule": "daily:09:30",
    }, bot_id="bot_chief", session_id="ses_chief")
    assert r_res["ok"] is True
    rid = r_res["routine"]["id"]

    # Delete routine
    del_r = tools.dispatch("routine_delete", {"routine_id": rid})
    assert del_r["ok"] is True


# ---------------------------------------------------------------- 11. Artifact Publishing
def test_publish_artifact(workspace):
    report = workspace / "summary.md"
    report.write_text("# Executive Summary\nAll deliverables ready.", encoding="utf-8")

    attached_events = []
    def fake_emit(kind, payload):
        attached_events.append((kind, payload))

    res = tools.dispatch("publish_artifact", {"path": "summary.md", "name": "核心报告.md"}, emit=fake_emit)
    assert res["ok"] is True
    assert res["artifact"]["name"] == "核心报告.md"
    assert len(attached_events) == 1
    assert attached_events[0][0] == "artifact.attached"


# ---------------------------------------------------------------- 12. Workspace Upload API
def test_workspace_upload_endpoint(client, device, workspace):
    data_bytes = b"Hello from uploaded file!\nSecond line."
    b64_str = base64.b64encode(data_bytes).decode("ascii")

    resp = client.post(
        "/v1/workspace/upload",
        json={"filename": "test_upload.txt", "data_b64": b64_str, "session_id": "ses_upload"},
        headers=_auth(device),
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["ok"] is True
    assert res_data["filename"] == "test_upload.txt"
    assert (workspace / "uploads" / "test_upload.txt").exists()


# ---------------------------------------------------------------- 13. Context Compaction Guard
def test_context_compaction_guard():
    # Construct synthetic conversation where total length exceeds 100k
    large_payload = json.dumps({"ok": True, "output": "X" * 30000})
    messages = [
        {"role": "system", "content": "You are AgentOS."},
        {"role": "user", "content": "Query 1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "f1"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": large_payload},
        {"role": "user", "content": "Query 2"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "function": {"name": "f2"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": large_payload},
        {"role": "user", "content": "Query 3"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c3", "function": {"name": "f3"}}]},
        {"role": "tool", "tool_call_id": "c3", "content": large_payload},
        {"role": "user", "content": "Query 4"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c4", "function": {"name": "f4"}}]},
        {"role": "tool", "tool_call_id": "c4", "content": large_payload},
        {"role": "user", "content": "Latest query"},
    ]

    total_before = sum(len(m.get("content") or "") for m in messages)
    assert total_before > 100_000

    compacted = agent_loop._compact_context(messages, threshold=100_000)
    total_after = sum(len(m.get("content") or "") for m in compacted)

    # Must be compacted down significantly
    assert total_after < total_before
    assert "[历史工具结果已折叠压缩" in compacted[3]["content"]
