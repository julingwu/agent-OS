"""v5.2 TASK-2.2：按 pack/role_tag 对齐 L2 判据 + 失败断言结构化回注下一轮 Prompt。

两条第一性原理：
1. 文稿/调研/分析类交付物不该被软件工程类断言（py_compile）误杀 —— 判据必须按题材绑。
2. 模型改稿要单点修正，所以下一轮 Prompt 顶部必须带「失败断言 + 期望文件 + stderr」，
   而不是一句笼统的「上一轮失败」。
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from app.acceptance_run import failure_digest
from app.api.tasks import _retry_prompt
from app.audit import new_id
from app.db import get_conn, init_db
from app.engine import task_generator


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v52.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
                  " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type,parent_entity_id)"
                  " VALUES ('LE-FIN','财务部','department','LE-GROUP-HQ')")
        c.commit()
        yield c


def _scripts(conn, task_id) -> list[str]:
    """判据里引用到的 L2 脚本名，按 seq 排序。"""
    out = []
    for row in conn.execute(
            "SELECT check_type, check_ref FROM acceptance_criteria WHERE task_id=?"
            " ORDER BY seq", (task_id,)).fetchall():
        if row["check_type"] != "script_exec" or not row["check_ref"]:
            continue
        try:
            cfg = json.loads(row["check_ref"])
        except (ValueError, TypeError):
            continue
        if isinstance(cfg, dict) and cfg.get("script"):
            out.append(cfg["script"])
    return out


# ------------------------------------------------- TASK-2.2a：判据按题材绑定
def test_research_task_gets_markdown_and_never_py_syntax(conn):
    """调研类交付物是 md，套 py_compile 必然 100% 驳回 —— 这是 77% 驳回率的主因之一。"""
    out = task_generator.generate(conn, "调研一下 2025 年企业级 Agent 平台竞争格局",
                                  legal_entity_id="LE-FIN", role_tag="researcher")
    scripts = _scripts(conn, out["task_id"])
    assert "markdown_structure.py" in scripts
    assert "py_syntax.py" not in scripts
    assert out["pack_id"] == "research-v1"


def test_content_task_gets_markdown_and_never_py_syntax(conn):
    out = task_generator.generate(conn, "写一篇秋季护肤短文 400-600 字",
                                  legal_entity_id="LE-FIN", role_tag="content")
    scripts = _scripts(conn, out["task_id"])
    assert "markdown_structure.py" in scripts
    assert "py_syntax.py" not in scripts
    assert out["pack_id"] == "content-v1"


def test_analyst_task_gets_data_structure_validator(conn):
    out = task_generator.generate(conn, "分析一下上季度各区域毛利率并给出结论",
                                  legal_entity_id="LE-FIN", role_tag="analyst")
    scripts = _scripts(conn, out["task_id"])
    assert "data_structure.py" in scripts
    assert "py_syntax.py" not in scripts
    assert out["pack_id"] == "analyst-v1"


def test_dev_task_keeps_py_syntax(conn):
    """dev 类必须保留真实语法执行 —— 这是 L2「不得让模型自评」的硬约束。"""
    out = task_generator.generate(conn, "写一个 python 脚本解析本地日志文件",
                                  legal_entity_id="LE-FIN", role_tag="dev")
    scripts = _scripts(conn, out["task_id"])
    assert "py_syntax.py" in scripts


# --------------------------------------- TASK-2.2b：失败断言结构化回注
def _failed_script_task(conn):
    """造一个「L2 语法检查失败」的任务：判据 failed + 子进程 stderr + 已挂载产物。"""
    out = task_generator.generate(conn, "写一个 python 脚本解析本地日志文件",
                                  legal_entity_id="LE-FIN", role_tag="dev")
    task_id = out["task_id"]
    run_id = new_id("RUN")
    conn.execute("""INSERT INTO task_runs
                    (id, task_id, run_seq, legal_entity_id, state, runner)
                    VALUES (?,?,1,'LE-FIN','failed','native')""", (run_id, task_id))
    target = pathlib.Path(tempfile.mkdtemp()) / "parse_log.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    script_path = str(target)
    conn.execute("""INSERT INTO artifacts (id, run_id, art_seq, file_path, checksum, bytes)
                    VALUES (?,?,1,?,?,?)""",
                 (new_id("A"), run_id, script_path, "deadbeef", target.stat().st_size))
    failed = conn.execute(
        "SELECT id FROM acceptance_criteria WHERE task_id=? AND check_type='script_exec'"
        " ORDER BY seq", (task_id,)).fetchall()
    assert len(failed) >= 2, "dev 任务应至少有 file_exists 与 py_syntax 两条 L2 判据"
    # 第 1 条（file_exists）通过，第 2 条（py_syntax）失败并带子进程 stderr
    conn.execute("UPDATE acceptance_criteria SET state='passed', note='文件可用' WHERE id=?",
                 (failed[0]["id"],))
    conn.execute("UPDATE acceptance_criteria SET state='failed', note=? WHERE id=?",
                 ("脚本执行失败: py_syntax.py exit=1:   File \"parse_log.py\", line 1\n"
                  "    def broken(:\n              ^\nSyntaxError: invalid syntax",
                  failed[1]["id"]))
    conn.execute("UPDATE task_runs SET error=? WHERE id=?",
                 ("验收未通过：Python 语法检查失败", run_id))
    conn.commit()
    return task_id, script_path


def test_failure_digest_separates_assertion_file_and_stderr(conn):
    task_id, script_path = _failed_script_task(conn)
    digest = failure_digest(conn, task_id)
    assert len(digest["items"]) == 1
    item = digest["items"][0]
    assert item["layer"] == "L2"
    assert item["expected_name"] == ".py"
    # stderr 必须被拆成独立字段，而不是糊在中文原因里
    assert item["stderr"].startswith("exit=1")
    assert "SyntaxError" in item["stderr"]
    assert "exit=1" not in item["detail"]
    # 期望文件 vs 实际产物都要能对上，模型才知道该改哪个文件
    assert digest["expected_names"] == [".py"]
    assert digest["actual_paths"] == [script_path]
    assert digest["run_error"] == "验收未通过：Python 语法检查失败"


def test_retry_prompt_carries_structured_fix_list(conn):
    task_id, script_path = _failed_script_task(conn)
    prompt = _retry_prompt(conn, task_id)
    # 向后兼容：既有测试与人工排障都依赖「判据N」这一锚点
    digest = failure_digest(conn, task_id)
    assert f"判据{digest['items'][0]['seq']}" in prompt
    # 计划要求的三类信息必须置顶可见
    assert "【上一轮验收失败 · 逐项修正清单】" in prompt
    assert "失败断言：" in prompt
    assert "期望产出文件：.py" in prompt
    assert "错误输出摘要：exit=1" in prompt
    assert script_path in prompt
    assert "【执行异常】验收未通过：Python 语法检查失败" in prompt
    # 单点修正约束必须在最后一句
    assert prompt.rstrip().endswith("改完只回复 DONE。")


def test_retry_prompt_falls_back_when_nothing_failed(conn):
    out = task_generator.generate(conn, "写一篇秋季护肤短文",
                                  legal_entity_id="LE-FIN", role_tag="content")
    prompt = _retry_prompt(conn, out["task_id"])
    assert "未知原因" in prompt
