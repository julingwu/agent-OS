"""M9: WeCom native approval — create, poll, sign, and the state transition it drives."""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

TOK = "wecom-approval-test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("RUNNER", "mock")
    monkeypatch.setenv("WECOM_MOCK", "1")
    monkeypatch.setenv("WECOM_WATCHER", "0")
    monkeypatch.setenv("WECOM_APPROVAL_POLLER", "0")
    monkeypatch.setenv("WECOM_SIM_TOKEN", TOK)
    monkeypatch.setenv("WECOM_APPROVAL_TEMPLATE_ID", "TPL-TEST")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_SECRET", "secret-test")
    monkeypatch.setenv("WECOM_IP_ALLOWLIST", "0")
    (tmp_path / "workspace").mkdir()

    from app import identity, db
    from app.main import app
    from app.wecom import approval

    with TestClient(app) as raw:
        conn = db.connect()
        conn.execute("""INSERT OR IGNORE INTO legal_entities (id, name, governance_mode)
                        VALUES ('LE-T','test','direct')""")
        conn.execute("""INSERT OR IGNORE INTO identities
                        (id, name, role, legal_entity_id, secret, scopes, state)
                        VALUES ('ID-test-ceo','董事长','ceo','LE-T','s3cret','["*"]','active')""")
        conn.execute("""INSERT OR IGNORE INTO tasks
                        (id, legal_entity_id, title, pack_id, state)
                        VALUES ('T-approval-1','LE-T','落地页验收','web-v1','pending_review')""")
        conn.execute("""INSERT OR IGNORE INTO acceptance_criteria
                        (id, legal_entity_id, task_id, criterion, check_type, check_ref,
                         is_blocking, evidence_required, acceptance_layer, state,
                         is_deterministic, seq)
                        VALUES ('C-human-1','LE-T','T-approval-1','视觉与文案','human_check',NULL,
                                1,0,'L1','pending',0,1)""")
        conn.commit()
        raw.post("/wecom/bind", headers={"X-Wecom-Token": TOK},
                 json={"userid": "demo-user", "identity_id": "ID-test-ceo"})
        yield raw, conn, approval
        conn.close()


def _bind(raw) -> str:
    """Bind demo-user to a real identity the shell knows."""
    with sqlite3.connect(os.environ["DB_PATH"]) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT id FROM identities WHERE role='ceo' LIMIT 1").fetchone()
    assert row is not None, "no ceo identity seeded"
    r = raw.post("/wecom/bind", headers={"X-Wecom-Token": TOK},
                 json={"userid": "demo-user", "identity_id": row["id"]})
    assert r.status_code == 200
    return row["id"]


def _pending_task(conn) -> tuple[str, str]:
    task = conn.execute("SELECT id FROM tasks WHERE state='pending_review' LIMIT 1").fetchone()
    crit = conn.execute("""SELECT id FROM acceptance_criteria WHERE task_id=? AND check_type='human_check'
                           AND state='pending' LIMIT 1""", (task["id"],)).fetchone()
    return task["id"], crit["id"]


class FakeHttp:
    """Stands in for qyapi: token, applyevent, getapprovaldetail."""

    def __init__(self, sp_status=1):
        self.sp_status = sp_status
        self.posts = []

    def get(self, path, params=None):
        if path == "/cgi-bin/gettoken":
            return _Resp({"errcode": 0, "access_token": "AT"})
        if path == "/cgi-bin/oa/getapprovaldetail":
            return _Resp({"errcode": 0, "info": {"sp_status": self.sp_status,
                                                 "sp_name": "AI任务人工验收"}})
        raise AssertionError(path)

    def post(self, path, params=None, json=None):
        self.posts.append((path, json))
        if path == "/cgi-bin/oa/applyevent":
            return _Resp({"errcode": 0, "sp_no": "SP-0001"})
        if path == "/cgi-bin/oa/getapprovaldetail":
            return _Resp({"errcode": 0, "info": {"sp_status": self.sp_status,
                                                 "sp_name": "AI任务人工验收"}})
        raise AssertionError(path)


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_create_requires_a_template(env, monkeypatch):
    raw, conn, approval = env
    monkeypatch.setenv("WECOM_APPROVAL_TEMPLATE_ID", "")
    with pytest.raises(approval.ApprovalError, match="TEMPLATE_ID"):
        approval.create(conn, task_id="T-x", userid="demo-user")


def test_create_posts_the_two_fields_and_stores_sp_no(env):
    raw, conn, approval = env
    _bind(raw)
    tid, cid = _pending_task(conn)
    fake = FakeHttp()
    out = approval.create(conn, task_id=tid, userid="demo-user", http=fake)
    assert out["sp_no"] == "SP-0001"
    body = fake.posts[0][1]
    assert body["template_id"] == "TPL-TEST"
    assert [c["id"] for c in body["apply_data"]["contents"]] == ["Text-01", "Text-02"]
    assert body["apply_data"]["contents"][0]["value"]["text"] == tid
    row = conn.execute("SELECT * FROM wecom_approvals WHERE sp_no='SP-0001'").fetchone()
    assert row["state"] == "pending" and row["criterion_id"] == cid


def test_poll_approves_and_signs_with_the_approval_note(env):
    raw, conn, approval = env
    _bind(raw)
    tid, cid = _pending_task(conn)
    approval.create(conn, task_id=tid, userid="demo-user", http=FakeHttp(sp_status=1))

    def fake_signer(conn_, row, note):
        # mirrors what the signed API call writes, without leaving the test process
        conn_.execute("UPDATE acceptance_criteria SET state='passed', checked_by=?, note=? WHERE id=?",
                      ("ID-test-ceo", note, row["criterion_id"]))
        conn_.commit()
        return "签字完成，验收 completed"

    out = approval.poll_once(conn, http=FakeHttp(sp_status=2), signer=fake_signer)
    assert out["approved"] == 1
    row = conn.execute("SELECT * FROM wecom_approvals WHERE sp_no='SP-0001'").fetchone()
    assert row["state"] == "approved"
    assert "签字完成" in row["result"]
    crit = conn.execute("SELECT state, checked_by, note FROM acceptance_criteria WHERE id=?",
                        (cid,)).fetchone()
    assert crit["state"] == "passed"
    assert crit["checked_by"] == "ID-test-ceo"
    assert "企微审批通过 SP-0001" in crit["note"]


def test_poll_rejection_leaves_the_criterion_pending(env):
    raw, conn, approval = env
    _bind(raw)
    tid, cid = _pending_task(conn)
    approval.create(conn, task_id=tid, userid="demo-user", http=FakeHttp(sp_status=1))
    out = approval.poll_once(conn, http=FakeHttp(sp_status=3))
    assert out["rejected"] == 1
    assert conn.execute("SELECT state FROM acceptance_criteria WHERE id=?",
                        (cid,)).fetchone()["state"] == "pending"


def test_approval_create_endpoint_needs_the_token(env):
    raw, conn, _ = env
    tid, _cid = _pending_task(conn)
    assert raw.post("/wecom/approval/create", json={"task_id": tid,
                                                    "userid": "demo-user"}).status_code == 401


def test_approval_list_reports_the_poller(env):
    raw, conn, approval = env
    r = raw.get("/wecom/approval/list", headers={"X-Wecom-Token": TOK})
    assert r.status_code == 200
    assert r.json()["poller"]["running"] is False
