"""本地电脑桥 + 外部接入目录（dsh-im）的验收。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, local_bridge
from app.agent import tools


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-local", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 外部接入目录
def test_integrations_list(client, device):
    res = client.get("/v1/integrations", headers=_auth(device)).json()
    assert isinstance(res.get("items"), list)


# ---------------------------------------------------------------- 本地桥
def test_local_devices_empty_by_default(client, device):
    body = client.get("/v1/local/devices", headers=_auth(device)).json()
    assert body["count"] == 0


def test_local_tool_blocked_when_disabled(client, device):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at)"
            " VALUES ('local_enabled','false','2026-01-01T00:00:00+08:00')"
            " ON CONFLICT(key) DO UPDATE SET value_json='false'")
    out = tools.dispatch("local_info", {})
    assert out["ok"] is False
    assert "未开启" in out["error"]


def test_local_tool_reports_offline(client, device, monkeypatch):
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, False))
    out = tools.dispatch("local_exec", {"cmd": "whoami"})
    assert out["ok"] is False
    assert "没有在线的本地电脑" in out["error"]


def test_local_tool_requires_approval(monkeypatch):
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, True))
    monkeypatch.setattr(local_bridge, "first_device", lambda: "dev_fake")
    events = []

    def fake_approval(emit, *, action, detail, risk="normal", preview=None):
        events.append((action, detail))
        return False

    monkeypatch.setattr(local_bridge, "request_approval", fake_approval)
    out = tools.dispatch("local_read", {"path": "C:/tmp/a.txt"},
                         emit=lambda k, p: None)
    assert out["ok"] is False and "拒绝" in out["error"]
    assert events and "读取本机文件" in events[0][0]


def test_approval_endpoint_unknown_returns_404(client, device):
    res = client.post("/v1/local/approvals/apr_nope", headers=_auth(device),
                      json={"allow": True})
    assert res.status_code == 404


def test_approval_endpoint_resolves_pending(client, device):
    approval_id = "apr_test_1"
    local_bridge._approvals[approval_id] = {
        "event": __import__("threading").Event(), "decision": None}
    res = client.post(f"/v1/local/approvals/{approval_id}", headers=_auth(device),
                      json={"allow": True})
    assert res.status_code == 200
    assert local_bridge._approvals[approval_id]["event"].is_set()
    local_bridge._approvals.pop(approval_id, None)


def test_session_approval_scope_and_toggle(client, device):
    session_id = "sess_test_123"
    approval_id = "apr_sess_test"
    local_bridge._approvals[approval_id] = {
        "event": __import__("threading").Event(), "decision": None,
        "session_id": session_id}

    # 一键允许当前会话所有操作
    res = client.post(f"/v1/local/approvals/{approval_id}", headers=_auth(device),
                      json={"allow": True, "scope": "session"})
    assert res.status_code == 200
    assert local_bridge.is_session_approved(session_id) is True

    # 再次发起审批，应当直接放行，无需 emit 任何 approval.requested 事件
    seen = []
    allowed = local_bridge.request_approval(
        lambda k, p: seen.append(k), action="读取", detail="", session_id=session_id)
    assert allowed is True
    assert len(seen) == 0

    # 手动撤回会话授权
    res = client.post(f"/v1/local/sessions/{session_id}/approval", headers=_auth(device),
                      json={"approved": False})
    assert res.status_code == 200
    assert local_bridge.is_session_approved(session_id) is False
    local_bridge._approvals.pop(approval_id, None)
    local_bridge.set_session_approval(session_id, False)


def test_device_ws_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/device?token=bad") as ws:
            ws.receive_json()


def test_pending_approvals_endpoint_empty(client, device):
    body = client.get("/v1/local/approvals", headers=_auth(device)).json()
    assert body["count"] == 0


def test_approval_timeout_emits_decided(monkeypatch):
    seen: list = []
    monkeypatch.setattr(local_bridge, "APPROVAL_TIMEOUT", 0.05)
    allowed = local_bridge.request_approval(
        lambda kind, payload: seen.append((kind, payload)), action="本地操作：x", detail="y")
    assert allowed is False
    assert seen[0][0] == "approval.requested"
    assert seen[-1][0] == "approval.decided"
    assert seen[-1][1]["decision"] == "expired"


@pytest.mark.parametrize('allow', [True, False])
def test_remote_decision_emits_terminal_event(allow):
    seen = []
    def emit(kind, payload):
        seen.append((kind, payload))
        if kind == 'approval.requested':
            assert local_bridge.resolve_approval(payload['approval_id'], allow)
    assert local_bridge.request_approval(emit, action='test', detail='', session_id='test-terminal') is allow
    assert seen[-1][0] == 'approval.decided'
    assert seen[-1][1]['decision'] == ('allowed' if allow else 'denied')
    assert seen[-1][1]['approval_id'] == seen[0][1]['approval_id']


# ---------------------------------------------------------------- 桌面操控工具（v1.6）
GUI_TOOLS = ("local_apps", "local_open", "local_windows", "local_focus",
             "local_type", "local_keys", "local_click", "local_close")


def test_gui_tools_are_registered():
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    missing = [n for n in GUI_TOOLS if n not in names]
    assert not missing, f"缺少桌面操控工具: {missing}"


def test_gui_tools_raise_approval_with_risk(monkeypatch):
    """打开应用=中风险，键盘输入=高风险，审批卡要能区分。"""
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, True))
    monkeypatch.setattr(local_bridge, "first_device", lambda: "dev_fake")
    monkeypatch.setattr(local_bridge, "call", lambda *a, **k: {"ok": True})
    seen = []

    def fake_approval(emit, *, action, detail, risk="normal", preview=None):
        seen.append((action, risk, detail))
        return True

    monkeypatch.setattr(local_bridge, "request_approval", fake_approval)
    tools.dispatch("local_open", {"target": "记事本"}, emit=lambda k, p: None)
    tools.dispatch("local_type", {"text": "你好"}, emit=lambda k, p: None)
    tools.dispatch("local_apps", {"query": "微信"}, emit=lambda k, p: None)

    assert seen[0][0] == "在你电脑上打开应用/文件" and seen[0][1] == "medium"
    assert seen[1][0] == "在你的键盘上输入文字" and seen[1][1] == "high"
    assert seen[2][1] == "low"
    assert "记事本" in seen[0][2]


def test_gui_tool_passes_args_to_bridge(monkeypatch):
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, False))
    monkeypatch.setattr(local_bridge, "first_device", lambda: "dev_fake")
    calls = []

    def fake_call(device_id, name, args):
        calls.append((device_id, name, dict(args)))
        return {"ok": True, "launched": True}

    monkeypatch.setattr(local_bridge, "call", fake_call)
    out = tools.dispatch("local_open", {"target": "C:/Windows/notepad.exe"})
    assert out["ok"] is True and calls[0][1] == "local_open"
    assert calls[0][2]["target"] == "C:/Windows/notepad.exe"


def test_gui_tools_blocked_when_local_disabled(monkeypatch):
    monkeypatch.setattr(tools, "_local_policy", lambda: (False, True))
    out = tools.dispatch("local_open", {"target": "notepad"})
    assert out["ok"] is False and "未开启" in out["error"]
