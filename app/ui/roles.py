"""角色库（Role Library）。

一个「角色模板」= 名字 + 标签 + 专属 System Prompt + 默认模型 + 技能，
老板在控制台新建角色后，系统会立刻把它实例化成一个 AI 员工（出现在组织架构里），
CEO 调度台按标签挑人。
"""
from __future__ import annotations

import hashlib
import secrets

from ..db import get_conn
from . import store

DDL = """
CREATE TABLE IF NOT EXISTS role_templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tag TEXT NOT NULL,                 -- dev / design / content / data / qa / translate
  prompt TEXT,
  model TEXT DEFAULT 'deepseek-chat',
  skills TEXT,                       -- 逗号分隔，给调度与展示用
  tools TEXT,                        -- 绑定的工具权限：code_exec, db_query, web_fetch …
  industry TEXT DEFAULT '通用',       -- 行业分类：研发/金融/市场/设计/法务/运营
  deliverable TEXT,                  -- 该角色的专属交付物模板
  veto INTEGER DEFAULT 0,            -- 是否拥有否决权
  state TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SEED = [
    ("ROLE-DEV", "Python极客", "dev",
     "你是资深 Python 工程师。先给最小可运行实现，再补边界处理；代码必须能直接跑。",
     "deepseek-chat", "写代码,调试,脚本,重构", "code_exec,file_write", "solution.py", 0),
    ("ROLE-QA", "严苛的测试员", "qa",
     "你是测试员。只挑毛病：边界、异常、可复现步骤、验收缺口。不要写实现。",
     "deepseek-chat", "用例设计,复现,验收", "code_exec", "test_report.md", 1),
    ("ROLE-CONTENT", "资深文案", "content",
     "你是公众号资深撰稿人。结构清晰、有案例、不夸大，避免广告法禁用词。",
     "deepseek-chat", "长文,改写,标题", "file_write", "article.md", 0),
    ("ROLE-DESIGN", "视觉设计师", "design",
     "你是 UI 设计师。先给一版可看的静态稿（HTML/CSS），再解释取舍。",
     "deepseek-chat", "界面,配色,原型", "file_write", "ui.html", 0),
    ("ROLE-DATA", "金融分析师", "data",
     "你是金融数据分析师。先定义口径，再给数据与结论，注明数据来源与时间。",
     "deepseek-chat", "取数,指标,建模", "db_query,file_write", "analysis.md", 0),
    ("ROLE-TRANSLATE", "翻译官", "translate",
     "你是中英互译译者。忠实原意，保留术语，给出必要注释。",
     "deepseek-chat", "中英互译,术语", "file_write", "translation.md", 0),
    ("ROLE-PRODUCT", "产品经理", "product",
     "你是 CPO。定义目标用户、范围边界与验收口径，拒绝范围蔓延。",
     "deepseek-chat", "需求,PRD,立项", "file_write,db_query", "charter.md", 1),
    ("ROLE-ARCHITECT", "架构师", "architect",
     "你是架构师。先给边界、依赖与回滚方案；对不可接受的方案行使否决权。",
     "deepseek-chat", "架构,选型,评审", "code_exec,file_write", "architecture.md", 1),
    ("ROLE-AUDIT", "审计/合规", "audit",
     "你是审计。检查数据边界、权限与留痕；不合规即出具否决意见。",
     "deepseek-chat", "合规,审计,留痕", "db_query,file_write", "audit_record.md", 1),
    ("ROLE-OPS", "运维/SRE", "ops",
     "你是 SRE。先给发布清单、回滚与监控方案，再谈上线。",
     "deepseek-chat", "发布,回滚,监控", "code_exec,file_write", "release_checklist.md", 1),
    ("ROLE-GROWTH", "市场/增长", "growth",
     "你是增长负责人。只谈可度量的转化与渠道，不给空洞口号。",
     "deepseek-chat", "增长,投放,转化", "file_write,db_query", "growth_plan.md", 0),
    ("ROLE-LEGAL", "法务", "legal",
     "你是法务。审查文案与条款风险，必要时行使否决权。",
     "deepseek-chat", "合规,条款,风险", "file_write", "legal_review.md", 1),
    ("ROLE-RESEARCH", "研究员", "research",
     "你是研究员。先给来源与口径，再给结论；查不到就写清楚缺口，不要编。",
     "deepseek-chat", "检索,取证,来源核验", "web_fetch,file_write", "research_notes.md", 0),
    ("ROLE-HR", "人事主管", "hr",
     "你是 HRD。负责组织招聘、岗位画像、绩效与团队激励。",
     "deepseek-chat", "招聘,岗位画像,薪酬,绩效", "file_write", "job_description.md", 0),
]


# 请求文本 → 标签。CEO 调度台先用它做初筛，再让模型确认（M15 基础版不调模型）。
TAG_WORDS: dict[str, tuple[str, ...]] = {
    "dev": ("开发", "写代码", "脚本", "爬虫", "接口", "程序", "计算器", "bug", "重构", "python", "代码", "量化", "mql5", "ea"),
    "design": ("设计", "界面", "ui", "原型", "配色", "海报", "视觉", "分镜", "aigc"),
    "content": ("文案", "文章", "公众号", "稿子", "推文", "slogan", "软文", "小说", "剧本", "网文", "短剧", "故事"),
    "data": ("数据", "分析", "报表", "指标", "财务", "对账", "取数", "研报", "产业链"),
    "qa": ("测试", "验收", "复现", "缺陷", "qa"),
    "translate": ("翻译", "英文", "中译英", "英译中", "translate"),
    "research": ("研究", "调研", "检索", "取证", "查资料", "行情", "文献", "考据", "论文"),
    "hr": ("人事", "招聘", "hr", "hrd", "面试", "绩效", "岗位", "入职"),
    "ops": ("运维", "sre", "监控", "发布", "部署", "运营", "上线"),
    "legal": ("法务", "合规", "合同", "条款", "审计"),
    "product": ("产品", "prd", "需求", "立项"),
}


def ensure(conn) -> None:
    conn.executescript(DDL)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(role_templates)")]
    if "tools" not in cols:
        conn.execute("ALTER TABLE role_templates ADD COLUMN tools TEXT")
    if "industry" not in cols:
        conn.execute("ALTER TABLE role_templates ADD COLUMN industry TEXT DEFAULT '通用'")
    if "deliverable" not in cols:
        conn.execute("ALTER TABLE role_templates ADD COLUMN deliverable TEXT")
    if "veto" not in cols:
        conn.execute("ALTER TABLE role_templates ADD COLUMN veto INTEGER DEFAULT 0")
    for row in SEED:
        conn.execute(
            """INSERT OR IGNORE INTO role_templates
               (id, name, tag, prompt, model, skills, tools, deliverable, veto)
               VALUES (?,?,?,?,?,?,?,?,?)""", row)
        conn.execute("""UPDATE role_templates SET industry=COALESCE(NULLIF(industry,'通用'),?),
                        tools=COALESCE(NULLIF(tools,''),?),
                        deliverable=COALESCE(NULLIF(deliverable,''),?), veto=?
                        WHERE id=?""", ("通用", row[6], row[7], row[8], row[0]))
    _seed_extra(conn)
    conn.commit()


def _seed_extra(conn) -> None:
    """从 app/data/roles_seed.json 灌行业细分角色（只加不覆盖）。"""
    import json as _json
    import pathlib as _pl
    path = _pl.Path(__file__).resolve().parents[1] / "data" / "roles_seed.json"
    if not path.exists():
        return
    try:
        rows = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        return
    for r in rows:
        rid = "ROLE-" + hashlib.sha1((r.get("tag", "") + ":" + r["name"]).encode()).hexdigest()[:6].upper()
        conn.execute(
            """INSERT OR IGNORE INTO role_templates
               (id, name, tag, prompt, model, skills, tools, deliverable, veto, industry)
               VALUES (?,?,?,?,?,?,?,?,0,?)""",
            (rid, r["name"], r.get("tag", "dev"), r.get("prompt", ""), "deepseek-chat",
             r.get("skills", ""), r.get("tools", "file_write"),
             r.get("deliverable", r["name"] + ".md"), r.get("industry", "通用")))


def update_role(conn, role_id: str, **fields) -> dict:
    """改 Prompt / 模型 / 工具，并立刻同步到已实例化的员工（下次派活即生效）。"""
    allowed = ("name", "prompt", "model", "skills", "tools", "deliverable", "industry")
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        conn.execute(f"UPDATE role_templates SET {', '.join(sets)} WHERE id=?",
                     (*vals, role_id))
        role = conn.execute("SELECT * FROM role_templates WHERE id=?", (role_id,)).fetchone()
        if role:
            conn.execute("UPDATE ai_employees SET soul=?, model=? WHERE role_id=?",
                         (role["prompt"] or "", role["model"] or "deepseek-chat", role_id))
        conn.commit()
    row = conn.execute("SELECT * FROM role_templates WHERE id=?", (role_id,)).fetchone()
    if row is None:
        raise ValueError("role not found")
    return dict(row)


def list_roles(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT r.*, (SELECT COUNT(*) FROM ai_employees e WHERE e.role_id = r.id
                        AND COALESCE(e.status,'online') <> 'fired') AS headcount
           FROM role_templates r WHERE COALESCE(r.state,'active')='active'
           ORDER BY COALESCE(r.industry,'通用'), r.name""").fetchall()
    return [dict(r) for r in rows]


