"""Embedded previews close from their own opaque frame without relaxing policy."""
from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_conn, init_db


@pytest.fixture()
def preview_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "preview.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from app.ui import admin, api
    from app import identity
    init_db()
    app = FastAPI()
    app.include_router(admin.router)
    client = TestClient(app)
    with get_conn() as conn:
        conn.execute("INSERT INTO legal_entities(id,name,type) VALUES('LE-PREVIEW','Preview','group_hq')")
        who = identity.issue(conn, name="preview owner", role="ceo", legal_entity_id="LE-PREVIEW")
    token = api._make_cookie(who["id"])
    client.headers["authorization"] = f"Bearer {token}"

    def artifact(suffix, raw):
        path = tmp_path / ("artifact" + suffix)
        path.write_bytes(raw)
        with get_conn() as conn:
            conn.execute("INSERT INTO tasks(id,legal_entity_id,title,state) VALUES('T-PREVIEW','LE-PREVIEW','Preview','completed')")
            conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,state) VALUES('R-PREVIEW','T-PREVIEW',1,'LE-PREVIEW','succeeded')")
            conn.execute("INSERT INTO artifacts(id,run_id,art_seq,file_path,checksum,bytes) VALUES('A-PREVIEW','R-PREVIEW',1,?,'test',?)",
                         (str(path), len(raw)))
        return path

    return client, artifact, token


@pytest.mark.parametrize(("suffix", "raw"), [
    (".html", b"<!doctype html><html><body><h1>Hello</h1><script>window.artifactScript=1</script></body></html>"),
    (".txt", b"<script>parent.shouldNotRun=1</script>"),
    (".json", b'{"html":"<script>parent.shouldNotRun=1</script>"}'),
    (".png", base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")),
    (".svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>parent.shouldNotRun=1</script></svg>'),
])
def test_embed_is_html_with_unchanged_opaque_sandbox_policy(preview_client, suffix, raw):
    client, artifact, _ = preview_client
    path = artifact(suffix, raw)
    original = client.get("/ui/files/A-PREVIEW/preview")
    embedded = client.get("/ui/files/A-PREVIEW/preview?embed=1")
    assert original.status_code == embedded.status_code == 200
    assert original.content == raw, "default preview is still the original file"
    assert embedded.headers["content-type"].startswith("text/html")
    assert embedded.headers["content-security-policy"] == original.headers["content-security-policy"]
    assert "connect-src 'none'" in embedded.headers["content-security-policy"]
    assert "allow-same-origin" not in embedded.headers["content-security-policy"]
    assert embedded.headers["x-content-type-options"] == "nosniff"
    assert embedded.headers["cache-control"] == "no-store"
    assert "agent-os-preview-close" in embedded.text
    assert "event.isComposing" in embedded.text
    assert "Escape" in embedded.text
    assert path.read_bytes() == raw
    if suffix == ".html":
        assert embedded.text.lower().startswith("<!doctype html>")
        assert embedded.text.index("agent-os-preview-close") < embedded.text.index("window.artifactScript")
    elif suffix in (".txt", ".json"):
        assert "&lt;script&gt;parent.shouldNotRun=1&lt;/script&gt;" in embedded.text
        assert "<script>parent.shouldNotRun" not in embedded.text
    else:
        assert f";base64,{base64.b64encode(raw).decode('ascii')}" in embedded.text
        assert "<svg" not in embedded.text


def test_embed_retains_authentication_and_soft_delete_checks(preview_client):
    client, artifact, token = preview_client
    artifact(".txt", b"private preview")
    anonymous = TestClient(client.app)
    assert anonymous.get("/ui/files/A-PREVIEW/preview?embed=1").status_code == 401
    assert anonymous.get("/ui/files/A-PREVIEW/preview?embed=1&t=forged").status_code == 400
    assert anonymous.get(f"/ui/files/A-PREVIEW/preview?embed=1&t={token}").status_code == 400
    ticket = client.post("/ui/files/A-PREVIEW/preview-ticket").json()["ticket"]
    assert anonymous.get(f"/ui/files/A-PREVIEW/preview?embed=1&ticket={ticket}").status_code == 200
    with get_conn() as conn:
        conn.execute("UPDATE artifacts SET state='deleted' WHERE id='A-PREVIEW'")
    assert client.get("/ui/files/A-PREVIEW/preview?embed=1").status_code == 404


def test_large_embed_has_download_hint_and_escape_listener(preview_client):
    client, artifact, _ = preview_client
    path = artifact(".txt", b"small")
    with path.open("wb") as output:
        output.seek(10 * 1024 * 1024)
        output.write(b"x")
    embedded = client.get("/ui/files/A-PREVIEW/preview?embed=1")
    assert embedded.status_code == 200
    assert "文件较大，请下载后查看" in embedded.text
    assert "agent-os-preview-close" in embedded.text
    assert len(embedded.content) < 2000
