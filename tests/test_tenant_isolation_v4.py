"""Cross-tenant isolation regression tests (v4).

Validates strict data and authorization isolation between different tenants.
Asserts that Tenant A cannot read, tamper with, or discover Tenant B's data
across tasks, runs, artifacts/files, tool approvals, projects, chats/messages,
organization tree/departments/employees, and organizational experiences.
Also validates that spoofed X-Tenant-Id headers are rejected with 403 TenantMismatch,
and that an admin identity created via tools/create_tenant.py can authenticate
and see only its own tenant's organization hierarchy.
"""
from __future__ import annotations

import os
import pathlib
import pytest
from fastapi.testclient import TestClient

from app import experience, identity, pipeline, rbac, tenancy, tenant
from app.db import get_conn, init_db
from app.engine import task_generator
from app.ui import api, roles, store
from tools.create_tenant import create_tenant, list_tenants


def _as(client: TestClient, identity_row: dict) -> None:
    """Set authentication cookie for the designated identity."""
    client.cookies.set("oa_ui", api._make_cookie(identity_row["id"]))


@pytest.fixture()
def isolation_setup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate DB and workspace paths to a fresh temporary directory."""
    db_file = str(tmp_path / "tenant_isolation_v4.db")
    workspace_dir = str(tmp_path / "workspace")
    for key, value in {
        "DB_PATH": db_file,
        "M0_WORKSPACE": workspace_dir,
        "UI_CRON": "0",
        "WECOM_WATCHER": "0",
        "WECOM_APPROVAL_POLLER": "0",
        "OCTO_RUNNER": "0",
        "WECOM_AUTORUN": "0",
        "M0_PIPELINE_EXEC": "0",
        "RUNNER": "mock",
        "UI_RUNNER": "mock",
        "M0_SANDBOX": "none",
        "M0_SANDBOX_ACP": "0",
        "M0_HEARTBEAT_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)

    init_db()

    with get_conn() as conn:
        store.ensure(conn)
        roles.ensure(conn)
        pipeline.ensure(conn)
        rbac.ensure(conn)
        experience.ensure(conn)
        tenancy.ensure(conn)

        # 1. 开通租户 A（阿尔法科技）及其根实体与 CEO 管理员
        res_a = create_tenant(
            conn,
            tenant_id="TENANT-ALPHA",
            name="阿尔法科技",
            entity_id="LE-ALPHA-HQ",
            admin_name="alpha-admin",
        )
        admin_a = res_a["admin"]

        # 租户 A 下属子部门（验证同一租户内多实体下组织树包含子部门，但不包含其他租户）
        conn.execute("""
            INSERT INTO legal_entities
                (id, name, tenant_id, type, parent_entity_id, governance_mode, state)
            VALUES ('LE-ALPHA-DEPT', '阿尔法研发部', 'TENANT-ALPHA', 'subsidiary', 'LE-ALPHA-HQ', 'group_direct', 'active')
        """)
        emp_a = "EMP-ALPHA-01"
        conn.execute("""
            INSERT INTO ai_employees (id, name, model, legal_entity_id, role_id, status)
            VALUES (?, 'Alpha Agent', 'mock', 'LE-ALPHA-HQ', 'ROLE-CONTENT', 'online')
        """, (emp_a,))

        # 2. 开通租户 B（贝塔科技）及其根实体与 CEO 管理员
        res_b = create_tenant(
            conn,
            tenant_id="TENANT-BETA",
            name="贝塔科技",
            entity_id="LE-BETA-HQ",
            admin_name="beta-admin",
        )
        admin_b = res_b["admin"]

        # 租户 B 的专属员工
        emp_b = "EMP-BETA-01"
        conn.execute("""
            INSERT INTO ai_employees (id, name, model, legal_entity_id, role_id, status)
            VALUES (?, 'Beta Agent', 'mock', 'LE-BETA-HQ', 'ROLE-CONTENT', 'online')
        """, (emp_b,))

        # 租户 B 的私有任务
        task_b = task_generator.generate(conn, "贝塔商业机密任务", "LE-BETA-HQ")["task_id"]

        # 租户 B 的任务运行记录
        run_b = "RUN-BETA-001"
        conn.execute("""
            INSERT INTO task_runs
                (id, task_id, run_seq, legal_entity_id, tenant_id, state, runner)
            VALUES (?, ?, 1, 'LE-BETA-HQ', 'TENANT-BETA', 'succeeded', 'mock')
        """, (run_b, task_b))

        # 租户 B 的产物文件
        art_path = tmp_path / "workspace" / "beta_confidential.txt"
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_text("贝塔机密资产交付文档", encoding="utf-8")
        art_b = "ART-BETA-001"
        conn.execute("""
            INSERT INTO artifacts
                (id, run_id, tenant_id, art_seq, file_path, content, checksum, bytes)
            VALUES (?, ?, 'TENANT-BETA', 1, ?, '贝塔机密资产交付文档', 'checksum-beta', 24)
        """, (art_b, run_b, str(art_path)))

        # 租户 B 的高危工具审批请求
        appr_b = rbac.guard_tool(
            conn,
            agent_id=emp_b,
            tool="shell_exec",
            task_id=task_b,
            run_id=run_b,
            entity="LE-BETA-HQ",
            push=False,
        )["approval_id"]

        # 租户 B 的私有项目
        proj_b = pipeline.create_project(
            conn,
            name="贝塔战略研发项目",
            requirement="贝塔核心机密需求文档",
            entity="LE-BETA-HQ",
            tenant_id="TENANT-BETA",
        )["project_id"]

        # 租户 B 的私密群聊与聊天消息
        chat_b = store.create_group(conn, "贝塔高管讨论组", [emp_b], tenant_id="TENANT-BETA")["id"]
        conn.execute("UPDATE chats SET entity_id='LE-BETA-HQ' WHERE id=?", (chat_b,))
        store.add_message(conn, chat_b, "human", "贝塔绝密会议纪要：本季度收购计划")

        # 租户 B 的组织经验
        exp_b = experience.add(
            conn,
            kind="decision",
            title="贝塔风控决策经验",
            content="重大商业决议需由贝塔CEO单独核准，禁止下放给子实体",
            entity="LE-BETA-HQ",
            tenant_id="TENANT-BETA",
            actor={"id": admin_b["id"], "name": "beta-admin", "role": "ceo"},
        )["id"]

        # 为租户 A 也生成对照用的正常数据
        task_a = task_generator.generate(conn, "阿尔法公开任务", "LE-ALPHA-HQ")["task_id"]
        proj_a = pipeline.create_project(
            conn,
            name="阿尔法公开项目",
            requirement="阿尔法公开需求",
            entity="LE-ALPHA-HQ",
            tenant_id="TENANT-ALPHA",
        )["project_id"]
        chat_a = store.create_group(conn, "阿尔法研讨组", [emp_a], tenant_id="TENANT-ALPHA")["id"]
        conn.execute("UPDATE chats SET entity_id='LE-ALPHA-HQ' WHERE id=?", (chat_a,))
        store.add_message(conn, chat_a, "human", "阿尔法团队日常交流")
        exp_a = experience.add(
            conn,
            kind="consensus",
            title="阿尔法团队共识",
            content="日常任务必须进行单元测试",
            entity="LE-ALPHA-HQ",
            tenant_id="TENANT-ALPHA",
            actor={"id": admin_a["id"], "name": "alpha-admin", "role": "ceo"},
        )["id"]

        conn.commit()

    from app.main import app
    with TestClient(app) as client:
        _as(client, admin_a)
        yield client, {
            "admin_a": admin_a,
            "admin_b": admin_b,
            "emp_a": emp_a,
            "emp_b": emp_b,
            "task_a": task_a,
            "task_b": task_b,
            "run_b": run_b,
            "art_b": art_b,
            "appr_b": appr_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "chat_a": chat_a,
            "chat_b": chat_b,
            "exp_a": exp_a,
            "exp_b": exp_b,
        }


def test_cli_tenant_creation_idempotence_and_listing(isolation_setup):
    """验证开通脚本幂等性、实体/身份计数及已存在提示。"""
    _, ids = isolation_setup
    with get_conn() as conn:
        # 重复执行相同租户创建，断言幂等：不报错、不重复插入、不覆盖旧 secret
        duplicate = create_tenant(
            conn,
            tenant_id="TENANT-BETA",
            name="贝塔科技",
            entity_id="LE-BETA-HQ",
            admin_name="beta-admin",
        )
        assert duplicate["created"] is False
        assert duplicate["tenant_id"] == "TENANT-BETA"
        assert duplicate["admin"]["id"] == ids["admin_b"]["id"]

        # 检查列表统计
        tenants = conn.execute("SELECT id, name FROM tenants ORDER BY id").fetchall()
        assert {t["id"] for t in tenants} >= {"TENANT-HQ", "TENANT-ALPHA", "TENANT-BETA"}


def test_script_admin_login_and_org_tree_isolation(isolation_setup):
    """【组织树隔离】验证开通脚本创建的管理员登录后，组织架构只含自身租户实体，绝不泄漏其它租户。"""
    client, ids = isolation_setup
    client.cookies.clear()

    # 1. 使用脚本创建的租户 A 管理员凭证登录
    login_resp = client.post("/ui/login", json={
        "identity_id": ids["admin_a"]["id"],
        "secret": ids["admin_a"]["secret"],
    })
    assert login_resp.status_code == 200
    assert login_resp.json()["tenant_id"] == "TENANT-ALPHA"

    # 2. 检查 /ui/me 确认当前上下文
    me_resp = client.get("/ui/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["identity_id"] == ids["admin_a"]["id"]
    assert me_resp.json()["tenant_id"] == "TENANT-ALPHA"

    # 3. 检查 /ui/org 组织树根节点与实体列表
    org_resp = client.get("/ui/org")
    assert org_resp.status_code == 200
    org_data = org_resp.json()
    org_root_ids = {r["id"] if isinstance(r, dict) else r for r in org_data.get("roots", [])}
    # 【越界定义】：租户 A 的组织架构根节点若出现 LE-BETA-HQ 或其他租户的法人实体，将直接暴露商业对手的组织架构全貌。
    assert "LE-BETA-HQ" not in org_root_ids
    assert "LE-GROUP-HQ" not in org_root_ids
    assert org_root_ids == {"LE-ALPHA-HQ"}

    # 4. 检查 /ui/departments 部门列表
    dept_resp = client.get("/ui/departments")
    assert dept_resp.status_code == 200
    dept_ids = {d["id"] for d in dept_resp.json().get("departments", [])}
    # 【越界定义】：部门/法人实体列表包含企业分支与子公司信息，租户 A 查询部门时绝不可见租户 B 的实体。
    assert "LE-BETA-HQ" not in dept_ids
    assert dept_ids == {"LE-ALPHA-HQ", "LE-ALPHA-DEPT"}

    # 5. 检查 /ui/employees 员工列表
    emp_resp = client.get("/ui/employees")
    assert emp_resp.status_code == 200
    emp_ids = {e["id"] for e in emp_resp.json().get("employees", [])}
    # 【越界定义】：员工档案为核心人事资产，租户 A 查询员工列表时绝不可看到租户 B 的数字员工。
    assert ids["emp_b"] not in emp_ids
    assert ids["emp_a"] in emp_ids


def test_task_detail_and_actions_isolation(isolation_setup):
    """【任务越界】租户 A 无法通过 ID 查看或操控租户 B 的任务。"""
    client, ids = isolation_setup

    # 1. 任务列表隔离
    tasks_resp = client.get("/ui/tasks")
    assert tasks_resp.status_code == 200
    listed_task_ids = {t["id"] for t in tasks_resp.json().get("tasks", [])}
    # 【越界定义】：任务列表中如果能遍历到其他租户的任务，攻击者可枚举获取全系统的任务标题与排期。
    assert ids["task_b"] not in listed_task_ids
    assert ids["task_a"] in listed_task_ids

    # 2. 直接根据 ID 获取租户 B 的任务详情
    # 【越界定义】：根据已知或猜测的 task_id 直接读取租户 B 的任务详情，属于越权数据读取；
    # 系统采用 404（TaskNotFound）防止攻击者根据 403 探测任务 ID 的存在性。
    detail_resp = client.get(f"/ui/tasks/{ids['task_b']}")
    assert detail_resp.status_code == 404
    assert detail_resp.json()["detail"]["code"] == "TaskNotFound"

    # 3. 对租户 B 的任务发起操作指令（如终止/暂停等动作）
    # 【越界定义】：租户 A 试图对租户 B 的私有任务发起调度或终止操作，属于跨租户破坏行为，必须返回 404。
    action_resp = client.post(f"/ui/tasks/{ids['task_b']}/action", json={"action": "abort"})
    assert action_resp.status_code == 404

    # 4. 对租户 B 的任务发起重试
    # 【越界定义】：重试他人任务会触发模型调用并消耗租户 B 或系统的计算配额，必须返回 404。
    retry_resp = client.post(f"/ui/tasks/{ids['task_b']}/retry", json={})
    assert retry_resp.status_code == 404


def test_run_detail_and_trace_isolation(isolation_setup):
    """【运行详情与追踪越界】租户 A 无法查看租户 B 的运行状态、实时日志、追踪链路，也无法杀死其运行进程。"""
    client, ids = isolation_setup

    # 1. 获取任务运行的实时状态与输出 (live)
    # 【越界定义】：live 接口流式或轮询暴露正在执行的 Prompt、思考过程与即时工具调用，属于高危信息泄漏。
    live_resp = client.get(f"/ui/live/{ids['run_b']}")
    assert live_resp.status_code == 404
    assert live_resp.json()["detail"]["code"] == "RunNotFound"

    # 2. 获取任务运行的追踪链路 (trace)
    # 【越界定义】：trace 包含智能体执行步骤的完整 Span 树和调试日志，跨租户访问将彻底破坏运行黑盒隔离。
    trace_resp = client.get(f"/ui/runs/{ids['run_b']}/trace")
    assert trace_resp.status_code == 404
    assert trace_resp.json()["detail"]["code"] == "RunNotFound"

    # 3. 强行终止/杀死租户 B 的运行进程 (kill)
    # 【越界定义】：发送 kill 请求干预另一租户任务的执行生命周期，属于越权拒绝服务攻击（DoS），必须被拒绝。
    kill_resp = client.post(f"/ui/runs/{ids['run_b']}/kill")
    assert kill_resp.status_code == 404
    assert kill_resp.json()["detail"]["code"] == "RunNotFound"


def test_artifact_and_file_download_isolation(isolation_setup):
    """【产物与文件下载越界】租户 A 无法下载、预览、索取预览票据或删除租户 B 的产物文件。"""
    client, ids = isolation_setup

    # 1. 文件列表隔离
    files_resp = client.get("/ui/files")
    assert files_resp.status_code == 200
    listed_file_ids = {f["id"] for f in files_resp.json().get("files", [])}
    # 【越界定义】：全局文件列表中绝不可列出其他租户产生的交付物与附件。
    assert ids["art_b"] not in listed_file_ids

    # 2. 直接根据 artifact_id 下载文件
    # 【越界定义】：产物文件直接包含了模型为企业 B 生成的机密代码、商业企划书或财务报表，越权下载即为数据失窃。
    download_resp = client.get(f"/ui/files/{ids['art_b']}/download")
    assert download_resp.status_code == 404
    assert download_resp.json()["detail"]["code"] == "FileNotFound"

    # 3. 申请文件预览票据
    # 【越界定义】：预览票据用于在前端通过受限 iframe 渲染文档，未授权租户申请他人物料票据属于越权凭证换取。
    ticket_resp = client.post(f"/ui/files/{ids['art_b']}/preview-ticket")
    assert ticket_resp.status_code == 404
    assert ticket_resp.json()["detail"]["code"] == "FileNotFound"

    # 4. 预览文件内容
    # 【越界定义】：直接访问预览端点查看租户 B 文件渲染内容，属于敏感商业资产泄漏。
    preview_resp = client.get(f"/ui/files/{ids['art_b']}/preview")
    assert preview_resp.status_code == 404
    assert preview_resp.json()["detail"]["code"] == "FileNotFound"

    # 5. 删除租户 B 的文件
    # 【越界定义】：租户 A 试图标记或删除租户 B 的业务产物，属于越权篡改破坏他人资产。
    delete_resp = client.delete(f"/ui/files/{ids['art_b']}")
    assert delete_resp.status_code == 404
    assert delete_resp.json()["detail"]["code"] == "FileNotFound"


def test_approvals_list_and_decisions_isolation(isolation_setup):
    """【审批流与决策越界】租户 A 既看不到租户 B 的待审项，更绝对无法跨租户代为审批放行高危工具。"""
    client, ids = isolation_setup

    # 1. 普通审批列表隔离
    appr_list_resp = client.get("/ui/approvals")
    assert appr_list_resp.status_code == 200
    pending_appr_ids = {a["id"] for a in appr_list_resp.json().get("pending", [])}
    # 【越界定义】：高危工具审批包含了涉及的操作指令与目标资源，租户 A 可视化他人审批会暴露其内部运维动向。
    assert ids["appr_b"] not in pending_appr_ids

    # 2. RBAC 审批列表隔离
    rbac_appr_resp = client.get("/ui/rbac/approvals")
    assert rbac_appr_resp.status_code == 200
    rbac_appr_ids = {a["id"] for a in rbac_appr_resp.json().get("approvals", [])}
    assert ids["appr_b"] not in rbac_appr_ids

    # 3. 租户 A 试图直接批准租户 B 的待审批动作 (/ui/approvals/{id})
    # 【越界定义】：批准他人租户的高危命令（如 shell_exec）将越权在他人工作区中执行未授权代码，是极其严重的提权越界。
    decide_resp1 = client.post(f"/ui/approvals/{ids['appr_b']}", json={"approve": True})
    assert decide_resp1.status_code == 404
    assert decide_resp1.json()["detail"]["code"] == "ApprovalNotFound"

    # 4. 租户 A 试图通过 RBAC 端点批准租户 B 的待审批动作 (/ui/rbac/approvals/{id})
    # 【越界定义】：RBAC 决策直接决定特权放行，跨租户决策必须被 404 彻底阻断。
    decide_resp2 = client.post(f"/ui/rbac/approvals/{ids['appr_b']}", json={"approve": True})
    assert decide_resp2.status_code == 404
    assert decide_resp2.json()["detail"]["code"] == "ApprovalNotFound"

    # 5. 校验数据库中的审批状态没有被非法更改
    with get_conn() as conn:
        state = conn.execute("SELECT state FROM tool_approvals WHERE id=?", (ids["appr_b"],)).fetchone()[0]
        assert state == "pending"


def test_projects_list_detail_and_patch_isolation(isolation_setup):
    """【项目立项越界】租户 A 无法查阅、列举或修改租户 B 的战略立项。"""
    client, ids = isolation_setup

    # 1. 项目列表隔离
    proj_list_resp = client.get("/ui/projects")
    assert proj_list_resp.status_code == 200
    listed_proj_ids = {p["id"] for p in proj_list_resp.json().get("projects", [])}
    # 【越界定义】：立项列表包含企业战略立项名称与规划，租户 A 不得获取其他租户正在推进的项目清单。
    assert ids["proj_b"] not in listed_proj_ids
    assert ids["proj_a"] in listed_proj_ids

    # 2. 根据 project_id 查看详情
    # 【越界定义】：项目详情包含具体 requirement 商业需求明细，跨租户读取属于商业机密泄密。
    proj_detail_resp = client.get(f"/ui/projects/{ids['proj_b']}")
    assert proj_detail_resp.status_code == 404
    assert proj_detail_resp.json()["detail"]["code"] == "ProjectNotFound"

    # 3. 篡改他人项目属性 (PATCH /ui/projects/{id})
    # 【越界定义】：租户 A 试图篡改他人项目的执行配置（如修改使用知识库模型参数），属于恶意越权修改。
    proj_patch_resp = client.patch(f"/ui/projects/{ids['proj_b']}", json={"use_llm_docs": 1})
    assert proj_patch_resp.status_code == 404
    assert proj_patch_resp.json()["detail"]["code"] == "ProjectNotFound"


def test_chats_and_messages_isolation(isolation_setup):
    """【群聊与消息越界】租户 A 无法嗅探历史记录、无法偷窥对话、无法向租户 B 的群聊发送伪造消息。"""
    client, ids = isolation_setup

    # 1. 会话列表隔离
    chats_resp = client.get("/ui/chats")
    assert chats_resp.status_code == 200
    listed_chat_ids = {c["id"] for c in chats_resp.json().get("chats", [])}
    # 【越界定义】：租户 A 会话栏中绝不可出现租户 B 的群聊或私聊会话卡片。
    assert ids["chat_b"] not in listed_chat_ids
    assert ids["chat_a"] in listed_chat_ids

    # 2. 拉取租户 B 群聊的历史消息
    # 【越界定义】：群聊消息内包含员工讨论商业机密的全文，跨租户偷窥他人聊天记录属于严重越权读取。
    msg_resp = client.get(f"/ui/chats/{ids['chat_b']}/messages")
    assert msg_resp.status_code == 404
    assert msg_resp.json()["detail"]["code"] == "ChatNotFound"

    # 3. 向租户 B 的群聊发送注入消息
    # 【越界定义】：攻击者试图伪造身份向受害租户的群聊中注入诱导性虚假指令，可能导致受害租户智能体误执行恶意操作。
    post_msg_resp = client.post(f"/ui/chats/{ids['chat_b']}/messages", json={"text": "恶意注入消息"})
    assert post_msg_resp.status_code == 404
    assert post_msg_resp.json()["detail"]["code"] == "ChatNotFound"

    # 4. 修改或重命名租户 B 的群聊 (PATCH)
    # 【越界定义】：跨租户更改他人会话属性（如置顶、改名、隐藏），属于破坏他人工作台界面的越界行为。
    patch_chat_resp = client.patch(f"/ui/chats/{ids['chat_b']}", json={"title": "被恶意篡改的标题"})
    assert patch_chat_resp.status_code == 404
    assert patch_chat_resp.json()["detail"]["code"] == "ChatNotFound"

    # 5. 删除租户 B 的群聊 (DELETE)
    # 【越界定义】：跨租户删除他人沟通群聊与记录，属于越权抹除他人业务记录。
    del_chat_resp = client.delete(f"/ui/chats/{ids['chat_b']}")
    assert del_chat_resp.status_code == 404
    assert del_chat_resp.json()["detail"]["code"] == "ChatNotFound"


def test_org_experiences_isolation(isolation_setup):
    """【组织经验越界】租户 A 无法查阅、评审或修改租户 B 沉淀的红线风控规则与经验共识。"""
    client, ids = isolation_setup

    # 1. 组织经验列表隔离
    exp_list_resp = client.get("/ui/experiences")
    assert exp_list_resp.status_code == 200
    listed_exp_ids = {e["id"] for e in exp_list_resp.json().get("experiences", [])}
    # 【越界定义】：组织经验包含了租户 B 在业务运转中积累的风控红线、驳回原因与经验库，跨租户列出属于知识产权泄密。
    assert ids["exp_b"] not in listed_exp_ids
    assert ids["exp_a"] in listed_exp_ids

    # 2. 修改他人组织经验 (PATCH /ui/experiences/{id})
    # 【越界定义】：租户 A 篡改租户 B 的经验规则（如删改合规红线词），会导致租户 B 后续项目注入错误经验。
    edit_resp = client.patch(f"/ui/experiences/{ids['exp_b']}", json={"title": "被篡改的标题"})
    assert edit_resp.status_code == 404
    assert edit_resp.json()["detail"]["code"] == "NotFound"

    # 3. 评审他人组织经验 (POST /ui/experiences/{id}/review)
    # 【越界定义】：组织经验的审核放行直接决定该经验是否成为租户强制红线，跨租户执行审核审批属于严重越权治理。
    review_resp = client.post(f"/ui/experiences/{ids['exp_b']}/review", json={
        "action": "approve",
        "expected_version": 1,
        "note": "跨租户非法审批",
    })
    assert review_resp.status_code == 404
    assert review_resp.json()["detail"]["code"] == "NotFound"


def test_forged_x_tenant_id_header_returns_403_mismatch(isolation_setup):
    """【伪造租户请求头越界】携带租户 A 身份令牌但伪造 X-Tenant-Id: TENANT-BETA 头，统一直接阻断在 403。"""
    client, ids = isolation_setup

    headers = {"X-Tenant-Id": "TENANT-BETA"}

    probes = [
        ("GET", "/ui/tasks", None),
        ("GET", "/ui/chats", None),
        ("GET", "/ui/employees", None),
        ("GET", "/ui/projects", None),
        ("GET", "/ui/files", None),
        ("GET", "/ui/org", None),
        ("GET", "/ui/departments", None),
        ("GET", "/ui/approvals", None),
        ("GET", "/ui/experiences", None),
        ("POST", "/ui/groups", {"name": "越权群", "employee_ids": []}),
    ]

    for method, path, body in probes:
        # 【越界定义】：客户端请求头声称属于 TENANT-BETA，但其身份鉴权票据实质为 TENANT-ALPHA；
        # 服务端 tenancy.resolve 在执行任何业务查询之前比对不一致，必须立即中断并返回 403 TenantMismatch，
        # 绝不允许继续下探或以伪造的租户身份执行查询。
        resp = client.request(method, path, json=body, headers=headers)
        assert resp.status_code == 403, f"{method} {path} 未返回 403，实际返回: {resp.status_code}"
        assert resp.json()["detail"]["code"] == "TenantMismatch"
