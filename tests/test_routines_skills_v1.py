"""例行任务 + 技能（v1.8）验收。

覆盖：调度表达式解析、到下期计算、CRUD、到期触发、手动触发、执行产出事件、
技能 CRUD/隔离/渲染、模型侧工具（list_skills / use_skill / list_routines）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, routines, skills
from app import events as ev
from app.agent import loop as agent_loop
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
        return devices.issue(conn, name="pytest-routines", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


@pytest.fixture()
def bot_session():
    """造一对可用的 bot + session，例行任务需要真实外键语义上成立的 id。"""
    with db.get_conn() as conn:
        bot = conn.execute("SELECT id FROM bots LIMIT 1").fetchone()
        bot_id = bot["id"] if bot else "bot_chief"
        sid = "ses_routine_test"
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, kind, title, created_at)"
            " VALUES (?,?,?,?)",
            (sid, "direct", "例行任务测试", ev.now_iso()),
        )
        conn.commit()
    return bot_id, sid


# ---------------------------------------------------------------- 调度表达式
def test_parse_schedule_interval_and_daily():
    assert routines.parse_schedule("interval:30m") == ("interval", 30)
    assert routines.parse_schedule("interval:2h") == ("interval", 120)
    assert routines.parse_schedule("interval:90") == ("interval", 90)
    assert routines.parse_schedule("daily:09:30") == ("daily", (9, 30))


def test_parse_schedule_rejects_garbage():
    for bad in ("", "every 5 min", "interval:0m", "daily:25:00", "cron:* * * * *"):
        with pytest.raises(ValueError):
            routines.parse_schedule(bad)


def test_next_run_interval_and_daily():
    base = datetime(2026, 10, 27, 10, 0, 0)
    assert routines.next_run_at("interval:30m", base) == base + timedelta(minutes=30)
    # 09:00 已经过了 → 顺到明天
    assert routines.next_run_at("daily:09:00", base).day == 28
    # 23:00 还没到 → 今天
    assert routines.next_run_at("daily:23:00", base).day == 27


def test_describe_schedule():
    assert routines.describe_schedule("interval:30m") == "每 30 分钟"
    assert routines.describe_schedule("interval:2h") == "每 2 小时"
    assert routines.describe_schedule("daily:09:05") == "每天 09:05"


# ---------------------------------------------------------------- CRUD / REST
def test_routine_crud_over_http(client, device, bot_session):
    bot_id, sid = bot_session
    res = client.post("/v1/routines", headers=_auth(device), json={
        "bot_id": bot_id, "session_id": sid, "title": "每日早报",
        "prompt": "汇总昨天的进展", "schedule": "daily:09:00"})
    assert res.status_code == 200, res.text
    item = res.json()["item"]
    assert item["enabled"] is True and item["next_run_at"]
    assert item["schedule_human"] == "每天 09:00"

    listed = client.get(f"/v1/routines?bot_id={bot_id}", headers=_auth(device)).json()
    assert listed["count"] == 1

    patched = client.patch(f"/v1/routines/{item['id']}", headers=_auth(device),
                           json={"enabled": False}).json()["item"]
    assert patched["enabled"] is False and patched["next_run_at"] is None

    assert client.delete(f"/v1/routines/{item['id']}",
                         headers=_auth(device)).status_code == 200
    assert client.get(f"/v1/routines?bot_id={bot_id}",
                      headers=_auth(device)).json()["count"] == 0


def test_routine_rejects_bad_schedule(client, device, bot_session):
    bot_id, sid = bot_session
    res = client.post("/v1/routines", headers=_auth(device), json={
        "bot_id": bot_id, "session_id": sid, "title": "x",
        "prompt": "y", "schedule": "每五分钟"})
    assert res.status_code == 400
    assert "schedule" in res.json()["detail"]


def test_schedule_preview(client, device):
    body = client.get("/v1/routines/schedule/preview?schedule=interval:2h",
                      headers=_auth(device)).json()
    assert body["human"] == "每 2 小时" and body["next_run_at"]


# ---------------------------------------------------------------- 触发
def test_due_picks_only_enabled_and_overdue(client, device, bot_session):
    bot_id, sid = bot_session
    with db.get_conn() as conn:
        routines.ensure(conn)
        now = datetime.now().astimezone()
        past = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
        future = (now + timedelta(hours=1)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO routines (id,bot_id,session_id,title,prompt,schedule,"
            "enabled,next_run_at,run_count,created_at) VALUES"
            " ('rtn_due',?,?,'到期','p','interval:30m',1,?,0,?)",
            (bot_id, sid, past, ev.now_iso()))
        conn.execute(
            "INSERT INTO routines (id,bot_id,session_id,title,prompt,schedule,"
            "enabled,next_run_at,run_count,created_at) VALUES"
            " ('rtn_later',?,?,'没到','p','interval:30m',1,?,0,?)",
            (bot_id, sid, future, ev.now_iso()))
        conn.execute(
            "INSERT INTO routines (id,bot_id,session_id,title,prompt,schedule,"
            "enabled,next_run_at,run_count,created_at) VALUES"
            " ('rtn_off',?,?,'停用','p','interval:30m',0,?,0,?)",
            (bot_id, sid, past, ev.now_iso()))
        conn.commit()
        items = routines.due(conn)
    ids = [i["id"] for i in items]
    assert "rtn_due" in ids and "rtn_later" not in ids and "rtn_off" not in ids


def test_run_due_executes_turn_and_emits_events(client, device, bot_session, monkeypatch):
    bot_id, sid = bot_session
    seen = {}

    def fake_run_turn(bot, session, *, db_path=None, hops=0, task_hint=None):
        seen["bot"] = bot
        seen["task_hint"] = task_hint
        return {"status": "done"}

    monkeypatch.setattr(agent_loop, "run_turn", fake_run_turn)

    with db.get_conn() as conn:
        item = routines.create(conn, bot_id=bot_id, session_id=sid,
                               title="巡检", prompt="检查服务器磁盘",
                               schedule="interval:30m")
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
        conn.execute("UPDATE routines SET next_run_at=? WHERE id=?", (past, item["id"]))
        conn.commit()

    results = routines.run_due_once()
    assert results and results[0]["status"] == "done"
    assert "检查服务器磁盘" in seen["task_hint"] and "巡检" in seen["task_hint"]

    with db.get_conn() as conn:
        kinds = [e.kind for e in ev.read(conn, since=0, limit=ev.MAX_READ_LIMIT)]
        after = routines.get(conn, item["id"])
    assert "routine.triggered" in kinds
    assert "routine.finished" in kinds
    assert "message.system" in kinds
    assert after["run_count"] == 1
    assert after["last_status"] == "done"
    assert after["next_run_at"] > past


def test_routine_failure_is_contained(client, device, bot_session, monkeypatch):
    bot_id, sid = bot_session

    def boom(bot, session, **kw):
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(agent_loop, "run_turn", boom)
    with db.get_conn() as conn:
        item = routines.create(conn, bot_id=bot_id, session_id=sid,
                               title="会挂的任务", prompt="x", schedule="interval:30m")
    with db.get_conn() as conn:
        rtn = routines.get(conn, item["id"])
    out = routines.execute(rtn)
    assert out["status"] == "failed"
    with db.get_conn() as conn:
        assert routines.get(conn, item["id"])["last_status"] == "failed"


# ---------------------------------------------------------------- 技能
def test_skill_crud_and_owner_isolation(client, device):
    body = {"name": "写周报", "description": "把零散进展写成周报",
            "body": "按 进展/风险/下周计划 三段写。本周内容：{input}"}
    res = client.post("/v1/skills", headers=_auth(device), json=body)
    assert res.status_code == 200, res.text
    global_id = res.json()["item"]["id"]

    # 同名再建要 409
    assert client.post("/v1/skills", headers=_auth(device),
                       json=body).status_code == 409

    # 私有技能可以覆盖全局同名
    private = client.post("/v1/skills", headers=_auth(device),
                          json={**body, "bot_id": "bot_a", "body": "私有不外传"}).json()["item"]
    with db.get_conn() as conn:
        found = skills.find_by_name(conn, "写周报", bot_id="bot_a")
        other = skills.find_by_name(conn, "写周报", bot_id="bot_b")
    assert found["id"] == private["id"]
    assert other["id"] == global_id

    assert client.delete(f"/v1/skills/{global_id}", headers=_auth(device)).status_code == 200


def test_skill_render_placeholder():
    skill = {"body": "按模板写：{input}", "name": "x"}
    assert skills.render(skill, "本周做了 A") == "按模板写：本周做了 A"
    plain = {"body": "固定流程", "name": "y"}
    assert "本次输入：abc" in skills.render(plain, "abc")


def test_use_skill_tool_returns_instructions_and_bumps_use(client, device):
    with db.get_conn() as conn:
        skills.create(conn, bot_id=None, name="服务器巡检",
                      description="检查磁盘/内存", body="1) df -h 2) free -h 3) 汇报异常")
    out = tools.dispatch("use_skill", {"name": "服务器巡检"}, bot_id="bot_chief")
    assert out["ok"] is True
    assert "df -h" in out["instructions"]
    with db.get_conn() as conn:
        row = conn.execute("SELECT use_count FROM skills WHERE name='服务器巡检'").fetchone()
    assert row["use_count"] == 1

    missing = tools.dispatch("use_skill", {"name": "不存在的技能"}, bot_id="bot_chief")
    assert missing["ok"] is False and "list_skills" in missing["hint"]


def test_list_skills_and_routines_tools(client, device, bot_session):
    bot_id, sid = bot_session
    with db.get_conn() as conn:
        skills.create(conn, bot_id=None, name="全局技能A", description="d", body="b")
        skills.create(conn, bot_id=bot_id, name="私有技能B", description="d", body="b")
        routines.create(conn, bot_id=bot_id, session_id=sid, title="每天早报",
                        prompt="p", schedule="daily:09:00")
    listed = tools.dispatch("list_skills", {}, bot_id=bot_id)
    names = {i["name"]: i["owner"] for i in listed["items"]}
    assert names["全局技能A"] == "全局" and names["私有技能B"] == "本 Bot"

    rtn = tools.dispatch("list_routines", {}, bot_id=bot_id)
    assert rtn["count"] >= 1
    assert any(i["title"] == "每天早报" and i["schedule"] == "每天 09:00"
               for i in rtn["items"])


def test_run_routine_endpoint_404(client, device):
    assert client.post("/v1/routines/rtn_nope/run",
                       headers=_auth(device)).status_code == 404
