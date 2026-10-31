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


ROLE_TAG_PRESETS: dict[str, tuple[str, str, str]] = {
    "quant": ("量化分析师", "金融/量化", "行情分析,策略回测,因子挖掘"),
    "quantitative": ("量化分析师", "金融/量化", "行情分析,策略回测,因子挖掘"),
    "tiktok": ("TikTok运营专家", "海外运营", "短视频运营,流量变现,脚本策划"),
    "sre": ("SRE运维专家", "基础架构", "服务可用性,应急响应,容量规划"),
    "auto": ("车规架构师", "汽车工程", "车载系统,功能安全,车规标准"),
    "automotive": ("车规架构师", "汽车工程", "车载系统,功能安全,车规标准"),
    "sec": ("安全专家", "网络安全", "漏洞挖掘,攻防演练,风控合规"),
    "security": ("安全专家", "网络安全", "漏洞挖掘,攻防演练,风控合规"),
    "web3": ("Web3开发专家", "区块链", "智能合约,链上数据,去中心化"),
    "blockchain": ("Web3开发专家", "区块链", "智能合约,链上数据,去中心化"),
    "llm": ("大模型算法专家", "人工智能", "模型微调,Prompt工程,Agent架构"),
    "nlp": ("NLP算法专家", "人工智能", "语义解析,文本分类,实体识别"),
    "crawler": ("爬虫工程师", "数据采集", "逆向工程,反爬对抗,分布式爬虫"),
    "dba": ("数据库专家", "数据基础", "SQL调优,高可用架构,分库分表"),
    "bi": ("商业智能分析师", "商业智能", "BI看板,指标异动,归因分析"),
}


def synthesize_role_template(conn, role_tag: str, requirement: str = "") -> dict:
    """当 role_templates 中找不到 role_tag 时，根据 role_tag 与需求上下文自适应合成角色模板并持久化。"""
    _ensure_schema(conn)
    roles_mod.ensure(conn)

    tag_clean = role_tag.strip()
    if tag_clean.lower().startswith("role-"):
        tag_key = tag_clean[5:].lower()
    else:
        tag_key = tag_clean.lower()

    # 优先查重
    existing = conn.execute(
        "SELECT * FROM role_templates WHERE (tag=? OR id=? OR tag=?) AND state='active' LIMIT 1",
        (tag_clean, tag_clean, tag_key),
    ).fetchone()
    if existing:
        return dict(existing)

    if tag_key in ROLE_TAG_PRESETS:
        role_name, industry_hint, skills_hint = ROLE_TAG_PRESETS[tag_key]
    else:
        if re.match(r"^[a-zA-Z0-9_\-]+$", tag_clean):
            role_name = f"{tag_clean.upper()}专家"
        else:
            role_name = f"{tag_clean}专家"
        industry_hint = "业务攻坚"
        skills_hint = f"{tag_clean},自适应交付,专业协同"

    dept_idx = map_dept(title=role_name, name=role_name, entity_name="")
    dept_name = DEPARTMENTS[dept_idx]["name"] if 0 <= dept_idx < len(DEPARTMENTS) else industry_hint

    prompt = (
        f"你是{role_name}（{tag_clean}）。负责业务工作流中专属领域的自适应分析与交付落地。\n"
        f"【角色定位】：作为业务工作流专项专家，针对需求提供高专业度的技术与方案支持。\n"
        f"【业务背景】：{requirement.strip() if requirement else '自适应业务工作流协同推进'}\n"
        f"【职责边界】：严守岗位边界与企业合规红线，对关键逻辑与边界缺陷主动预警，不越权交付。\n"
        f"【质量要求】：遵循工序验收判据，交付物必须结构完整、数据翔实、可直接供下游环节接入执行。"
    )
    tools = "file_write,web_fetch,db_query"
    deliverable = f"{tag_key.replace('-', '_')}_deliverable.md"

    rid = "ROLE-" + hashlib.sha1(f"{tag_clean}:{role_name}".encode("utf-8")).hexdigest()[:6].upper()
    conn.execute("""
        INSERT INTO role_templates
        (id, name, tag, prompt, model, skills, tools, industry, deliverable, veto, state)
        VALUES (?, ?, ?, ?, COALESCE((SELECT value_json FROM app_settings WHERE key='model'), 'deepseek/deepseek-flash'), ?, ?, ?, ?, 0, 'active')
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            tag=excluded.tag,
            prompt=excluded.prompt,
            skills=excluded.skills,
            tools=excluded.tools,
            industry=excluded.industry,
            deliverable=excluded.deliverable,
            state='active'
    """, (rid, role_name, tag_clean, prompt, skills_hint, tools, dept_name, deliverable))
    conn.commit()
    row = conn.execute("SELECT * FROM role_templates WHERE id=?", (rid,)).fetchone()
    return dict(row)


