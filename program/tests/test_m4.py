"""M4 acceptance: business packs, usage reports, workbench page."""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("RUNNER", "mock")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        ident = identity.issue(conn, name="pytest", role="system",
                               legal_entity_id="LE-GROUP-HQ")
        conn.commit()
        yield {"raw": raw, "client": SignedClient(raw, ident["id"], ident["secret"])}
        conn.close()


def run_task(client, text, pack=None, runner="mock") -> dict:
    body = {"text": text}
    if pack:
        body["pack_id"] = pack
    t = client.post("/tasks/from_sentence", json=body).json()
    run = client.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    out = client.post(f"/runs/{run['run_id']}/execute", json={"runner": runner}).json()
    return {"task": t, "exec": out}


# --------------------------------------------------------------- packs
def test_m4_1_packs_are_listed(env):
    packs = env["client"].get("/packs").json()["packs"]
    ids = [p["id"] for p in packs]
    assert {"content-v1", "code-v1", "code-local-v1", "review-v1", "finance-v1"} <= set(ids)
    assert all(p["template"] for p in packs)


def test_m4_2_finance_pack_has_its_own_criteria(env):
    t = env["client"].post("/tasks/from_sentence", json={
        "text": "帮我做这个月的报销费用审核单", "pack_id": "finance-v1"}).json()
    assert t["pack_id"] == "finance-v1"
    assert t["task_type"] == "finance_write"
    words = " ".join(c["criterion"] for c in t["criteria"])
    assert "科目" in words or "金额" in words
    assert "复核" in words
    assert len(t["criteria"]) == 4


def test_m4_3_pack_can_be_inferred_from_the_sentence(env):
    t = env["client"].post("/tasks/from_sentence", json={
        "text": "写一篇 500 字秋季护肤短文"}).json()
    assert t["pack_id"] == "content-v1"

    code = env["client"].post("/tasks/from_sentence", json={
        "text": "写一个 Python 脚本抓取 https://example.com"}).json()
    assert code["pack_id"] == "code-v1"


# --------------------------------------------------------------- reports
def test_m4_4_usage_report_by_pack_and_identity(env):
    client = env["client"]
    run_task(client, "写一篇 500 字秋季护肤短文")                 # content
    run_task(client, "写一个 Python 脚本抓取 https://example.com")  # code

    rep = client.get("/reports/usage").json()
    assert rep["totals"]["tasks"] == 2
    assert rep["totals"]["tokens"] > 0
    assert rep["totals"]["artifacts"] >= 2
    packs = {p["pack"] for p in rep["by_pack"]}
    assert {"content-v1", "code-v1"}.issubset(packs)
    assert rep["by_identity"][0]["tokens"] > 0
    assert rep["totals"]["run_success_rate"] == 1.0


def test_m4_5_report_filters(env):
    client = env["client"]
    run_task(client, "写一篇 500 字秋季护肤短文")
    rep = client.get("/reports/usage", params={"pack": "content-v1"}).json()
    assert rep["totals"]["tasks"] == 1
    assert {p["pack"] for p in rep["by_pack"]} == {"content-v1"}


# --------------------------------------------------------------- workbench
def test_m4_6_workbench_page_served(env):
    page = env["client"].get("/legacy")
    assert page.status_code == 200
    for needle in ("董事长看板", "调度看板", "任务详情", "X-OA-Sig"):
        assert needle in page.text
