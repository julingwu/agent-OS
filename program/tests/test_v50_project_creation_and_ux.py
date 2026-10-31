import pytest
from starlette.testclient import TestClient

from app.db import connect, init_db
from app.main import app
from app import identity, pipeline, finance
from app.ui import api as ui_api, store as ui_store


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v50.db")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(ws),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v50-secret",
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
    guest = TestClient(app)
    return {"boss": boss, "client": guest, "ceo": ceo, "db_path": db_path, "ws": ws}


def test_project_create_api_software_flow(ctx):
    boss = ctx["boss"]
    payload = {
        "name": "电商营销自动化系统",
        "requirement": "构建营销触达与漏斗分析闭环系统",
        "flow": "FLOW-SOFTWARE",
        "use_llm_docs": 1,
    }
    res = boss.post("/ui/projects", json=payload)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out.get("ok") is True
    pid = out.get("project_id")
    assert pid and pid.startswith("P-")
    assert out.get("stages_count") == 9
    assert out.get("flow_id") == "FLOW-SOFTWARE"

    # Verify project exists in database
    conn = connect(ctx["db_path"])
    try:
        proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        assert proj is not None
        assert proj["name"] == "电商营销自动化系统"
        stages = conn.execute("SELECT * FROM project_stages WHERE project_id=? ORDER BY seq ASC", (pid,)).fetchall()
        assert len(stages) == 9
        pb = conn.execute("SELECT * FROM project_budgets WHERE project_id=?", (pid,)).fetchone()
        assert pb is not None
        assert pb["pool_tokens"] > 0
    finally:
        conn.close()


def test_project_create_api_analysis_flow(ctx):
    boss = ctx["boss"]
    payload = {
        "name": "Q3 财务竞品分析",
        "requirement": "多维竞品财务指标建模与可视化",
        "flow": "FLOW-ANALYSIS",
    }
    res = boss.post("/ui/projects", json=payload)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out.get("ok") is True
    pid = out.get("project_id")
    assert pid and pid.startswith("P-")
    assert out.get("stages_count") == 5


def test_project_create_unauthenticated(ctx):
    client = ctx["client"]
    res = client.post("/ui/projects", json={"name": "No Auth", "requirement": "test"})
    assert res.status_code == 401
