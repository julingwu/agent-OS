"""Tests for v4.9 system-wide functional enhancements:
- Pipeline stage deliverable preview vs download disposition
- FinOps stage-by-stage budget overview query
"""

import pytest
from fastapi.testclient import TestClient

from app import finance, identity, pipeline
from app.db import connect, init_db
from app.main import app
from app.ui import api as ui_api, store as ui_store


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v49.db")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(ws),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v49-secret",
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
    return {"boss": boss, "ceo": ceo, "db_path": db_path, "ws": ws}


def test_stage_deliverable_preview_and_download(ctx):
    conn = connect(ctx["db_path"])
    boss = ctx["boss"]
    ws = ctx["ws"]

    pid = "P-TEST-STAGE-PREVIEW"
    stage_key = "test_stage"
    fid = "DELIV-1"

    # Create dummy file on disk
    dummy_file = ws / "test_calc.py"
    dummy_file.write_text("print('hello v4.9')", encoding="utf-8")

    try:
        conn.execute("INSERT INTO projects (id,name,requirement,state) VALUES (?,?,?,?)",
                     (pid, "Stage Preview Test", "Testing stage preview", "active"))
        conn.execute("INSERT INTO project_stages (project_id,seq,key,title,state)"
                     " VALUES (?,0,?,?,'done')", (pid, stage_key, "Test Stage"))
        conn.execute("INSERT INTO stage_deliverables (id,project_id,stage_key,kind,file_path,bytes)"
                     " VALUES (?,?,?,?,?,?)",
                     (fid, pid, stage_key, "code", str(dummy_file), len("print('hello v4.9')")))
        conn.commit()
    finally:
        conn.close()

    # 1. Normal download (preview=0 default) should have attachment header
    res_dl = boss.get(f"/ui/projects/{pid}/stages/{stage_key}/files/{fid}")
    assert res_dl.status_code == 200
    assert "attachment" in res_dl.headers.get("content-disposition", "")
    assert res_dl.text == "print('hello v4.9')"

    # 2. Preview mode (preview=1) should serve inline without attachment header
    res_pv = boss.get(f"/ui/projects/{pid}/stages/{stage_key}/files/{fid}?preview=1")
    assert res_pv.status_code == 200
    assert "attachment" not in res_pv.headers.get("content-disposition", "")
    assert "text/plain" in res_pv.headers.get("content-type", "")
    assert res_pv.text == "print('hello v4.9')"


def test_finops_and_project_stage_budgets(ctx):
    conn = connect(ctx["db_path"])
    boss = ctx["boss"]

    pid = "P-TEST-BUDGET-DRILL"
    try:
        conn.execute("INSERT INTO projects (id,name,requirement,state) VALUES (?,?,?,?)",
                     (pid, "Budget Drill Test", "Testing budget drill", "active"))
        finance.open_project(conn, pid, [
            {"key": "stg_research", "title": "需求调研", "role": "research"},
            {"key": "stg_dev", "title": "开发实现", "role": "dev"},
        ])
        conn.commit()
    finally:
        conn.close()

    # Query project budget via API
    res = boss.get(f"/ui/projects/{pid}/budget")
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == pid
    assert data["pool"] > 0
    assert len(data["stages"]) == 2
    stage_keys = [s["key"] for s in data["stages"]]
    assert "stg_research" in stage_keys
    assert "stg_dev" in stage_keys
