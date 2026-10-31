"""Project orchestration uses real ACP authorization, with only transport faked."""
from __future__ import annotations

import pathlib

import pytest

from app import acceptance_run, identity, mcp, pipeline, tenancy
from app.db import get_conn, init_db
from app.engine import dsh_adapter, runner_acp
from app.engine.acp_client import AcpError
from app.ui import roles, store


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_PIPELINE_EXEC", "1")
    monkeypatch.setenv("M0_PROJECT_TASK_BUDGET", "1000")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("M0_SANDBOX_ACP", "0")
    monkeypatch.setenv("UI_RUNNER", "acp")
    monkeypatch.setattr(dsh_adapter, "mount_shared", lambda _: None)
    monkeypatch.setattr(dsh_adapter, "_log_path", lambda rid: tmp_path / (rid + ".jsonl"))
    init_db()
    calls = {"starts": 0, "configs": [], "prompts": [], "failures": 0, "on_session": None}

    class FakeAcpClient:
        """No process or network; runner_acp's real auth runs before every prompt."""
        pid = None

        def __init__(self, cmd, *, cwd, env, default_timeout):
            self.cwd = pathlib.Path(cwd)
            self.log, self.stderr = [], []

        def start(self):
            calls["starts"] += 1

        def new_session(self, *, mcp_servers=None):
            calls["configs"].append(mcp_servers or [])
            if calls["on_session"]:
                calls["on_session"]()
            return {"sessionId": f"session-{calls['starts']}"}

        def prompt(self, sid, prompt, **kwargs):
            calls["prompts"].append(prompt)
            if calls["failures"]:
                calls["failures"] -= 1
                raise AcpError("isolated simulated transport interruption")
            (self.cwd / "solution.py").write_text(
                '"""Run with python solution.py. No third-party dependencies.\n'
                'This isolated fixture prints a predictable result and has no '
                'network, filesystem mutations, or external services.\n'
                'The extra description makes the actual artifact-size check meaningful.\n'
                '"""\nprint("runtime identity verified")\n', encoding="utf-8")
            return {"result": {"stopReason": "end_turn"}, "updates": 0}

        def close(self, **kwargs):
            pass

    monkeypatch.setattr(runner_acp, "AcpClient", FakeAcpClient)
    with get_conn() as conn:
        tenancy.ensure(conn)
        roles.ensure(conn)
        store.ensure(conn)
        mcp.ensure(conn)
        conn.execute("INSERT INTO tenants(id,name) VALUES ('TENANT-B','B')")
        conn.execute("""INSERT INTO legal_entities(id,name,type,tenant_id) VALUES
            ('LE-B','B','subsidiary','TENANT-B'),
            ('LE-B-SIBLING','B sibling','subsidiary','TENANT-B')""")
        people, employees, servers = {}, {}, {}
        for name, entity in (("own", "LE-B"), ("foreign", "LE-GROUP-HQ"),
                             ("sibling", "LE-B-SIBLING")):
            people[name] = identity.issue(conn, name=name, role="ceo", legal_entity_id=entity)
            employees[name] = roles.instantiate(conn, "ROLE-DEV", entity)
            tenant_id = tenancy.of_entity(conn, entity)
            server = mcp.add_server(conn, tenant_id=tenant_id, actor=people[name]["id"],
                                    name=name, url=f"https://{name}.example.test/mcp",
                                    api_key=f"isolated-{name}-credential")
            mcp.set_bindings(conn, employees[name], [server["id"]], tenant_id=tenant_id,
                             actor=people[name]["id"])
            servers[name] = server
        conn.commit()
        yield dict(conn=conn, people=people, employees=employees, servers=servers,
                   calls=calls)


def _project(runtime):
    conn = runtime["conn"]
    return pipeline.create_project(conn, name="Scoped project", requirement="开发命令行程序",
                                   entity="LE-B", owner=runtime["people"]["own"]["id"])["project_id"]


def _assert_owned_execution(runtime, pid, expected_runs):
    conn = runtime["conn"]
    rows = conn.execute("""SELECT r.actor_identity,r.tenant_id AS run_tenant,
        t.tenant_id AS task_tenant,t.legal_entity_id,t.owner_employee_id,
        t.owner_position_id,r.prompt_text FROM task_runs r
        JOIN tasks t ON t.id=r.task_id WHERE t.project_id=? ORDER BY r.rowid""", (pid,)).fetchall()
    assert len(rows) == expected_runs
    for row in rows:
        assert row["actor_identity"] == runtime["people"]["own"]["id"]
        assert row["task_tenant"] == row["run_tenant"] == "TENANT-B"
        assert row["legal_entity_id"] == "LE-B"
        assert row["owner_employee_id"] == runtime["employees"]["own"]
        assert row["owner_position_id"]
        assert "你是开发。职责边界" in row["prompt_text"]
    assert len(runtime["calls"]["configs"]) == expected_runs
    for configs in runtime["calls"]["configs"]:
        assert [cfg["url"] for cfg in configs] == ["https://own.example.test/mcp"]
        assert configs[0]["headers"][0]["value"] == "Bearer isolated-own-credential"


