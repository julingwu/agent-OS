"""M12.1: PM 项目制（拉群/设计闸）与修复环的 pack 隔离。"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import get_conn, init_db
from app.engine import repair
from app.ui import pm, store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_conn() as c:
        store.ensure(c)
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-X','X','group_hq')")
        c.commit()
        yield c


def test_project_signal():
    assert pm.is_project("把控制台 UI 彻底重做，先出设计稿")
    assert pm.is_project("官网落地页要重做")
    assert not pm.is_project("写一篇 200 字的秋季护肤短文")


def test_roles_pick_the_designer_by_title():
    members = [
        {"employee_id": "PM", "name": "小文", "title": "项目经理"},
        {"employee_id": "DS", "name": "小设", "title": "UI 设计师"},
        {"employee_id": "DV", "name": "小码", "title": "前端工程师"},
        {"employee_id": "QA", "name": "小测", "title": "测试工程师"},
    ]
    r = pm.roles(members)
    assert r["pm"]["employee_id"] == "PM"
    assert r["designer"]["employee_id"] == "DS"
    assert r["dev"]["employee_id"] == "DV"


def test_repair_only_runs_for_content_packs(conn):
    conn.execute("INSERT INTO tasks (id,legal_entity_id,title,state,pack_id)"
                 " VALUES ('T-web','LE-X','ui','rejected','web-v1')")
    conn.execute("INSERT INTO tasks (id,legal_entity_id,title,state,pack_id)"
                 " VALUES ('T-con','LE-X','文案','rejected','content-v1')")
    conn.commit()
    out = repair.maybe_repair(conn, "T-web")
    assert out["attempts"] == 0 and "disabled" in out["reason"]
    # 内容包不会被 pack 闸挡住（没产物时会以 no artifact 结束，但不会误判成跳过）
    out2 = repair.maybe_repair(conn, "T-con")
    assert "非 content-v1" not in (out2["reason"] or "")


def test_design_brief_asks_for_a_previewable_page():
    brief = pm.design_brief("重做控制台 UI", "UI重构组")
    assert "index.html" in brief
    assert "阶段一" in brief
    assert "不要接后端" in brief


def test_dev_brief_carries_the_approved_design():
    brief = pm.dev_brief("重做控制台 UI", "配色 #0b0d12，左侧三 Tab")
    assert "阶段二" in brief
    assert "配色 #0b0d12" in brief