def tags_of(text: str) -> list[str]:
    low = (text or "").lower()
    hits = [tag for tag, words in TAG_WORDS.items() if any(w in low for w in words)]
    return hits


def create_role(conn, *, name: str, tag: str, prompt: str = "", model: str = "deepseek-chat",
                skills: str = "") -> dict:
    rid = "ROLE-" + hashlib.sha1(f"{tag}:{name}".encode()).hexdigest()[:6].upper()
    conn.execute("""INSERT OR REPLACE INTO role_templates (id, name, tag, prompt, model, skills, state)
                    VALUES (?,?,?,?,?,?, 'active')""",
                 (rid, name, tag, prompt, model, skills))
    conn.commit()
    return dict(conn.execute("SELECT * FROM role_templates WHERE id=?", (rid,)).fetchone())


def instantiate(conn, role_id: str, entity_id: str = "LE-GROUP-HQ") -> str:
    """把角色模板落地成一个可调度的 AI 员工（幂等：同一角色同一条线只建一个）。"""
    from ..tenancy import of_entity
    tenant_id = of_entity(conn, entity_id)
    role = conn.execute("SELECT * FROM role_templates WHERE id=?", (role_id,)).fetchone()
    if role is None:
        raise ValueError(f"unknown role {role_id}")

    slug = hashlib.sha1(f"{entity_id}:{role['name']}".encode()).hexdigest()[:6].upper()
    emp_id, pos_id = f"AI-{slug}", f"POS-{slug}"

    exist = conn.execute(
        """SELECT id, status FROM ai_employees WHERE (role_id=? OR id=?) AND legal_entity_id=?
           ORDER BY CASE WHEN COALESCE(status,'online') != 'fired' THEN 0 ELSE 1 END LIMIT 1""",
        (role_id, emp_id, entity_id)).fetchone()
    if exist:
        if exist["status"] == "fired":
            conn.execute(
                "UPDATE ai_employees SET status='online', model=?, soul=?, focus=? WHERE id=?",
                (role["model"], role["prompt"] or f"你是{role['name']}。", "按判据交付", exist["id"]))
            conn.execute("UPDATE identities SET state='active' WHERE employee_id=?", (exist["id"],))
            conn.execute("UPDATE positions SET state='active' WHERE id=?", (pos_id,))
            store.get_or_create_dm(conn, exist["id"], role["name"], entity_id)
            conn.commit()
        return exist["id"]

    conn.execute("""INSERT INTO positions (id, legal_entity_id, title, pack_id, state)
                    VALUES (?,?,?, 'content-v1', 'active')
                    ON CONFLICT(id) DO UPDATE SET state='active'""",
                 (pos_id, entity_id, role["name"]))
    conn.execute("""INSERT INTO ai_employees
                    (id, legal_entity_id, name, position_id, model, soul, focus, status, role_id)
                    VALUES (?,?,?,?,?,?,?,'online',?)
                    ON CONFLICT(id) DO UPDATE SET status='online', model=excluded.model, soul=excluded.soul, role_id=excluded.role_id""",
                 (emp_id, entity_id, role["name"], pos_id, role["model"],
                  role["prompt"] or f"你是{role['name']}。", "按判据交付", role_id))
    exist_ident = conn.execute("SELECT id FROM identities WHERE employee_id=?", (emp_id,)).fetchone()
    if exist_ident:
        conn.execute("UPDATE identities SET state='active' WHERE employee_id=?", (emp_id,))
    else:
        conn.execute("""INSERT INTO identities
                        (id, name, role, legal_entity_id, secret, scopes, state, employee_id, tenant_id)
                        VALUES (?,?,'agent',?,?, '["task:create","task:read","accept:judge"]',
                                'active', ?,?)""",
                     ("ID-" + secrets.token_hex(8), role["name"], entity_id,
                      secrets.token_hex(16), emp_id, tenant_id))
    store.get_or_create_dm(conn, emp_id, role["name"], entity_id)
    conn.commit()
    return emp_id