def _review_human_criteria(runtime, task_id):
    conn = runtime["conn"]
    for criterion in conn.execute("""SELECT id FROM acceptance_criteria
            WHERE task_id=? AND check_type='human_check'""", (task_id,)).fetchall():
        acceptance_run.judge(conn, criterion["id"], passed=True,
                             checked_by=runtime["people"]["own"]["id"],
                             evidence_snippet="Reviewed isolated print-only fixture, no destructive operations.")
    conn.commit()


def test_project_build_uses_verified_initiator_role_and_mcp(runtime, monkeypatch):
    # v5.4：这条用例的前提是「fake ACP 不会替人签批 L1」，必须走严格路径，
    # 否则测试通道的自动签批会把 L1 悄悄过掉，等于验证不到东西。
    monkeypatch.delenv("M0_AUTO_SIGN_L1_FOR_TEST", raising=False)
    conn = runtime["conn"]
    pid = _project(runtime)
    pipeline.build_wbs(conn, pid, "开发命令行程序")
    result = pipeline.execute_build(conn, pid, "开发命令行程序")
    # v5.4 规则 1：L0/L2 全绿即可放行，未签批的 blocking L1 不再算 build 失败；
    # 但也不许自动通过 —— 任务停在 pending_review，等第 7 阶段统一人工验收。
    assert result["tasks"] == 1 and result["failed"] == 0
    assert len(result["awaiting_signoff"]) == 1
    task = conn.execute("SELECT id,state FROM tasks WHERE project_id=?", (pid,)).fetchone()
    assert task["state"] == "pending_review"
    assert conn.execute("SELECT state FROM task_runs WHERE task_id=?", (task["id"],)).fetchone()[0] == "succeeded"
    _assert_owned_execution(runtime, pid, 1)
    # 规则 2 的负向断言：自动判定可以过，人工判据一条都不许被自动置 passed
    assert conn.execute(
        "SELECT COUNT(*) FROM acceptance_criteria WHERE task_id=?"
        " AND check_type='human_check' AND state='passed'", (task["id"],)).fetchone()[0] == 0
    _review_human_criteria(runtime, task["id"])
    # 规则 3：人工签批后重入 build，任务结算为 completed、WBS 收口，且不重复起 kernel
    second = pipeline.execute_build(conn, pid, "开发命令行程序")
    assert second["tasks"] == second["failed"] == 0
    assert runtime["calls"]["starts"] == 1
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == "completed"
    assert conn.execute("SELECT state FROM wbs_items WHERE project_id=?", (pid,)).fetchone()[0] == "done"


def test_recovery_preserves_initiator_and_employee_through_real_auth(runtime, monkeypatch):
    monkeypatch.delenv("M0_AUTO_SIGN_L1_FOR_TEST", raising=False)
    conn = runtime["conn"]
    pid = _project(runtime)
    runtime["calls"]["failures"] = 1
    with pytest.raises(RuntimeError, match="build failed"):
        pipeline.run_project(conn, pid, "开发命令行程序")
    first_task = conn.execute("SELECT task_id FROM wbs_items WHERE project_id=?", (pid,)).fetchone()[0]
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (first_task,)).fetchone()[0] == "failed"
    actor = dict(identity.get_identity(conn, runtime["people"]["own"]["id"]))
    claim = pipeline.prepare_retry(conn, pid, "开发命令行程序；修正传输失败后再次交付", actor=actor)
    # v5.4 规则 1/2：重跑成功后 build 不再判失败，项目推进到 accept 并挂起等人工签批。
    # 挂起不是异常也不是重试完成，所以 run_retry 返回 paused 而不是抛 build failed。
    resumed = pipeline.run_retry(conn, claim)
    assert resumed.get("paused") == "awaiting_accept", resumed
    current = conn.execute("SELECT task_id FROM wbs_items WHERE project_id=?", (pid,)).fetchone()[0]
    assert current != first_task
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (current,)).fetchone()[0] == "pending_review"
    _assert_owned_execution(runtime, pid, 2)
    project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert project["state"] == "awaiting_accept"
    assert project["owner_identity"] == actor["id"]

    # v5.4 规则 3：人工把项目里每一条 blocking L1 都签完（真实人类就是这么做的），
    # 然后任务才会结算、项目才会被唤醒收尾。这里同步执行，等价于后台唤醒。
    for _ in range(30):
        left = conn.execute(
            "SELECT DISTINCT c.task_id FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
            " WHERE t.project_id=? AND c.is_blocking=1 AND c.check_type='human_check'"
            " AND c.state NOT IN ('passed','na')", (pid,)).fetchall()
        if not left:
            break
        for r in left:
            _review_human_criteria(runtime, r["task_id"])
    assert conn.execute(
        "SELECT COUNT(*) FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
        " WHERE t.project_id=? AND c.is_blocking=1 AND c.check_type='human_check'"
        " AND c.state<>'passed'", (pid,)).fetchone()[0] == 0
    pipeline.settle_signed_tasks(conn, pid)
    conn.execute("UPDATE projects SET state='active' WHERE id=?", (pid,))
    conn.commit()
    keys = [r["key"] for r in conn.execute(
        "SELECT key FROM project_stages WHERE project_id=? AND state<>'done' ORDER BY seq",
        (pid,)).fetchall()]
    pipeline.run_project(conn, pid, "开发命令行程序", keys=keys)

    project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    # 这套 harness 的项目在阶段跑完后停在 active（没有 flow_id 时不会自动收成 done），
    # 关键不变量是「已脱离挂起、重试令牌已释放」。
    assert project["state"] == "active" and project["retry_token"] is None
    assert project["owner_identity"] == actor["id"]
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (current,)).fetchone()[0] == "completed"
    # 唤醒后的 release/retro 仍然用同一个发起人身份执行
    owned = conn.execute(
        "SELECT COUNT(*) FROM task_runs r JOIN tasks t ON t.id=r.task_id"
        " WHERE t.project_id=? AND r.actor_identity<>?", (pid, actor["id"])).fetchone()[0]
    assert owned == 0


