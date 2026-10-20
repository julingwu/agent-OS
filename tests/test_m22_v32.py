"""M22 (v3.2): Role Alignment / 研讨会一票否决 / 企业红线 L0。"""
from __future__ import annotations

import pathlib

import pytest

from app import acceptance_run, experience, pipeline, role_alignment
from app.ui import roles
from app.db import connect, get_conn, init_db
from app.ui import store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v32.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    (tmp_path / "workspace").mkdir()
    init_db()
    c = connect()
    store.ensure(c)
    roles.ensure(c)
    c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
              " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
    c.execute("""INSERT OR IGNORE INTO ai_employees
                 (id, legal_entity_id, name, model, status, preset)
                 VALUES ('AI-QA-01','LE-GROUP-HQ','小测','deepseek-chat','online','standard')""")
    c.execute("""INSERT OR IGNORE INTO positions (id, legal_entity_id, title, state)
                 VALUES ('POS-QA','LE-GROUP-HQ','测试工程师','active')""")
    c.execute("INSERT OR IGNORE INTO positions (id, legal_entity_id, title, state)"
              " VALUES ('POS-QADEV','LE-GROUP-HQ','测试开发工程师','active')")
    c.execute("""INSERT OR IGNORE INTO ai_employees
                 (id, legal_entity_id, name, model, status, preset)
                 VALUES ('AI-QA-02','LE-GROUP-HQ','小测二','deepseek-chat','online','standard')""")
    c.execute("UPDATE ai_employees SET position_id='POS-QA' WHERE id='AI-QA-01'")
    c.execute("UPDATE ai_employees SET position_id='POS-QADEV' WHERE id='AI-QA-02'")
    c.commit()
    yield c
    c.close()


def test_role_alignment_tags_and_boundary(conn):
    tags = role_alignment.tags_for(conn, "AI-QA-01")
    assert "qa" in tags
    assert "dev" not in tags
    qa_dev = role_alignment.tags_for(conn, "AI-QA-02")
    assert "qa" in qa_dev and "dev" not in qa_dev, qa_dev
    block = role_alignment.boundary_for(tags)
    assert "测试" in block and "不得替开发改代码" in block
    prompt = role_alignment.inject(conn, "写测试报告", "AI-QA-01")
    assert "【职责边界声明】" in prompt and "测试" in prompt
    assert role_alignment.inject(conn, prompt, "AI-QA-01") == prompt   # 幂等
    row = conn.execute("SELECT 1 FROM ai_audit_logs WHERE action='role_alignment_injected'"
                       " AND target='AI-QA-01'").fetchone()
    assert row


def test_workshop_audit_veto_blocks_and_requires_rewrite(conn, monkeypatch):
    from app import pipeline_llm
    proj = pipeline.create_project(conn, name="Veto 项目", requirement="做一个营销活动页")
    pid = proj["project_id"]

    def fake_statement(pid_, requirement, role, title):
        return {"question": f"{title}质询：预算与合规边界？",
                "risk": f"{title}风险：可能使用绝对化用语。",
                "stance": "反对" if role == "audit" else "同意"}

    monkeypatch.setattr(pipeline_llm, "workshop_statement", fake_statement)
    monkeypatch.setattr(pipeline_llm, "minutes", lambda *a, **k: "# 纪要\n\n审计一票否决")
    pipeline.run_project(conn, pid, "做一个营销活动页", keys=["intake", "charter"])
    conn.execute("UPDATE projects SET use_llm_docs=1 WHERE id=?", (pid,))
    conn.commit()
    with pytest.raises(Exception):
        pipeline.run_stage(conn, pid, "workshop", "做一个营销活动页")
    p = conn.execute("SELECT state,veto_note FROM projects WHERE id=?", (pid,)).fetchone()
    assert p["state"] == "blocked" and "禁止强行推进" in p["veto_note"]
    gate = conn.execute("SELECT state,note FROM stage_gates WHERE project_id=?"
                        " AND name='审计/法务一票否决'", (pid,)).fetchone()
    assert gate["state"] == "vetoed" and "一票否决" in gate["note"]
    revision = conn.execute("SELECT content FROM workshop_turns WHERE workshop_id=("
                            "SELECT id FROM workshops WHERE project_id=?) AND round=3",
                            (pid,)).fetchone()
    assert "一票否决" in revision["content"] and "禁止强行推进" in revision["content"]


def test_hard_redline_rejects_without_human_review(conn, tmp_path):
    e = experience.add(conn, kind="decision", title="广告绝对化红线",
                       content="营销文案禁止使用绝对化用语。",
                       tags=["合规", "营销"], source_type="manual", source_id="V32-RED",
                       mandatory=True, redline_terms="全网最低,第一品牌")
    assert e["state"] == "approved" and e["redline_terms"]
    art = tmp_path / "post.md"
    art.write_text("我们的产品全网最低，销量第一，欢迎购买。", encoding="utf-8")
    tid = "T-RED-1"
    conn.execute("""INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title, state)
                    VALUES (?, 'LE-GROUP-HQ','TENANT-HQ','content_write','红线测试','pending_review')""",
                 (tid,))
    conn.execute("""INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, tenant_id,
                                          state, runner)
                    VALUES ('RUN-RED-1',?,1,'LE-GROUP-HQ','TENANT-HQ','succeeded','mock')""",
                 (tid,))
    conn.execute("""INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, checksum, bytes)
                    VALUES ('ART-RED-1','RUN-RED-1','TENANT-HQ',1,?, 'x', 10)""",
                 (str(art),))
    conn.commit()
    out = acceptance_run.run(conn, tid, ["LE-GROUP-HQ"])
    assert out["task_state"] == "rejected"
    crit = conn.execute("SELECT state,note,acceptance_layer FROM acceptance_criteria"
                        " WHERE task_id=? AND check_ref='redline'", (tid,)).fetchone()
    assert crit["state"] == "failed" and crit["acceptance_layer"] == "L0"
    assert "全网最低" in crit["note"]
    audit = conn.execute("SELECT 1 FROM ai_audit_logs WHERE action='hard_redline_blocked'"
                         " AND target=?", (tid,)).fetchone()
    assert audit
