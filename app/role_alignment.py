"""Role guidance injected into agent prompts; authorization remains in RBAC.

Prompt guidance is advisory and cannot enforce a security boundary.
Tags come from the role template, falling back to the position title.
"""
from __future__ import annotations

import sqlite3

from .audit import write_audit

TITLE_TAGS = [
    ("audit", ("审计", "合规", "风控")),
    ("legal", ("法务", "律师", "合同")),
    ("qa", ("测试", "QA", "质量")),
    ("architect", ("架构",)),
    ("design", ("设计", "视觉", "UI", "交互")),
    ("content", ("文案", "内容", "撰稿", "编辑", "品牌")),
    ("product", ("产品", "项目", "PM", "调度")),
    ("finance", ("财务", "会计", "预算")),
    ("ops", ("运维", "SRE", "发布")),
    ("data", ("数据", "分析", "量化")),
    ("dev", ("开发", "前端", "后端", "工程师", "程序", "Python")),
]

BOUNDARIES = {
    "dev": ("你是开发。职责边界：只负责写代码、修 Bug、给出可运行实现。"
            "遇到预算、排期、范围变更、发布决策，必须呼叫 PM 或 CEO；你无权批准预算，"
            "也不得自行承诺上线日期。不要替文案定对外口径。"),
    "frontend": ("你是前端开发。职责边界：只负责页面、交互与可访问性；不得擅自改后端接口或数据口径。"
                 "预算、排期、发布必须呼叫 PM/CEO。"),
    "backend": ("你是后端开发。职责边界：只负责接口、数据与错误处理；不得替 PM 承诺交付日期，"
                "不得绕过审计改权限。预算问题上报 PM/CEO。"),
    "qa": ("你是测试。职责边界：只对质量负责；每个结论必须附复现步骤或证据；"
           "不得替开发改代码，也不得为了让任务通过而放宽判据。发现红线违规必须直接升级。"),
    "audit": ("你是审计/合规。底线是合规与留痕；每次研讨必须至少找出一处具体风险，否则视为失职。"
              "对触碰公司红线的方案有权一票否决，禁止‘先上线再补手续’。"),
    "legal": ("你是法务。底线是合同与广告合规；每次评审至少指出一处法律风险。"
              "涉及绝对化用语、客户数据、未上线承诺时有权一票否决。"),
    "architect": ("你是架构师。职责边界：负责边界、依赖、回滚与技术风险；可以否决不可接受的方案，"
                  "但不得替 PM 排期或替开发写业务代码。"),
    "content": ("你是文案。职责边界：只基于已确认事实写作；不得使用绝对化用语，不得承诺未上线功能，"
                "价格与效果必须注明‘以正式合同为准’。遇到事实缺口先问 PM，不要编。"),
    "product": ("你是 PM/产品。职责边界：负责拆解、协调与验收口径；不得替执行角色写代码或文案，"
                "也不得越过审计/法务强行推进被否决的方案。"),
    "finance": ("你是财务。职责边界：只做预算、成本与回款判断；无权批准业务范围或技术方案，"
                "发现超预算必须升级给 CEO。"),
    "ops": ("你是运维/SRE。职责边界：只负责发布、回滚、监控与稳定性；不得绕过审批改生产权限。"),
    "data": ("你是数据/分析。职责边界：先定义口径再给结论，必须注明来源与时间；"
             "不得用未经验证的数据替业务做承诺。"),
}


def tags_for(conn: sqlite3.Connection, agent_id: str | None) -> list[str]:
    if not agent_id:
        return []
    try:
        row = conn.execute(
            """SELECT e.name, p.title, r.tag FROM ai_employees e
               LEFT JOIN positions p ON p.id=e.position_id
               LEFT JOIN role_templates r ON r.id=e.role_id
               WHERE e.id=?""", (agent_id,)).fetchone()
    except sqlite3.OperationalError:
        return []
    if row is None:
        return []
    tags = []
    if row["tag"]:
        tags.append(row["tag"])
    text = f"{row['title'] or ''} {row['name'] or ''}"
    for specific, words in (("frontend", ("前端",)), ("backend", ("后端", "服务端"))):
        if any(w in text for w in words) and specific not in tags:
            tags.append(specific)
    for tag, words in TITLE_TAGS:
        if tag not in tags and any(w.lower() in text.lower() for w in words):
            tags.append(tag)
    # “测试开发/前端开发/后端架构师”不是通用 dev：避免把开发职责边界叠给审计/测试。
    if any(t in tags for t in ("frontend", "backend", "qa", "ops", "pm", "product",
                               "content", "audit", "legal", "architect", "marketing",
                               "finance", "hr")):
        tags = [t for t in tags if t != "dev"]
    return tags


def boundary_for(tags: list[str]) -> str:
    order = ["audit", "legal", "qa", "architect", "product", "finance", "ops",
             "content", "frontend", "backend", "data", "dev"]
    picked = []
    for tag in order:
        if tag in tags and tag in BOUNDARIES:
            picked.append(BOUNDARIES[tag])
    if not picked:
        picked = ["你是数字集团的一名专业员工。只做岗位内的事；预算、权限、发布和对外承诺，"
                  "必须升级给 PM/CEO，不得自行决定。"]
    return "\n".join(picked)


def inject(conn: sqlite3.Connection, prompt: str, agent_id: str | None,
           *, task_id: str | None = None, run_id: str | None = None) -> str:
    tags = tags_for(conn, agent_id)
    text = (prompt or "") + "\n\n【职责边界声明】\n" + boundary_for(tags)
    task = (conn.execute("SELECT legal_entity_id FROM tasks WHERE id=?",
                         (task_id,)).fetchone() if task_id else None)
    employee = (conn.execute("SELECT legal_entity_id FROM ai_employees WHERE id=?",
                             (agent_id,)).fetchone() if agent_id else None)
    entity = (task or employee or {"legal_entity_id": "LE-GROUP-HQ"})["legal_entity_id"]
    try:
        write_audit(conn, legal_entity_id=entity, verb="alignment",
                    task_id=task_id, run_id=run_id, layer="system",
                    action="role_alignment_injected", target=agent_id or "unknown",
                    payload={"tags": tags, "chars": len(text)},
                    response={"ok": True})
        conn.commit()
    except Exception:                        # noqa: BLE001 - audit must not block run
        pass
    return text
