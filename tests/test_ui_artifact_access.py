"""Artifact direct URLs obey identity state, scopes and the task's read tenant."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import identity
from app.db import get_conn, init_db


@pytest.fixture()
def access(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "access.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    init_db()
    from app.ui import admin, api
    app = FastAPI()
    app.include_router(admin.router)
    tokens = {}
    with get_conn() as conn:
        for ident, parent in (("A", "LE-GROUP-HQ"), ("B", "LE-GROUP-HQ"),
                              ("CHILD", "LE-A"), ("OUTSIDE", None)):
            entity = "LE-" + ident
            conn.execute("INSERT INTO legal_entities(id,name,type,parent_entity_id) VALUES(?,?,'subsidiary',?)",
                         (entity, ident, parent))
            path = tmp_path / "workspace" / ident / "private.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Private output from {ident}", encoding="utf-8")
            conn.execute("INSERT INTO tasks(id,legal_entity_id,title,state) VALUES(?,?,?,'completed')",
                         ("T-" + ident, entity, ident))
            conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,state) VALUES(?,?,1,?,'succeeded')",
                         ("R-" + ident, "T-" + ident, entity))
            conn.execute("INSERT INTO artifacts(id,run_id,art_seq,file_path,checksum,bytes) VALUES(?,?,1,?,'test',20)",
                         ("ART-" + ident, "R-" + ident, str(path)))
        for name, role, entity in (("reader", "agent", "LE-A"), ("manager", "manager", "LE-A"),
                                   ("group", "ceo", "LE-GROUP-HQ"), ("subgroup", "ceo", "LE-A"),
                                   ("system", "system", "LE-GROUP-HQ"), ("revoked", "agent", "LE-A"),
                                   ("no_read", "agent", "LE-A")):
            who = identity.issue(conn, name=name, role=role, legal_entity_id=entity)
            tokens[name] = api._make_cookie(who["id"])
            if name == "revoked":
                identity.revoke(conn, who["id"])
            elif name == "no_read":
                # Persisted custom roles must not gain read access without a scope.
                conn.execute("UPDATE identities SET role='auditor',scopes='[\"audit:read\"]' WHERE id=?", (who["id"],))
        tokens["unknown"] = api._make_cookie("ID-no-longer-present")
        tokens["forged"] = "not.a.valid-ticket"

    def request(user, artifact="ART-A", endpoint="preview", *, mode="cookie", embed=False, method="GET"):
        with TestClient(app) as client:
            params = {"embed": "1"} if embed else {}
            token = tokens.get(user)
            if mode == "cookie" and token:
                client.cookies.set("oa_ui", token)
            elif mode == "bearer" and token:
                client.headers["authorization"] = f"Bearer {token}"
            elif mode == "query" and token:
                if endpoint == "preview":
                    issued = client.post(f"/ui/files/{artifact}/preview-ticket", headers={"authorization": f"Bearer {token}"})
                    if not issued.is_success:
                        return issued
                    params["ticket"] = issued.json()["ticket"]
                else:
                    params["t"] = token
            return client.request(method, f"/ui/files/{artifact}" + (f"/{endpoint}" if endpoint else ""), params=params)
    return request


FORMATS = [("download", False), ("preview", False), ("preview", True)]


@pytest.mark.parametrize("mode", ["cookie", "bearer", "query"])
@pytest.mark.parametrize(("endpoint", "embed"), FORMATS)
def test_own_read_and_foreign_direct_url_isolation(access, mode, endpoint, embed):
    own = access("reader", endpoint=endpoint, mode=mode, embed=embed)
    assert own.status_code == 200
    assert "Private output from A" in own.text
    foreign = access("reader", "ART-B", endpoint, mode=mode, embed=embed)
    missing = access("reader", "ART-MISSING", endpoint, mode=mode, embed=embed)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "FileNotFound"}}


@pytest.mark.parametrize(("endpoint", "embed"), FORMATS)
def test_group_reads_descendants_and_system_reads_all(access, endpoint, embed):
    for artifact in ("ART-A", "ART-B", "ART-CHILD"):
        assert access("group", artifact, endpoint, embed=embed).status_code == 200
    assert access("group", "ART-OUTSIDE", endpoint, embed=embed).status_code == 404
    assert access("subgroup", "ART-CHILD", endpoint, embed=embed).status_code == 200
    assert access("subgroup", "ART-B", endpoint, embed=embed).status_code == 404
    assert access("manager", "ART-CHILD", endpoint, embed=embed).status_code == 404
    assert access("system", "ART-OUTSIDE", endpoint, embed=embed).status_code == 200


@pytest.mark.parametrize("mode", ["cookie", "bearer", "query"])
@pytest.mark.parametrize(("endpoint", "embed"), FORMATS)
def test_revoked_unknown_and_unprivileged_identity_cannot_use_direct_urls(access, mode, endpoint, embed):
    for user in ("revoked", "unknown"):
        rejected = access(user, endpoint=endpoint, mode=mode, embed=embed)
        assert rejected.status_code == 401
        assert rejected.json()["detail"]["code"] == "UnknownIdentity"
    assert access("forged", endpoint=endpoint, mode=mode, embed=embed).status_code == 401
    assert access("anonymous", endpoint=endpoint, mode=mode, embed=embed).status_code == 401
    forbidden = access("no_read", endpoint=endpoint, mode=mode, embed=embed)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "Forbidden"


def test_read_permission_does_not_expand_delete_permission(access):
    assert access("reader", endpoint="", method="DELETE").status_code == 403
    assert access("manager", "ART-B", endpoint="", method="DELETE").status_code == 404
    assert access("group", "ART-A", endpoint="", method="DELETE").status_code == 404
    assert access("system", "ART-OUTSIDE", endpoint="", method="DELETE").status_code == 200
    assert access("system", "ART-OUTSIDE").status_code == 404
    assert access("manager", endpoint="", method="DELETE").status_code == 200
    assert access("reader").status_code == 404
