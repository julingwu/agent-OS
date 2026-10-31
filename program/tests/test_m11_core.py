"""M11: 记忆 / 上下文 / 查库 / 群与员工管理 / 告警 / 定时任务 / 知识库."""
from __future__ import annotations

import pytest

from app.db import get_conn, init_db
from app.ui import admin, api as ui_api, intel, ops, store

def tok() -> str:
    """Computed at call time — the signing secret can change between tests."""
    return ui_api._make_cookie("ID-boss")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_conn() as c:
        store.ensure(c)
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type,governance_mode)"
                  " VALUES ('LE-GROUP-HQ','集团总部','group_hq','group_direct')")
        c.execute("INSERT OR IGNORE INTO identities (id,name,role,legal_entity_id,secret,state)"
                  " VALUES ('ID-boss','老板','ceo','LE-GROUP-HQ','x'*32,'active')")
        for eid, name in (("AI-A", "小文"), ("AI-B", "小码"), ("AI-C", "小测")):
            c.execute("INSERT OR REPLACE INTO ai_employees (id,name,model,legal_entity_id,status)"
                      " VALUES (?,?,'deepseek-chat','LE-GROUP-HQ','online')", (eid, name))
        c.execute("INSERT OR REPLACE INTO budgets (id,legal_entity_id,limit_tokens,used_tokens)"
                  " VALUES ('B-1','LE-GROUP-HQ',100000,9000)")
        c.commit()
        yield c


# ---------------------------------------------------------------- 记忆
def test_preference_extraction_is_conservative():
    assert intel.extract_preference("以后叫我老板") == ("boss_title", "老板")
    assert intel.extract_preference("以后请称呼我为老板") == ("boss_title", "老板")
    assert intel.extract_preference("叫我看看这个") is None      # 动词不是称呼
    assert intel.extract_preference("今天天气不错") is None


def test_preference_and_history_go_into_the_prompt(conn):
    intel.save_preference(conn, "ID-boss", "boss_title", "老板")
    chat = store.create_group(conn, "写作组", ["AI-A", "AI-B", "AI-C"])
    store.add_message(conn, chat["id"], "human", "昨天说的秘书呢", author="我")
    prompt = intel.build_task_prompt(conn, "任务：写文案", chat_id=chat["id"],
                                     identity_id="ID-boss", entity="LE-GROUP-HQ")
    assert "称呼用户为「老板」" in prompt
    assert "【群里最近的对话】" in prompt and "昨天说的秘书呢" in prompt
    assert prompt.rstrip().endswith("任务：写文案")


# ---------------------------------------------------------------- 查库
def test_lookup_returns_real_org_and_budget_facts(conn):
    facts = intel.lookup(conn, "我的秘书是谁？公司有哪些人？预算还剩多少？", "LE-GROUP-HQ")
    assert "小文" in facts and "【组织架构】" in facts
    assert "剩余 91,000 tokens" in facts


# ---------------------------------------------------------------- 群管理
def test_invite_then_kick_is_soft_and_audited(conn):
    chat = store.create_group(conn, "组", ["AI-A"])
    admin.invite_members(chat["id"], admin.MembersIn(employee_ids=["AI-B"]), token=tok())
    assert {m["employee_id"] for m in store.members(conn, chat["id"])} == {"AI-A", "AI-B"}

    admin.kick_member(chat["id"], "AI-B", token=tok())
    assert {m["employee_id"] for m in store.members(conn, chat["id"])} == {"AI-A"}
    row = conn.execute("SELECT state FROM chat_members WHERE chat_id=? AND employee_id=?",
                       (chat["id"], "AI-B")).fetchone()
    assert row["state"] == "removed"                    # 软删除，行还在
    assert conn.execute("SELECT COUNT(*) n FROM ai_audit_logs WHERE action='member_kicked'"
                        ).fetchone()["n"] >= 1


def test_dissolve_removes_everyone_and_audits(conn):
    chat = store.create_group(conn, "临时组", ["AI-A", "AI-B"])
    admin.leave_group(chat["id"], token=tok())
    assert store.members(conn, chat["id"]) == []
    assert "已解散" in conn.execute("SELECT title FROM chats WHERE id=?", (chat["id"],)).fetchone()["title"]
    assert conn.execute("SELECT COUNT(*) n FROM ai_audit_logs WHERE action='group_dissolved'"
                        ).fetchone()["n"] == 1


