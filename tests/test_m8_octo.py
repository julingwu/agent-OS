"""M7: Octo bridge — plan split, loop guard, budget mute, dedupe, scoped artifacts."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

TOK = "octo-test-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("OCTO_SIM_TOKEN", TOK)
    monkeypatch.setenv("OCTO_RUNNER", "0")
    monkeypatch.setenv("OCTO_MAX_BOT_CHAIN", "2")
    monkeypatch.setenv("OCTO_MUTE_SECONDS", "3600")
    (tmp_path / "workspace").mkdir()

    from app import db
    from app.main import app
    from app.octo import guard, runner

    with TestClient(app) as raw:
        conn = db.connect()
        conn.execute("DELETE FROM octo_chains")
        conn.execute("DELETE FROM octo_seen")
        conn.execute("DELETE FROM octo_bots")
        conn.execute("""INSERT INTO octo_bots (bot_uid, bot_token, display_name, role,
                        identity_id, budget_calls) VALUES
                        ('pm_bot','bf_test','小文','pm','ID-x', 2),
                        ('fe_bot','bf_fe','前端小前','frontend','ID-fe', 100)""")
        conn.commit()
        yield raw, conn, guard, runner
        conn.close()


# ---------------------------------------------------------------- planning
def test_plan_picks_frontend_and_content():
    from app.octo.agent import plan_for

    roles = [p["role"] for p in plan_for("双十一大促：做一个商品落地页，再写一段营销文案")]
    assert "frontend" in roles and "content" in roles


def test_plan_never_empty():
    from app.octo.agent import plan_for

    assert len(plan_for("随便看看")) == 1


# ---------------------------------------------------------------- loop guard
def test_human_message_always_allowed(env):
    _, conn, guard, _ = env
    ok, why = guard.chain_allows(conn, "g1", "pm_bot", "human_uid", mentions_bot=False)
    assert ok and why == "human"


def test_bot_to_bot_needs_a_mention(env):
    _, conn, guard, _ = env
    guard.chain_allows(conn, "g1", "pm_bot", "human_uid", False)
    ok, why = guard.chain_allows(conn, "g1", "pm_bot", "fe_bot", mentions_bot=False)
    assert not ok and "mention" in why


def test_bot_chain_is_capped(env):
    _, conn, guard, _ = env
    guard.chain_allows(conn, "g1", "pm_bot", "human_uid", False)
    guard.chain_allows(conn, "g1", "pm_bot", "fe_bot", mentions_bot=True)
    guard.note_reply(conn, "g1", "pm_bot")
    guard.chain_allows(conn, "g1", "pm_bot", "fe_bot", mentions_bot=True)
    guard.note_reply(conn, "g1", "pm_bot")
    ok, why = guard.chain_allows(conn, "g1", "pm_bot", "fe_bot", mentions_bot=True)
    assert not ok and "chain limit" in why


def test_budget_mutes_the_bot(env):
    _, conn, guard, _ = env
    assert guard.note_call(conn, "pm_bot") is True        # 1/2
    assert guard.note_call(conn, "pm_bot") is True        # 2/2
    assert guard.note_call(conn, "pm_bot") is False       # 3 > 2 -> mute
    assert guard.is_muted(conn, "pm_bot") is True


def test_human_message_resets_the_chain(env):
    _, conn, guard, _ = env
    guard.chain_allows(conn, "g1", "pm_bot", "human_uid", False)
    guard.note_reply(conn, "g1", "pm_bot")
    conn.execute("UPDATE octo_chains SET bot_replies = 2 WHERE channel_id = 'g1'")
    conn.commit()
    ok, why = guard.chain_allows(conn, "g1", "pm_bot", "human_uid", False)
    assert ok and why == "human"
    assert conn.execute("SELECT bot_replies FROM octo_chains WHERE channel_id='g1'"
                        ).fetchone()[0] == 0


# ---------------------------------------------------------------- dedupe
def test_same_message_is_only_handled_once(env):
    _, conn, _, runner = env
    r = runner.OctoRunner("pm_bot")
    assert r._first_sight(conn, "m-1") is True
    assert r._first_sight(conn, "m-1") is False


def test_own_messages_are_ignored(env):
    _, _, _, runner = env

    class FakeClient:
        def __init__(self):
            self.sent = []

        def send(self, *a):
            self.sent.append(a)

        def ack(self, *a):
            pass

    r = runner.OctoRunner("pm_bot")
    c = FakeClient()
    r.handle_event({"event_id": 1, "message": {"message_id": "m-2", "from_uid": "pm_bot",
                                               "payload": {"type": 1, "content": "hi"},
                                               "channel_id": "g1", "channel_type": 2}}, c)
    assert c.sent == []


# ---------------------------------------------------------------- isolation
def test_artifacts_are_scoped_to_the_bot_identity(env):
    raw, conn, _, _ = env
    conn.execute("""INSERT INTO legal_entities (id, name, governance_mode) VALUES
                    ('LE-T','test entity','direct')""")
    conn.execute("""INSERT INTO tasks (id, legal_entity_id, title, pack_id, state)
                    VALUES ('T-fe','LE-T','fe','content-v1','completed'),
                           ('T-other','LE-T','be','code-v1','completed')""")
    conn.execute("""INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, actor_identity, state)
                    VALUES ('R-fe','T-fe',1,'LE-T','ID-fe','completed'),
                           ('R-other','T-other',1,'LE-T','ID-other','completed')""")
    conn.execute("""INSERT INTO artifacts (id, run_id, art_seq, file_path, bytes, checksum)
                    VALUES ('A1','R-fe',1,'fe.py',10,'x'), ('A2','R-other',1,'be.py',10,'y')""")
    conn.commit()

    r = raw.get("/octo/artifacts?bot_uid=fe_bot", headers={"X-Octo-Token": TOK})
    assert r.status_code == 200
    names = [a["file_path"] for a in r.json()["artifacts"]]
    assert names == ["fe.py"]


def test_octo_endpoints_need_the_token(env):
    raw, *_ = env
    assert raw.get("/octo/status").status_code == 401
    assert raw.get("/octo/artifacts?bot_uid=fe_bot").status_code == 401


def test_mute_endpoint_sets_muted_until(env):
    raw, conn, guard, _ = env
    r = raw.post("/octo/mute/pm_bot", json={"minutes": 30}, headers={"X-Octo-Token": TOK})
    assert r.status_code == 200
    assert guard.is_muted(conn, "pm_bot") is True
