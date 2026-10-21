import io
import json
import pathlib
import zipfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect, init_db
from app import pipeline, identity
from app.engine import dsh_adapter

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v45.db")
    ws_path = str(tmp_path / "workspace")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": ws_path,
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v45-secret-key",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    pipeline.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()

    # Login to get cookie
    c = TestClient(app)
    from app.ui import api as ui_api
    token = ui_api._make_cookie(ceo["id"])
    c.cookies.set("oa_ui", token)
    return c, ceo, ws_path


def test_release_files_and_zip_and_preview(client):
    c, ceo, ws_path = client
    
    # Create project and release folder with dummy files
    pid = "P-TEST-REL-01"
    with connect() as conn:
        conn.execute("INSERT INTO projects (id, name, requirement, legal_entity_id, owner_identity, state)"
                     " VALUES (?, 'Release Test Project', 'Landing Page', 'LE-GROUP-HQ', ?, 'done')",
                     (pid, ceo["id"]))
        conn.commit()

    rel_dir = pathlib.Path(ws_path) / "projects" / pid / "release"
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "index.html").write_text("<!doctype html><html><body><h1>Hello</h1></body></html>", encoding="utf-8")
    (rel_dir / "api_schema.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (rel_dir / "guide.md").write_text("# Delivery Guide\nContent here.", encoding="utf-8")

    # 1. Test GET /ui/projects/{pid}/release/files
    res = c.get(f"/ui/projects/{pid}/release/files")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["has_release"] is True
    file_names = [f["name"] for f in data["files"]]
    assert "index.html" in file_names
    assert "api_schema.json" in file_names
    assert "guide.md" in file_names

    # 2. Test GET /ui/projects/{pid}/release/zip
    zip_res = c.get(f"/ui/projects/{pid}/release/zip")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(zip_res.content))
    assert set(zf.namelist()) == {"index.html", "api_schema.json", "guide.md"}
    assert "<h1>Hello</h1>" in zf.read("index.html").decode("utf-8")

    # 3. Test GET /ui/projects/{pid}/release/preview/{filename}
    html_prev = c.get(f"/ui/projects/{pid}/release/preview/index.html")
    assert html_prev.status_code == 200
    assert "text/html" in html_prev.headers["content-type"]
    assert "<h1>Hello</h1>" in html_prev.text

    json_prev = c.get(f"/ui/projects/{pid}/release/preview/api_schema.json")
    assert json_prev.status_code == 200
    assert "application/json" in json_prev.headers["content-type"]
    assert json_prev.json() == {"status": "ok"}

    # 4. Test path traversal blocking
    trav_res = c.get(f"/ui/projects/{pid}/release/preview/..%2fsecret.txt")
    assert trav_res.status_code in (400, 404)
