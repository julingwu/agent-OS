"""M6: WeCom middleware — crypto, text→task, card push, card action, binding."""
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
AES_KEY = base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("RUNNER", "mock")
    monkeypatch.setenv("WECOM_MOCK", "1")
    monkeypatch.setenv("WECOM_WATCHER", "0")
    monkeypatch.setenv("WECOM_AUTORUN", "0")
    monkeypatch.setenv("WECOM_TOKEN", TOKEN)
    monkeypatch.setenv("WECOM_CORP_ID", CORP)
    monkeypatch.setenv("WECOM_AES_KEY", AES_KEY)
    monkeypatch.setenv("WECOM_SIM_TOKEN", "sim-token")
    monkeypatch.setenv("WECOM_IP_ALLOWLIST", "0")   # the .env now enforces it for real
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app
    from app.wecom import bridge

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        sys_id = identity.issue(conn, name="ops", role="system")
        mgr = identity.issue(conn, name="mgr", role="manager", legal_entity_id="LE-GROUP-HQ")
        ceo = identity.issue(conn, name="ceo", role="ceo", legal_entity_id="LE-GROUP-HQ")
        conn.commit()
        bridge.use_transport(raw._transport)   # in-process, still signed
        yield {"raw": raw,
               "sys": SignedClient(raw, sys_id["id"], sys_id["secret"]),
               "mgr_id": mgr["id"], "ceo_id": ceo["id"],
               "sim": {"X-Wecom-Token": "sim-token"}}
        bridge.use_transport(None)
        conn.close()


# ------------------------------------------------------------------ crypto
def test_m6_1_crypto_roundtrip_and_signature():
    from app.wecom import crypto

    enc = crypto.encrypt("<xml><Content>你好</Content></xml>", AES_KEY, CORP)
    assert crypto.decrypt(enc, AES_KEY, CORP) == "<xml><Content>你好</Content></xml>"

    sig = crypto.signature(TOKEN, "123", "abc", enc)
    assert crypto.verify(TOKEN, "123", "abc", enc, sig)
    assert not crypto.verify(TOKEN, "123", "abc", enc, "0" * 40)
    with pytest.raises(crypto.WeComCryptoError):
        crypto.decrypt(enc, AES_KEY, "other-corp")


def test_m6_2_url_verification_echoes_plaintext(env):
    from app.wecom import crypto

    challenge = "echo-12345"
    enc = crypto.encrypt(challenge, AES_KEY, CORP)
    ts, nonce = "1700000000", "n0nce"
    sig = crypto.signature(TOKEN, ts, nonce, enc)
    r = env["raw"].get("/wecom/callback", params={
        "msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": enc})
    assert r.status_code == 200 and r.text == challenge

    bad = env["raw"].get("/wecom/callback", params={
        "msg_signature": "0" * 40, "timestamp": ts, "nonce": nonce, "echostr": enc})
    assert bad.status_code == 403


# ------------------------------------------------------------------ binding + text
def test_m6_3_unbound_user_is_told_how_to_bind(env):
    r = env["raw"].post("/wecom/simulate", headers=env["sim"],
                        json={"userid": "stranger", "text": "写一篇 300 字短文"}).json()
    assert "没有绑定" in r["reply"]


def test_m6_4_text_creates_a_task(env):
    env["raw"].post("/wecom/bind", headers=env["sim"],
                    json={"userid": "ZhangSan", "identity_id": env["mgr_id"]})
    r = env["raw"].post("/wecom/simulate", headers=env["sim"],
                        json={"userid": "ZhangSan", "text": "写一篇 400-600 字的秋季护肤短文"}).json()
    assert "已建任务 T-" in r["reply"]
    assert "content-v1" in r["reply"]

    tid = r["reply"].split("已建任务 ")[1].split()[0]
    detail = env["sys"].get(f"/tasks/{tid}").json()
    assert detail["task"]["legal_entity_id"] == "LE-GROUP-HQ"
    assert len(detail["criteria"]) >= 5


def test_m6_5_sim_endpoints_need_the_token(env):
    assert env["raw"].post("/wecom/simulate", json={"userid": "x", "text": "y"}).status_code == 401


