"""组织经验（v2.4 / v5.2 降噪）：把高价值信号沉淀成可复用记忆。

三层记忆：
  1. 个人偏好   user_preferences（老板称呼/口味，原有）
  2. 项目记忆   memory_entries（scope = entity/pack/global，原有）
  3. 组织经验   org_experiences（本模块：自愈对比、决策否决、复盘）

v5.2 TASK-3.1 降噪策略（默认拒绝 vs 默认沉淀的翻转）：
  历史上每次任务 `rejected` 都会落一条 pending 经验，积压 452 条低质记录。
  v5.2 起自动沉淀改为**白名单**，只有三类高价值信号写入 `org_experiences(state='pending')`：
    a) 任务 rejected 后重跑通过 → `capture_self_healed`（失败原因与修复的差分）；
    b) 研讨会一票否决 / 决策包被否决 → `capture_consensus`（含 veto 时）/ `capture_decision_rejection`；
    c) 项目走完复盘阶段 → `capture_retro`（可由治理页读取时幂等补齐 `capture_retro_sweep`）。
  `capture_rejection` 保留能力但默认不落库：仅当调用方显式 opt_in，或 verdict 属于
  veto 类语义时才写。这样 `app/acceptance_run.py` 的既有调用点无需改动即可降噪。

注入点：`pipeline.run_project` 在立项时按需求标签检索经验，把命中条目追加到
requirement 后面（写进立项书/纪要，审计 action=experience_injected）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .audit import new_id, write_audit

DDL = """
CREATE TABLE IF NOT EXISTS org_experiences (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- rejection | consensus | decision
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT,
  source_type TEXT,
  source_id TEXT,
  project_id TEXT,
  task_id TEXT,
  legal_entity_id TEXT DEFAULT 'LE-GROUP-HQ',
  tenant_id TEXT NOT NULL DEFAULT 'TENANT-HQ',
  weight REAL DEFAULT 1.0,
  uses INTEGER DEFAULT 0,
  mandatory INTEGER NOT NULL DEFAULT 0,
  redline_terms TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  version INTEGER NOT NULL DEFAULT 1,
  reviewed_by TEXT,
  reviewed_at TIMESTAMP,
  review_note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exp_kind ON org_experiences(kind);
CREATE INDEX IF NOT EXISTS idx_exp_project ON org_experiences(project_id);
CREATE TABLE IF NOT EXISTS experience_reviews (
  id TEXT PRIMARY KEY,
  experience_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  legal_entity_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

KIND_LABEL = {"rejection": "驳回经验", "consensus": "共识结论", "decision": "决策否决"}

# v5.2 降噪：这些 verdict 属于「决策否决 / 闸门一票否决」语义，保留经验能力。
VETO_VERDICTS = {"veto", "vetoed", "veto_failed", "blocked", "denied", "cancelled",
                 "canceled", "aborted", "rejected_by_veto"}
# 高价值来源标记，写进 source_type，便于治理页按来源分组审计。
SOURCE_SELF_HEALED = "task_self_healed"
SOURCE_WORKSHOP_VETO = "workshop_veto"
SOURCE_DECISION_VETO = "decision"
SOURCE_RETRO = "project_retro"
SOURCE_REJECTION = "task"
SOURCE_TASK_VETO = "task_veto"


class ReviewConflict(RuntimeError):
    """A reviewer must reload content changed or decided since their page loaded."""


def ensure(conn) -> None:
    caller_transaction = conn.in_transaction
    statement = ""
    for line in DDL.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(org_experiences)")}
    additions = {
        "tenant_id": "TEXT NOT NULL DEFAULT 'TENANT-HQ'",
        "mandatory": "INTEGER NOT NULL DEFAULT 0",
        "redline_terms": "TEXT", "state": "TEXT NOT NULL DEFAULT 'pending'",
        "version": "INTEGER NOT NULL DEFAULT 1", "reviewed_by": "TEXT",
        "reviewed_at": "TIMESTAMP", "review_note": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE org_experiences ADD COLUMN {name} {ddl}")
    # Only the one-time migration may correct the default assigned to old rows.
    if "tenant_id" not in cols:
        conn.execute("""UPDATE org_experiences SET tenant_id=COALESCE(
            (SELECT tenant_id FROM legal_entities WHERE id=legal_entity_id),'TENANT-HQ')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_scope ON org_experiences"
                 "(tenant_id,legal_entity_id,state)")
    if not caller_transaction:
        conn.commit()


def _scope(conn, entity: str | None, tenant_id: str | None) -> tuple[str, str]:
    from . import tenancy
    entity = entity or "LE-GROUP-HQ"
    actual = tenancy.of_entity(conn, entity)
    if tenant_id is not None and tenant_id != actual:
        raise PermissionError("TenantMismatch")
    return entity, actual


def _reviewer(conn, actor: str | dict | None, entity: str, tenant_id: str) -> dict:
    from . import identity, tenancy
    actor_id = actor.get("id") if isinstance(actor, dict) else actor
    row = identity.get_identity(conn, actor_id or "")
    if row is None or row["role"] not in {"ceo", "system"}:
        raise PermissionError("ExperienceReviewerRequired")
    if tenancy.of_identity(conn, row["id"]) != tenant_id:
        raise PermissionError("TenantMismatch")
    if row["legal_entity_id"] != entity:
        raise PermissionError("CrossEntityForbidden")
    return dict(row)


def _history(conn, row, action: str, actor: str, note: str = "") -> None:
    import json
    conn.execute("""INSERT INTO experience_reviews
        (id,experience_id,tenant_id,legal_entity_id,version,action,actor,snapshot,note)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (new_id("ER"), row["id"], row["tenant_id"], row["legal_entity_id"],
         row["version"], action, actor, json.dumps(dict(row), ensure_ascii=False), note[:500]))


def _tags(text: str) -> list[str]:
    try:
        from .ui.roles import tags_of
        tags = list(tags_of(text or ""))
    except Exception:                     # noqa: BLE001
        tags = []
    # 补几个高频业务词，标签库太窄时也能命中
    for word in ("小程序", "记账", "财务", "报销", "审批", "计算器", "落地页", "文案",
                 "数据", "报表", "接口", "测试", "重构", "安全"):
        if word in (text or "") and word not in tags:
            tags.append(word)
    return tags[:8]


# ---------------------------------------------------------------- 标题注入套娃（v5.4.1）
# 召回的经验会被拼成「… 【组织经验（自动注入）】 - [决策否决] …」塞进下一轮 prompt，
# 而任务标题又是从这个 prompt 里截出来的。于是经验标题带着上一轮的注入尾巴，
# 下一轮再沉淀时又把这个尾巴抄进去 —— 生产库里已经长成这样：
#   驳回：开发最小可用版本…需求 失败的原因是什么  【组织经验（自动注入）】 - [决策否决] 任何 Agent
# 这里在唯一写入口 add() 上做剥离：标题截断到第一个注入标记之前，正文里把标记到
# 《…》闭合之间的那段挖掉（保留「未通过判据：…」这类真正有用的信息）。
INJECTION_MARKERS = (
    "【组织经验（自动注入）】",
    "【组织经验(自动注入)】",
    "【组织经验（自动注入)",          # 历史数据里存在被截断的半截标记
    "【项目上游已交付结论与上下文】",
    "【决策否决】",
)


def _first_marker(text: str) -> int:
    cut = -1
    for marker in INJECTION_MARKERS:
        i = text.find(marker)
        if i != -1 and (cut == -1 or i < cut):
            cut = i
    return cut


def strip_injected_title(title: str | None) -> str:
    """标题只保留注入标记之前的部分（标题本身在最前面，截断不丢信息）。"""
    s = (title or "").strip()
    cut = _first_marker(s)
    if cut != -1:
        s = s[:cut]
    return s.rstrip(" \t\r\n　-—·，,、；;")


def strip_injected_content(content: str | None) -> str:
    """正文里挖掉注入段：标记到同一段内的 《…》 闭合为止；没有闭合就截到行尾。"""
    s = content or ""
    while True:
        cut = _first_marker(s)
        if cut == -1:
            return s
        close = s.find("》", cut)
        nl = s.find("\n", cut)
        if close != -1 and (nl == -1 or close < nl):
            s = s[:cut] + s[close + 1:]        # 连同注入段一起丢掉，保留闭合符
        elif nl != -1:
            s = s[:cut] + s[nl:]
        else:
            s = s[:cut]
        s = s.replace("  》", "》").replace(" 》", "》")


def add(conn, *, kind: str, title: str, content: str, tags: list[str] | None = None,
        source_type: str = "", source_id: str = "", project_id: str | None = None,
        task_id: str | None = None, entity: str = "LE-GROUP-HQ",
        weight: float = 1.0, mandatory: bool = False,
        redline_terms: str | list[str] | None = None,
        tenant_id: str | None = None, actor: str | dict | None = None) -> dict:
    ensure(conn)
    # 唯一写入口：先剥离注入尾巴，再落库（不依赖各 caller 自觉）
    title = strip_injected_title(title)
    content = strip_injected_content(content)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    if mandatory:
        _reviewer(conn, actor, entity, tenant_id)
    existing = conn.execute(
        """SELECT * FROM org_experiences WHERE kind=? AND source_id=? AND title=?
           AND tenant_id=? AND legal_entity_id=? LIMIT 1""",
        (kind, source_id, title, tenant_id, entity)).fetchone()
    if existing is not None:
        return dict(existing)
    eid = new_id("EXP")
    tags = tags if tags is not None else _tags(title + " " + content)
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
    if isinstance(redline_terms, list):
        redline_terms = ",".join(redline_terms)
    conn.execute(
        """INSERT INTO org_experiences
             (id, kind, title, content, tags, source_type, source_id, project_id,
              task_id, legal_entity_id, weight, tenant_id, mandatory, redline_terms, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
        (eid, kind, title[:160], content[:4000], ",".join(tags), source_type, source_id,
         project_id, task_id, entity, weight, tenant_id, int(mandatory),
         (redline_terms or "").strip()[:2000]))
    write_audit(conn, legal_entity_id=entity, verb="memory_write", task_id=task_id,
                layer="system", action="experience_added", target=eid,
                payload={"kind": kind, "tags": tags, "source": source_id,
                         "title": title[:120]},
                response={"ok": True})
    conn.commit()
    row = dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (eid,)).fetchone())
    try:
        from . import telemetry
        telemetry.emit(
            "EXP_CRYSTAL_DROP",
            exp_id=eid,
            kind=kind,
            title=title,
            content=content,
            tags=tags,
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
            task_id=task_id,
            weight=weight,
        )
    except Exception:
        pass
    return row


def capture_self_healed(conn, task_id: str) -> dict | None:
    """经历过 rejected 且最终 completed 的自愈成功经验（高价值经验）。"""
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        return None
    existing = conn.execute(
        "SELECT 1 FROM org_experiences WHERE source_id=? AND title LIKE '自愈成功：%' LIMIT 1",
        (task_id,)).fetchone()
    if existing:
        return None
    content = (f"任务《{task['title']}》此前曾遭遇验收阻断，经过重试修正后成功通过全部确定性判据。"
               f"该任务探索出的交付格式与逻辑具备高价值复用性。")
    tenant_id = dict(task).get("tenant_id")
    return add(conn, kind="consensus", title=f"自愈成功：{task['title']}",
               content=content, tags=_tags(task["title"]),
               source_type="task", source_id=task_id, task_id=task_id,
               entity=task["legal_entity_id"], tenant_id=tenant_id, weight=1.5)


def capture_rejection(conn, task_id: str, *, verdict: str = "rejected",
                      opt_in: bool = False, note: str | None = None) -> dict | None:
    """驳回沉淀的降噪门（v5.2 TASK-3.1）。

    为什么门槛放在函数内部而不是调用点：`app/acceptance_run.py:259` 只传 task_id 与
    verdict，且该文件不在本任务的写入范围。把策略收敛在经验模块内部，既不动验收链路，
    又能让所有来源（验收、手工重试、外部集成）共享同一套降噪规则。

    落库条件（满足任一）：
      * `opt_in=True` —— 调用方显式判定为高价值驳回，要求沉淀；
      * verdict 属于 veto 类语义（`VETO_VERDICTS`）—— 决策/闸门否决是计划保留的来源 b；
    其余情况（含默认 ``verdict='rejected'``）直接返回 ``None``，不产生 pending 噪音。
    同时：已经自愈成功（存在「自愈成功：」经验）的任务不再写驳回记录，避免两条互相矛盾；
    同一任务同一来源只写一条（幂等）。
    """
    norm = str(verdict or "").strip().lower()
    veto_class = norm in VETO_VERDICTS
    if not opt_in and not veto_class:
        return None                    # 单次 rejected：只停留在 task_runs.error / 审计里
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        return None
    healed = conn.execute(
        "SELECT 1 FROM org_experiences WHERE source_id=? AND title LIKE '自愈成功：%' LIMIT 1",
        (task_id,)).fetchone()
    if healed:
        return None                    # 已被「自愈成功」覆盖，不写反向噪音
    kind = "decision" if veto_class else "rejection"
    existing = conn.execute(
        "SELECT 1 FROM org_experiences WHERE source_id=? AND kind=? LIMIT 1",
        (task_id, kind)).fetchone()
    if existing:
        return None
    failed = conn.execute(
        """SELECT criterion, note FROM acceptance_criteria
           WHERE task_id=? AND state='failed' ORDER BY seq""", (task_id,)).fetchall()
    reasons = "；".join(f"{f['criterion']}（{f['note'] or '未达标'}）" for f in failed)
    if note:
        reasons = f"{reasons}；补充：{note}" if reasons else f"补充：{note}"
    if not reasons:
        if not veto_class:
            return None                # 没有失败判据的普通驳回没有可复用信息
        reasons = "决策/闸门否决，无具体失败判据，按否决意见复核"
    label = "否决" if veto_class else "驳回"
    content = (f"任务《{task['title']}》被{label}（verdict={norm or 'rejected'}）。"
               f"未通过判据：{reasons}。下次同类任务先按这些判据自检，再提交。")
    tenant_id = dict(task).get("tenant_id")
    return add(conn, kind=kind, title=f"{label}：{task['title']}",
               content=content, tags=_tags(task["title"] + " " + reasons),
               source_type=SOURCE_TASK_VETO if veto_class else SOURCE_REJECTION,
               source_id=task_id, task_id=task_id,
               entity=task["legal_entity_id"], tenant_id=tenant_id,
               weight=1.3 if veto_class else 1.2)


def capture_consensus(conn, *, project_id: str, workshop_id: str, requirement: str,
                      statements: list[dict], minutes: str | None = None,
                      opt_in: bool = False) -> dict | None:
    """研讨会收尾沉淀（v5.2：只保留「一票否决」这一高价值来源 b）。

    普通「附条件通过」的共识不再自动落库（那正是 452 条 pending 噪音的一大来源）；
    只有审计/法务投出明确反对（与 pipeline 的 veto 判定同口径）时才写一条
    ``kind='decision'`` 的否决经验。调用方确实需要全文共识时传 ``opt_in=True``。
    """
    if not statements:
        return None
    vetoes = [s for s in statements
              if s.get("role") in {"audit", "legal"} and s.get("stance") == "反对"]
    if not vetoes and not opt_in:
        return None
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    name = project["name"] if project else project_id
    risks = "；".join(f"{s.get('title', s.get('role', ''))}：{str(s.get('risk', ''))[:90]}"
                     for s in statements if s.get("risk"))
    votes = "；".join(f"{s.get('title', s.get('role', ''))}：{s.get('stance', '同意')}"
                      for s in statements)
    tenant_id = dict(project).get("tenant_id") if project else None
    if vetoes:
        veto_note = "；".join(
            f"{s.get('title', s.get('role', ''))}：{s.get('question', '')}"
            f" / {s.get('risk', '')}" for s in vetoes)
        content = (f"项目《{name}》研讨会被一票否决。需求「{requirement[:160]}」。"
                   f"否决意见：{veto_note[:600]}。必须先按否决意见修订需求并重新评审，"
                   "禁止强行推进。")
        return add(conn, kind="decision", title=f"研讨会否决：{name}",
                   content=content, tags=_tags(requirement + " " + name + " 否决"),
                   source_type=SOURCE_WORKSHOP_VETO, source_id=workshop_id,
                   project_id=project_id,
                   entity=project["legal_entity_id"] if project else "LE-GROUP-HQ",
                   tenant_id=tenant_id, weight=1.3)
    content = (f"项目《{name}》研讨会共识：需求「{requirement[:160]}」。"
               f"风险：{risks[:500]}。表决：{votes[:300]}。")
    return add(conn, kind="consensus", title=f"共识：{name}",
               content=content, tags=_tags(requirement + " " + name),
               source_type="workshop", source_id=workshop_id, project_id=project_id,
               entity=project["legal_entity_id"] if project else "LE-GROUP-HQ",
               tenant_id=tenant_id, weight=1.0)


def capture_retro(conn, project_id: str) -> dict | None:
    """项目完成 retro（复盘阶段）后的高价值来源 c（v5.2 TASK-3.1）。

    必须真实走完 retro 阶段才会落库；幂等（按 project_id + 标题前缀去重）。
    复盘数字来自库内事实，不依赖模型自评 —— 与"模型自评通过"无关。
    """
    ensure(conn)
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        return None
    retro = conn.execute(
        "SELECT state FROM project_stages WHERE project_id=? AND key='retro'",
        (project_id,)).fetchone()
    if retro is None or retro["state"] != "done":
        return None
    existing = conn.execute(
        "SELECT 1 FROM org_experiences WHERE source_id=? AND title LIKE '复盘：%' LIMIT 1",
        (project_id,)).fetchone()
    if existing:
        return None
    entity = project["legal_entity_id"]
    tenant_id = dict(project).get("tenant_id")
    stages = conn.execute(
        "SELECT COUNT(*) n FROM project_stages WHERE project_id=? AND state='done'",
        (project_id,)).fetchone()["n"]
    tasks = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=?",
                         (project_id,)).fetchone()["n"]
    wbs = conn.execute(
        "SELECT SUM(CASE WHEN state IN ('done','completed') THEN 1 ELSE 0 END) d,"
        " COUNT(*) n FROM wbs_items WHERE project_id=?", (project_id,)).fetchone()
    tokens = conn.execute(
        "SELECT COALESCE(SUM(r.usage_tokens),0) t FROM task_runs r"
        " JOIN tasks t ON t.id=r.task_id WHERE t.project_id=?",
        (project_id,)).fetchone()["t"]
    failures = conn.execute(
        """SELECT c.criterion, c.note FROM acceptance_criteria c
           JOIN tasks t ON t.id=c.task_id
           WHERE t.project_id=? AND c.state='failed' ORDER BY c.rowid DESC LIMIT 3""",
        (project_id,)).fetchall()
    improvements = "；".join(f"{f['criterion']}（{f['note'] or '未达标'}）" for f in failures)
    content = (f"项目《{project['name']}》已完成复盘。需求「{(project['requirement'] or '')[:160]}」。"
               f"阶段完成 {stages} 个，WBS 完成 {int(wbs['d'] or 0)}/{int(wbs['n'] or 0)}，"
               f"任务 {tasks} 个，累计 tokens {int(tokens or 0)}。"
               f"改进项：{improvements or '复盘阶段闸门已通过，无遗留失败判据'}。"
               "同类项目立项时先复用本项目的阶段裁剪与判据口径。")
    return add(conn, kind="consensus", title=f"复盘：{project['name']}",
               content=content, tags=_tags((project["requirement"] or "") + " " +
                                           (project["name"] or "") + " 复盘"),
               source_type=SOURCE_RETRO, source_id=project_id, project_id=project_id,
               entity=entity, tenant_id=tenant_id, weight=1.1)


def capture_retro_sweep(conn, *, tenant_id: str | None = None,
                        entity: str | None = None, limit: int = 50) -> int:
    """幂等补齐「已复盘但尚未沉淀」的项目（治理页读取时调用）。

    为什么是懒补齐：复盘发生在 `pipeline.run_stage('retro')`，而 pipeline 不在
    TASK-3.1 的写入范围；把补齐挂在治理审核读取入口，既保证来源 c 一定可达，
    又不产生额外写放大（只有缺失时才 INSERT，且仍有租户/实体作用域）。
    """
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    rows = conn.execute(
        """SELECT p.id FROM projects p
           JOIN project_stages s ON s.project_id=p.id
           WHERE p.tenant_id=? AND p.legal_entity_id=?
             AND s.key='retro' AND s.state='done'
             AND NOT EXISTS (SELECT 1 FROM org_experiences e
                             WHERE e.source_id=p.id AND e.title LIKE '复盘：%')
           ORDER BY p.rowid DESC LIMIT ?""",
        (tenant_id, entity, min(int(limit), 200))).fetchall()
    created = 0
    for row in rows:
        try:
            if capture_retro(conn, row["id"]):
                created += 1
        except Exception:                  # noqa: BLE001 - 补齐失败不影响读取
            continue
    return created


def capture_decision_rejection(conn, decision_id: str, note: str | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM decision_packages WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        return None
    content = (f"决策包《{row['title']}》被否决。摘要：{row['summary'] or '-'}。"
               f"否决意见：{note or row['decision_note'] or '未填写'}。"
               "同类决策下次先补齐收益/风险/回滚证据。")
    tenant_id = dict(row).get("tenant_id") if row else None
    return add(conn, kind="decision", title=f"否决：{row['title']}",
               content=content, tags=_tags(row["title"] + " " + (row["summary"] or "")),
               source_type="decision", source_id=decision_id,
               project_id=None, task_id=row["task_id"],
               entity=row["legal_entity_id"], tenant_id=tenant_id, weight=1.3)


def _grams(text: str) -> list[str]:
    out = re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z0-9_]{1,20}", text or "")
    return list(dict.fromkeys(out))[:60]


def context_for(conn, text: str, *, entity: str | None = None, limit: int = 3,
                record: bool = True, tenant_id: str | None = None) -> dict:
    """按标签 + 词面重合度召回经验；返回可直接拼进 prompt 的文本与 ids。"""
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    tags = set(_tags(text))
    grams = _grams(text)
    rows = conn.execute(
        """SELECT * FROM org_experiences WHERE state='approved'
           AND tenant_id=? AND legal_entity_id=? AND (weight>0 OR mandatory=1)
           ORDER BY mandatory DESC, rowid DESC""", (tenant_id, entity)).fetchall()
    scored = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        if row["mandatory"]:
            scored.append((float("inf"), row))
            continue
        row_tags = set((row["tags"] or "").split(",")) - {""}
        score = 3.0 * len(tags & row_tags)
        hay = (row["title"] or "") + " " + (row["content"] or "")
        score += sum(1 for g in grams if g in hay) * 0.8
        try:
            age = (now - datetime.fromisoformat(str(row["created_at"]))).days
            score += max(0.0, 1.0 - age / 120.0) * 0.5
        except (ValueError, TypeError):
            pass
        score *= float(row["weight"] or 1.0)
        if score > 2.0:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    mandatory = [r for _, r in scored if r["mandatory"]]
    picked = mandatory + [r for _, r in scored if not r["mandatory"]][:max(0, limit)]
    if record and picked:
        for r in picked:
            conn.execute("UPDATE org_experiences SET uses = uses + 1 WHERE id=?", (r["id"],))
        write_audit(conn, legal_entity_id=entity or "LE-GROUP-HQ", verb="memory_read",
                    task_id=None, layer="system", action="experience_used",
                    target=",".join(r["id"] for r in picked),
                    payload={"query": (text or "")[:200],
                             "ids": [r["id"] for r in picked]},
                    response={"ok": True, "count": len(picked)})
        conn.commit()
    lines = []
    for r in picked:
        label = KIND_LABEL.get(r["kind"], r["kind"])
        snippet = (r["content"] or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        lines.append(f"- [{label}] {snippet}")
    return {"text": "\n".join(lines), "ids": [r["id"] for r in picked],
            "count": len(picked), "tags": sorted(tags)}


def _sweep_retros(conn, entity: str, tenant_id: str) -> None:
    """读入口的懒补齐：直接把「已复盘未沉淀」的项目补成 pending 经验。

    只在经验模块的读取入口调用（``list_all``/``stats``），不改变 pipeline 的执行路径；
    失败不影响读取，且 ``capture_retro_sweep`` 本身幂等。
    """
    try:
        capture_retro_sweep(conn, tenant_id=tenant_id, entity=entity)
    except Exception:                      # noqa: BLE001 - 补齐失败不影响读取
        pass


def list_all(conn, *, kind: str | None = None, state: str | None = None,
             limit: int = 100, tenant_id: str | None = None,
             entity: str | None = None) -> list[dict]:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    _sweep_retros(conn, entity, tenant_id)
    where, args = ["tenant_id=?", "legal_entity_id=?"], [tenant_id, entity]
    for col, value in (("kind", kind), ("state", state)):
        if value:
            where.append(f"{col}=?")
            args.append(value)
    return [dict(r) for r in conn.execute("SELECT * FROM org_experiences WHERE "
        + " AND ".join(where) + " ORDER BY rowid DESC LIMIT ?", args + [min(limit, 500)])]


def stats(conn, *, tenant_id: str | None = None, entity: str | None = None) -> dict:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    _sweep_retros(conn, entity, tenant_id)
    by_kind = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? GROUP BY kind", (tenant_id, entity))}
    by_state = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? GROUP BY state", (tenant_id, entity))}
    total = sum(by_kind.values())
    layers = {"preferences": 0, "project_memories": 0, "org_experiences": total}
    try:
        layers["preferences"] = conn.execute(
            "SELECT COUNT(*) FROM user_preferences p JOIN identities i ON i.id=p.identity_id"
            " WHERE i.tenant_id=? AND i.legal_entity_id=?", (tenant_id, entity)).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    try:
        layers["project_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memory_entries m JOIN ai_employees e ON e.id=m.employee_id"
            " WHERE e.tenant_id=? AND e.legal_entity_id=?", (tenant_id, entity)).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    mandatory = conn.execute("SELECT COUNT(*) FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? AND mandatory=1 AND state='approved'",
        (tenant_id, entity)).fetchone()[0]
    return {"total": total, "by_kind": by_kind, "by_state": by_state,
            "mandatory": mandatory, "layers": layers}


def red_lines(conn, *, tenant_id: str | None = None, entity: str | None = None) -> list[dict]:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    return [dict(r) for r in conn.execute("""SELECT * FROM org_experiences
        WHERE tenant_id=? AND legal_entity_id=? AND state='approved' AND mandatory=1
        ORDER BY rowid""", (tenant_id, entity))]


def review(conn, exp_id: str, action: str, *, reviewer: str, note: str | None = None,
           tenant_id: str | None = None, entity: str | None = None,
           expected_version: int | None = None, require_pending: bool = False) -> dict:
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    actor = _reviewer(conn, reviewer, entity, tenant_id)
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve|reject")
    row = conn.execute("SELECT * FROM org_experiences WHERE id=? AND tenant_id=?"
                       " AND legal_entity_id=?", (exp_id, tenant_id, entity)).fetchone()
    if row is None:
        raise KeyError(exp_id)
    if expected_version is not None and row["version"] != expected_version:
        raise ReviewConflict("ExperienceVersionChanged")
    if require_pending and row["state"] != "pending":
        raise ReviewConflict("ExperienceAlreadyReviewed")
    expected_version = row["version"] if expected_version is None else expected_version
    changed = conn.execute("""UPDATE org_experiences SET state=?,reviewed_by=?,
        reviewed_at=CURRENT_TIMESTAMP,review_note=?
        WHERE id=? AND tenant_id=? AND legal_entity_id=? AND version=?
          AND (?=0 OR state='pending')""",
        ("approved" if action == "approve" else "rejected", actor["id"],
         (note or "")[:500], exp_id, tenant_id, entity, expected_version, int(require_pending)))
    if changed.rowcount != 1:
        raise ReviewConflict("ExperienceVersionChanged")
    _history(conn, row, action, actor["id"], note or "")
    write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=actor["id"],
                action="experience_reviewed", target=exp_id,
                payload={"action": action, "version": row["version"]}, response={"ok": True})
    conn.commit()
    return dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (exp_id,)).fetchone())


def batch_review(conn, exp_ids: list[str], action: str, *, reviewer: str | dict,
                 note: str | None = None, tenant_id: str | None = None,
                 entity: str | None = None,
                 require_pending: bool = True) -> dict:
    """批量审核（v5.2 TASK-3.1）：单事务内逐条改状态并写 ``ai_audit_logs``。

    与单条 `review()` 的差别：
      * 一条 BEGIN IMMEDIATE 包住整批，避免半批成功、半批回滚的中间态；
      * 每条都写 ``action='experience_reviewed'`` 审计（payload.batch=True 标明批次）；
      * 越权/不存在的 id 只记入 ``skipped``，不抛异常（批量操作不能因一条坏数据整批失败），
        也不会跨租户写入 —— 作用域条件写进 SQL，不靠调用方过滤。
    """
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    actor = _reviewer(conn, reviewer, entity, tenant_id)
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve|reject")
    ids = list(dict.fromkeys(str(i) for i in (exp_ids or []) if i))
    if not ids:
        raise ValueError("experience_ids required")
    processed: list[str] = []
    skipped: list[dict] = []
    for exp_id in ids:
        row = conn.execute(
            "SELECT * FROM org_experiences WHERE id=? AND tenant_id=? AND legal_entity_id=?",
            (exp_id, tenant_id, entity)).fetchone()
        if row is None:
            skipped.append({"id": exp_id, "code": "NotFound"})
            continue
        if require_pending and row["state"] != "pending":
            skipped.append({"id": exp_id, "code": "ExperienceAlreadyReviewed"})
            continue
        changed = conn.execute(
            """UPDATE org_experiences SET state=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                 review_note=? WHERE id=? AND tenant_id=? AND legal_entity_id=?
                 AND (?=0 OR state='pending')""",
            ("approved" if action == "approve" else "rejected", actor["id"],
             (note or "")[:500], exp_id, tenant_id, entity, int(require_pending)))
        if changed.rowcount != 1:
            skipped.append({"id": exp_id, "code": "ExperienceVersionChanged"})
            continue
        _history(conn, row, action, actor["id"], note or "")
        write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=actor["id"],
                    action="experience_reviewed", target=exp_id,
                    payload={"action": action, "version": row["version"],
                             "batch": True, "note": (note or "")[:200]},
                    response={"ok": True})
        processed.append(exp_id)
    conn.commit()
    return {"action": action, "processed": len(processed), "processed_ids": processed,
            "skipped": skipped}


def groups(conn, *, tenant_id: str | None = None, entity: str | None = None,
           limit: int = 500) -> dict:
    """治理页分组视图：按 kind / 标签 / 来源统计待审核经验（只读，不做写操作）。"""
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    rows = conn.execute(
        """SELECT kind, tags, source_type, state FROM org_experiences
           WHERE tenant_id=? AND legal_entity_id=?
           ORDER BY rowid DESC LIMIT ?""",
        (tenant_id, entity, min(int(limit), 500))).fetchall()
    by_kind: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_state: dict[str, int] = {}
    pending = 0
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        source = row["source_type"] or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
        if row["state"] == "pending":
            pending += 1
            for tag in (row["tags"] or "").split(","):
                tag = tag.strip()
                if tag:
                    by_tag[tag] = by_tag.get(tag, 0) + 1
    return {"kind": by_kind, "tag": by_tag, "source": by_source,
            "state": by_state, "pending": pending, "sampled": len(rows)}


def delete(conn, exp_id: str, *, actor: str | dict,
           tenant_id: str | None = None, entity: str | None = None) -> dict:
    """彻底删除一条组织经验，连同它的审阅历史。

    为什么是硬删除而不是打标记：经验库的语义是「当前生效的规则」。
    留一条看不见的僵尸记录既占检索空间，又会让 redline 判定产生歧义；
    删除动作本身写进 ai_audit_logs，追责链仍然完整。
    """
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    who = _reviewer(conn, actor, entity, tenant_id)
    row = conn.execute("SELECT * FROM org_experiences WHERE id=? AND tenant_id=?"
                       " AND legal_entity_id=?", (exp_id, tenant_id, entity)).fetchone()
    if row is None:
        raise KeyError(exp_id)
    reviews = conn.execute("SELECT COUNT(*) FROM experience_reviews WHERE experience_id=?",
                           (exp_id,)).fetchone()[0]
    conn.execute("DELETE FROM experience_reviews WHERE experience_id=?", (exp_id,))
    conn.execute("DELETE FROM org_experiences WHERE id=?", (exp_id,))
    write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=who["id"],
                action="experience_deleted", target=exp_id,
                payload={"title": row["title"], "kind": row["kind"], "state": row["state"],
                         "version": row["version"], "reviews": reviews},
                response={"deleted": True})
    conn.commit()
    return {"id": exp_id, "title": row["title"], "reviews_removed": reviews}


def edit(conn, exp_id: str, changes: dict, *, actor: str,
         tenant_id: str | None = None, entity: str | None = None) -> dict:
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    who = _reviewer(conn, actor, entity, tenant_id)
    row = conn.execute("SELECT * FROM org_experiences WHERE id=? AND tenant_id=?"
                       " AND legal_entity_id=?", (exp_id, tenant_id, entity)).fetchone()
    if row is None:
        raise KeyError(exp_id)
    allowed = {"title", "content", "mandatory", "redline_terms", "tags"}
    values = {k: v for k, v in changes.items() if k in allowed}
    if not values:
        return dict(row)
    # 人工编辑同样是写入口，注入尾巴一样要剥
    if "title" in values:
        values["title"] = strip_injected_title(values["title"])
    if "content" in values:
        values["content"] = strip_injected_content(values["content"])
    if isinstance(values.get("redline_terms"), list):
        values["redline_terms"] = ",".join(values["redline_terms"])
    if "tags" in values:
        tags = values["tags"]
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
        values["tags"] = ",".join(tags)[:500]
    _history(conn, row, "edit", who["id"])
    conn.execute("UPDATE org_experiences SET "
        + ",".join(f"{k}=?" for k in values)
        + ",state='pending',version=version+1,reviewed_by=NULL,reviewed_at=NULL,review_note=NULL"
        + " WHERE id=?", list(values.values()) + [exp_id])
    write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=who["id"],
                action="experience_edited", target=exp_id,
                payload={"previous_version": row["version"]}, response={"state": "pending"})
    conn.commit()
    return dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (exp_id,)).fetchone())
