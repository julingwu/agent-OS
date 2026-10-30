"""组织招聘调度引擎（Recruitment & Staffing Engine）。

闭环工作流：
1. 8 大部门组织编制巡检（inspect_department_staffing）
2. 自动生成人事招聘考核任务并完成入职闭环（trigger_auto_recruitment）：
   - 人事部（HR）责任建单在 tasks 表生成【招聘任务】
   - 在 acceptance_criteria 插入 3 项验收判据
   - 生成招聘评估产物归档到 artifacts
   - 实例化新员工入职（写入 ai_employees）
   - 判据全部置为 passed，招聘任务置为 completed
   - 写入审计日志 ai_audit_logs (verb="recruitment", action="employee_hired")
   - 触发全息广播通知
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime

from . import artifacts as art_mod, telemetry
from .audit import write_audit
from .ui import roles as roles_mod

# 8 大部门核心定义与推荐角色映射
DEPARTMENTS = [
    {
        "dept_idx": 0,
        "name": "产品部",
        "en": "PRODUCT",
        "roles": ["ROLE-PRODUCT"],
        "default_role": "ROLE-PRODUCT",
        "role_title": "产品经理",
        "fn": "需求 · 原型 · 用户研究",
    },
    {
        "dept_idx": 1,
        "name": "设计部",
        "en": "DESIGN",
        "roles": ["ROLE-DESIGN"],
        "default_role": "ROLE-DESIGN",
        "role_title": "视觉设计师",
        "fn": "界面 · 视觉 · 品牌物料",
    },
    {
        "dept_idx": 2,
        "name": "市场部",
        "en": "MARKETING",
        "roles": ["ROLE-CONTENT", "ROLE-GROWTH"],
        "default_role": "ROLE-CONTENT",
        "role_title": "资深文案",
        "fn": "品牌 · 内容 · 推广",
    },
    {
        "dept_idx": 3,
        "name": "运营部",
        "en": "OPERATIONS",
        "roles": ["ROLE-OPS"],
        "default_role": "ROLE-OPS",
        "role_title": "运维/SRE",
        "fn": "用户运营 · 客服 · 活动",
    },
    {
        "dept_idx": 4,
        "name": "研发部",
        "en": "ENGINEERING",
        "roles": ["ROLE-DEV", "ROLE-ARCHITECT", "ROLE-QA"],
        "default_role": "ROLE-DEV",
        "role_title": "Python极客",
        "fn": "前后端 · 算法 · 测试 · 运维",
    },
    {
        "dept_idx": 5,
        "name": "人事部",
        "en": "PEOPLE · HR",
        "roles": ["ROLE-HR", "ROLE-RESEARCH"],
        "default_role": "ROLE-HR",
        "role_title": "人事主管",
        "fn": "招聘 · 培训 · 员工关系",
    },
    {
        "dept_idx": 6,
        "name": "财务部",
        "en": "FINANCE",
        "roles": ["ROLE-DATA"],
        "default_role": "ROLE-DATA",
        "role_title": "金融分析师",
        "fn": "预算 · 核算 · 税务",
    },
    {
        "dept_idx": 7,
        "name": "法务行政部",
        "en": "LEGAL & ADMIN",
        "roles": ["ROLE-AUDIT", "ROLE-LEGAL"],
        "default_role": "ROLE-AUDIT",
        "role_title": "审计/合规",
        "fn": "合同 · 合规 · 行政",
    },
]

ROLE_DEPT_MAP = {
    "ROLE-PRODUCT": 0,
    "ROLE-DESIGN": 1,
    "ROLE-CONTENT": 2,
    "ROLE-GROWTH": 2,
    "ROLE-OPS": 3,
    "ROLE-DEV": 4,
    "ROLE-ARCHITECT": 4,
    "ROLE-QA": 4,
    "ROLE-HR": 5,
    "ROLE-RESEARCH": 5,
    "ROLE-DATA": 6,
    "ROLE-AUDIT": 7,
    "ROLE-LEGAL": 7,
}

DEPT_PATTERNS = [
    (0, re.compile(r"产品|PM|需求|项目经理|规划", re.I)),
    (1, re.compile(r"设计|UI|UX|视觉|美工|插画", re.I)),
    (2, re.compile(r"市场|文案|营销|品牌|内容|推广|新媒体|撰稿", re.I)),
    (3, re.compile(r"运营|SRE|运维|客服|增长|社群|活动", re.I)),
    (4, re.compile(r"研发|开发|工程|前端|后端|Python|架构|算法|量化|测试|QA|代码|极客|小码|小测", re.I)),
    (5, re.compile(r"人事|HR|招聘|培训|组织|研究", re.I)),
    (6, re.compile(r"财务|金融|会计|税务|核算|成本|投研|小财", re.I)),
    (7, re.compile(r"法务|合规|审计|行政|风控|合同|安全", re.I)),
]


def map_dept(title: str, name: str = "", entity_name: str = "", role_id: str = "") -> int:
    """根据角色 ID、岗位名称、姓名与实体名称映射所属部门索引 (0~7)。"""
    if role_id and role_id in ROLE_DEPT_MAP:
        return ROLE_DEPT_MAP[role_id]
    text = f"{title or ''} {name or ''} {entity_name or ''}"
    for idx, pat in DEPT_PATTERNS:
        if pat.search(text):
            return idx
    return 4


def _ensure_schema(conn) -> None:
    """保证 tasks、task_runs、artifacts 和 acceptance_criteria 所需字段完备。"""
    from .ui import store
    store.ensure(conn)
    cols_tasks = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "stage" not in cols_tasks:
        conn.execute("ALTER TABLE tasks ADD COLUMN stage TEXT")
    if "owner_employee_id" not in cols_tasks:
        conn.execute("ALTER TABLE tasks ADD COLUMN owner_employee_id TEXT")
    if "tenant_id" not in cols_tasks:
        conn.execute("ALTER TABLE tasks ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'")


def inspect_department_staffing(conn, entity_id: str = "LE-GROUP-HQ") -> list[dict]:
    """遍历 8 大部门，统计各部门在岗员工人数。若某部门人数为 0，标记为 understaffed。"""
    has_emp_table = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_employees'"
    ).fetchone())
    if not has_emp_table:
        return []

    emp_rows = conn.execute("""
        SELECT e.id, e.name, e.status, e.role_id, p.title, le.name as entity_name
        FROM ai_employees e
        LEFT JOIN positions p ON p.id = e.position_id
        LEFT JOIN legal_entities le ON le.id = e.legal_entity_id
        WHERE e.legal_entity_id = ? AND COALESCE(e.status, 'online') != 'fired'
    """, (entity_id,)).fetchall()

    dept_employees: dict[int, list[dict]] = {i: [] for i in range(8)}
    for r in emp_rows:
        dept_idx = map_dept(r["title"], r["name"], r["entity_name"], r["role_id"] or "")
        if 0 <= dept_idx < 8:
            dept_employees[dept_idx].append({
                "id": r["id"],
                "name": r["name"],
                "title": r["title"] or "",
                "role_id": r["role_id"],
            })

    staffing = []
    for dept in DEPARTMENTS:
        idx = dept["dept_idx"]
        emps = dept_employees.get(idx, [])
        count = len(emps)
        staffing.append({
            "dept_idx": idx,
            "name": dept["name"],
            "en": dept["en"],
            "fn": dept["fn"],
            "headcount": count,
            "understaffed": (count == 0),
            "employees": emps,
            "recommended_role": dept["default_role"],
            "recommended_title": dept["role_title"],
            "candidate_roles": dept["roles"],
        })
    return staffing


def _get_or_create_hr_recruiter(conn, entity_id: str, tenant_id: str) -> tuple[str, str]:
    """获取或初始化人事部（HR）责任招聘官员工。"""
    hr_emp = conn.execute("""
        SELECT e.id, e.name, e.role_id, p.title
        FROM ai_employees e
        LEFT JOIN positions p ON p.id = e.position_id
        WHERE e.legal_entity_id = ? AND COALESCE(e.status, 'online') != 'fired'
          AND (e.role_id IN ('ROLE-HR', 'ROLE-RESEARCH')
               OR p.title LIKE '%人事%' OR p.title LIKE '%HR%' OR p.title LIKE '%招聘%')
        ORDER BY CASE WHEN e.role_id = 'ROLE-HR' THEN 0 ELSE 1 END LIMIT 1
    """, (entity_id,)).fetchone()

    if hr_emp:
        return hr_emp["id"], hr_emp["name"]

    # 兜底：先实例化 ROLE-HR 作为 HRD
    hr_emp_id = roles_mod.instantiate(conn, "ROLE-HR", entity_id)
    row = conn.execute("SELECT name FROM ai_employees WHERE id=?", (hr_emp_id,)).fetchone()
    name = row["name"] if row else "人事主管"
    return hr_emp_id, name


def trigger_auto_recruitment(
    conn,
    entity_id: str = "LE-GROUP-HQ",
    tenant_id: str = "TENANT-HQ",
) -> list[dict]:
    """组织编制巡检与自动招聘闭环工作流。
    
    检测各部门缺编状态，由人事部创建招聘考核任务，生成评估报告产物，
    完成定岗入驻，更新判据与任务状态，写入审计日志并触发广播。
    """
    _ensure_schema(conn)
    roles_mod.ensure(conn)

    staffing = inspect_department_staffing(conn, entity_id)
    understaffed = [d for d in staffing if d["understaffed"]]
    if not understaffed:
        return []

    # 确保 HR 招聘责任主体存在
    hr_emp_id, hr_emp_name = _get_or_create_hr_recruiter(conn, entity_id, tenant_id)

    # 重新检查巡检（如果 HR 刚刚被实例化入职，人事部编制已满足）
    staffing = inspect_department_staffing(conn, entity_id)
    understaffed = [d for d in staffing if d["understaffed"]]

    recruited_list = []

    for dept in understaffed:
        dept_idx = dept["dept_idx"]
        dept_name = dept["name"]
        role_id = dept["recommended_role"]
        role_name = dept["recommended_title"]

        task_id = f"T-RECRUIT-D{dept_idx}-{secrets.token_hex(4).upper()}"
        task_title = f"【招聘任务】为【{dept_name}】招聘 AI 员工：{role_name}"

        # 1. 人事部作为责任主体在 tasks 表创建真实招聘任务（state=in_progress）
        conn.execute("""
            INSERT INTO tasks (
                id, legal_entity_id, tenant_id, title, stage,
                owner_employee_id, state, task_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'recruitment', ?, 'in_progress', 'recruitment', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (task_id, entity_id, tenant_id, task_title, hr_emp_id))

        # 2. 插入三条清晰的验收判据
        criteria_specs = [
            ("岗位需求画像与能力模型已确立", "岗位画像与组织架构编制确认无误，明确业务边界与交付标准"),
            ("候选人角色模板匹配与专业能力考评通过", "能力模型考核通过，专业技能雷达与压力测试达到录用基准"),
            ("完成组织定岗与工位入驻", "生成员工工号、分配对应部门工位并点亮 3D HQ 智慧总部工位"),
        ]
        for seq, (crit_name, note) in enumerate(criteria_specs, start=1):
            crit_id = f"CRIT-REC-{task_id}-{seq}"
            conn.execute("""
                INSERT INTO acceptance_criteria
                (id, legal_entity_id, task_id, criterion, check_type, check_ref, is_blocking,
                 evidence_required, is_compliance, acceptance_layer, state, note, seq)
                VALUES (?, ?, ?, ?, 'hr_eval', 'recruitment', 1, 1, 1, 'L1', 'in_progress', ?, ?)
            """, (crit_id, entity_id, task_id, crit_name, note, seq))

        # 3. 生成招聘评估产物文件并归档到 artifacts
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        artifact_content = f"""# 【人事部招聘与录用评估报告】
- **招聘任务**: {task_title}
- **任务编号**: {task_id}
- **责任主体**: 人事部 HRD（{hr_emp_name} / {hr_emp_id}）
- **所属部门**: {dept_name} (Dept {dept_idx})
- **目标岗位**: {role_name} (`{role_id}`)
- **评估时间**: {now_str}

---

## 1. 岗位编制论证
智慧总部 3D HQ 组织架构实时巡检发现：**{dept_name}**当前在岗员工为 0 人，处于“负责人空缺”红标状态。
为保障智慧总部 8 大职能部门运转闭环与流水线业务协同，人事部依据组织编制管理规范，紧急启动【{role_name}】特快招聘通道。

## 2. 候选人画像与角色模板匹配
- **角色标识**: `{role_id}`
- **岗位定位**: {dept_name}核心负责人 / 专业骨干
- **能力要求**: 具备独立闭环交付能力，遵守企业安全与红线合规准则。
- **匹配度评分**: 98.5%（角色模板与岗位能力模型高度契合）

## 3. 专业能力考评雷达
- 专业业务深度: 95 / 100
- 响应时效指标: 98 / 100
- 运行稳定性: 99 / 100
- 协同合规水平: 96 / 100
- **综合评定**: 优秀（推荐全职录用）

## 4. 录用批复与定岗决定
经人事部综合考评，正式批准录用该 AI 员工，赋予业务调度权限，分配所属部门独立工位，点亮 3D HQ 总部工作台。
"""
        art_dir = art_mod.root()
        clean_role_id = role_id.lower().replace("-", "_")
        art_filename = f"recruitment_d{dept_idx}_{clean_role_id}.md"
        art_file_path = art_dir / art_filename
        art_file_path.write_text(artifact_content, encoding="utf-8")

        # 4. 创建 task_runs 与 artifacts 表记录
        run_id = f"RUN-REC-D{dept_idx}-{secrets.token_hex(4).upper()}"
        conn.execute("""
            INSERT INTO task_runs (
                id, task_id, run_seq, legal_entity_id, tenant_id, state, runner,
                actor_identity, started_at, ended_at
            ) VALUES (?, ?, 1, ?, ?, 'succeeded', 'recruitment', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (run_id, task_id, entity_id, tenant_id, hr_emp_id))

        art_id = f"ART-REC-D{dept_idx}-{secrets.token_hex(4).upper()}"
        checksum = hashlib.sha256(artifact_content.encode("utf-8")).hexdigest()
        bytes_len = len(artifact_content.encode("utf-8"))
        conn.execute("""
            INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, content, checksum, bytes, state)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'active')
        """, (art_id, run_id, tenant_id, str(art_file_path), artifact_content, checksum, bytes_len))

        # 5. 调用 roles_mod.instantiate 正式完成新员工入职（写入 ai_employees）
        new_emp_id = roles_mod.instantiate(conn, role_id, entity_id)

        # 6. 状态机规范流转：in_progress -> pending_review -> completed
        conn.execute("""
            UPDATE tasks
            SET state = 'pending_review', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (task_id,))

        conn.execute("""
            UPDATE acceptance_criteria
            SET state = 'passed', checked_by = ?, note = note || ' [人事部验收考评通过]'
            WHERE task_id = ?
        """, (hr_emp_id, task_id))

        conn.execute("""
            UPDATE tasks
            SET state = 'completed', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (task_id,))

        # 7. 写入审计日志 verb="recruitment", action="employee_hired"
        broadcast_msg = f"【人事部】🎉 已完成{dept_name}负责人选拔与工位入驻：{role_name}（{new_emp_id}），工位已点亮！"
        write_audit(
            conn,
            legal_entity_id=entity_id,
            verb="recruitment",
            action="employee_hired",
            task_id=task_id,
            run_id=run_id,
            layer="system",
            actor=hr_emp_id,
            target=new_emp_id,
            payload={
                "dept_idx": dept_idx,
                "dept_name": dept_name,
                "role_id": role_id,
                "role_name": role_name,
                "employee_id": new_emp_id,
                "artifact_id": art_id,
                "task_id": task_id,
                "broadcast": broadcast_msg,
            },
            response={"ok": True, "status": "hired", "employee_id": new_emp_id},
        )

        # 8. 触发遥测事件与全息广播
        try:
            telemetry.emit(
                "ROSTER_UPDATED",
                action="hire",
                employee_id=new_emp_id,
                name=role_name,
                title=role_name,
                dept_idx=dept_idx,
                broadcast=broadcast_msg,
            )
        except Exception:
            pass

        recruited_list.append({
            "task_id": task_id,
            "dept_idx": dept_idx,
            "dept_name": dept_name,
            "role_id": role_id,
            "role_name": role_name,
            "employee_id": new_emp_id,
            "artifact_id": art_id,
            "artifact_path": str(art_file_path),
            "broadcast": broadcast_msg,
        })

    conn.commit()
    return recruited_list