def pick(conn, tags: list[str], entity_id: str | None = None) -> list[dict]:
    """按标签挑人：先看已实例化的员工，没有再按角色库实例化。"""
    if not tags:
        return []
    marks = ",".join("?" * len(tags))
    rows = conn.execute(
        f"""SELECT e.id, e.name, e.model, e.legal_entity_id, r.tag, r.name AS role_name
            FROM ai_employees e JOIN role_templates r ON r.id = e.role_id
            WHERE r.tag IN ({marks}) AND COALESCE(e.status,'online')='online'
              {'AND e.legal_entity_id = ?' if entity_id else ''}
            ORDER BY CASE r.tag WHEN 'dev' THEN 0 ELSE 1 END, e.name""",
        (*tags, entity_id) if entity_id else tuple(tags)).fetchall()
    out = [dict(r) for r in rows]
    if out:
        return _ensure_each_tag(conn, out, tags, entity_id)
    for tag in tags:
        role = conn.execute(
            "SELECT id FROM role_templates WHERE tag=? AND state='active' LIMIT 1",
            (tag,)).fetchone()
        if role:
            emp_id = instantiate(conn, role["id"], entity_id or "LE-GROUP-HQ")
            row = conn.execute(
                """SELECT e.id, e.name, e.model, e.legal_entity_id, r.tag, r.name AS role_name
                   FROM ai_employees e JOIN role_templates r ON r.id = e.role_id
                   WHERE e.id=?""", (emp_id,)).fetchone()
            out.append(dict(row))
    return _ensure_each_tag(conn, out, tags, entity_id)


def _ensure_each_tag(conn, people: list[dict], tags: list[str],
                     entity_id: str | None) -> list[dict]:
    """多标签需求（开发+文案）必须每个标签都有人。"""
    have = {p["tag"] for p in people}
    for tag in tags:
        if tag in have:
            continue
        role = conn.execute(
            "SELECT id FROM role_templates WHERE tag=? AND state='active' LIMIT 1",
            (tag,)).fetchone()
        if not role:
            continue
        emp_id = instantiate(conn, role["id"], entity_id or "LE-GROUP-HQ")
        row = conn.execute(
            """SELECT e.id, e.name, e.model, e.legal_entity_id, r.tag, r.name AS role_name
               FROM ai_employees e JOIN role_templates r ON r.id = e.role_id
               WHERE e.id=?""", (emp_id,)).fetchone()
        if row:
            people.append(dict(row))
            have.add(tag)
    return people
