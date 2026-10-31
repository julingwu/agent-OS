"""本地操作护栏验收：目录白名单 + 审批预览（v1.9）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db, local_bridge, local_policy
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
        return devices.issue(conn, name="pytest-guard", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def _set_dirs(dirs):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (local_policy.SETTING_KEY, json.dumps(dirs), "2026-01-01T00:00:00+08:00"),
        )
        conn.commit()


# ---------------------------------------------------------------- 白名单
def test_allowlist_empty_means_unrestricted():
    _set_dirs([])
    assert local_policy.allow_dirs() == []
    ok, reason = local_policy.check_path("local_write", "C:/Windows/system32/x.dll")
    assert ok and reason == ""


def test_allowlist_blocks_outside_and_allows_inside():
    _set_dirs(["C:\\Users\\me\\Documents", "D:/work"])
    assert local_policy.check_path("local_write", "C:\\Users\\me\\Documents\\a.txt")[0]
    assert local_policy.check_path("local_write", "c:/users/me/documents/sub/b.txt")[0]
    assert local_policy.check_path("local_write", "D:\\work\\proj\\x.py")[0]
    ok, reason = local_policy.check_path("local_write", "C:\\Windows\\evil.dll")
    assert not ok and "不在允许目录" in reason


def test_allowlist_is_not_prefix_naive():
    """C:\\Users\\mew 不能被 C:\\Users\\me 放行——按分隔符边界比较。"""
    _set_dirs(["C:\\Users\\me"])
    assert not local_policy.check_path("local_write", "C:\\Users\\mew\\x.txt")[0]


def test_allowlist_blocks_path_traversal():
    """利用 .. 跨目录逃逸必须在服务端归一化后精准拦截。"""
    _set_dirs(["C:\\Users\\me\\Documents"])
    # 试图从 Documents 逃逸到 Windows
    evil = "C:\\Users\\me\\Documents\\..\\..\\Windows\\System32\\calc.exe"
    ok, reason = local_policy.check_path("local_write", evil)
    assert not ok and "不在允许目录" in reason
    # 相对路径正常在目录内则放行
    safe_rel = "C:\\Users\\me\\Documents\\sub\\..\\safe.txt"
    assert local_policy.check_path("local_write", safe_rel)[0]


def test_tool_rejects_before_asking_user(monkeypatch):
    """越界调用直接拒绝，连审批都不该弹。"""
    _set_dirs(["C:\\safe"])
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, True))
    asked = []
    monkeypatch.setattr(local_bridge, "request_approval",
                        lambda *a, **kw: asked.append(1) or True)
    out = tools.dispatch("local_write", {"path": "C:\\Windows\\x.txt", "content": "hi"})
    assert out["ok"] is False and "不在允许目录" in out["error"]
    assert asked == []


def test_exec_cwd_is_checked(monkeypatch):
    _set_dirs(["C:\\safe"])
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, True))
    out = tools.dispatch("local_exec", {"cmd": "dir", "cwd": "C:\\Windows"})
    assert out["ok"] is False and "不在允许目录" in out["error"]


# ---------------------------------------------------------------- 预览
def test_write_preview_shows_size_lines_and_excerpt():
    preview = local_policy.build_preview(
        "local_write", {"path": "C:\\a\\b.txt", "content": "第一行\n第二行"})
    assert preview["kind"] == "write"
    assert preview["lines"] == 2 and preview["bytes"] > 0
    assert "第一行" in preview["excerpt"]


def test_exec_preview_keeps_command_visible():
    preview = local_policy.build_preview(
        "local_exec", {"cmd": "del /q C:\\tmp\\*", "cwd": "C:\\tmp"})
    assert preview["kind"] == "exec" and "del /q" in preview["cmd"]
    assert preview["cwd"] == "C:\\tmp"


def test_type_preview_counts_chars():
    preview = local_policy.build_preview(
        "local_type", {"text": "你好世界", "title": "记事本"})
    assert preview["kind"] == "type" and preview["chars"] == 4
    assert preview["title"] == "记事本"


def test_describe_is_human_readable():
    line = local_policy.describe("local_write",
                                {"path": "C:\\a.txt", "content": "x\ny"})
    assert "C:\\a.txt" in line and "2 行" in line
    assert "打开" in local_policy.describe("local_open", {"target": "记事本"})
    assert "关闭" in local_policy.describe("local_close", {"title": "记事本"})


def test_approval_event_carries_preview(monkeypatch):
    _set_dirs([])
    monkeypatch.setattr(tools, "_local_policy", lambda: (True, True))
    monkeypatch.setattr(local_bridge, "first_device", lambda: "dev_fake")
    monkeypatch.setattr(local_bridge, "call", lambda *a, **k: {"ok": True})
    captured = {}

    def fake_approval(emit, *, action, detail, risk="normal", preview=None):
        captured.update({"action": action, "detail": detail,
                         "risk": risk, "preview": preview})
        return True

    monkeypatch.setattr(local_bridge, "request_approval", fake_approval)
    tools.dispatch("local_write", {"path": "C:\\safe\\x.txt", "content": "hello"})
    assert captured["preview"]["kind"] == "write"
    assert captured["preview"]["path"] == "C:\\safe\\x.txt"
    assert "hello" in captured["preview"]["excerpt"]
    assert "字节" in captured["detail"]


def test_pending_exposes_preview(monkeypatch):
    """刷新界面后从 /v1/local/approvals 拉到的卡片也要带 preview。"""
    import threading

    local_bridge._approvals["apr_x"] = {
        "event": threading.Event(), "decision": None,
        "action": "写入本机文件", "detail": "C:\\a.txt", "risk": "high",
        "preview": {"kind": "write", "path": "C:\\a.txt"},
    }
    try:
        items = local_bridge.pending()
        assert items[0]["preview"]["kind"] == "write"
    finally:
        local_bridge._approvals.pop("apr_x", None)


# ---------------------------------------------------------------- 设置接口
def test_settings_accepts_allow_dirs(client, device):
    res = client.put("/v1/settings", headers=_auth(device),
                     json={"values": {"local_allow_dirs": ["C:\\work"]}})
    assert res.status_code == 200
    assert res.json()["values"]["local_allow_dirs"] == ["C:\\work"]
    with db.get_conn() as conn:
        directions = local_policy.allow_dirs()
    assert directions == ["C:\\work"]


def test_norm_tolerates_paste_mistakes():
    """用户会把路径粘成双反斜杠/正斜杠/带引号——归一化必须都认，否则白名单拦自己的目录。"""
    assert local_policy._norm("C:/Users/me/Documents/") == local_policy._norm(
        "C:\\Users\\me\\Documents")
    assert local_policy._norm('"C:\\Users\\me"') == local_policy._norm("c:\\users\\me")


def test_allowlist_tolerates_doubled_separators():
    _set_dirs(["C:\\Users\\me\\Documents"])       # 多一层转义存进来的坏值
    ok, _ = local_policy.check_path("local_write", "C:\\Users\\me\\Documents\\a.txt")
    assert ok is True
