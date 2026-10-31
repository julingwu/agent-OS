"""M12: 沙箱里的 node/npm、产物预览、回滚脚本的契约。"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn, init_db
from app.engine import sandbox_env


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()
    from app.main import app
    from app.ui import api as ui_api
    from app import identity

    with get_conn() as conn:
        who = identity.issue(conn, name="preview test operator", role="system")
    tok = ui_api._make_cookie(who["id"])
    c = TestClient(app)
    c.headers.update({"authorization": f"Bearer {tok}"})
    return c, tok


def test_node_and_npm_are_on_the_sandbox_path(tmp_path):
    env = sandbox_env.build_env(tmp_path)
    assert sandbox_env.which_node(env), "沙箱里找不到 node"
    # npm 的 cache 必须落在任务目录里，否则 Low 完整性写不了用户目录
    assert str(tmp_path) in env["npm_config_cache"]


def test_preview_serves_html_inline_with_a_sandbox_csp(client, tmp_path):
    c, tok = client
    f = tmp_path / "page.html"
    f.write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-X','X','group_hq')")
        conn.execute("INSERT INTO tasks (id,legal_entity_id,title,state) VALUES ('T-1','LE-X','t','completed')")
        conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,state) VALUES ('R-1','T-1',1,'LE-X','succeeded')")
        conn.execute("""INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)
                        VALUES ('A-1','R-1',1,?, 'x', 40)""", (str(f),))
        conn.commit()

    r = c.get("/ui/files/A-1/preview")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "sandbox" in r.headers["content-security-policy"]
    assert "connect-src 'none'" in r.headers["content-security-policy"]

    # The opaque frame receives a short artifact ticket, never the session.
    ticket = c.post("/ui/files/A-1/preview-ticket").json()["ticket"]
    r2 = TestClient(c.app).get(f"/ui/files/A-1/preview?ticket={ticket}")
    assert r2.status_code == 200
    assert TestClient(c.app).get(f"/ui/files/A-1/preview?t={tok}").status_code == 400


def test_preview_requires_a_valid_ticket(client, tmp_path):
    c, _ = client
    f = tmp_path / "x.html"
    f.write_text("<html></html>", encoding="utf-8")
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-X','X','group_hq')")
        conn.execute("INSERT INTO tasks (id,legal_entity_id,title,state) VALUES ('T-2','LE-X','t','completed')")
        conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,state) VALUES ('R-2','T-2',1,'LE-X','succeeded')")
        conn.execute("""INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)
                        VALUES ('A-2','R-2',1,?,'x',10)""", (str(f),))
        conn.commit()
    r = TestClient(c.app).get("/ui/files/A-2/preview?ticket=forged.token.value")
    assert r.status_code == 401


def test_preview_404_for_missing_file(client):
    c, _ = client
    assert c.get("/ui/files/A-none/preview").status_code == 404


def test_rollback_script_exists_and_documents_the_tag():
    p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rollback_v11.ps1"
    text = p.read_text(encoding="utf-8-sig")
    assert "v1.1.0" in text
    assert "pre-rollback-" in text          # 回滚前先留分支
    assert "oa_v11_baseline.db" in text     # -WithDb 的基线快照
