"""M21 (v3.1): 人话报错 / 公司红线 / SMTP 设置 / tenant 彻底收口。"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import db as db_mod
from app import experience, identity, mcp, pipeline, rbac, tenancy
from app.db import connect, get_conn, init_db
from app.errors import humanize
from app.ui import store

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v31.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    (tmp_path / "workspace").mkdir()
    init_db()
    conn = connect()
    store.ensure(conn)
    pipeline.ensure(conn)
    rbac.ensure(conn)
    conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
                 " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
    conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type,parent_entity_id,tenant_id)"
                 " VALUES ('LE-BETA','贝塔事业部','department','LE-GROUP-HQ','TENANT-BETA')")
    conn.execute("INSERT OR IGNORE INTO tenants (id,name,plan) VALUES ('TENANT-BETA','贝塔租户','team')")
    hq = identity.issue(conn, name="HQ老板", role="ceo", legal_entity_id="LE-GROUP-HQ")
    beta = identity.issue(conn, name="Beta老板", role="ceo", legal_entity_id="LE-BETA",
                          tenant_id="TENANT-BETA")
    conn.execute("""INSERT OR IGNORE INTO ai_employees
                    (id, legal_entity_id, name, model, status, preset)
                    VALUES ('AI-BETA-01','LE-BETA','小贝','deepseek-chat','online','standard')""")
    hq_chat = store.get_or_create_dm(conn, "AI-BETA-01", "HQ 会话", "LE-BETA")
    beta_chat = store.get_or_create_dm(conn, "AI-BETA-01", "Beta 会话", "LE-BETA",
                                       tenant_id="TENANT-BETA")
    hq_proj = pipeline.create_project(conn, name="HQ 项目", requirement="HQ only")
    beta_proj = pipeline.create_project(conn, name="Beta 项目", requirement="Beta only",
                                        entity="LE-BETA", tenant_id="TENANT-BETA")
    # Beta task/run/artifact: deliberately in the same SQLite file.
    beta_task = "T-BETA-TASK"
    conn.execute("""INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title, state,
                                      owner_employee_id, project_id)
                    VALUES (?, 'LE-BETA','TENANT-BETA','content_write','Beta 秘密任务','todo',
                            'AI-BETA-01', ?)""", (beta_task, beta_proj["project_id"]))
    beta_run = "RUN-BETA-1"
    conn.execute("""INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, tenant_id,
                                          state, runner)
                    VALUES (?,?,1,'LE-BETA','TENANT-BETA','succeeded','mock')""",
                 (beta_run, beta_task))
    art_path = tmp_path / "workspace" / "beta_secret.txt"
    art_path.write_text("beta secret", encoding="utf-8")
    art_id = "ART-BETA-1"
    conn.execute("""INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, checksum, bytes)
                    VALUES (?,?, 'TENANT-BETA',1,?, 'x', 11)""",
                 (art_id, beta_run, str(art_path)))
    conn.execute("""INSERT INTO tool_approvals (id, agent_id, tool, args, state, tenant_id,
                                               task_id)
                    VALUES ('AP-BETA','AI-BETA-01','write','{}','pending','TENANT-BETA',?)""",
                 (beta_task,))
    conn.commit()
    ids = {"hq": hq, "beta": beta, "hq_chat": hq_chat["id"], "beta_chat": beta_chat["id"],
           "hq_proj": hq_proj["project_id"], "beta_proj": beta_proj["project_id"],
           "beta_task": beta_task, "beta_run": beta_run, "beta_art": art_id}
    conn.close()
    from app.main import app
    with TestClient(app) as c:
        yield c, ids


def _login(c, ident):
    r = c.post("/ui/login", json={"identity_id": ident["id"], "secret": ident["secret"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_humanize_messages():
    assert "审批未通过" in humanize("GateNotPassed")
    assert "代码构建失败" in humanize("build failed: 1 task(s)")
    assert "执行超时" in humanize("acp_timeout")
    assert humanize("正常文本") == "正常文本"


def test_manual_red_line_is_approved_and_always_recalled(env):
    c, ids = env
    _login(c, ids["hq"])
    r = c.post("/ui/experiences", json={
        "title": "公司红线：不得承诺未上线功能",
        "content": "销售/文案不得向客户承诺尚未上线的功能，必须写明「以实际发布为准」。",
        "tags": "合规,销售", "kind": "decision", "mandatory": True})
    assert r.status_code == 200, r.text
    e = r.json()["experience"]
    assert e["state"] == "approved" and e["mandatory"] == 1
    with get_conn() as conn:
        reds = experience.red_lines(conn)
        assert any(x["id"] == e["id"] for x in reds)
        ctx = experience.context_for(conn, "写一首关于月亮的诗", record=False)
        assert e["id"] in ctx["ids"]          # 与标签无关也必须命中
        stats = experience.stats(conn)
        assert stats["mandatory"] >= 1


def test_tenant_blocks_cross_tenant_task_run_artifact(env):
    c, ids = env
    _login(c, ids["hq"])
    h = {"x-tenant-id": "TENANT-HQ"}
    assert c.get(f"/ui/tasks/{ids['beta_task']}", headers=h).status_code == 404
    assert c.get(f"/ui/runs/{ids['beta_run']}/trace", headers=h).status_code == 404
    files = c.get("/ui/files", headers=h).json()["files"]
    assert all(f["id"] != ids["beta_art"] for f in files)
    assert c.get(f"/ui/files/{ids['beta_art']}/download", headers=h).status_code == 404
    ap = c.get("/ui/approvals", headers=h).json()
    assert all(x["id"] != "AP-BETA" for x in ap["pending"])

    # The owner tenant still sees its own task/file/approval.
    _login(c, ids["beta"])
    b = {"x-tenant-id": "TENANT-BETA"}
    assert c.get(f"/ui/tasks/{ids['beta_task']}", headers=b).status_code == 200
    files = c.get("/ui/files", headers=b).json()["files"]
    assert any(f["id"] == ids["beta_art"] for f in files)
    assert c.get(f"/ui/files/{ids['beta_art']}/download", headers=b).status_code == 200
    ap = c.get("/ui/approvals", headers=b).json()
    assert any(x["id"] == "AP-BETA" for x in ap["pending"])


def test_ui_retry_creates_new_run(env, monkeypatch):
    c, ids = env
    _login(c, ids["hq"])
    with get_conn() as conn:
        conn.execute("""INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title,
                                          state, owner_employee_id)
                        VALUES ('T-RETRY','LE-GROUP-HQ','TENANT-HQ','content_write',
                                '失败可重试','failed','AI-BETA-01')""")
        conn.commit()
    monkeypatch.setattr("app.engine.dsh_adapter.execute_run",
                        lambda conn, run_id, **kw: {"run_id": run_id, "spawned": True})
    r = c.post("/ui/tasks/T-RETRY/retry", json={})
    assert r.status_code == 200, r.text
    import time
    time.sleep(0.4)
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id='T-RETRY'").fetchone()[0]
        st = conn.execute("SELECT state FROM tasks WHERE id='T-RETRY'").fetchone()[0]
    assert n == 1 and st == "in_progress"


def test_project_gate_approve_unblocks(env):
    c, ids = env
    _login(c, ids["hq"])
    with get_conn() as conn:
        conn.execute("UPDATE projects SET state='blocked' WHERE id=?", (ids["hq_proj"],))
        conn.execute("""INSERT INTO stage_gates (id, project_id, stage_key, name, kind,
                                                state, note)
                        VALUES ('GATE-VETO', ?, 'build', '架构师否决权', 'veto',
                                'vetoed', '架构师投了反对票')""", (ids["hq_proj"],))
        conn.commit()
    r = c.post(f"/ui/projects/{ids['hq_proj']}/gates/GATE-VETO/approve", json={})
    assert r.status_code == 200, r.text
    with get_conn() as conn:
        g = conn.execute("SELECT state FROM stage_gates WHERE id='GATE-VETO'").fetchone()[0]
        p = conn.execute("SELECT state FROM projects WHERE id=?", (ids["hq_proj"],)).fetchone()[0]
    assert g == "passed" and p == "active"


def test_smtp_settings_masks_password_and_audits_without_it(env):
    c, ids = env
    _login(c, ids["hq"])
    r = c.put("/ui/smtp", json={
        "host": "smtp.example.com", "port": 587, "user": "bot@example.com",
        "password": "super-secret", "from_addr": "bot@example.com", "secure": False})
    assert r.status_code == 200, r.text
    assert r.json()["pass_set"] is True and "password" not in r.json()
    got = c.get("/ui/smtp").json()
    assert got["host"] == "smtp.example.com" and got["pass_set"] is True
    assert "super-secret" not in json.dumps(got)
    with get_conn() as conn:
        saved = conn.execute("SELECT value FROM settings WHERE key='smtp_pass'").fetchone()[0]
        assert saved == "super-secret"
        payloads = " ".join(r["payload"] or "" for r in conn.execute(
            "SELECT payload FROM ai_audit_logs WHERE action='smtp_saved'"))
        assert "super-secret" not in payloads
