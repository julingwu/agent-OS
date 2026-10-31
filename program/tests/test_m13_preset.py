"""M13：员工运行预设（standard / cordis 创造模式）。"""
from __future__ import annotations

import pytest

from app.db import get_conn, init_db
from app.ui import admin, api as ui_api, store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()
    with get_conn() as c:
        store.ensure(c)
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.execute("INSERT OR REPLACE INTO identities (id,name,role,legal_entity_id,secret,state)"
                  " VALUES ('ID-boss','老板','ceo','LE-GROUP-HQ',?,'active')", ("x" * 32,))
        c.execute("INSERT OR REPLACE INTO ai_employees (id,name,model,legal_entity_id,status)"
                  " VALUES ('AI-1','小构','deepseek-chat','LE-GROUP-HQ','online')")
        c.commit()
        yield c


def tok() -> str:
    return ui_api._make_cookie("ID-boss")


def test_preset_defaults_to_standard(conn):
    row = conn.execute("SELECT preset FROM ai_employees WHERE id='AI-1'").fetchone()
    assert row["preset"] == "standard"


def test_preset_can_be_switched_and_is_audited(conn):
    admin.edit_employee("AI-1", admin.EmployeePatch(preset="cordis"), token=tok())
    row = conn.execute("SELECT preset FROM ai_employees WHERE id='AI-1'").fetchone()
    assert row["preset"] == "cordis"
    n = conn.execute("SELECT COUNT(*) n FROM ai_audit_logs WHERE action='agent_edited'").fetchone()["n"]
    assert n >= 1


def test_bad_preset_is_rejected(conn):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        admin.edit_employee("AI-1", admin.EmployeePatch(preset="god-mode"), token=tok())
    assert exc.value.status_code == 400


def test_employee_list_exposes_preset(conn):
    conn.execute("UPDATE ai_employees SET preset='cordis' WHERE id='AI-1'")
    conn.commit()
    rows = conn.execute("SELECT id,preset FROM ai_employees").fetchall()
    assert dict(rows[0])["preset"] == "cordis"
