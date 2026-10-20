"""M0 acceptance tests — one test per row of the v2 acceptance sheet (§8)."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("RUNNER", "mock")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_RUN_TIMEOUT", "120")
    monkeypatch.setenv("M0_BUDGET_LIMIT_TOKENS", "200000")
    (tmp_path / "workspace").mkdir()
    from app.main import app
    from app import identity

    with TestClient(app) as c:
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.row_factory = sqlite3.Row
        ident = identity.issue(con, name="pytest", role="system")
        con.commit()
        con.close()
        yield SignedClient(c, ident["id"], ident["secret"], role="system")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def make_task(client, text: str = "写一篇 500 字秋季护肤短文") -> dict:
    r = client.post("/tasks/from_sentence", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def spawn(client, task_id: str, run_seq: int = 1) -> dict:
    r = client.post("/verbs/spawn_run", json={"task_id": task_id, "run_seq": run_seq})
    assert r.status_code == 200, r.text
    return r.json()


def execute(client, run_id: str) -> dict:
    r = client.post(f"/runs/{run_id}/execute", json={})
    assert r.status_code == 200, r.text
    return r.json()


def criterion(criteria: list[dict], seq: int) -> dict:
    return next(c for c in criteria if c["seq"] == seq)


# --------------------------------------------------------------- M0-1
def test_m0_1_schema(client):
    con = db()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    assert {"legal_entities", "ai_employees", "tasks", "acceptance_criteria",
            "evidence_refs", "task_runs", "artifacts", "budgets", "ai_audit_logs",
            "packs", "identities", "chats", "chat_messages"} <= set(tables)
    assert con.execute("SELECT COUNT(*) FROM legal_entities").fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='trg_tasks_state_guard'"
    ).fetchone()[0] == 1


# --------------------------------------------------------------- M0-2
def test_m0_2_task_from_one_sentence(client):
    data = make_task(client)
    crit = data["criteria"]
    assert len(crit) == 6
    # v1.5：字数与关键词都降为提示（非阻断），真正一票否决的是产出/广告法/调性
    assert sum(1 for c in crit if c["is_blocking"]) == 3
    assert any("广告法" in c["criterion"] and c["is_blocking"] for c in crit)
    assert any("字" in c["criterion"] and not c["is_blocking"] for c in crit)
    assert sum(1 for c in crit if c["state"] == "na") == 1
    assert {c["acceptance_layer"] for c in crit} == {"L0", "L1", "L2"}
    assert data["word_range"] == [500, 800]


# --------------------------------------------------------------- M0-3
def test_m0_3_idempotent_emit_event(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    body = {"run_id": run["run_id"], "evt_seq": 1, "event_type": "run_started"}

    first = client.post("/verbs/emit_event", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["new_task_state"] == "in_progress"

    con = db()
    audit_before = con.execute("SELECT COUNT(*) FROM ai_audit_logs").fetchone()[0]
    transitions_before = con.execute(
        "SELECT COUNT(*) FROM ai_audit_logs WHERE action='state_transition'").fetchone()[0]

    second = client.post("/verbs/emit_event", json=body)
    assert second.status_code == 200
    assert second.json() == first.json()

    con = db()
    assert con.execute("SELECT COUNT(*) FROM ai_audit_logs").fetchone()[0] == audit_before + 1
    assert con.execute(
        "SELECT COUNT(*) FROM ai_audit_logs WHERE action='state_transition'").fetchone()[0] == transitions_before
    replay = con.execute(
        "SELECT * FROM ai_audit_logs WHERE verb='replay' AND replay_of=?",
        (f"{run['run_id']}:evt:1",)).fetchall()
    assert len(replay) == 1
    assert client.get(f"/tasks/{t['task_id']}").json()["task"]["state"] == "in_progress"


# --------------------------------------------------------------- M0-4
def test_m0_4_adapter_poc_with_mock(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    out = execute(client, run["run_id"])

    assert out["spawned"] is True
    assert out["exit_code"] == 0
    assert out["event_count"] == 10          # same 10 events the real DSH sample has
    assert out["task_state"] == "pending_review"
    assert out["artifacts"], "no artifact attached"

    art = out["artifacts"][0]
    on_disk = hashlib.sha256(pathlib.Path(art["file_path"]).read_bytes()).hexdigest()
    assert art["checksum"] == on_disk

    con = db()
    verbs = {r[0] for r in con.execute(
        "SELECT verb FROM ai_audit_logs WHERE task_id=?", (t["task_id"],))}
    assert {"spawn_run", "reserve_budget", "emit_event", "attach_artifact"}.issubset(verbs)


# --------------------------------------------------------------- M0-5
def test_m0_5_l0_rules_and_false_positives(client):
    from app.acceptance import l0_rules

    assert l0_rules.check_banned("最近天气干燥，最终效果不错，这是第一步").get("ok") is True
    assert l0_rules.check_banned("这是最好的面霜，绝对有效").get("hits") == ["最好", "绝对"]
    assert l0_rules.check_banned("可以根治敏感肌").get("ok") is False
    assert l0_rules.check_keywords("秋季保湿和修护屏障，还要抗老").get("ok") is True
    assert l0_rules.check_keywords("只提保湿").get("ok") is False

    t = make_task(client)
    spawn(client, t["task_id"])
    client.post(f"/tasks/{t['task_id']}/acceptance/run")
    crit = {c["seq"]: c for c in client.get(f"/tasks/{t['task_id']}").json()["criteria"]}
    # no artifact yet -> deliverable + word count fail, rules stay deterministic
    assert crit[4]["state"] == "failed" or crit[4]["note"]


# --------------------------------------------------------------- M0-6 / M0-7 / full flow
def test_m0_6_l1_manual_requires_evidence(client):
    t = make_task(client)
    c5 = criterion(t["criteria"], 5)
    r = client.post(f"/criteria/{c5['id']}/check", json={"passed": True})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "EvidenceRequired"

    r = client.post(f"/criteria/{c5['id']}/check", json={
        "passed": True, "checked_by": "tester",
        "note": "语气亲切，无夸大", "evidence_snippet": "给皮肤一点时间，它会自己找回节奏"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "passed"

    fresh = {c["seq"]: c for c in client.get(f"/tasks/{t['task_id']}").json()["criteria"]}
    assert fresh[5]["checked_by"] == client.ident_id
    assert fresh[5]["note"] == "语气亲切，无夸大"


def test_m0_7_l2_script_writes_evidence(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    execute(client, run["run_id"])
    r = client.post(f"/tasks/{t['task_id']}/acceptance/run")
    assert r.status_code == 200, r.text

    data = client.get(f"/tasks/{t['task_id']}").json()
    crit = {c["seq"]: c for c in data["criteria"]}
    assert crit[2]["state"] == "passed", crit[2]
    assert "字数" in (crit[2]["note"] or "")
    ev = [e for e in data["evidence"] if e["owner_id"] == crit[2]["id"]]
    assert ev and ev[0]["content_snippet"]
    assert len(ev[0]["file_checksum"]) == 64


# --------------------------------------------------------------- M0-8
def test_m0_8_state_machine_is_enforced_in_the_database(client):
    t = make_task(client)
    con = db()
    with pytest.raises(sqlite3.IntegrityError) as exc:
        con.execute("UPDATE tasks SET state='completed' WHERE id=?", (t["task_id"],))
    assert "IllegalStateTransition" in str(exc.value)
    con.rollback()
    assert con.execute("SELECT state FROM tasks WHERE id=?", (t["task_id"],)).fetchone()[0] == "todo"

    run = spawn(client, t["task_id"])
    bad = client.post("/verbs/emit_event", json={
        "run_id": run["run_id"], "evt_seq": 1, "event_type": "review_passed"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "IllegalStateTransition"

    con = db()
    rejected = con.execute(
        "SELECT COUNT(*) FROM ai_audit_logs WHERE action='rejected' AND task_id=?",
        (t["task_id"],)).fetchone()[0]
    assert rejected == 1


# --------------------------------------------------------------- M0-9
def test_m0_9_budget_denied_spawns_nothing(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    con = db()
    con.execute("UPDATE budgets SET limit_tokens=0 WHERE task_id=?", (t["task_id"],))
    con.commit()

    out = execute(client, run["run_id"])
    assert out["spawned"] is False
    assert out["reason"] == "budget_exhausted"
    ws = pathlib.Path(os.environ["M0_WORKSPACE"]) / t["task_id"]
    assert not (ws / f".spawned-{run['run_id']}.txt").exists()
    assert client.get(f"/tasks/{t['task_id']}").json()["task"]["state"] == "failed"


# --------------------------------------------------------------- M0-10
def test_m0_10_audit_timeline_and_replay_lookup(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    body = {"run_id": run["run_id"], "evt_seq": 1, "event_type": "run_started"}
    client.post("/verbs/emit_event", json=body)
    client.post("/verbs/emit_event", json=body)

    tl = client.get(f"/tasks/{t['task_id']}/timeline").json()
    assert tl["count"] >= 4
    verbs = [e["verb"] for e in tl["events"]]
    for expected in ("spawn_run", "emit_event", "state_transition", "replay"):
        assert expected in verbs, verbs

    hist = client.get(f"/audit/by-key/{run['run_id']}:evt:1").json()
    assert hist["found"] is True
    assert len(hist["replays"]) == 1


# --------------------------------------------------------------- M0-11 / full acceptance flow
def test_m0_11_full_flow_reaches_completed(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    execute(client, run["run_id"])

    first = client.post(f"/tasks/{t['task_id']}/acceptance/run").json()
    assert first["task_state"] == "pending_review"
    assert first["verdict"] == "pending"

    data = client.get(f"/tasks/{t['task_id']}").json()
    c5 = criterion(data["criteria"], 5)
    client.post(f"/criteria/{c5['id']}/check", json={
        "passed": True, "checked_by": "tester",
        "evidence_snippet": "换季不需要一次换掉整套护肤，把保湿做扎实。"})

    second = client.post(f"/tasks/{t['task_id']}/acceptance/run").json()
    assert second["task_state"] == "completed"
    assert second["verdict"] == "completed"

    data = client.get(f"/tasks/{t['task_id']}").json()
    crit = {c["seq"]: c for c in data["criteria"]}
    assert all(crit[s]["state"] == "passed" for s in (1, 2, 3, 4, 5))
    assert crit[6]["state"] == "na"
    ev = [e for e in data["evidence"] if e["owner_id"] == crit[1]["id"]]
    assert ev and ev[0]["file_checksum"] and ev[0]["content_snippet"]


# --------------------------------------------------------------- M0-12
def test_m0_12_design_gaps_recorded():
    p = ROOT / "DESIGN_GAPS.md"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    entries = [ln for ln in text.splitlines() if ln.strip().startswith(("-", "*", "1.", "2.", "3."))]
    assert len(entries) >= 1


# --------------------------------------------------------------- M0-13
def test_m0_13_review_page(client):
    dash = client.get("/")
    assert dash.status_code == 200
    assert "工作台" in dash.text
    assert "cdn" not in dash.text.lower()

    review = client.get("/review")
    assert review.status_code == 200
    assert "验收台" in review.text


# --------------------------------------------------------------- M0-14 ACP transport
def test_m0_14_acp_run_streams_updates(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    out = client.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock_acp"}).json()

    assert out["spawned"] is True
    assert out["runner"] == "mock_acp"
    assert out["exit_code"] == 0
    assert out["task_state"] == "pending_review"
    assert out["session_id"], "ACP session id must be stored for resume"
    assert out["event_count"] >= 5          # session/update notifications seen live
    assert out["gate"]["checks"] >= 2       # usage_update arrived per model call
    assert out["artifacts"], "artifact from tool_call rawInput"
    art = out["artifacts"][0]
    on_disk = hashlib.sha256(pathlib.Path(art["file_path"]).read_bytes()).hexdigest()
    assert art["checksum"] == on_disk


# --------------------------------------------------------------- M0-15 mid-turn gate
def test_m0_15_midturn_budget_cancel(client, monkeypatch):
    monkeypatch.setenv("MOCK_ACP_OVERSPEND", "1")
    t = make_task(client)
    run = spawn(client, t["task_id"])
    out = client.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock_acp"}).json()

    assert out["exit_code"] == 3
    assert out["stop_reason"] == "cancelled"
    assert out["gate"]["cancelled"] is True
    assert out["gate"]["used"] > out["gate"]["cap"]
    assert client.get(f"/tasks/{t['task_id']}").json()["task"]["state"] == "failed"

    con = db()
    gate = con.execute(
        "SELECT * FROM ai_audit_logs WHERE verb='budget_check' AND run_id=?",
        (run["run_id"],)).fetchall()
    assert len(gate) == 1
    payload = json.loads(gate[0]["payload"])
    assert payload["used"] > payload["cap"]


# --------------------------------------------------------------- M0-16 retry keeps the session
def test_m0_16_retry_resumes_the_same_session(client):
    t = make_task(client)
    first = spawn(client, t["task_id"])
    client.post(f"/runs/{first['run_id']}/execute", json={"runner": "mock_acp"})

    data = client.get(f"/tasks/{t['task_id']}").json()
    c5 = criterion(data["criteria"], 5)
    client.post(f"/criteria/{c5['id']}/check", json={
        "passed": False, "checked_by": "tester", "note": "广告语气偏重",
        "evidence_snippet": "这款面霜能彻底解决干燥。"})
    verdict = client.post(f"/tasks/{t['task_id']}/acceptance/run").json()
    assert verdict["verdict"] == "rejected"

    retry = client.post(f"/tasks/{t['task_id']}/retry", json={"runner": "mock_acp"}).json()
    assert retry["resumed"] is True
    assert retry["session_id"] == data["runs"][0]["session_id"]
    # usage_update is cumulative per session: the resumed turn must not be charged
    # (or cancelled) for the tokens the earlier turn already spent.
    assert retry["gate"]["baseline"] == 980
    assert retry["gate"]["cancelled"] is False
    assert retry["usage_total"] == 1960
    assert retry["usage_tokens"] == 980
    assert retry["task_state"] == "pending_review"

    fresh = client.get(f"/tasks/{t['task_id']}").json()["criteria"]
    assert criterion(fresh, 5)["state"] == "pending"   # re-judged, not carried over
    assert criterion(fresh, 6)["state"] == "na"

    runs = client.get(f"/tasks/{t['task_id']}").json()["runs"]
    assert [r["run_seq"] for r in runs] == [1, 2]
    assert runs[1]["session_id"] == runs[0]["session_id"]
    # stored columns: cumulative total + the baseline it was charged against
    assert runs[0]["usage_tokens"] == 980 and runs[0]["usage_baseline"] == 0
    assert runs[1]["usage_tokens"] == 1960 and runs[1]["usage_baseline"] == 980

    timeline = [e["verb"] for e in client.get(f"/tasks/{t['task_id']}/timeline").json()["events"]]
    assert "retry" in timeline
    # the follow-up prompt carries the failed criterion, not a blank restart
    con = db()
    row = con.execute("SELECT payload FROM ai_audit_logs WHERE verb='retry'").fetchone()
    assert "判据5" in json.loads(row[0])["prompt"]


# --------------------------------------------------------------- M0-18 workspace
def test_m0_18_each_task_gets_its_own_workspace(client):
    outs = []
    for _ in range(2):
        t = make_task(client)
        run = spawn(client, t["task_id"])
        out = execute(client, run["run_id"])
        outs.append((t["task_id"], pathlib.Path(out["artifacts"][0]["file_path"]).parent))

    (id1, dir1), (id2, dir2) = outs
    assert dir1 != dir2, "tasks must not share a directory"
    assert dir1.name == id1 and dir2.name == id2, (dir1, dir2)
    assert (dir1 / "article.md").exists() and (dir2 / "article.md").exists()


# --------------------------------------------------------------- M0-19 sandbox primitive
ESCAPE_CODE = (
    "import pathlib, sys\n"
    "pathlib.Path('inside.txt').write_text('ok', encoding='utf-8')\n"
    "try:\n"
    "    pathlib.Path('..', 'escape.txt').write_text('nope', encoding='utf-8')\n"
    "    print('ESCAPE_OK'); sys.exit(9)\n"
    "except OSError as exc:\n"
    "    print('ESCAPE_BLOCKED', type(exc).__name__)\n"
)


@pytest.mark.skipif(os.name != "nt", reason="Low integrity is Windows-only")
def test_m0_19_low_integrity_blocks_writes_outside(tmp_path):
    import subprocess
    import sys

    task = tmp_path / "task"
    task.mkdir()
    launcher = ROOT / "tools" / "sandbox_win.py"

    proc = subprocess.run(
        [sys.executable, str(launcher), "--cwd", str(task), "--",
         sys.executable, "-c", ESCAPE_CODE],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)

    combined = (proc.stdout or "") + (proc.stderr or "")
    if "cannot label" in combined:
        pytest.skip("cannot set integrity label here: " + combined[-200:])
    assert proc.returncode == 0, combined[-400:]
    assert "ESCAPE_BLOCKED" in combined, combined[-400:]
    assert (task / "inside.txt").exists()
    assert not (tmp_path / "escape.txt").exists(), "sandboxed child escaped its directory"


# --------------------------------------------------------------- M0-20 escape via the runner
def test_m0_20_runner_cannot_escape_its_workspace(client, monkeypatch):
    monkeypatch.setenv("M0_SANDBOX", "lowil")
    monkeypatch.setenv("MOCK_ESCAPE", "1")

    t = make_task(client)
    run = spawn(client, t["task_id"])
    out = execute(client, run["run_id"])

    if not out.get("final"):
        pytest.skip(f"sandbox unavailable: {out.get('stderr_tail') or out.get('error')}")
    assert "ESCAPE_BLOCKED" in out["final"], out["final"]

    task_dir = pathlib.Path(out["artifacts"][0]["file_path"]).parent
    assert not (task_dir.parent / "escape.txt").exists(), "child wrote into the workspace root"


# --------------------------------------------------------------- M0-17 retry after a failed run
def test_m0_17_retry_after_budget_failure(client):
    t = make_task(client)
    run = spawn(client, t["task_id"])
    con = db()
    con.execute("UPDATE budgets SET limit_tokens=0 WHERE task_id=?", (t["task_id"],))
    con.commit()
    denied = client.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock_acp"}).json()
    assert denied["spawned"] is False

    con = db()
    con.execute("UPDATE budgets SET limit_tokens=200000 WHERE task_id=?", (t["task_id"],))
    con.commit()

    retry = client.post(f"/tasks/{t['task_id']}/retry", json={"runner": "mock_acp"}).json()
    assert retry["spawned"] is True
    assert retry["task_state"] == "pending_review"
    assert retry["resumed"] is False       # nothing ran, so there is no session to resume