@pytest.mark.parametrize("who", ["foreign", "sibling"])
def test_project_creation_rejects_foreign_or_sibling_owner(runtime, who):
    with pytest.raises(PermissionError, match="ProjectOwnerScopeMismatch"):
        pipeline.create_project(runtime["conn"], name="Rejected", requirement="开发",
                                entity="LE-B", owner=runtime["people"][who]["id"])
    assert runtime["calls"]["starts"] == 0


@pytest.mark.parametrize("change", ["revoked", "tenant", "entity"])
def test_build_rechecks_durable_owner_before_launch(runtime, change):
    conn = runtime["conn"]
    pid = _project(runtime)
    pipeline.build_wbs(conn, pid, "开发命令行程序")
    owner = runtime["people"]["own"]["id"]
    if change == "revoked":
        identity.revoke(conn, owner)
    elif change == "tenant":
        conn.execute("UPDATE identities SET tenant_id='TENANT-HQ' WHERE id=?", (owner,))
    else:
        conn.execute("UPDATE identities SET legal_entity_id='LE-B-SIBLING' WHERE id=?", (owner,))
    conn.commit()
    with pytest.raises(PermissionError):
        pipeline.execute_build(conn, pid, "开发命令行程序")
    assert runtime["calls"]["starts"] == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (pid,)).fetchone()[0] == 0


def test_actual_runner_rechecks_revocation_after_session_created(runtime):
    conn = runtime["conn"]
    pid = _project(runtime)
    pipeline.build_wbs(conn, pid, "开发命令行程序")
    def revoke_at_session_creation():
        identity.revoke(conn, runtime["people"]["own"]["id"])
        conn.commit()
    runtime["calls"]["on_session"] = revoke_at_session_creation
    result = pipeline.execute_build(conn, pid, "开发命令行程序")
    assert result["failed"] == 1
    assert runtime["calls"]["starts"] == 1 and runtime["calls"]["prompts"] == []
    run = conn.execute("""SELECT r.* FROM task_runs r JOIN tasks t ON t.id=r.task_id
                       WHERE t.project_id=?""", (pid,)).fetchone()
    assert run["state"] == "failed"
    assert "McpAuthorityDenied" in dsh_adapter._log_path(run["id"]).read_text(encoding="utf-8")


def test_recovery_rechecks_initiator_revoked_after_claim(runtime):
    conn = runtime["conn"]
    pid = _project(runtime)
    runtime["calls"]["failures"] = 1
    with pytest.raises(RuntimeError, match="build failed"):
        pipeline.run_project(conn, pid, "开发命令行程序")
    actor = dict(identity.get_identity(conn, runtime["people"]["own"]["id"]))
    claim = pipeline.prepare_retry(conn, pid, "开发命令行程序；修复后重试", actor=actor)
    identity.revoke(conn, actor["id"])
    conn.commit()
    with pytest.raises(PermissionError, match="ProjectOwnerInactive"):
        pipeline.run_retry(conn, claim)
    assert runtime["calls"]["starts"] == 1
    assert conn.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()[0] == "blocked"


def test_legacy_ownerless_project_fails_closed_in_real_mode(runtime):
    conn = runtime["conn"]
    pid = pipeline.create_project(conn, name="Legacy", requirement="开发", entity="LE-B")["project_id"]
    with pytest.raises(PermissionError, match="ProjectOwnerRequired"):
        pipeline.run_project(conn, pid, "开发")
    assert runtime["calls"]["starts"] == 0
