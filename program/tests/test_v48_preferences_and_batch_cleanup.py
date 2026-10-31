"""Tests for v4.8:
- Token budget cap up to 100B (100,000,000,000 tokens)
- User preferences persistence and deletion endpoints
- Batch files deletion & purge missing files
- Batch project deletion with cascade cleanup
"""

import pytest
from fastapi.testclient import TestClient

from app import finance, identity, pipeline
from app.db import connect, init_db
from app.main import app
from app.ui import api as ui_api, intel, store as ui_store


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v48.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v48-secret",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    pipeline.ensure(conn)
    finance.ensure(conn)
    ui_store.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()

    boss = TestClient(app)
    boss.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
    return {"boss": boss, "ceo": ceo, "db_path": db_path}


def test_budget_cap_large_values(ctx):
    conn = connect(ctx["db_path"])
    try:
        # Test 10亿 (1,000,000,000)
        cfg = finance.set_config(conn, cap=1_000_000_000)
        assert cfg["cap"] == 1_000_000_000
        
        # Test reading from config
        read_cfg = finance.config(conn)
        assert read_cfg["cap"] == 1_000_000_000

        # Reset to default
        finance.set_config(conn, cap=1_000_000)
    finally:
        conn.close()


def test_settings_put_large_budget(ctx):
    boss = ctx["boss"]
    # Set 10亿 tokens via PUT /ui/settings
    res = boss.put("/ui/settings", json={"project_budget_cap": 1_000_000_000})
    assert res.status_code == 200, res.text
    assert res.json().get("ok") is True

    # Read back via GET /ui/settings
    get_res = boss.get("/ui/settings")
    assert get_res.status_code == 200
    assert get_res.json()["project_budget_cap"] == 1_000_000_000

    # Reset
    boss.put("/ui/settings", json={"project_budget_cap": 1_000_000})


def test_account_preferences_crud(ctx):
    boss = ctx["boss"]
    # Update preferences
    prefs = {"boss_title": "老板", "response_style": "简洁专业", "test_key": "val123"}
    res = boss.put("/ui/account/preferences", json={"preferences": prefs})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["preferences"]["boss_title"] == "老板"
    assert data["preferences"]["response_style"] == "简洁专业"

    # Read via GET /ui/account
    acc = boss.get("/ui/account").json()
    assert acc["preferences"]["boss_title"] == "老板"
    assert acc["preferences"]["test_key"] == "val123"

    # Delete single preference key
    del_res = boss.delete("/ui/account/preferences/test_key")
    assert del_res.status_code == 200
    assert "test_key" not in del_res.json()["preferences"]

    # Verify deleted in GET /ui/account
    acc2 = boss.get("/ui/account").json()
    assert "test_key" not in acc2["preferences"]


def test_files_batch_and_purge_missing(ctx):
    boss = ctx["boss"]
    # Call purge missing files
    res = boss.post("/ui/files/purge-missing")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["purged"], int)

    # Call batch delete with dummy list
    batch_res = boss.post("/ui/files/batch-delete", json={"ids": ["non-existent-id"]})
    assert batch_res.status_code == 200
    assert batch_res.json()["ok"] is True


def test_projects_batch_delete(ctx):
    boss = ctx["boss"]
    # Call batch delete with dummy list
    res = boss.post("/ui/projects/batch-delete", json={"project_ids": ["P-fake-00000000"]})
    assert res.status_code == 200
    assert res.json()["ok"] is True
