"""V4.1 流程模板、DAG 规划、黑板与失败诊断。

设计取舍（读完再改）：

* **模板存库**（`flow_templates`）：节点带 `key/title/role/gate/optional`。项目阶段由
  模板生成，不再写死九段；**存量项目没有 flow 记录时仍走 `pipeline.STAGES`**，
  所以老数据、老测试、老接口都不受影响。
* **规划先关键词、后模型**：关键词免费且可解释（「分析黄金走势」→ 分析流），
  命不中才调一次便宜模型；模型输出必须落回已知模板，抽风就退回关键词结果，
  绝不因为规划失败而卡住派活。
* **黑板只存摘要**：`project_artifacts` 存每段产出的正文（截断 4k），文件本体仍留在
  `deliveries/`；PM 汇总读黑板而不是重读文件，避免 token 爆炸。
* **诊断先规则、后模型**：预算/超时/红线/闸门这些有确定语义的失败，规则直接给结论；
  模型只在可用时补充「怎么改」。任何异常都退回规则结论——诊断不能变成新的失败点。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid

log = logging.getLogger("flows")

PLATFORM = "platform"          # 平台/PM 自己推进的阶段，不拉人进群

DDL = """
CREATE TABLE IF NOT EXISTS flow_templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  description TEXT,
  nodes TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_artifacts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage_key TEXT NOT NULL,
  role_tag TEXT,
  title TEXT,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'deliverable',
  file_path TEXT,
  tenant_id TEXT,
  legal_entity_id TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_project_artifacts_pid ON project_artifacts(project_id);

