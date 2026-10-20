"""M10: group chat routing and the invisible repair pass."""
from __future__ import annotations

import json

import pytest

from app.db import get_conn, init_db
from app.ui import store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db()
    with get_conn() as c:
        store.ensure(c)
        yield c


def _emp(conn, eid, name="X"):
    conn.execute("INSERT OR IGNORE INTO legal_entities (id, name, type, governance_mode)"
                 " VALUES ('LE-GROUP-HQ', '集团总部', 'group_hq', 'group_direct')")
    conn.execute("INSERT OR REPLACE INTO ai_employees (id, name, model, legal_entity_id, status)"
                 " VALUES (?,?, 'deepseek-chat', 'LE-GROUP-HQ', 'online')", (eid, name))
    conn.commit()


def test_roles_are_pm_member_reviewer(conn):
    for i in (1, 2, 3):
        _emp(conn, f"AI-{i}", f"人{i}")
    chat = store.create_group(conn, "g", ["AI-1", "AI-2", "AI-3"])
    got = [(m["employee_id"], m["role"]) for m in store.members(conn, chat["id"])]
    assert got == [("AI-1", "pm"), ("AI-2", "member"), ("AI-3", "reviewer")]


def test_hop_budget_stops_the_group_talking(conn):
    _emp(conn, "AI-7")
    chat = store.create_group(conn, "g2", ["AI-7"])
    for _ in range(4):
        assert store.hop_allows(conn, chat["id"])
        store.note_hop(conn, chat["id"])
    assert not store.hop_allows(conn, chat["id"])


def test_word_rule_is_read_from_the_criterion_snapshot():
    from app.engine.repair import _word_rule

    class R(dict):
        def __getitem__(self, k):
            return self.get(k)

    ref = json.dumps({"script": "word_count.py", "min": 500, "max": 800, "artifact": "article.md"})
    assert _word_rule([R(check_ref=ref)]) == (500, 800)
    assert _word_rule([R(check_ref=json.dumps({"script": "x.py"}))]) is None
