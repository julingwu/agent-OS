"""Primary deliverable classification and complete execution requirements."""
from __future__ import annotations

import json

import pytest

from app.db import get_conn
from app.engine import dsh_adapter, task_generator
from app.ui import api as ui_api, intel, store
from tests.test_ui_task_actions import console


TAIL = "生成 report.json、report.html 和 README.md，实际运行 workspace_report.py 并 assert 总数为45。"
LONG_REQUIREMENT = (
    "写一个可运行的 Python 脚本 workspace_report.py（不少于200字节，仅标准库）。"
    "创建 demo_tasks.csv，数据包含任务名称、负责人、状态和工时字段。"
    + "每条数据都要校验，报告要清楚标出分组统计、总数以及异常处理方式。" * 8
    + TAIL)


@pytest.mark.parametrize("text,expected", [
    (LONG_REQUIREMENT, "code_write"),
    ("编写main.py读取CSV并输出report.html", "code_write"),
    ("Write a Python script that creates a JSON summary and HTML report", "code_write"),
    ("写一个脚本，生成HTML页面和JSON统计", "code_write"),
    ("做一个专业网页，含完整代码", "web_write"),
    ("做一个网页，展示 Python 脚本 main.py 的代码示例", "web_write"),
    ("Create an HTML page with a Python script example", "web_write"),
    ("审查 Python 脚本 main.py 并给出评审结论", "review_write"),
])
def test_primary_script_is_not_reclassified_by_secondary_html_output(text, expected):
    assert task_generator.task_type_of(text) == expected


def test_generated_script_criteria_and_signed_prompt_keep_full_requirement(console):
    raw, signed, _ = console
    generated = signed.post("/tasks/from_sentence", json={"text": LONG_REQUIREMENT}).json()
    assert generated["task_type"] == "code_write" and generated["pack_id"] == "code-local-v1"
    assert len(generated["title"]) == 120 and TAIL not in generated["title"]
    checks = [json.loads(c["check_ref"]) for c in generated["criteria"] if c["check_type"] == "script_exec"]
    assert {c["script"] for c in checks} == {"file_exists.py", "py_syntax.py"}
    assert all(c.get("name") == ".py" for c in checks)
    tid = generated["task_id"]
    view = raw.get(f"/ui/tasks/{tid}").json()
    assert view["requirement"] == view["task"]["requirement"] == LONG_REQUIREMENT
    assert "requirement" not in raw.get("/ui/tasks").json()["tasks"][0]
    run = signed.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    executed = signed.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})
    assert executed.status_code == 200, executed.text
    with get_conn() as conn:
        prompt = conn.execute("SELECT prompt_text FROM task_runs WHERE id=?", (run["run_id"],)).fetchone()[0]
    assert LONG_REQUIREMENT in prompt and TAIL in prompt
    assert "产出主文件 workspace_report.py" in prompt
    assert "产出主文件 solution.py" not in prompt and "产出主文件 index.html" not in prompt
    assert "全部附属文件" in prompt and "必须实际运行" in prompt


@pytest.mark.parametrize("pack,title,filename", [
    ("code-local-v1", "写一个 Python 脚本 main.py并输出HTML报告", "main.py"),
    ("code-v1", "编写Python脚本并保存为 fetch_data.py，抓取 https://example.com", "fetch_data.py"),
    ("code-local-v1", "读取 input.py，输出为 main.py", "main.py"),
    ("code-local-v1", "写一个Python计算器脚本", "solution.py"),
    ("web-v1", "做一个专业网页", "index.html"),
    ("web-v1", "做网页，保存为 landing.html，附带README", "landing.html"),
])
def test_prompt_honors_explicit_main_filename_and_keeps_defaults(pack, title, filename):
    prompt = dsh_adapter.default_prompt({"pack_id": pack, "title": title})
    assert f"产出主文件 {filename}，" in prompt
    assert title in prompt


@pytest.mark.parametrize("kind", ["dm", "group"])
def test_ui_prompt_uses_complete_current_request_after_truncated_chat_history(console, kind):
    _, signed, who = console
    tid = signed.post("/tasks/from_sentence", json={"text": LONG_REQUIREMENT}).json()["task_id"]
    with get_conn() as conn:
        conn.execute("INSERT INTO ai_employees (id,legal_entity_id,name,model,status,preset)"
                     " VALUES ('AI-PROMPT','LE-GROUP-HQ','Prompt tester','test-model','online','standard')")
        if kind == "dm":
            chat = store.get_or_create_dm(conn, "AI-PROMPT", "Prompt DM", "LE-GROUP-HQ")
        else:
            chat = store.create_group(conn, "Prompt group", ["AI-PROMPT"])
        store.add_message(conn, chat["id"], "human", LONG_REQUIREMENT, author="我", task_id=tid)
        assert TAIL not in intel.history_block(conn, chat["id"])
    current = LONG_REQUIREMENT + "\n当前补充要求：日志保留中文。"
    ui_api._run_turn(chat["id"], "AI-PROMPT", current, who["id"], task_id=tid, ack=False)
    with get_conn() as conn:
        prompt = conn.execute("SELECT prompt_text FROM task_runs WHERE task_id=?", (tid,)).fetchone()[0]
    assert current in prompt and TAIL in prompt
    assert "产出主文件 workspace_report.py" in prompt


def test_request_text_can_override_a_short_title_without_an_audit_row():
    task = {"title": "简短阶段标题", "pack_id": "code-local-v1"}
    prompt = dsh_adapter.default_prompt(task, request_text=LONG_REQUIREMENT)
    assert LONG_REQUIREMENT in prompt and "产出主文件 workspace_report.py" in prompt