CREATE TABLE IF NOT EXISTS run_diagnoses (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT,
  cause TEXT NOT NULL,
  fixes TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'rules',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_run_diagnoses_run ON run_diagnoses(run_id);
"""


def _node(key: str, title: str, role: str, gate: str, *, kind: str = "doc",
          optional: bool = False) -> dict:
    return {"key": key, "title": title, "role": role, "gate": gate,
            "kind": kind, "optional": optional}


PRESETS: dict[str, dict] = {
    "FLOW-SOFTWARE": {
        "name": "软件交付流",
        "kind": "software",
        "description": "接需求 → 方案 → 实现 → 测试 → 评审 → 验收 → 发布 → 复盘（8 步）",
        "nodes": [
            _node("intake", "接需求", "product", "需求可交付"),
            _node("wbs", "拆解与方案", "architect", "DAG 无环且依赖可满足"),
            _node("build", "实现开发", "dev", "代码可运行"),
            _node("test", "联调测试", "qa", "测试报告通过"),
            _node("review", "合规评审", "audit", "无合规风险", optional=True),
            _node("accept", "验收", "product", "验收通过"),
            _node("release", "发布", "ops", "发布清单齐全", optional=True),
            _node("retro", "复盘", "product", "含改进项", optional=True),
        ],
    },
    "FLOW-ANALYSIS": {
        "name": "分析报告流",
        "kind": "analysis",
        "description": "检索 → 分析 → 撰写 → 审核 → 交付（5 步，不含开发/测试/发布）",
        "nodes": [
            _node("retrieve", "资料检索", "research", "来源与口径已记录"),
            _node("analyze", "数据分析", "data", "结论有数据支撑"),
            _node("author", "报告撰写", "data", "报告结构完整"),
            _node("review", "审核", "audit", "审核通过"),
            _node("deliver", "交付", PLATFORM, "已交付", kind="deliver"),
        ],
    },
    "FLOW-RESEARCH": {
        "name": "调研流",
        "kind": "research",
        "description": "界定 → 检索 → 交叉验证 → 撰写 → 审核 → 交付（6 步）",
        "nodes": [
            _node("scope", "界定问题", "product", "范围与假设明确"),
            _node("gather", "资料检索", "research", "来源可追溯"),
            _node("verify", "交叉验证", "data", "关键结论双源验证"),
            _node("draft", "结论撰写", "content", "结构完整"),
            _node("review", "审核", "audit", "审核通过"),
            _node("deliver", "交付", PLATFORM, "已交付", kind="deliver"),
        ],
    },
    "FLOW-URGENT": {
        "name": "紧急任务流",
        "kind": "urgent",
        "description": "定级 → 处置 → 确认 → 交付（4 步，先止血再补记录）",
        "nodes": [
            _node("triage", "定级与影响面", "product", "影响面已确认"),
            _node("act", "快速处置", "ops", "处置动作已执行"),
            _node("verify", "结果确认", "qa", "确认已恢复"),
            _node("deliver", "交付", PLATFORM, "已交付", kind="deliver"),
        ],
    },
}

# 关键词权重：先命中先得，顺序即优先级（紧急 > 调研 > 分析 > 软件）
KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("FLOW-URGENT", ("紧急", "立刻", "马上", "今天必须", "急单", "线上故障", "宕机", "救火", "止血")),
    ("FLOW-RESEARCH", ("调研", "访谈", "问卷", "市场研究", "竞品", "尽调", "选型", "走访")),
    ("FLOW-ANALYSIS", ("分析", "走势", "行情", "黄金", "白银", "汇率", "报表", "指标", "评估",
                       "预测", "研判", "复盘报告", "研究报告", "研报")),
    ("FLOW-SOFTWARE", ("开发", "实现", "系统", "功能", "页面", "接口", "脚本", "bug", "重构",
                       "上线", "程序", "计算器", "爬虫", "部署")),
]

DROP_WORDS = {
    "release": ("内部使用", "不用发布", "不上线", "仅内部"),
    "review": ("不用评审", "无合规要求"),
    "retro": ("不用复盘", "没有复盘"),
    "test": ("不用测试",),
}


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    # 允许在 pipeline 建表之前被调用（调度台先做流程规划、后立项）
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='project_stages'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(project_stages)")}
        for name, ddl in (("role_tag", "TEXT"), ("node_kind", "TEXT"),
                          ("assignee_id", "TEXT"), ("gate_name", "TEXT")):
            if name not in cols:
                conn.execute(f"ALTER TABLE project_stages ADD COLUMN {name} {ddl}")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='projects'").fetchone():
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        if "flow_id" not in pcols:
            conn.execute("ALTER TABLE projects ADD COLUMN flow_id TEXT")
    for fid, tpl in PRESETS.items():
        conn.execute(
            "INSERT OR IGNORE INTO flow_templates (id,name,kind,description,nodes)"
            " VALUES (?,?,?,?,?)",
            (fid, tpl["name"], tpl["kind"], tpl["description"],
             json.dumps(tpl["nodes"], ensure_ascii=False)))
    conn.commit()


# ------------------------------------------------------------------ 模板读取
def templates(conn) -> list[dict]:
    ensure(conn)
    rows = conn.execute("SELECT * FROM flow_templates WHERE enabled=1 ORDER BY rowid").fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "description": r["description"],
             "nodes": json.loads(r["nodes"] or "[]")} for r in rows]


def template(conn, flow_id: str) -> dict | None:
    ensure(conn)
    row = conn.execute("SELECT * FROM flow_templates WHERE id=?", (flow_id,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "description": row["description"], "nodes": json.loads(row["nodes"] or "[]")}


def _trim(nodes: list[dict], text: str) -> list[dict]:
    """只裁 optional 节点；必选节点永不动（避免把交付链裁断）。"""
    low = (text or "").lower()
    out = []
    for node in nodes:
        words = DROP_WORDS.get(node["key"], ())
        if node.get("optional") and any(w in low for w in words):
            continue
        out.append(node)
    return out


CLARIFY_QUESTION = "请问您是需要一份报告，还是开发一个小工具？"

# v4.2：意图分类的提示词刻意压到 ~200 token —— 每次派单只花一次极轻量调用。
INTENT_PROMPT = """把需求归到一类。只输出 JSON：
{{"task_type":"analysis|software|content|design","confidence":0.0-1.0,"reason":"一句话","secondary":"可选，第二类"}}

analysis=行情/数据/研判/报告类；software=开发/脚本/系统/修复类；
content=文案/文章/改写类；design=界面/视觉/原型类。
口语也算：例如"瞧瞧金子啥情况"=analysis（要看金价行情）。

需求：{text}"""

# 口语里不出现「分析」「走势」的说法也要能兜住（模型不可用时的后备）
FALLBACK_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("analysis", ("金子", "黄金", "金价", "金市", "白银", "汇率", "行情", "走势", "看一眼",
                  "瞧瞧", "看看", "情况", "数据", "报表", "指标", "研判", "预测", "评估",
                  "分析", "研究", "报告")),
    ("software", ("开发", "实现", "系统", "功能", "页面", "接口", "脚本", "bug", "重构",
                  "上线", "程序", "计算器", "爬虫", "部署", "监控")),
    ("content", ("文案", "文章", "公众号", "稿子", "推文", "宣传", "软文", "改写")),
    ("design", ("设计", "界面", "原型", "配色", "海报", "视觉", "ui")),
    ("research", ("调研", "竞品", "尽调", "选型", "访谈", "问卷", "走访")),
]

# 安全兜底：这类词直接走紧急流（不等模型判断；线上故障先止血）
URGENT_WORDS = ("紧急", "立刻", "马上", "急单", "线上故障", "宕机", "救火", "止血")

TASK_TO_FLOW = {"analysis": "FLOW-ANALYSIS", "software": "FLOW-SOFTWARE",
                "content": "FLOW-ANALYSIS", "design": "FLOW-RESEARCH",
                "research": "FLOW-RESEARCH", "urgent": "FLOW-URGENT"}


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _heuristic_intent(text: str) -> dict:
    """模型不可用/离线时的后备：口语词表 + 置信度 0.75（低于 0.6 会走追问）。"""
    low = (text or "").lower()
    best, hits = None, []
    for task_type, words in FALLBACK_WORDS:
        found = [w for w in words if w in low]
        if len(found) > len(hits):
            best, hits = task_type, found
    if not best:
        return {"task_type": "analysis", "confidence": 0.3,
                "reason": "没有可用信号", "secondary": "", "how": "fallback"}
    return {"task_type": best, "confidence": 0.75,
            "reason": "离线词表命中：" + "、".join(hits[:3]), "secondary": "",
            "how": "fallback"}


def classify(text: str) -> dict:
    """v4.2 战役一：一次轻量模型调用做意图分类；失败才退回离线词表。

    诚实边界：模型只拿到需求前 400 字，输出只认四种 task_type；置信度由模型自己给，
    低于 0.6 由调用方（dispatcher）追问用户，而不是硬猜一个流程。
    """
    from . import llm
    if os.environ.get("M0_INTENT_LLM", "1") != "1":
        return _heuristic_intent(text)
    try:
        raw = llm.complete(INTENT_PROMPT.format(text=(text or "")[:400]),
                           system="你是任务意图分类器，只输出 JSON，不要解释。",
                           max_tokens=120, temperature=0.0, timeout=20.0)
    except Exception as exc:                      # noqa: BLE001 - 分类失败不阻塞派活
        log.info("intent llm failed: %s", exc)
        raw = None
    data = _parse_json(raw)
    if data and str(data.get("task_type")) in TASK_TO_FLOW:
        try:
            confidence = float(data.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        secondary = str(data.get("secondary") or "")
        return {"task_type": str(data["task_type"]), "confidence": max(0.0, min(1.0, confidence)),
                "reason": str(data.get("reason") or "")[:200],
                "secondary": secondary if secondary in TASK_TO_FLOW else "",
                "how": "llm"}
    return _heuristic_intent(text)


def _compose_nodes(primary: str, secondary: str) -> tuple[list[dict], str]:
    """v4.2 战役三：复合流 = 先主流程，再副流程（副流程节点 key 加前缀防撞车）。"""
    first = PRESETS[primary]["nodes"]
    second = PRESETS[secondary]["nodes"]
    nodes = [dict(n) for n in first if n["key"] != "deliver"]
    prefix = {"FLOW-SOFTWARE": "sw", "FLOW-ANALYSIS": "an",
              "FLOW-RESEARCH": "rs", "FLOW-URGENT": "ug"}.get(secondary, "x")
    for node in second:
        copy = dict(node)
        if copy["key"] == "deliver":
            continue                             # 交付统一放最后，别中途交一次
        copy["key"] = f"{prefix}_{node['key']}"
        copy["title"] = node["title"] + "（续）"
        nodes.append(copy)
    nodes.append(_node("deliver", "交付", PLATFORM, "已交付", kind="deliver"))
    return nodes, f"{PRESETS[primary]['name']} + {PRESETS[secondary]['name']}"


def plan(conn, text: str, tags: list[str] | None = None) -> dict:
    """需求 → 流程规划。v4.2 起由 LLM 意图分类驱动（关键词只作离线后备）。"""
    ensure(conn)
    cls = classify(text)
    low = (text or "").lower()
    urgent = any(w in low for w in URGENT_WORDS)
    if not urgent and cls["confidence"] < 0.6:
        return {"need_clarify": True, "question": CLARIFY_QUESTION, "flow_id": None,
                "name": "", "kind": "", "nodes": [], "roles": [],
                "how": cls["how"], "reason": cls["reason"],
                "task_type": cls["task_type"], "confidence": cls["confidence"]}

    primary = TASK_TO_FLOW[cls["task_type"]]
    if urgent:
        primary = "FLOW-URGENT"                     # 线上故障先止血，不等模型判类
    secondary = TASK_TO_FLOW.get(cls.get("secondary") or "", "")
    if secondary and secondary != primary:
        nodes, name = _compose_nodes(primary, secondary)
        roles_needed: list[str] = []
        for node in nodes:
            if node["role"] != PLATFORM and node["role"] not in roles_needed:
                roles_needed.append(node["role"])
        return {"flow_id": f"{primary}+{secondary}", "name": name, "kind": "composite",
                "nodes": nodes, "roles": roles_needed, "how": cls["how"],
                "reason": cls["reason"], "task_type": cls["task_type"],
                "secondary": cls["secondary"], "confidence": cls["confidence"]}

    tpl = template(conn, primary)
    nodes = [dict(n) for n in tpl["nodes"]]
    nodes = _trim(nodes, text)
    if tpl["kind"] != "software" and not any(n["key"] == "deliver" for n in nodes):
        nodes.append(_node("deliver", "交付", PLATFORM, "已交付", kind="deliver"))
    roles_needed = []
    for node in nodes:
        if node["role"] != PLATFORM and node["role"] not in roles_needed:
            roles_needed.append(node["role"])
    return {"flow_id": primary, "name": tpl["name"], "kind": tpl["kind"],
            "nodes": nodes, "roles": roles_needed, "how": cls["how"],
            "reason": cls["reason"], "task_type": cls["task_type"],
            "confidence": cls["confidence"]}


def instantiate(conn, pid: str, plan_or_id: dict | str) -> list[dict]:
    """按规划/模板生成项目阶段（幂等：已有阶段就不动）。"""
    ensure(conn)
    if conn.execute("SELECT 1 FROM project_stages WHERE project_id=? LIMIT 1",
                    (pid,)).fetchone():
        return nodes(conn, pid)
    if isinstance(plan_or_id, str):
        tpl = template(conn, plan_or_id)
        nodes_ = tpl["nodes"] if tpl else PRESETS["FLOW-ANALYSIS"]["nodes"]
        flow_id = plan_or_id
    else:
        nodes_ = plan_or_id["nodes"]
        flow_id = plan_or_id["flow_id"]
    for seq, node in enumerate(nodes_):
        conn.execute(
            "INSERT INTO project_stages (id, project_id, seq, key, title, role_tag, node_kind,"
            " gate_name) VALUES (?,?,?,?,?,?,?,?)",
            (f"S-{uuid.uuid4().hex[:12]}", pid, seq, node["key"], node["title"],
             node["role"], node.get("kind") or "doc", node.get("gate") or ""))
    conn.execute("UPDATE projects SET flow_id=? WHERE id=?", (flow_id, pid))
    conn.commit()
    return nodes(conn, pid)


def nodes(conn, pid: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM project_stages WHERE project_id=? ORDER BY seq",
                        (pid,)).fetchall()
    return [{"key": r["key"], "title": r["title"], "seq": r["seq"], "state": r["state"],
             "role": r["role_tag"] or "", "kind": r["node_kind"] or "doc",
             "gate": r["gate_name"] or f"{r['title']}完成",
             "assignee": r["assignee_id"] or "",
             "started_at": r["started_at"], "ended_at": r["ended_at"]} for r in rows]


def node_of(conn, pid: str, key: str) -> dict:
    for node in nodes(conn, pid):
        if node["key"] == key:
            return node
    return {"key": key, "title": key, "role": "", "kind": "doc", "seq": 0,
            "state": "active", "gate": f"{key}完成"}


# -------------------------------------------------------------------- 黑板
def blackboard_write(conn, *, pid: str, stage_key: str, role_tag: str, title: str,
                     content: str, kind: str = "deliverable",
                     file_path: str | None = None) -> str:
    proj = conn.execute("SELECT tenant_id, legal_entity_id FROM projects WHERE id=?",
                        (pid,)).fetchone()
    aid = "PA-" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO project_artifacts (id,project_id,stage_key,role_tag,title,content,"
        "kind,file_path,tenant_id,legal_entity_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, pid, stage_key, role_tag, title, (content or "")[:4000], kind, file_path,
         proj["tenant_id"] if proj else None,
         proj["legal_entity_id"] if proj else None))
    conn.commit()
    return aid


def blackboard(conn, pid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id,stage_key,role_tag,title,kind,file_path,substr(content,1,600) AS excerpt,"
        "created_at FROM project_artifacts WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()
    return [dict(r) for r in rows]


def pm_summary(conn, pid: str, *, llm_docs: bool = False) -> dict:
    """PM 读黑板做汇总：默认模板拼装（0 token），llm_docs=1 时才让模型写。"""
    items = [i for i in blackboard(conn, pid) if i.get("kind") != "summary"]
    if not items:
        return {"summary": "", "count": 0, "source": "empty"}
    lines = [f"# 项目汇总 {pid}", "",
             "| 阶段 | 角色 | 交付 |", "|---|---|---|"]
    lines += [f"| {i['stage_key']} | {i['role_tag'] or '平台'} | {i['title']} |" for i in items]
    body = "\n".join(lines)
    source = "template"
    if llm_docs:
        try:
            from . import llm
            text = llm.complete(
                "把下面这些阶段交付压成一页中文汇总，保留关键结论与未决风险，不要复述全文：\n\n"
                + "\n\n".join(f"## {i['stage_key']} · {i['title']}\n{i['excerpt']}" for i in items),
                system="你是项目经理，只写结论与风险。", max_tokens=800)
            if text and text.strip():
                body, source = text.strip(), "llm"
        except Exception as exc:                 # noqa: BLE001
            log.info("pm summary llm failed: %s", exc)
    conn.execute("DELETE FROM project_artifacts WHERE project_id=? AND kind='summary'", (pid,))
    blackboard_write(conn, pid=pid, stage_key="summary", role_tag="product",
                     title="PM 汇总", content=body, kind="summary")
    return {"summary": body, "count": len(items), "source": source}


# ---------------------------------------------------------------- 阶段详情
def stage_detail(conn, pid: str, key: str) -> dict:
    stage = conn.execute("SELECT * FROM project_stages WHERE project_id=? AND key=?",
                         (pid, key)).fetchone()
    if stage is None:
        raise KeyError("StageNotFound")
    deliverables = [dict(r) for r in conn.execute(
        "SELECT id,kind,file_path,bytes,created_at FROM stage_deliverables"
        " WHERE project_id=? AND stage_key=? ORDER BY rowid", (pid, key)).fetchall()]
    gates = [dict(r) for r in conn.execute(
        "SELECT id,name,kind,required_role,state,note,updated_at FROM stage_gates"
        " WHERE project_id=? AND stage_key=? AND superseded=0 ORDER BY rowid",
        (pid, key)).fetchall()]
    audits = [dict(r) for r in conn.execute(
        "SELECT action,target,created_at,substr(COALESCE(payload,''),1,400) AS payload"
        " FROM ai_audit_logs WHERE payload LIKE ? OR target=? ORDER BY rowid DESC LIMIT 30",
        (f"%{pid}%", pid)).fetchall()]
    runs = []
    for r in conn.execute(
            "SELECT r.id run_id,r.task_id,t.title,r.state,r.usage_tokens,r.started_at,"
            "r.ended_at,r.error FROM task_runs r JOIN tasks t ON t.id=r.task_id"
            " WHERE t.project_id=? AND (t.stage=? OR ?='') ORDER BY r.rowid DESC LIMIT 20",
            (pid, key, key)).fetchall():
        trace = conn.execute("SELECT trace_id,total_ms,total_tokens,state FROM agent_traces"
                             " WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                             (r["run_id"],)).fetchone()
        runs.append({**dict(r), "trace": dict(trace) if trace else None})
    artifacts = [dict(r) for r in conn.execute(
        "SELECT * FROM project_artifacts WHERE project_id=? AND stage_key=?"
        " ORDER BY rowid", (pid, key)).fetchall()]
    return {"project_id": pid, "stage": dict(stage), "deliverables": deliverables,
            "gates": gates, "audit": audits, "runs": runs, "artifacts": artifacts}


# ------------------------------------------------------------------ 失败诊断
RULES = [
    (("tool_cap", "检索次数超限", "抓取次数超限"), "工具调用超限",
     "同一轮里搜索/抓取次数超过底座上限，系统已强制中断，避免无限发散。",
     ["基于已有资料直接写报告，不要再检索", "在需求里限定只看 2-3 个来源",
      "确实需要更多来源时，拆成多个任务分别跑"]),
    (("budget", "budget_exhausted"), "预算耗尽",
     "本轮 token 预算在任务完成前用完，模型被强制中断。",
     ["把该任务预算调大（实体池剩余要够）", "缩小交付范围后重试", "把长文拆成两段跑"]),
    (("timeout", "acp_timeout", "timed out"), "执行超时",
     "ACP 会话在超时时间内没有收敛，通常是模型反复读写同一文件。",
     ["调大 M0_RUN_TIMEOUT", "在 prompt 里限定只改一个文件", "改用更小的任务粒度重试"]),
    (("redline", "企业红线", "命中红线"), "红线拦截（L0）",
     "交付物命中已批准的企业红线词，系统直接驳回，没有人工审批入口。",
     ["删掉禁用词后重试", "在组织经验里补充/收敛红线词表"]),
    (("GateNotPassed", "gate"), "阶段闸门未通过",
     "上一阶段的闸门没有通过记录，阶段状态机拒绝了这次进入。",
     ["先在项目卡里复核闸门", "补充上一段交付后重试"]),
    (("ProjectRevisionRequired", "blocked"), "项目被冻结",
     "项目因否决/失败处于 blocked，必须先修订需求解冻。",
     ["在项目卡提交修订后的完整需求", "解冻后一键重试"]),
    (("runner handle", "kill"), "执行句柄丢失",
     "运行进程已不在当前服务进程的句柄表里（服务重启或跨进程）。",
     ["确认没有残留进程后直接重试", "避免在多 worker 下部署"]),
]


def _rule_diagnosis(error: str, state: str) -> dict:
    low = f"{error or ''} {state or ''}".lower()
    for words, title, cause, fixes in RULES:
        if any(w.lower() in low for w in words):
            return {"cause": f"【{title}】{cause}", "fixes": fixes, "source": "rules"}
    return {"cause": "本次运行没有留下明确的错误码；常见原因是任务描述过宽、模型在"
                     "读写文件之间打转，或交付物不满足判据。",
            "fixes": ["把需求缩到一个可交付物", "在描述里指定主文件名与格式",
                      "查看链路追踪里最耗时的 span 后重试"],
            "source": "rules"}


def diagnose(conn, run_id: str, *, use_llm: bool = True) -> dict:
    ensure(conn)
    row = conn.execute(
        "SELECT r.*, t.title, t.task_type, t.project_id FROM task_runs r"
        " LEFT JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError("RunNotFound")
    cached = conn.execute("SELECT * FROM run_diagnoses WHERE run_id=?", (run_id,)).fetchone()
    if cached is not None:
        return {"run_id": run_id, "cause": cached["cause"],
                "fixes": json.loads(cached["fixes"] or "[]"),
                "source": cached["source"], "cached": True}
    spans = [dict(r) for r in conn.execute(
        "SELECT kind,name,detail,started_ms,ended_ms FROM trace_spans s"
        " JOIN agent_traces t ON t.trace_id=s.trace_id WHERE t.run_id=?"
        " ORDER BY s.rowid DESC LIMIT 12", (run_id,)).fetchall()]
    verdict = _rule_diagnosis(row["error"] or "", row["state"] or "")
    if not (row["error"] or "").strip() and row["state"] == "succeeded":
        # 运行本身成功、阶段却失败：问题在交付物/闸门，不在模型调用
        verdict = {"cause": "本次运行本身成功，但阶段验收没通过：通常是没产出交付物，"
                            "或交付物没满足本阶段的闸门口径。",
                   "fixes": ["确认任务要求里写清了主交付文件名",
                             "把阶段描述收窄到一个可交付物",
                             "在链路追踪里看最后一次 write/read 是否真的落盘"],
                   "source": "rules"}
    if use_llm and row["state"] != "succeeded":
        try:
            from . import llm
            ctx = json.dumps({"error": (row["error"] or "")[:400],
                              "task": row["title"], "type": row["task_type"],
                              "spans": [{k: s[k] for k in ("kind", "name")}
                                        for s in spans]}, ensure_ascii=False)
            raw = llm.complete(
                "下面是一次失败的 AI 任务运行。给中文诊断，只输出 JSON："
                '{"cause":"一句话根因","fixes":["最多3条可执行修复"]}\n\n' + ctx,
                system="你是故障诊断专家，不猜不存在的事实。", max_tokens=400)
            start, end = (raw or "").find("{"), (raw or "").rfind("}")
            if start >= 0 and end > start:
                data = json.loads(raw[start:end + 1])
                fixes = [str(f)[:200] for f in (data.get("fixes") or [])][:3]
                if data.get("cause"):
                    verdict = {"cause": str(data["cause"])[:600],
                               "fixes": fixes or verdict["fixes"], "source": "llm"}
        except Exception as exc:                 # noqa: BLE001 - 诊断不能变成新的失败点
            log.info("diagnosis llm failed: %s", exc)
    conn.execute(
        "INSERT OR REPLACE INTO run_diagnoses (id,run_id,task_id,cause,fixes,source)"
        " VALUES (?,?,?,?,?,?)",
        ("RD-" + uuid.uuid4().hex[:12], run_id, row["task_id"], verdict["cause"],
         json.dumps(verdict["fixes"], ensure_ascii=False), verdict["source"]))
    conn.commit()
    return {"run_id": run_id, "task_id": row["task_id"], "state": row["state"],
            "error": row["error"], **verdict, "cached": False}


# ---------------------------------------------------------------- 段内文档
def template_document(pid: str, requirement: str, node: dict) -> str:
    role = node.get("role") or PLATFORM
    return "\n".join([
        f"# {node['title']}",
        "",
        f"- 项目：{pid}",
        f"- 负责角色：{role}",
        f"- 阶段：{node['key']}",
        "",
        "## 输入需求",
        (requirement or "").strip()[:800],
        "",
        "## 本阶段产出要点",
        f"1. 按【{node['gate']}】这一口径完成 {node['title']}。",
        "2. 结论要可核对：写清来源、时间与口径。",
        "3. 未决问题单独列出，交给下一阶段。",
    ])


def stage_document(pid: str, requirement: str, node: dict) -> str | None:
    """模型写阶段文档（贵）：只在 projects.use_llm_docs=1 时调用。"""
    from . import llm
    try:
        text = llm.complete(
            f"项目 {pid} 的「{node['title']}」阶段。\n"
            f"验收口径：{node['gate']}\n\n原始需求：\n{(requirement or '')[:800]}\n\n"
            "用中文写这一阶段的正式交付文档：结论先行，写清来源/口径/未决问题，"
            "不要复述需求。",
            system=f"你是{node['title']}负责人，只交付与本阶段有关的结论。",
            max_tokens=1200)
        return text.strip() if text and text.strip() else None
    except Exception as exc:                     # noqa: BLE001
        log.info("stage doc llm failed: %s", exc)
        return None
