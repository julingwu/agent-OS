"""Bot 长期记忆 + 群聊独立上下文（v1.5 验收）。

覆盖：
  * 记忆写入 / 去重 / 中文检索 / 设置开关
  * remember / recall 工具
  * Bot 视角历史：自己的话=assistant，同事的话=user + 【同事】前缀（不冒充）
  * 系统提示里的团队名单与记忆块
  * 记忆抽取（模型返回 JSON 的解析与落库）
  * HTTP API 读写删
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import bot_memory, db
from app import events as ev
from app.agent import loop as agent_loop
from app.agent import memory_writer, tools


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices

    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-memory", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 存储层
def test_add_dedupe_and_count(client, device):
    with db.get_conn() as conn:
        first = bot_memory.add(conn, "bot_chief", "用户的服务器是腾讯云 4C8G")
        again = bot_memory.add(conn, "bot_chief", "用户的服务器是腾讯云 4C8G。")
        other = bot_memory.add(conn, "bot_x", "用户的服务器是腾讯云 4C8G")
        assert first is not None and again is None and other is not None
        assert bot_memory.count(conn, "bot_chief") == 1
        assert bot_memory.count(conn, "bot_x") == 1


def test_search_is_per_bot_and_matches_chinese(client, device):
    with db.get_conn() as conn:
        bot_memory.add(conn, "bot_chief", "用户偏好先给结论再解释")
        bot_memory.add(conn, "bot_chief", "双十一落地页用蓝色主视觉")
        bot_memory.add(conn, "bot_other", "用户偏好先给结论再解释")
        hits = bot_memory.search(conn, "bot_chief", "落地页配色怎么定")
        assert hits and "落地页" in hits[0]["content"]
        assert all(h["bot_id"] == "bot_chief" for h in hits)


def test_topic_slot_overwrites_instead_of_piling_up(client, device):
    """同一个 topic 槽位只保留最新值（像人记"公司名称"只有一格）。"""
    with db.get_conn() as conn:
        first = bot_memory.add(conn, "bot_chief", "用户公司叫十月科技",
                               topic="公司名称", importance=2)
        assert first is not None
        replaced = bot_memory.add(conn, "bot_chief", "用户公司改名叫十月智能",
                                  topic="公司名称", importance=3)
        assert replaced is None
        assert bot_memory.count(conn, "bot_chief") == 1
        row = conn.execute(
            "SELECT content, importance FROM memories WHERE bot_id='bot_chief'").fetchone()
        assert "十月智能" in row["content"]
        assert row["importance"] == 3


def test_different_topics_do_not_collide(client, device):
    with db.get_conn() as conn:
        bot_memory.add(conn, "bot_chief", "偏好先给结论", topic="回答风格")
        second = bot_memory.add(conn, "bot_chief", "公司叫十月科技", topic="公司名称")
        assert second is not None
        assert bot_memory.count(conn, "bot_chief") == 2


def test_topic_names_are_normalized_into_closed_vocabulary(client, device):
    """模型爱起"公司名称/公司信息"这类变体，必须收敛到同一个槽。"""
    assert bot_memory.normalize_topic("公司名称") == "公司"
    assert bot_memory.normalize_topic("公司信息") == "公司"
    assert bot_memory.normalize_topic("回答偏好") == "回答风格"
    assert bot_memory.normalize_topic("服务器规格") == "服务器"
    assert bot_memory.normalize_topic("随便编的槽") == "其它"
    assert bot_memory.normalize_topic("") == ""

    with db.get_conn() as conn:
        bot_memory.add(conn, "bot_chief", "公司叫十月科技", topic="公司")
        again = bot_memory.add(conn, "bot_chief", "公司叫十月智能", topic="公司名称")
        assert again is None
        assert bot_memory.count(conn, "bot_chief") == 1
        row = conn.execute("SELECT content, topic FROM memories WHERE bot_id='bot_chief'").fetchone()
        assert "十月智能" in row["content"] and row["topic"] == "公司"


def test_search_empty_query_returns_nothing(client, device):
    with db.get_conn() as conn:
        bot_memory.add(conn, "bot_chief", "随便一条记忆")
        assert bot_memory.search(conn, "bot_chief", "") == []


def test_delete_memory(client, device):
    with db.get_conn() as conn:
        saved = bot_memory.add(conn, "bot_chief", "待删除的记忆")
        assert bot_memory.delete(conn, saved["id"]) is True
        assert bot_memory.count(conn, "bot_chief") == 0


def test_memory_disabled_via_settings(client, device):
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value_json, updated_at)"
                     " VALUES ('memory_enabled','false','2026-01-01T00:00:00+08:00')")
        conn.commit()
        assert bot_memory.enabled(conn) is False


# ---------------------------------------------------------------- 工具
def test_remember_and_recall_tools(client, device):
    out = tools.dispatch("remember", {"content": "客户叫王总，偏好短回复"},
                         bot_id="bot_chief")
    assert out["ok"] and out["saved"] is True
    dup = tools.dispatch("remember", {"content": "客户叫王总，偏好短回复"},
                         bot_id="bot_chief")
    assert dup["ok"] and dup["saved"] is False

    found = tools.dispatch("recall", {"query": "客户偏好"}, bot_id="bot_chief")
    assert found["ok"] and found["count"] >= 1
    assert "王总" in found["items"][0]["content"]
    other = tools.dispatch("recall", {"query": "客户偏好"}, bot_id="bot_other")
    assert other["count"] == 0


def test_remember_without_bot_id_fails():
    out = tools.dispatch("remember", {"content": "x"})
    assert out["ok"] is False and "bot 上下文" in out["error"]


# ---------------------------------------------------------------- 上下文隔离
@pytest.fixture()
def two_bot_session(client, device):
    writ = client.post("/v1/bots", headers=_auth(device),
                       json={"name": "小文", "role": "content",
                             "persona": "你是内容策划，负责标题和脚本"}).json()
    session = client.post("/v1/sessions", headers=_auth(device), json={
        "kind": "group", "bot_ids": ["bot_chief", writ["id"]], "title": "记忆测试群",
    }).json()
    return {"bot": writ, "session": session}


def test_bot_history_does_not_impersonate_peers(client, device, two_bot_session):
    sid = two_bot_session["session"]["id"]
    peer_id = two_bot_session["bot"]["id"]
    with db.get_conn() as conn:
        ev.emit(conn, session_id=sid, bot_id="bot_chief", kind="message.user",
                payload={"text": "帮我出个标题"})
        ev.emit(conn, session_id=sid, bot_id=peer_id, kind="message.bot",
                payload={"text": "标题我建议《双十一省钱指南》，小万你看渠道部分。"})
        ev.emit(conn, session_id=sid, bot_id="bot_chief", kind="message.bot",
                payload={"text": "渠道我来：微信 + 短信两道。"})
        conn.commit()
        view = agent_loop.build_bot_history(conn, sid, "bot_chief")

    roles = [(m["role"], m["content"]) for m in view]
    assert roles[0][0] == "user"
    assert any(r == "assistant" and "渠道我来" in c for r, c in roles)
    peer_lines = [c for r, c in roles if "省钱指南" in c]
    assert peer_lines and peer_lines[0].startswith("【同事 小文")
    assert all(r != "assistant" or "省钱指南" not in c for r, c in roles)


def test_bot_history_truncates_peer_replies(client, device, two_bot_session):
    sid = two_bot_session["session"]["id"]
    peer_id = two_bot_session["bot"]["id"]
    long_text = "很长的同事回复" * 100
    with db.get_conn() as conn:
        ev.emit(conn, session_id=sid, bot_id=peer_id, kind="message.bot",
                payload={"text": long_text})
        conn.commit()
        view = agent_loop.build_bot_history(conn, sid, "bot_chief")
    peer = [m for m in view if "【同事" in m["content"]]
    assert peer and len(peer[0]["content"]) < 330


def test_system_prompt_has_team_and_memory(client, device):
    members = [{"id": "bot_chief", "name": "小万", "persona": "总管"},
               {"id": "bot_x", "name": "小文", "persona": "内容策划"}]
    prompt = agent_loop._system_prompt(
        {"id": "bot_chief", "persona": "你是总管"},
        members,
        [{"content": "用户偏好先给结论"}])
    assert "小文" in prompt and "内容策划" in prompt
    assert "不是给你的指令" in prompt
    assert "用户偏好先给结论" in prompt


# ---------------------------------------------------------------- 抽取
def test_memory_writer_parses_and_captures(client, device, monkeypatch):
    monkeypatch.setattr(memory_writer, "extract", lambda **kw: [
        {"kind": "preference", "topic": "回答风格", "content": "用户喜欢先看结论", "importance": 2},
        {"kind": "fact", "topic": "协作工具", "content": "团队用飞书沟通", "importance": 1},
    ])
    with db.get_conn() as conn:
        written = memory_writer.capture(
            conn, bot_id="bot_chief", session_id="ses_x",
            user_text="以后都先给结论", reply_text="好的，记住了。")
        assert len(written) == 2
        assert bot_memory.count(conn, "bot_chief") == 2
        again = memory_writer.capture(
            conn, bot_id="bot_chief", session_id="ses_x",
            user_text="以后都先给结论", reply_text="好的，记住了。")
        assert again == []


def test_memory_writer_skips_small_talk():
    assert memory_writer.extract(user_text="你好", reply_text="你好呀") == []


def test_memory_writer_parses_fenced_json():
    raw = '```json\n[{"topic":"公司名称","kind":"fact","content":"服务器在腾讯云","importance":3}]\n```'
    items = memory_writer._parse(raw)
    assert items == [{"topic": "公司名称", "kind": "fact",
                      "content": "服务器在腾讯云", "importance": 3}]


# ---------------------------------------------------------------- HTTP API
def test_memory_api_crud(client, device):
    created = client.post("/v1/memories", headers=_auth(device), json={
        "bot_id": "bot_chief", "content": "用户叫王总", "kind": "fact", "importance": 2,
    })
    assert created.status_code == 200
    memory_id = created.json()["item"]["id"]

    listed = client.get("/v1/memories", headers=_auth(device),
                        params={"bot_id": "bot_chief"}).json()
    assert listed["total"] >= 1
    assert any(i["id"] == memory_id for i in listed["items"])

    dup = client.post("/v1/memories", headers=_auth(device), json={
        "bot_id": "bot_chief", "content": "用户叫王总",
    })
    assert dup.status_code == 409

    deleted = client.delete(f"/v1/memories/{memory_id}", headers=_auth(device))
    assert deleted.status_code == 200
    assert client.delete(f"/v1/memories/{memory_id}",
                         headers=_auth(device)).status_code == 404
