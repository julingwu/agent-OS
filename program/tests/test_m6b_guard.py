"""M6b: callback hardening — MsgId replay guard + source-IP allowlist."""
from __future__ import annotations

import base64
import os
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient

TOKEN = "tok-wecom"
CORP = "ww-demo-corp"
AES = base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("RUNNER", "mock")
    monkeypatch.setenv("WECOM_MOCK", "1")
    monkeypatch.setenv("WECOM_WATCHER", "0")
    monkeypatch.setenv("WECOM_TOKEN", TOKEN)
    monkeypatch.setenv("WECOM_CORP_ID", CORP)
    monkeypatch.setenv("WECOM_AES_KEY", AES)
    monkeypatch.setenv("WECOM_SIM_TOKEN", "sim")
    monkeypatch.setenv("WECOM_IP_ALLOWLIST", "0")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app
    from app.wecom import bridge

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        mgr = identity.issue(conn, name="mgr", role="manager")
        conn.commit()
        bridge.use_transport(raw._transport)
        yield {"raw": raw, "mgr": mgr,
               "client": SignedClient(raw, mgr["id"], mgr["secret"]),
               "sim": {"X-Wecom-Token": "sim"}}
        bridge.use_transport(None)
        conn.close()


def post_message(raw, content: str, msg_id: str, user: str = "ZhangSan", ts="1700000000"):
    from app.wecom import crypto

    inner = ("<xml><ToUserName><![CDATA[ww]]></ToUserName>"
             f"<FromUserName><![CDATA[{user}]]></FromUserName>"
             "<CreateTime>1700000000</CreateTime><MsgType><![CDATA[text]]></MsgType>"
             f"<Content><![CDATA[{content}]]></Content><MsgId>{msg_id}</MsgId></xml>")
    enc = crypto.encrypt(inner, AES, CORP)
    sig = crypto.signature(TOKEN, ts, "n1", enc)
    body = f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
    return raw.post("/wecom/callback", params={"msg_signature": sig, "timestamp": ts,
                                               "nonce": "n1"}, content=body.encode())


def bind(raw, user, ident_id):
    raw.post("/wecom/bind", headers={"X-Wecom-Token": "sim"},
             json={"userid": user, "identity_id": ident_id})


def count_tasks(env) -> int:
    return env["client"].get("/tasks").json()["total"]


# ------------------------------------------------------------------ replay
def test_m6b_1_same_msgid_is_handled_once(env):
    raw = env["raw"]
    bind(raw, "ZhangSan", env["mgr"]["id"])
    first = post_message(raw, "写一篇 500 字短文", msg_id="1001")
    assert first.status_code == 200 and first.text == "success"
    after_first = count_tasks(env)

    second = post_message(raw, "写一篇 500 字短文", msg_id="1001")
    assert second.status_code == 200 and second.text == "success"      # still acked
    assert count_tasks(env) == after_first                      # but not re-executed


def test_m6b_2_a_different_msgid_still_creates_a_task(env):
    raw = env["raw"]
    bind(raw, "ZhangSan", env["mgr"]["id"])
    post_message(raw, "写一篇 500 字短文", msg_id="1002")
    n1 = count_tasks(env)
    post_message(raw, "写一篇 600 字短文", msg_id="1003")
    assert count_tasks(env) == n1 + 1


def test_m6b_3_message_key_falls_back_for_events():
    from app.wecom import guard

    msg = {"FromUserName": "a", "CreateTime": "1", "MsgType": "event",
           "Event": "template_card_event", "EventKey": "accept:T-1"}
    assert guard.message_key(msg, "") == "evt:a|1|event|template_card_event|accept:T-1"
    assert guard.message_key({"MsgId": "9"}, "") == "msg:9"
    import hashlib
    assert guard.message_key({}, "raw-xml") == "raw:" + hashlib.sha256(b"raw-xml").hexdigest()


# ------------------------------------------------------------------ ip allowlist
def test_m6b_4_ip_allowlist_blocks_outsiders(env, monkeypatch):
    raw = env["raw"]
    bind(raw, "ZhangSan", env["mgr"]["id"])
    monkeypatch.setenv("WECOM_IP_ALLOWLIST", "1")
    monkeypatch.setenv("WECOM_IP_RANGES", "10.0.0.0/8")
    monkeypatch.setenv("WECOM_TRUST_PROXY", "1")

    r = post_message(raw, "写一篇 500 字短文", msg_id="2001")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "IpNotAllowed"

    # a forwarded address inside the allowlist is accepted
    from app.wecom import crypto
    inner = ("<xml><FromUserName><![CDATA[ZhangSan]]></FromUserName>"
             "<CreateTime>1700000000</CreateTime><MsgType><![CDATA[text]]></MsgType>"
             "<Content><![CDATA[写一篇 500 字短文]]></Content><MsgId>2002</MsgId></xml>")
    enc = crypto.encrypt(inner, AES, CORP)
    sig = crypto.signature(TOKEN, "1700000000", "n1", enc)
    ok = raw.post("/wecom/callback", params={"msg_signature": sig, "timestamp": "1700000000",
                                             "nonce": "n1"},
                  headers={"CF-Connecting-IP": "10.1.2.3"},
                  content=f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>".encode())
    assert ok.status_code == 200 and ok.text == "success"


def test_m6b_5_ip_allowlist_off_by_default(env):
    from app.wecom import guard

    assert guard.ip_allowed("8.8.8.8") is True