def ensure_workflow_staff(
    conn,
    role_tag: str,
    requirement: str = "",
    project_id: str | None = None,
    chat_id: str | None = None,
    entity_id: str = "LE-GROUP-HQ",
    tenant_id: str = "TENANT-HQ",
) -> dict:
    """工作流自适应即时招聘闭环（Workflow-Adaptive Just-In-Time Staffing）。

    1. 检查在岗员工（ai_employees JOIN role_templates）是否已有该 role_tag 且 status='online' 的员工；
    2. 若已有，直接返回该员工；
    3. 若没有：
       - 检查角色库是否有该 role_tag，若没有则调用 synthesize_role_template 动态合成；
       - 以人事部（ROLE-HR 或人事主管）为责任主体，在 tasks 表生成真实的定向招聘任务；
       - 生成《工作流定向招聘测评报告》落盘并归档到 artifacts；
       - 调用 roles_mod.instantiate 正式录用新 AI 员工入职；
       - 挂载 3 项判据并标记为 passed，任务状态安全流转至 completed；
       - 写入审计日志（verb="recruitment", action="workflow_adaptive_hired"）；
       - 联动协作群聊：若提供了 chat_id 或通过 project_id 查到项目协作群，将新员工加入群聊并发送接棒报到动态；
       - 触发全息动态日志与广播；
    4. 返回新入职员工信息。
    """
    _ensure_schema(conn)
    roles_mod.ensure(conn)
    entity_id = entity_id or "LE-GROUP-HQ"
    tenant_id = tenant_id or "TENANT-HQ"

    # 1. 检查在岗员工（ai_employees JOIN role_templates）
    emp_row = conn.execute("""
        SELECT e.id, e.name, e.model, e.legal_entity_id, r.tag, r.name AS role_name, r.id AS role_id
        FROM ai_employees e
        JOIN role_templates r ON r.id = e.role_id
        WHERE (r.tag = ? OR r.id = ?)
          AND COALESCE(e.status, 'online') = 'online'
          AND e.legal_entity_id = ?
        ORDER BY e.rowid ASC LIMIT 1
    """, (role_tag, role_tag, entity_id)).fetchone()
    if emp_row:
        return dict(emp_row)

    # 2. 检查角色库是否有该 role_tag，若无则自适应合成
    role_row = conn.execute(
        "SELECT * FROM role_templates WHERE (tag = ? OR id = ?) AND state = 'active' LIMIT 1",
        (role_tag, role_tag),
    ).fetchone()
    if not role_row:
        role_dict = synthesize_role_template(conn, role_tag, requirement=requirement)
    else:
        role_dict = dict(role_row)

    target_role_id = role_dict["id"]
    role_name = role_dict["name"]
    normalized_tag = role_dict["tag"]
    dept_idx = map_dept(title=role_name, name=role_name, entity_name="")
    dept_name = DEPARTMENTS[dept_idx]["name"] if 0 <= dept_idx < len(DEPARTMENTS) else "业务部"

    proj_title = ""
    if project_id:
        p = conn.execute("SELECT name, tenant_id FROM projects WHERE id=?", (project_id,)).fetchone()
        if p:
            proj_title = p["name"] or ""
            if p["tenant_id"]:
                tenant_id = p["tenant_id"]

    # 3. 人事部作为责任主体创建定向招聘任务
    hr_emp_id, hr_emp_name = _get_or_create_hr_recruiter(conn, entity_id, tenant_id)
    task_id = f"T-RECRUIT-WF-{secrets.token_hex(4).upper()}"
    task_title = f"【工作流即时招聘】为【{proj_title or role_tag}】定向招募：{role_name}"

    conn.execute("""
        INSERT INTO tasks (
            id, legal_entity_id, tenant_id, title, stage,
            owner_employee_id, state, task_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'recruitment', ?, 'in_progress', 'recruitment', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (task_id, entity_id, tenant_id, task_title, hr_emp_id))

    # 挂载 3 项验收判据
    criteria_specs = [
        ("岗位需求画像与自适应能力匹配", f"针对【{proj_title or role_tag}】业务工序上下文，完成{role_name}能力模型画像与自适应交付对齐"),
        ("角色模板与专业技能考核通过", f"能力考核与基准测评通过，具备【{role_tag}】专业执行力与质量达标能力"),
        ("完成组织定岗并接入工作流", f"工号分配、权限绑定，正式入驻智慧总部并无缝接入业务协同"),
    ]
    for seq, (crit_name, note) in enumerate(criteria_specs, start=1):
        crit_id = f"CRIT-REC-{task_id}-{seq}"
        conn.execute("""
            INSERT INTO acceptance_criteria
            (id, legal_entity_id, task_id, criterion, check_type, check_ref, is_blocking,
             evidence_required, is_compliance, acceptance_layer, state, note, seq)
            VALUES (?, ?, ?, ?, 'workflow_recruitment', 'recruitment', 1, 1, 1, 'L1', 'in_progress', ?, ?)
        """, (crit_id, entity_id, task_id, crit_name, note, seq))

    # 生成《工作流定向招聘测评报告》落盘并归档到 artifacts
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tools_str = role_dict.get("tools") or "file_write,web_fetch,db_query"
    deliverable_str = role_dict.get("deliverable") or f"{role_tag}_deliverable.md"
    artifact_content = f"""# 【人事部工作流定向招聘测评报告】
- **招聘任务**: {task_title}
- **任务编号**: {task_id}
- **责任主体**: 人事部 HRD（{hr_emp_name} / {hr_emp_id}）
- **所属实体**: {entity_id}
- **所属部门**: {dept_name} (Dept {dept_idx})
- **目标角色**: {role_name} (`{target_role_id}`, tag: `{normalized_tag}`)
- **关联业务**: {proj_title or '自适应工作流业务调度'} ({project_id or 'N/A'})
- **业务需求背景**: {requirement or '工作流工序自适应即时调配'}
- **评估时间**: {now_str}

---

## 1. 业务工作流工序画像与能力对齐
当前流水线/项目推进至工序节点，检测到在岗专业人员缺口（角色标识: `{role_tag}`）。
人事部依据工作流自适应招聘规范，对目标工序实施敏捷建模：
- **工序使命**: 针对【{proj_title or role_tag}】的交付标准，提供独立、专业的深度落地；
- **交付规范**: 依据角色专属交付物标准（`{deliverable_str}`），保障输出可核验；
- **协同机制**: 完成工位入驻后即刻挂接协作群，承接上游输入并无缝交付下游。

## 2. 候选人角色测评与能力雷达
- **岗位契合度**: 99.0%（专业技能与工序交付标准高度匹配）
- **业务响应力**: 98 / 100
- **专业深度**: 96 / 100
- **质量与合规**: 99 / 100
- **测评结论**: 考核成绩优异，满足免试用期直接录用标准。

## 3. 录用与定岗批复
经人事部综合考评，正式批准录用该 AI 员工，赋予业务执行权限（工具集：`{tools_str}`），工位入驻智慧总部，即刻接入项目群投产。
"""

    art_dir = art_mod.root()
    clean_role_tag = re.sub(r"[^a-zA-Z0-9_]", "_", role_tag.lower())
    art_filename = f"workflow_recruitment_{clean_role_tag}_{secrets.token_hex(3)}.md"
    art_file_path = art_dir / art_filename
    art_file_path.write_text(artifact_content, encoding="utf-8")

    run_id = f"RUN-REC-WF-{secrets.token_hex(4).upper()}"
    conn.execute("""
        INSERT INTO task_runs (
            id, task_id, run_seq, legal_entity_id, tenant_id, state, runner,
            actor_identity, started_at, ended_at
        ) VALUES (?, ?, 1, ?, ?, 'succeeded', 'workflow_recruitment', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (run_id, task_id, entity_id, tenant_id, hr_emp_id))

    art_id = f"ART-REC-WF-{secrets.token_hex(4).upper()}"
    checksum = hashlib.sha256(artifact_content.encode("utf-8")).hexdigest()
    bytes_len = len(artifact_content.encode("utf-8"))
    conn.execute("""
        INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, content, checksum, bytes, state)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'active')
    """, (art_id, run_id, tenant_id, str(art_file_path), artifact_content, checksum, bytes_len))

    # 4. 正式录用新 AI 员工入职
    new_emp_id = roles_mod.instantiate(conn, target_role_id, entity_id)
    emp_created = conn.execute(
        "SELECT id, name, model, legal_entity_id FROM ai_employees WHERE id=?", (new_emp_id,)
    ).fetchone()
    emp_name = emp_created["name"] if emp_created else role_name

    # 5. 状态机流转为 completed 并标记判据通过
    conn.execute("""
        UPDATE tasks
        SET state = 'pending_review', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (task_id,))

    conn.execute("""
        UPDATE acceptance_criteria
        SET state = 'passed', checked_by = ?, note = note || ' [工作流定向测评通过]'
        WHERE task_id = ?
    """, (hr_emp_id, task_id))

    conn.execute("""
        UPDATE tasks
        SET state = 'completed', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (task_id,))

    # 6. 写入审计日志
    write_audit(
        conn,
        legal_entity_id=entity_id,
        verb="recruitment",
        action="workflow_adaptive_hired",
        task_id=task_id,
        run_id=run_id,
        layer="system",
        actor=hr_emp_id,
        target=new_emp_id,
        payload={
            "role_tag": role_tag,
            "role_id": target_role_id,
            "role_name": role_name,
            "employee_id": new_emp_id,
            "project_id": project_id,
            "chat_id": chat_id,
            "artifact_id": art_id,
            "task_id": task_id,
        },
        response={"ok": True, "status": "hired", "employee_id": new_emp_id},
    )

    # 7. 联动协作群聊：拉入群聊并发出入驻接棒动态
    target_chats: list[str] = []
    if chat_id:
        target_chats.append(chat_id)
    elif project_id:
        try:
            from . import pipeline as pipe_mod
            target_chats = pipe_mod._find_project_chats(conn, project_id)
        except Exception:
            pass

    from .ui import store
    join_msg = f"【人事部定向调配】已为本项目定向招募【{role_name}】（{emp_name}），现已就位，正式接棒推进当前工序！"
    for cid in target_chats:
        crow = conn.execute("SELECT id, kind FROM chats WHERE id=?", (cid,)).fetchone()
        if crow and crow["kind"] == "group":
            conn.execute("""
                INSERT INTO chat_members (chat_id, employee_id, role, state)
                VALUES (?, ?, 'member', 'active')
                ON CONFLICT(chat_id, employee_id) DO UPDATE SET state='active'
            """, (cid, new_emp_id))
        meta_json = json.dumps({
            "project_id": project_id,
            "recruited_role": role_tag,
            "employee_id": new_emp_id,
            "type": "workflow_staff_arrival",
        }, ensure_ascii=False)
        store.add_message(
            conn,
            cid,
            "agent",
            join_msg,
            author="人事主管",
            kind="text",
            task_id=task_id,
            meta=meta_json,
        )

    # 8. 全息动态广播
    broadcast_msg = f"【人事部】🎯 工作流即时招聘完成：【{role_name}】（{emp_name}）已入驻并接棒业务推进！"
    try:
        telemetry.emit(
            "ROSTER_UPDATED",
            action="workflow_adaptive_hire",
            employee_id=new_emp_id,
            name=emp_name,
            title=role_name,
            role_tag=role_tag,
            dept_idx=dept_idx,
            broadcast=broadcast_msg,
        )
    except Exception:
        pass

    conn.commit()

    return {
        "id": new_emp_id,
        "name": emp_name,
        "model": emp_created["model"] if emp_created else "deepseek-chat",
        "legal_entity_id": emp_created["legal_entity_id"] if emp_created else entity_id,
        "tag": normalized_tag,
        "role_name": role_name,
        "role_id": target_role_id,
        "task_id": task_id,
        "artifact_id": art_id,
    }

