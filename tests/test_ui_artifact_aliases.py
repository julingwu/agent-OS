"""Retry versions share a file identity without resurrecting soft-deleted aliases."""
from __future__ import annotations

import json
import os
import pathlib

from app import identity
from app.db import get_conn
from app.engine import task_generator
from app.ui import api as ui_api
from tests.test_ui_task_actions import console, _submission


def test_retry_artifacts_dedupe_by_canonical_path_and_delete_all_task_aliases(console):
    raw, signed, _ = console
    tid, _, first = _submission(console)
    old_id = first["artifacts"][0]["artifact_id"]
    raw.post(f"/ui/tasks/{tid}/action", json={"action": "reject", "note": "请优化文案"})
    retried = signed.post(f"/tasks/{tid}/retry", json={"runner": "mock"})
    assert retried.status_code == 200, retried.text
    retry = retried.json()
    new_id = retry["artifacts"][0]["artifact_id"]
    path = pathlib.Path(retry["artifacts"][0]["file_path"])
    assert path == pathlib.Path(first["artifacts"][0]["file_path"])

    # Equal names in different folders are separate deliverables.
    nested = path.parent / "nested" / path.name
    nested.parent.mkdir()
    nested.write_text("separate nested deliverable", encoding="utf-8")
    added = signed.post("/verbs/attach_artifact", json={"run_id": retry["run_id"], "art_seq": 2,
                                                       "file_path": str(nested)})
    assert added.status_code == 200, added.text
    nested_id = added.json()["artifact_id"]
    alias_path = str(path.parent) + os.sep + "." + os.sep + path.name
    with get_conn() as conn:
        conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)"
                     " VALUES ('ART-canonical-alias',?,3,?,'same-file',?)",
                     (retry["run_id"], alias_path, path.stat().st_size))

    expected = {"ART-canonical-alias", nested_id}
    view = raw.get(f"/ui/tasks/{tid}").json()
    assert {a["id"] for a in view["artifacts"]} == expected
    files = [f for f in raw.get("/ui/files").json()["files"] if f["task_id"] == tid]
    assert {f["id"] for f in files} == expected
    assert {f["name"] for f in files} == {path.name}
    limited = raw.get("/ui/files", params={"limit": 1}).json()
    assert len(limited["files"]) == 1 and limited["total"] == 2

    deleted = raw.delete("/ui/files/ART-canonical-alias")
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 3, deleted.text
    assert [a["id"] for a in raw.get(f"/ui/tasks/{tid}").json()["artifacts"]] == [nested_id]
    assert [f["id"] for f in raw.get("/ui/files").json()["files"] if f["task_id"] == tid] == [nested_id]
    assert path.exists() and nested.exists(), "soft deletion must preserve physical files"
    for alias in (old_id, new_id, "ART-canonical-alias"):
        assert raw.get(f"/ui/files/{alias}/preview").status_code == 404
        assert raw.get(f"/ui/files/{alias}/download").status_code == 404
    assert raw.get(f"/ui/files/{nested_id}/preview").status_code == 200
    with get_conn() as conn:
        states = {r["id"]: r["state"] for r in conn.execute(
            "SELECT id,state FROM artifacts WHERE id IN (?,?,?,?)",
            (old_id, new_id, "ART-canonical-alias", nested_id))}
        assert states == {old_id: "deleted", new_id: "deleted", "ART-canonical-alias": "deleted", nested_id: "active"}
        audit = conn.execute("SELECT legal_entity_id,task_id,payload FROM ai_audit_logs"
                             " WHERE action='file_deleted' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert audit["legal_entity_id"] == "LE-GROUP-HQ" and audit["task_id"] == tid
        assert set(json.loads(audit["payload"])["artifact_ids"]) == {old_id, new_id, "ART-canonical-alias"}


def test_deleting_file_aliases_stays_inside_task_and_write_tenant(console):
    raw, _, boss = console
    tid, _, output = _submission(console)
    artifact_id = output["artifacts"][0]["artifact_id"]
    path = output["artifacts"][0]["file_path"]
    with get_conn() as conn:
        conn.execute("INSERT INTO legal_entities (id,name,type) VALUES ('LE-OTHER','其他组织','subsidiary')")
        other_task = task_generator.generate(conn, "写一篇独立短文", "LE-OTHER")["task_id"]
        conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,state)"
                     " VALUES ('RUN-other-alias',?,1,'LE-OTHER','succeeded')", (other_task,))
        conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)"
                     " VALUES ('ART-other-alias','RUN-other-alias',1,?,'other',10)", (path,))
        agent = identity.issue(conn, name="read-only agent", role="agent", legal_entity_id="LE-GROUP-HQ")
    assert raw.delete("/ui/files/ART-other-alias").status_code == 404
    raw.cookies.set("oa_ui", ui_api._make_cookie(agent["id"]))
    assert raw.delete(f"/ui/files/{artifact_id}").status_code == 403
    raw.cookies.set("oa_ui", ui_api._make_cookie(boss["id"]))
    assert raw.delete(f"/ui/files/{artifact_id}").status_code == 200
    assert raw.get(f"/ui/tasks/{tid}").json()["artifacts"] == []
    assert raw.get("/ui/files").json()["files"] == [], "other tenant alias must not appear as a restored file"
    with get_conn() as conn:
        assert conn.execute("SELECT state FROM artifacts WHERE id='ART-other-alias'").fetchone()[0] == "active"
