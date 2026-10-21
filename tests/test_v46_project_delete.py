"""v4.5.3：项目彻底删除 + 工作产物删除的残留清理。

覆盖两件事：
1. `DELETE /ui/projects/{id}` 连带清掉阶段/闸门/WBS/任务/Run/交付物行与磁盘文件；
2. `DELETE /ui/files/{id}` 除了软删，还要真的删盘、清黑板与阶段交付物记录。
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app import identity, pipeline
from app.db import connect, init_db
from app.main import app


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v46.db")
    ws_path = tmp_path / "workspace"
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(ws_path),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v46-secret-key",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    from app import flows
    pipeline.ensure(conn)
    flows.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    agent = identity.issue(conn, name="agent-x", role="agent", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()

    from app.ui import api as ui_api
    ceo_client = TestClient(app)
    ceo_client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
    agent_client = TestClient(app)
    agent_client.cookies.set("oa_ui", ui_api._make_cookie(agent["id"]))
    return {"ceo": ceo_client, "agent": agent_client, "ws": ws_path,
            "db": db_path, "ceo_id": ceo["id"]}


def _seed_project(pid: str, ws: pathlib.Path, owner: str, *, state: str = "blocked"):
    """造一个有阶段、闸门、WBS、任务、Run、产物、黑板、阶段交付物的项目。"""
    conn = connect()
    conn.execute(
        "INSERT INTO projects (id,name,requirement,legal_entity_id,owner_identity,state,tenant_id)"
        " VALUES (?,?,?,'LE-GROUP-HQ',?,?,'TENANT-HQ')",
        (pid, "删除测试项目", "做一个落地页", owner, state))
    conn.execute("INSERT INTO project_stages (id,project_id,seq,key,title,state)"
                 " VALUES (?,?,0,'intake','接需求','done')", ("S-" + pid, pid))
    conn.execute("INSERT INTO stage_gates (id,project_id,stage_key,name,kind,state)"
                 " VALUES (?,?,'intake','需求已登记','auto','passed')", ("G-" + pid, pid))
    conn.execute("INSERT INTO wbs_items (id,project_id,code,title,role_tag,pack_id,depends_on,state,task_id)"
                 " VALUES (?,?,'T1','前端页面','dev','web-v1','[]','done',?)",
                 ("W-" + pid, pid, "T-" + pid))
    conn.execute("INSERT INTO tasks (id,legal_entity_id,task_type,title,state,project_id,tenant_id)"
                 " VALUES (?,'LE-GROUP-HQ','write','前端页面','completed',?,'TENANT-HQ')",
                 ("T-" + pid, pid))
    conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,tenant_id,state,runner,actor_identity)"
                 " VALUES (?,?,1,'LE-GROUP-HQ','TENANT-HQ','succeeded','stub',?)",
                 ("RUN-" + pid, "T-" + pid, owner))

    deliverable = ws / "projects" / pid / "deliveries" / "index.html"
    deliverable.parent.mkdir(parents=True, exist_ok=True)
    deliverable.write_text("<!doctype html><html><body>hi</body></html>", encoding="utf-8")
    task_dir = ws / owner / ("T-" + pid) / "calc.py"
    task_dir.parent.mkdir(parents=True, exist_ok=True)
    task_dir.write_text("print('ok')", encoding="utf-8")

    conn.execute("INSERT INTO artifacts (id,run_id,tenant_id,art_seq,file_path,checksum,bytes)"
                 " VALUES (?,?, 'TENANT-HQ',1,?,'x',?)",
                 ("ART-" + pid, "RUN-" + pid, str(deliverable), deliverable.stat().st_size))
    conn.execute("INSERT INTO project_artifacts (id,project_id,stage_key,role_tag,title,content,kind,file_path,"
                 " tenant_id,legal_entity_id) VALUES (?,?,'build','dev','前端页面','<html>','deliverable',?,"
                 " 'TENANT-HQ','LE-GROUP-HQ')", ("PA-" + pid, pid, str(deliverable)))
    conn.execute("INSERT INTO stage_deliverables (id,project_id,stage_key,kind,file_path,bytes)"
                 " VALUES (?,?,'build','web',?,?)",
                 ("D-" + pid, pid, str(deliverable), deliverable.stat().st_size))
    conn.commit()
    conn.close()
    return deliverable, task_dir


def _count(sql: str, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def test_delete_project_purges_db_and_disk(ctx):
    pid = "P-DEL-01"
    deliverable, task_file = _seed_project(pid, ctx["ws"], ctx["ceo_id"])

    res = ctx["ceo"].delete(f"/ui/projects/{pid}")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["ok"] is True
    assert out["files_removed"] >= 2          # 交付物 + 任务工作区文件
    assert out["dirs_removed"] >= 2           # projects/<pid> + <owner>/<task>

    # 关联行全清
    assert _count("SELECT COUNT(*) FROM projects WHERE id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM project_stages WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM stage_gates WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM wbs_items WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM stage_deliverables WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM project_artifacts WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM tasks WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM task_runs WHERE task_id=?", ("T-" + pid,)) == 0
    assert _count("SELECT COUNT(*) FROM artifacts WHERE run_id=?", ("RUN-" + pid,)) == 0

    # 磁盘产物与目录真删
    assert not deliverable.exists()
    assert not task_file.exists()
    assert not (ctx["ws"] / "projects" / pid).exists()
    assert not task_file.parent.exists()


def test_delete_project_forbidden_for_agent(ctx):
    pid = "P-DEL-02"
    _seed_project(pid, ctx["ws"], ctx["ceo_id"])
    res = ctx["agent"].delete(f"/ui/projects/{pid}")
    assert res.status_code == 403
    # 数据必须还在
    assert _count("SELECT COUNT(*) FROM projects WHERE id=?", (pid,)) == 1


def test_delete_project_rejects_running(ctx):
    pid = "P-DEL-03"
    _seed_project(pid, ctx["ws"], ctx["ceo_id"])
    conn = connect()
    conn.execute("UPDATE task_runs SET state='running' WHERE id=?", ("RUN-" + pid,))
    conn.commit()
    conn.close()
    res = ctx["ceo"].delete(f"/ui/projects/{pid}")
    assert res.status_code == 409
    assert _count("SELECT COUNT(*) FROM projects WHERE id=?", (pid,)) == 1


def test_delete_project_404(ctx):
    res = ctx["ceo"].delete("/ui/projects/P-NOT-EXIST")
    assert res.status_code == 404


def test_delete_file_keeps_path_shared_with_other_task(ctx):
    """同一物理文件被别的任务（别的租户）引用时，删盘必须让路。"""
    pid = "P-DEL-05"
    deliverable, _ = _seed_project(pid, ctx["ws"], ctx["ceo_id"])
    conn = connect()
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type) VALUES ('LE-OTHER','其他','subsidiary')")
    conn.execute("INSERT INTO tasks (id,legal_entity_id,task_type,title,state,tenant_id)"
                 " VALUES ('T-OTHER','LE-OTHER','write','别的任务','completed','TENANT-HQ')")
    conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,state)"
                 " VALUES ('RUN-OTHER','T-OTHER',1,'LE-OTHER','succeeded')")
    conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)"
                 " VALUES ('ART-OTHER','RUN-OTHER',1,?,'x',10)", (str(deliverable),))
    conn.commit()
    conn.close()

    res = ctx["ceo"].delete(f"/ui/files/ART-{pid}")
    assert res.status_code == 200, res.text
    assert res.json()["files_removed"] == 0
    assert deliverable.exists(), "shared physical file must survive"


def test_delete_file_removes_disk_and_residue(ctx):
    pid = "P-DEL-04"
    deliverable, _ = _seed_project(pid, ctx["ws"], ctx["ceo_id"])

    res = ctx["ceo"].delete(f"/ui/files/ART-{pid}")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["deleted"] >= 1
    assert out["files_removed"] >= 1
    # 黑板与阶段交付物里的同路径记录一并清掉，避免列表里留死链
    assert out["purged"]["project_artifacts"] >= 1
    assert out["purged"]["stage_deliverables"] >= 1
    assert not deliverable.exists()
    assert _count("SELECT COUNT(*) FROM project_artifacts WHERE project_id=?", (pid,)) == 0
    assert _count("SELECT COUNT(*) FROM stage_deliverables WHERE project_id=?", (pid,)) == 0
    # artifacts 行是软删（保留审计线索），不再出现在 active 列表
    assert _count("SELECT COUNT(*) FROM artifacts WHERE id=? AND COALESCE(state,'active')='active'",
                  ("ART-" + pid,)) == 0
