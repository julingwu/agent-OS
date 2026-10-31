"""V5.1: MetaCorp Cyber-Factorio & MCP Multi-World Exchange integration tests."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.mcp_bridge.pack_exporter import export_department_pack, import_department_pack
from app.mcp_bridge.mcp_server import call_tool, handle_jsonrpc
from app.mcp_bridge.mcp_client import hire_remote_mcp
from app import telemetry


def test_export_and_import_department_pack(tmp_path):
    assert "data/oa.db" not in os.path.abspath(os.getenv("DB_PATH", "")).replace("\\", "/")
    with connect() as conn:
        blueprint = export_department_pack(conn, "content-v1", dest_dir=tmp_path)
        assert blueprint["format"] == "metacorp.blueprint"
        assert blueprint["pack"]["id"] == "content-v1"
        assert "checksum" in blueprint
        assert (tmp_path / "content-v1.metacorp.json").exists()

        # Import as a new pack
        blueprint["pack"]["id"] = "content-v2-imported"
        blueprint["pack"]["name"] = "内容营销进阶包"
        res = import_department_pack(conn, blueprint)
        assert res["ok"] is True
        assert res["pack_id"] == "content-v2-imported"

        row = conn.execute("SELECT * FROM packs WHERE id='content-v2-imported'").fetchone()
        assert row is not None
        assert row["name"] == "内容营销进阶包"


def test_mcp_server_jsonrpc_protocol():
    # 1. Initialize
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    init_res = handle_jsonrpc(init_req)
    assert init_res["result"]["serverInfo"]["name"] == "MetaCorp-AgentOS-Exchange"

    # 2. Tools list
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    list_res = handle_jsonrpc(list_req)
    tool_names = [t["name"] for t in list_res["result"]["tools"]]
    assert "metacorp.run_l2_audit" in tool_names
    assert "metacorp.inspect_pack" in tool_names
    assert "metacorp.execute_stage" in tool_names

    # 3. Call tool: audit failure on banned keyword
    bad_req = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "metacorp.run_l2_audit",
            "arguments": {"content": "这是全行业最好、全国第一品牌的产品，100% 治愈！"}
        }
    }
    bad_res = handle_jsonrpc(bad_req)
    content_text = bad_res["result"]["content"][0]["text"]
    audit_data = json.loads(content_text)
    assert audit_data["passed"] is False
    assert len(audit_data["l0_banned_hits"]) > 0

    # 4. Call tool: audit success
    good_text = "秋季换季补水锁水，修护敏感肌肌肤屏障，舒缓干燥滋润温和耐受。"
    good_req = {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {
            "name": "metacorp.run_l2_audit",
            "arguments": {"content": good_text}
        }
    }
    good_res = handle_jsonrpc(good_req)
    good_audit = json.loads(good_res["result"]["content"][0]["text"])
    assert good_audit["passed"] is True


def test_hire_remote_mcp_and_bundle_inclusion(tmp_path, monkeypatch):
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO projects (id, name, requirement) VALUES ('PROJ-MCP-TEST', '测试项目', '需求')")
        conn.commit()

    res = hire_remote_mcp(
        endpoint_url="local_mock",
        tool_name="metacorp.execute_stage",
        arguments={"stage_key": "build", "requirement": "生成高并发计数器组件", "role_tag": "dev"},
        project_id="PROJ-MCP-TEST",
        stage_key="build"
    )
    assert res["ok"] is True
    file_path = res["file_path"]
    import pathlib
    assert pathlib.Path(file_path).exists()
    assert "Remote MetaCorp Deliverable" in pathlib.Path(file_path).read_text(encoding="utf-8")


def test_telemetry_websocket_and_events():
    client = TestClient(app)
    # Test recent telemetry REST endpoint
    r = client.get("/api/telemetry/recent")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Test WebSocket connection
    with client.websocket_connect("/ws/metacorp") as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        received = []
        for _ in range(25):
            msg = json.loads(ws.receive_text())
            received.append(msg)
            if msg.get("type") == "PONG":
                break
        assert any(m.get("type") == "PONG" for m in received)

        # Emit an event and verify broadcast
        telemetry.emit("EXP_CRYSTAL_DROP", exp_id="EXP-TEST-99", title="测试经验晶石", kind="rejection")
        evt = json.loads(ws.receive_text())
        assert evt["type"] == "EXP_CRYSTAL_DROP"
        assert evt["data"]["exp_id"] == "EXP-TEST-99"