# ------------------------------------------------------------------ cards
def test_m6_6_watcher_pushes_once_and_renders_the_card(env):
    raw = env["raw"]
    raw.post("/wecom/bind", headers=env["sim"], json={"userid": "Boss", "identity_id": env["ceo_id"]})
    t = env["sys"].post("/tasks/from_sentence", json={"text": "写一篇 500 字短文"}).json()
    run = env["sys"].post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    env["sys"].post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})

    first = raw.post("/wecom/watcher/tick", headers=env["sim"]).json()
    assert first["sent"]["task_review"] >= 1

    out = raw.get("/wecom/outbox", headers=env["sim"]).json()
    card = out["messages"][0]
    assert card["msgtype"] == "template_card"
    assert t["task_id"] in card["body"]
    assert "accept:" + t["task_id"] in card["body"]

    second = raw.post("/wecom/watcher/tick", headers=env["sim"]).json()
    assert second["sent"]["task_review"] == 0          # ledger prevents duplicates


def test_m6_7_card_click_runs_acceptance(env):
    raw = env["raw"]
    raw.post("/wecom/bind", headers=env["sim"], json={"userid": "Boss", "identity_id": env["ceo_id"]})
    t = env["sys"].post("/tasks/from_sentence", json={"text": "写一篇 500 字短文"}).json()
    run = env["sys"].post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    env["sys"].post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})

    r = raw.post("/wecom/simulate/event", headers=env["sim"],
                 json={"userid": "Boss", "event_key": f"accept:{t['task_id']}"}).json()
    assert "验收结果" in r["reply"] or "验收失败" in r["reply"]
    detail = env["sys"].get(f"/tasks/{t['task_id']}").json()
    assert some_judged(detail["criteria"])


def some_judged(criteria) -> bool:
    return any(c["state"] != "pending" or c["check_type"] == "human_check" for c in criteria)


def test_m6_8_decision_card_and_approve(env):
    raw = env["raw"]
    raw.post("/wecom/bind", headers=env["sim"], json={"userid": "Boss", "identity_id": env["ceo_id"]})
    dec = env["sys"].post("/decisions", json={"title": "追加预算 8 万",
                                              "amount": 80000}).json()
    env["sys"].post(f"/decisions/{dec['id']}/evaluate")

    tick = raw.post("/wecom/watcher/tick", headers=env["sim"]).json()
    assert tick["sent"]["decision_ceo"] >= 1

    out = raw.get("/wecom/outbox", headers=env["sim"]).json()
    assert any("approve:" + dec["id"] in m["body"] for m in out["messages"])

    r = raw.post("/wecom/simulate/event", headers=env["sim"],
                 json={"userid": "Boss", "event_key": f"approve:{dec['id']}"}).json()
    assert "已同意" in r["reply"]
    assert env["sys"].get(f"/decisions/{dec['id']}").json()["state"] == "approved"


def test_m6_9_autorun_moves_the_task_forward(env, monkeypatch):
    """A sentence from WeCom is not just a task row: it starts work (mock runner)."""
    import time

    monkeypatch.setenv("WECOM_AUTORUN", "1")
    monkeypatch.setenv("WECOM_AUTORUN_RUNNER", "mock")
    raw = env["raw"]
    raw.post("/wecom/bind", headers=env["sim"], json={"userid": "ZhangSan",
                                                      "identity_id": env["ceo_id"]})
    r = raw.post("/wecom/simulate", headers=env["sim"],
                 json={"userid": "ZhangSan", "text": "写一篇 500 字短文"}).json()
    assert "已开工" in r["reply"]
    tid = r["reply"].split("已建任务 ")[1].split()[0]

    for _ in range(40):
        state = env["sys"].get(f"/tasks/{tid}").json()["task"]["state"]
        if state in ("pending_review", "completed", "failed"):
            break
        time.sleep(0.1)
    assert state in ("pending_review", "completed"), state


def test_m6_10_human_criterion_can_be_signed_from_the_chat(env):
    raw = env["raw"]
    raw.post("/wecom/bind", headers=env["sim"], json={"userid": "Boss", "identity_id": env["ceo_id"]})
    t = env["sys"].post("/tasks/from_sentence", json={"text": "写一篇 500 字短文"}).json()
    run = env["sys"].post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    env["sys"].post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})
    env["sys"].post(f"/tasks/{t['task_id']}/acceptance/run")

    detail = env["sys"].get(f"/tasks/{t['task_id']}").json()
    human = [c for c in detail["criteria"] if c["check_type"] == "human_check"
             and c["state"] == "pending"][0]

    r = raw.post("/wecom/simulate/event", headers=env["sim"],
                 json={"userid": "Boss", "event_key": f"judge:{human['id']}"}).json()
    assert "已通过" in r["reply"]
    after = env["sys"].get(f"/tasks/{t['task_id']}").json()
    assert [c for c in after["criteria"] if c["id"] == human["id"]][0]["state"] == "passed"
