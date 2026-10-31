"""M15：CEO 调度台 + 角色库。

关键验收（董事长原话）：我向 CEO 发送「开发一个计算器」，CEO 必须自动建群，
并且**只拉开发相关的员工**，绝不能乱拉人。
"""
from __future__ import annotations

import json

import pytest

from app.db import get_conn, init_db
from app.ui import api as ui_api, dispatcher, roles, store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("M0_DISPATCH_SYNC", "1")
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "m15.db"))
    init_db()
    with get_conn() as c:
        store.ensure(c)
        roles.ensure(c)
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.execute("INSERT OR REPLACE INTO identities (id,name,role,legal_entity_id,secret,state)"
                  " VALUES ('ID-boss','老板','ceo','LE-GROUP-HQ',?,'active')", ("x" * 32,))
        c.commit()
        yield c


def test_ceo_chat_exists_and_is_pinned(conn):
    chat = dispatcher.ensure_ceo_chat(conn)
    assert chat["kind"] == "ceo"
    row = conn.execute("SELECT pinned FROM chats WHERE id=?", (chat["id"],)).fetchone()
    assert row["pinned"] == 1


def test_tag_routing_reads_intent():
    assert "dev" in roles.tags_of("开发一个计算器")
    assert "content" in roles.tags_of("写一篇公众号文章")
    assert roles.tags_of("今天天气不错") == []


def test_dev_request_only_pulls_dev_people(conn):
    """开发需求 → 项目 WBS 里只有开发任务，不能混进文案。"""
    res = dispatcher.route(conn, "开发一个计算器")
    pid = res["project_id"]
    assert pid
    codes = [r["code"] for r in conn.execute(
        "SELECT code FROM wbs_items WHERE project_id=? ORDER BY code", (pid,))]
    assert "T1" in codes and "T2" not in codes, codes


def test_project_card_is_posted_to_ceo(conn):
    res = dispatcher.route(conn, "开发一个计算器")
    msgs = store.messages(conn, "C-CEO")
    kinds = [m["kind"] for m in msgs]
    assert "project" in kinds
    card = [m for m in msgs if m["kind"] == "project"][-1]
    assert res["project_id"] in (card.get("meta") or "") + card["content"]


def test_chitchat_does_not_create_a_group(conn):
    before = conn.execute("SELECT COUNT(*) n FROM chats WHERE kind='group'").fetchone()["n"]
    dispatcher.ensure_ceo_chat(conn)
    store.add_message(conn, "C-CEO", "human", "你好", author="我")
    # is_chitchat 由 API 层判断，这里只验证判定本身
    assert dispatcher.is_chitchat("你好") is True
    after = conn.execute("SELECT COUNT(*) n FROM chats WHERE kind='group'").fetchone()["n"]
    assert before == after


def test_role_library_create_instantiates_employee(conn):
    role = roles.create_role(conn, name="Go 工程师", tag="dev", prompt="写 Go", model="deepseek-chat")
    emp = roles.instantiate(conn, role["id"], "LE-GROUP-HQ")
    row = conn.execute("SELECT role_id, name FROM ai_employees WHERE id=?", (emp,)).fetchone()
    assert row["role_id"] == role["id"]
    # 幂等：再实例化一次不会多出人
    assert roles.instantiate(conn, role["id"], "LE-GROUP-HQ") == emp


def test_multi_tag_pulls_one_role_per_tag(conn, monkeypatch):
    """开发+文案的复合需求 → WBS 里既有开发 T1 也有文案 T2，且 T2 依赖 T1。

    意图分类在这里被钉死为「软件为主流程、内容为副流程」：真实分类会调模型，
    而 app/llm.py 的 complete() 没有 mock 开关——只要环境里有 key 就真发 HTTP。
    那样这条用例会随「本机有没有 .env」时绿时红（干净 checkout 上必红），
    而本用例要验的是复合流的 WBS 组合，不是模型分类能力。
    """
    from app import flows
    monkeypatch.setattr(flows, "classify", lambda text: {
        "task_type": "software", "confidence": 0.95,
        "reason": "test-forced: 开发 + 文案复合需求",
        "secondary": "content", "how": "test"})
    res = dispatcher.route(conn, "帮我开发一个算账工具，顺便写篇公众号宣传一下")
    assert "dev" in res["tags"] and "content" in res["tags"], res
    rows = {r["code"]: r for r in conn.execute(
        "SELECT code, role_tag, depends_on FROM wbs_items WHERE project_id=?",
        (res["project_id"],))}
    assert rows["T1"]["role_tag"] == "dev"
    assert rows["T2"]["role_tag"] == "content"
    assert json.loads(rows["T2"]["depends_on"]) == ["T1"]


def test_role_tools_are_bound(conn):
    rows = {r["name"]: r["tools"] for r in roles.list_roles(conn)}
    assert "code_exec" in (rows.get("Python极客") or "")
    assert "db_query" in (rows.get("金融分析师") or "")


def test_role_request_card_pending_then_approve(conn):
    role = roles.create_role(conn, name="临时翻译", tag="translate")
    group = store.create_group(conn, "调人测试群", [])
    mid = store.add_message(conn, group["id"], "agent", "申请调配角色：临时翻译",
                            author="小文", kind="role_request",
                            meta='{"role_id": "%s", "name": "临时翻译", "tag": "translate", "state": "pending"}'
                                 % role["id"])
    import json
    row = conn.execute("SELECT meta FROM chat_messages WHERE id=?", (mid,)).fetchone()
    assert json.loads(row["meta"])["state"] == "pending"
    assert store.members(conn, group["id"]) == []
