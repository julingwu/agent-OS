"""v4.0 企微群机器人通知：9/9 闭环与红线拦截必须推送，且推送失败不能拖垮主流程。

断言口径：
* 事件先落 ``notifications``（证据），再投递；重复事件按 (tenant,event,ref) 去重。
* 机器人未配置/停用/网络失败时，通知记 failed，但产线与验收结果不受影响。
* 配置接口只回 ``webhook_set`` + 尾部提示，绝不回整串 key。
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import acceptance_run, experience, identity, notify, pipeline
from app.db import connect, init_db
from app.ui import api as ui_api
from app.ui.extensions import router as extensions_router
from app.wecom import robot

OFFICIAL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + "a" * 32


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = tmp_path / "notify.db"
    for key, value in {
        "DB_PATH": str(path),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "UI_SESSION_SECRET": "notify-test-only-secret",
        "WECOM_MOCK": "1",          # 群机器人走本地 outbox，不碰真实企微
        "NOTIFY_SYNC": "1",         # 同步投递，测试可确定地断言
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WECOM_ROBOT_WEBHOOK", raising=False)
    monkeypatch.delenv("WECOM_ROBOT_ALLOW_CUSTOM_HOST", raising=False)
    init_db()
    conn = connect(str(path))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    ceo = identity.issue(conn, name="CEO", role="ceo")
    agent = identity.issue(conn, name="Agent", role="agent")
    notify.ensure(conn)
    robot.ensure(conn)
    pipeline.ensure(conn)
    conn.commit()
    yield conn, {"ceo": ceo, "agent": agent}
    conn.close()


def set_robot(conn, who, url=OFFICIAL, **kwargs):
    return robot.save(conn, tenant_id="TENANT-HQ", actor=who["id"], webhook=url, **kwargs)


def project(conn, *, done=9, gates=12, pid="P-NOTIFY"):
    conn.execute("INSERT INTO projects(id,name,requirement,legal_entity_id,tenant_id,owner_identity)"
                 " VALUES (?,?,?,'LE-GROUP-HQ','TENANT-HQ',?)",
                 (pid, "官网外单", "做一个官网", "ID-CEO"))
    for seq, (key, title) in enumerate(pipeline.STAGES):
        conn.execute("INSERT INTO project_stages(id,project_id,seq,key,title,state)"
                     " VALUES (?,?,?,?,?,?)",
                     (f"ST-{seq}", pid, seq, key, title, "done" if seq < done else "pending"))
    for i in range(gates):
        conn.execute("INSERT INTO stage_gates(id,project_id,stage_key,name,state)"
                     " VALUES (?,?,?,?, 'passed')", (f"G-{i}", pid, "intake", f"闸门{i}"))
    conn.commit()
    return pid


def submission(conn, folder, *, task="T1", run="R1", text="绝对最好的产品"):
    conn.execute("INSERT OR IGNORE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES (?,'LE-GROUP-HQ','TENANT-HQ','content_write','宣传稿','pending_review')",
                 (task,))
    conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state)"
                 " VALUES (?,?,1,'LE-GROUP-HQ','TENANT-HQ','succeeded')", (run, task))
    path = folder / run / "page.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    conn.execute("INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,content,checksum,bytes)"
                 " VALUES (?,?,'TENANT-HQ',1,?,?,?,?)",
                 ("A" + run, run, str(path), text,
                  hashlib.sha256(text.encode()).hexdigest(), len(text.encode())))
    conn.commit()
    return path


def outbox(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM wecom_robot_outbox ORDER BY rowid").fetchall()]


# ------------------------------------------------------------------ 配置面
def test_settings_never_return_the_webhook_key(env):
    conn, who = env
    out = set_robot(conn, who["ceo"])
    assert out["webhook_set"] is True and out["source"] == "tenant"
    assert "a" * 32 not in str(out), "配置读接口不能回整串 key"
    assert out["webhook_hint"].startswith("…")


def test_agent_cannot_configure_robot(env):
    conn, who = env
    with pytest.raises(PermissionError, match="AdminRequired"):
        robot.save(conn, tenant_id="TENANT-HQ", actor=who["agent"]["id"], webhook=OFFICIAL)
    with pytest.raises(PermissionError, match="AdminRequired"):
        robot.settings(conn, tenant_id="TENANT-HQ", actor=who["agent"]["id"])


def test_only_official_endpoint_is_accepted(env):
    conn, who = env
    with pytest.raises(ValueError, match="UnsupportedWebhookHost"):
        robot.validate_webhook("https://evil.example.com/cgi-bin/webhook/send?key=" + "a" * 32)
    with pytest.raises(ValueError, match="WebhookMustBeHttps"):
        robot.validate_webhook("http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + "a" * 32)
    with pytest.raises(ValueError, match="InvalidWebhookKey"):
        robot.validate_webhook("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=short")


def test_http_endpoints_are_admin_only_and_masked(env):
    conn, who = env
    app = FastAPI()
    app.include_router(extensions_router)
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(who["agent"]["id"]))
        assert client.get("/ui/wecom/robot").status_code == 403
        client.cookies.set("oa_ui", ui_api._make_cookie(who["ceo"]["id"]))
        saved = client.put("/ui/wecom/robot", json={"webhook": OFFICIAL})
        assert saved.status_code == 200
        body = saved.json()
        assert body["webhook_set"] is True and "a" * 32 not in saved.text
        tested = client.post("/ui/wecom/robot/test")
        assert tested.status_code == 200 and tested.json()["transport"] == "outbox"


# ------------------------------------------------------------ 9/9 商业闭环
def test_project_closed_pushes_once_when_all_nine_stages_are_done(env):
    conn, who = env
    set_robot(conn, who["ceo"])
    pid = project(conn)
    first = notify.project_closed(conn, pid)
    assert first and first["event"] == "project_closed"
    rows = outbox(conn)
    assert len(rows) == 1, "9/9 闭环只能推一条"
    assert "9/9" in rows[0]["body"] and "官网外单" in rows[0]["body"]
    # 重复调用（轮询/重入）不能重复推送
    assert notify.project_closed(conn, pid) is None
    assert len(outbox(conn)) == 1
    state = conn.execute("SELECT state FROM notifications WHERE event='project_closed'").fetchone()
    assert state["state"] == "sent"


def test_project_closed_waits_for_the_last_stage(env):
    conn, who = env
    set_robot(conn, who["ceo"])
    pid = project(conn, done=8)
    assert notify.project_closed(conn, pid) is None
    assert outbox(conn) == []


def test_pipeline_run_project_triggers_the_push(env, monkeypatch):
    """真跑一遍 9 阶段（stub 执行模式），闭环后必须自动推送，不靠人工调用。"""
    conn, who = env
    set_robot(conn, who["ceo"])
    pid = "P-RUN"
    conn.execute("INSERT INTO projects(id,name,requirement,legal_entity_id,tenant_id,owner_identity)"
                 " VALUES (?,?,?,'LE-GROUP-HQ','TENANT-HQ',?)",
                 (pid, "产线闭环", "做一个 demo", who["ceo"]["id"]))
    for seq, (key, title) in enumerate(pipeline.STAGES):
        conn.execute("INSERT INTO project_stages(id,project_id,seq,key,title,state)"
                     " VALUES (?,?,?,?,?,'pending')", (f"RST-{seq}", pid, seq, key, title))
    conn.commit()
    pipeline.run_project(conn, pid, "做一个 demo")
    closed = conn.execute("SELECT COUNT(*) FROM notifications WHERE event='project_closed'"
                          " AND ref=?", (pid,)).fetchone()[0]
    assert closed == 1, "9 个阶段跑完必须落一条闭环通知"


# --------------------------------------------------------------- 红线拦截
def approve_rule(conn, who, terms="绝对,100%"):
    row = experience.add(conn, kind="decision", title="宣传红线",
                         content="宣传不得夸大承诺", mandatory=True, redline_terms=terms,
                         entity="LE-GROUP-HQ", actor=who["id"])
    return experience.review(conn, row["id"], "approve", reviewer=who["id"],
                             entity="LE-GROUP-HQ")


def test_redline_block_pushes_the_hit_terms(env, tmp_path):
    conn, who = env
    set_robot(conn, who["ceo"])
    approve_rule(conn, who["ceo"])
    submission(conn, tmp_path)
    out = acceptance_run.run(conn, "T1", ["LE-GROUP-HQ"])
    assert out["task_state"] == "rejected"
    rows = outbox(conn)
    assert len(rows) == 1 and rows[0]["event"] == "redline_blocked"
    assert "绝对" in rows[0]["body"], "推送必须写清命中了哪个禁用词"
    assert "没有人工审批入口" in rows[0]["body"]


def test_redline_dedupes_within_a_run_but_reports_a_new_run(env, tmp_path):
    conn, who = env
    set_robot(conn, who["ceo"])
    approve_rule(conn, who["ceo"])
    submission(conn, tmp_path)
    notify.redline_blocked(conn, task_id="T1", run_id="R1", detail="命中企业红线：绝对",
                           hits=[{"terms": ["绝对"]}])
    notify.redline_blocked(conn, task_id="T1", run_id="R1", detail="命中企业红线：绝对",
                           hits=[{"terms": ["绝对"]}])
    assert len(outbox(conn)) == 1, "同一 run 的重复拦截不能刷屏"
    notify.redline_blocked(conn, task_id="T1", run_id="R2", detail="命中企业红线：绝对",
                           hits=[{"terms": ["绝对"]}])
    assert len(outbox(conn)) == 2, "换一次 run 再被拦是新的信息"


def test_disabled_robot_marks_failed_without_breaking_acceptance(env, tmp_path):
    conn, who = env
    set_robot(conn, who["ceo"], enabled=False)
    approve_rule(conn, who["ceo"])
    submission(conn, tmp_path)
    out = acceptance_run.run(conn, "T1", ["LE-GROUP-HQ"])
    assert out["task_state"] == "rejected", "通知发不出去也必须照常驳回"
    row = conn.execute("SELECT state,detail FROM notifications"
                       " WHERE event='redline_blocked'").fetchone()
    assert row["state"] == "failed" and "disabled" in row["detail"]
    assert outbox(conn) == []


def test_toggle_off_silences_that_event(env, tmp_path):
    """只关「红线」这一类：拦截照常驳回，但不推送、也不留通知行。"""
    conn, who = env
    set_robot(conn, who["ceo"], on_redline=False)
    approve_rule(conn, who["ceo"])
    submission(conn, tmp_path)
    out = acceptance_run.run(conn, "T1", ["LE-GROUP-HQ"])
    assert out["task_state"] == "rejected"
    assert outbox(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


def test_unconfigured_robot_records_the_event_as_failed(env):
    conn, who = env
    pid = project(conn)
    notify.project_closed(conn, pid)
    row = conn.execute("SELECT state,detail FROM notifications").fetchone()
    assert row["state"] == "failed" and "unconfigured" in row["detail"]


def test_notifications_endpoint_is_tenant_scoped(env):
    conn, who = env
    set_robot(conn, who["ceo"])
    notify.project_closed(conn, project(conn))
    conn.execute("INSERT INTO notifications(id,tenant_id,entity_id,event,ref,subject)"
                 " VALUES ('NTF-OTHER','TENANT-B','LE-B','project_closed','P-X','别人的')")
    conn.commit()
    app = FastAPI()
    app.include_router(extensions_router)
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(who["ceo"]["id"]))
        rows = client.get("/ui/notifications").json()["notifications"]
    assert [r["subject"] for r in rows] == ["商业闭环 9/9：官网外单"]