# ---------------------------------------------------------------- 员工
def test_fire_is_soft_releases_budget_and_stops_runs(conn):
    conn.execute("INSERT OR REPLACE INTO identities (id,name,role,legal_entity_id,employee_id,secret,state)"
                 " VALUES ('ID-e1','小文','agent','LE-GROUP-HQ','AI-A','y'*32,'active')")
    conn.execute("INSERT OR REPLACE INTO budgets (id,legal_entity_id,identity_id,limit_tokens,used_tokens)"
                 " VALUES ('B-2','LE-GROUP-HQ','ID-e1',50000,1222)")
    conn.commit()
    out = admin.fire_employee("AI-A", token=tok())
    assert out["ok"]
    assert conn.execute("SELECT status FROM ai_employees WHERE id='AI-A'").fetchone()["status"] == "fired"
    assert conn.execute("SELECT state FROM identities WHERE id='ID-e1'").fetchone()["state"] == "inactive"
    b = conn.execute("SELECT limit_tokens, used_tokens FROM budgets WHERE id='B-2'").fetchone()
    assert b["limit_tokens"] == b["used_tokens"]        # 剩余额度清零
    assert conn.execute("SELECT COUNT(*) n FROM ai_audit_logs WHERE action='agent_fired'"
                        ).fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM ai_employees WHERE id='AI-A'").fetchone()["n"] == 1


# ---------------------------------------------------------------- 告警
def test_reject_storm_and_low_budget_raise_alerts(conn):
    conn.execute("INSERT OR REPLACE INTO tasks (id,legal_entity_id,title,state)"
                 " VALUES ('T-1','LE-GROUP-HQ','x','rejected')")
    for i, st in enumerate(("rejected", "failed", "timeout"), start=1):
        conn.execute("INSERT OR REPLACE INTO task_runs (id,task_id,run_seq,legal_entity_id,state)"
                     " VALUES (?,?,?, 'LE-GROUP-HQ', ?)", (f"RUN-{i}", "T-1", i, st))
    conn.execute("UPDATE budgets SET used_tokens=95000 WHERE id='B-1'")
    conn.commit()

    assert ops.check_reject_storm(conn, "T-1")
    hits = ops.check_budgets(conn)
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM alerts")]
    assert "reject_storm" in kinds and "budget_low" in kinds and hits


# ---------------------------------------------------------------- 知识库 / 定时任务
def test_kb_excerpt_is_injected(conn, tmp_path):
    doc = tmp_path / "品牌指南.txt"
    doc.write_text("所有文案必须亲切、不夸大功效。", encoding="utf-8")
    conn.execute("INSERT INTO kb_documents (id,legal_entity_id,name,path,bytes,excerpt)"
                 " VALUES ('KB-1','LE-GROUP-HQ','品牌指南.txt',?,?,?)",
                 (str(doc), 12, doc.read_text(encoding="utf-8")))
    conn.commit()
    prompt = intel.build_task_prompt(conn, "任务：写文案", chat_id=None,
                                     identity_id="ID-boss", entity="LE-GROUP-HQ")
    assert "【部门知识库】" in prompt and "不夸大功效" in prompt


def test_job_runs_and_posts_a_digest(conn):
    chat = store.create_group(conn, "日报组", ["AI-A"])
    conn.execute("INSERT INTO tasks (id,legal_entity_id,title,state,created_at)"
                 " VALUES ('T-2','LE-GROUP-HQ','昨天的文案','completed',datetime('now','-2 hours'))")
    conn.execute("INSERT INTO scheduled_jobs (id,chat_id,title,prompt,at_time)"
                 " VALUES ('JOB-1',?, '每日报告','昨晚报告','09:00')", (chat["id"],))
    conn.commit()
    text = ops.run_job_now("JOB-1")
    assert "昨日任务报告" in text
    last = conn.execute("SELECT content, author FROM chat_messages WHERE chat_id=?"
                        " ORDER BY id DESC LIMIT 1", (chat["id"],)).fetchone()
    assert last["author"] == "值班助理" and "昨日任务报告" in last["content"]
