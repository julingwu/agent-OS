import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect, init_db
from app import flows, pipeline, identity

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v45_flow.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v45-flow-secret",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    flows.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()

    c = TestClient(app)
    from app.ui import api as ui_api
    token = ui_api._make_cookie(ceo["id"])
    c.cookies.set("oa_ui", token)
    return c, ceo, db_path


def test_custom_business_template_registration_and_planning(client):
    c, ceo, db_path = client
    
    # 1. Register custom business flow: FLOW-MARKETING
    payload = {
        "id": "FLOW-MARKETING",
        "name": "营销增长与商业落地页交付流",
        "kind": "marketing",
        "description": "客群定位 → 视觉方案 → 合规文案 → 前端落地页 → 质检验收 → 交付（6 步）",
        "nodes": [
            {"key": "market_research", "title": "客群与痛点分析", "role": "research", "gate": "目标客群明确"},
            {"key": "creative_design", "title": "视觉设计与版式", "role": "design", "gate": "视觉规范锁定"},
            {"key": "copy_audit", "title": "文案撰写与红线复核", "role": "content", "gate": "广告法复核通过"},
            {"key": "page_build", "title": "前端落地页开发", "role": "dev", "gate": "HTML/CSS完整"},
            {"key": "conversion_test", "title": "页面转化质检", "role": "qa", "gate": "表单与交互测试通过"},
            {"key": "deliver", "title": "交付", "role": "platform", "gate": "已交付", "kind": "deliver"}
        ]
    }
    res = c.post("/ui/flow-templates", json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert res.json()["flow_id"] == "FLOW-MARKETING"
    
    # 2. Query flow-templates
    list_res = c.get("/ui/flow-templates")
    assert list_res.status_code == 200
    tpl_ids = [t["id"] for t in list_res.json()["templates"]]
    assert "FLOW-MARKETING" in tpl_ids
    
    # 3. Test flows.plan explicitly matches FLOW-MARKETING
    with connect(db_path) as conn:
        plan = flows.plan(conn, "请按【FLOW-MARKETING】流程执行营销落地页交付")
        assert plan["flow_id"] == "FLOW-MARKETING"
        assert plan["confidence"] == 1.0
        assert len(plan["nodes"]) == 6
        assert plan["nodes"][0]["key"] == "market_research"
        assert "research" in plan["roles"]
        assert "dev" in plan["roles"]
        assert "design" in plan["roles"]
