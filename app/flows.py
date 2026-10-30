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

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid

log = logging.getLogger("flows")

PLATFORM = "platform"          # 平台/PM 自己推进的阶段，不拉人进群

# v4.2.1：单阶段 token 预算。界面上可随时改（settings 表优先），环境变量次之。
FLOW_STAGE_BUDGET_DEFAULT = 20000
FLOW_STAGE_BUDGET_MIN = 5000
FLOW_STAGE_BUDGET_MAX = 500000

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
        "name": "标准 9 阶段软件工程流水线",
        "kind": "software",
        "description": "接需求 → 立项 → 4轮研讨 → 黑板拆解 → 生产装配 → 激光质检 → 验收 → 出货 → 复盘（9 步）",
        "nodes": [
            _node("intake", "接需求", "product", "需求可交付"),
            _node("charter", "立项界标", "product", "立项书章节完整"),
            _node("workshop", "4轮研讨会", "architect", "全员留下风险意见"),
            _node("wbs", "黑板拆解", "architect", "DAG 无环且依赖可满足"),
            _node("build", "生产装配", "dev", "代码可运行"),
            _node("test", "脉冲激光质检", "qa", "测试报告通过"),
            _node("accept", "验收网关", "product", "验收通过"),
            _node("release", "出货港", "ops", "发布清单齐全", optional=True),
            _node("retro", "紫晶金库", "product", "含改进项", optional=True),
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
    conn.execute("DELETE FROM flow_templates WHERE id IN ('FLOW-SOFTWARE', 'FLOW-ANALYSIS', 'FLOW-RESEARCH', 'FLOW-URGENT', 'FLOW-STANDARD')")
    conn.commit()


# ------------------------------------------------------------------ 模板读取
def templates(conn, *, include_adaptive: bool = False) -> list[dict]:
    ensure(conn)
    if include_adaptive:
        rows = conn.execute("SELECT * FROM flow_templates WHERE enabled=1 ORDER BY rowid").fetchall()
    else:
        rows = conn.execute("SELECT * FROM flow_templates WHERE enabled=1 AND kind!='adaptive' ORDER BY rowid").fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "description": r["description"],
             "nodes": json.loads(r["nodes"] or "[]")} for r in rows]


def template(conn, flow_id: str) -> dict | None:
    ensure(conn)
    if flow_id == "FLOW-STANDARD":
        flow_id = "FLOW-SOFTWARE"
    row = conn.execute("SELECT * FROM flow_templates WHERE id=?", (flow_id,)).fetchone()
    if row is None:
        if flow_id in PRESETS:
            p = PRESETS[flow_id]
            return {"id": flow_id, "name": p["name"], "kind": p["kind"],
                    "description": p["description"], "nodes": [dict(n) for n in p["nodes"]]}
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


ADAPTIVE_WORKFLOW_SYSTEM = (
    "你是AI架构师，深入分析用户需求核心，动态拆解输出一组结构化严谨的工序节点流水线。"
    "只输出 JSON，不要任何废话或解释。"
)

ADAPTIVE_WORKFLOW_PROMPT = """深入分析以下业务需求核心，推导项目交付目标与交付物，动态规划输出一组结构化的工序节点流水线：
需求：{text}

只输出纯 JSON，格式如下：
{{
  "name": "工作流名称（如：TikTok美妆出海营销全流程）",
  "kind": "adaptive",
  "summary": "规划思路",
  "nodes": [
    {{"key": "market_intel", "title": "行业情报与竞品抓取", "role": "research", "gate": "数据源可追溯", "kind": "doc"}},
    {{"key": "script_write", "title": "分镜脚本与内容创作", "role": "content", "gate": "脚本结构完整且无极限词", "kind": "doc"}},
    {{"key": "ui_design", "title": "视觉物料与落地页设计", "role": "design", "gate": "物料齐全", "kind": "code"}},
    {{"key": "roi_plan", "title": "投流排期与ROI测算", "role": "data", "gate": "测算逻辑严密", "kind": "doc"}},
    {{"key": "deliver", "title": "总装交付与归档", "role": "platform", "gate": "全套物料归档", "kind": "deliver"}}
  ]
}}
要求：
1. 节点数量自适应（3到6步），工序前后依赖逻辑严密。
2. 最后一个节点强制为 deliver（role='platform', kind='deliver'）。
3. 责任角色 role 应准确对应工种（如 research, content, design, dev, qa, ops, data, quant, audit, product 等）。
"""


def _heuristic_adaptive_workflow(text: str) -> dict:
    """智能自适应启发式拆解器：模型不可用/离线/测试环境时的动态工序分解。

    根据需求中识别出的目标（开发、设计、数据、营销、文案、合规、量化、研究、紧急等）
    动态生成 3 到 6 个逻辑严密的前后工序节点，绝非死板固定的模板。
    """
    low = (text or "").lower()
    raw_head = re.split(r"[，,。；;\n!！?？]", (text or "").strip())[0][:20].strip()
    head = raw_head or "专项任务"

    # 1. 紧急故障类
    if any(w in low for w in ("紧急", "线上故障", "立刻", "马上", "宕机", "救火", "止血", "急单")):
        nodes = [
            _node("triage", "故障定级与影响面评估", "product", "影响范围与止血目标已确认", kind="doc"),
            _node("hotfix", "应急热修与服务止血", "ops", "应急处置生效且指标回升", kind="code"),
            _node("verify", "业务可用性回归验证", "qa", "核心链路回归无异常", kind="code"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·紧急处置全流程"
        summary = "针对突发线上故障，以最快速度止血、定级、热修并完成回归验证"

    # 2. 出海/营销/TikTok/电商类
    elif any(w in low for w in ("tiktok", "出海", "海外", "营销", "带货", "短视频", "投放", "广告", "美妆", "电商", "转化", "爆款")):
        nodes = [
            _node("market_intel", "行业情报与竞品抓取", "research", "数据源可追溯", kind="doc"),
            _node("script_write", "分镜脚本与内容创作", "content", "脚本结构完整且无极限词", kind="doc"),
            _node("ui_design", "视觉物料与落地页设计", "design", "物料齐全", kind="code"),
            _node("roi_plan", "投流排期与ROI测算", "data", "测算逻辑严密", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·出海营销全流程" if ("出海" in low or "tiktok" in low) else f"{head}·营销增长全流程"
        summary = "覆盖全网情报抓取、脚本创意、视觉设计与投流ROI全链路闭环"

    # 3. 量化/高频/多因子/交易策略类
    elif any(w in low for w in ("量化", "高频", "alpha", "多因子", "选股", "回测", "套利", "交易系统", "因子回测", "策略研发")):
        nodes = [
            _node("market_data", "行情数据与因子特征工程", "data", "时间序列清洗完备", kind="doc"),
            _node("strategy_model", "Alpha多因子建模与策略研发", "quant", "多因子策略逻辑闭环", kind="doc"),
            _node("backtest_verify", "历史回测与风险压力测试", "quant", "最大回撤与夏普比率达标", kind="code"),
            _node("risk_audit", "穿透式风控与合规审查", "audit", "风控与合规审查通过", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·量化策略研发流"
        summary = "涵盖行情数据抽取、Alpha因子构建、压力回测与风控审计严谨闭环"

    # 4. 黄金/走势/金融研报/行情分析类
    elif any(w in low for w in ("分析", "走势", "行情", "黄金", "白银", "汇率", "报表", "指标", "评估", "研判", "金子", "金价")):
        nodes = [
            _node("retrieve", "资料检索", "research", "数据源与口径已记录", kind="doc"),
            _node("analyze", "数据分析", "data", "分析结论有数据支撑", kind="doc"),
            _node("author", "报告撰写", "data", "报告结构完整", kind="doc"),
            _node("review", "合规审核", "audit", "审核通过", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·专题研报流"
        summary = "覆盖跨源数据检索、趋势建模、报告撰写与合规审核"

    # 5. 软件/全栈/系统研发类
    elif any(w in low for w in ("开发", "系统", "全栈", "接口", "代码", "功能", "计算器", "爬虫", "重构", "微服务", "应用", "前端", "后端", "web", "app", "脚本", "算账")):
        nodes = [
            _node("arch_design", "系统架构与数据模型设计", "architect", "需求规格与架构锁定", kind="doc"),
        ]
        if any(w in low for w in ("页面", "前端", "ui", "界面", "视觉")):
            nodes.append(_node("ui_design", "交互原型与界面设计", "design", "UI规范锁定", kind="code"))
        nodes.append(_node("build", "核心功能研发与装配", "dev", "代码可运行且测试覆盖", kind="code"))
        nodes.append(_node("test", "自动化质检与测试", "qa", "测试报告通过", kind="code"))
        if not any(w in low for w in ("不用发布", "不上线", "仅内部")):
            nodes.append(_node("release", "容器构建与交付出货", "ops", "发布清单齐全", kind="code", optional=True))
        nodes.append(_node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"))
        name = f"{head}·研发交付流水线"
        summary = "端到端软件工程自适应工序：架构设计、核心研发、质检验收与总装交付"

    # 6. 调研/竞品/行业研究类
    elif any(w in low for w in ("调研", "竞品", "尽调", "选型", "问卷", "走访", "访谈", "市场研究")):
        nodes = [
            _node("scope", "界定调研问题与边界", "product", "范围与核心假设明确", kind="doc"),
            _node("gather", "跨源资料与全网情报搜集", "research", "多源证据链可追溯", kind="doc"),
            _node("verify", "核心数据交叉核验与对比", "data", "关键指标核验一致", kind="doc"),
            _node("review", "调研研报编纂与专家评审", "audit", "研报结论严谨可靠", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·深度调研流"
        summary = "从问题界定、多源情报采集到交叉核验与审核的深度调研工序"

    # 7. 文案/文章/推文创作类
    elif any(w in low for w in ("文案", "文章", "公众号", "推文", "稿子", "软文", "改写", "宣传", "分镜")):
        nodes = [
            _node("topic_intel", "选题策划与素材调研", "research", "素材真实可查", kind="doc"),
            _node("content_write", "文案撰写与母本创作", "content", "文案结构完整且无极限词", kind="doc"),
            _node("copy_audit", "广告法合规与红线复核", "audit", "广告法与红线复核通过", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·内容创作流水线"
        summary = "涵盖选题调研、文案创作与红线合规审核的内容交付闭环"

    # 8. 通用自适应组合工序
    else:
        nodes = [
            _node("scope", "需求梳理与边界界定", "product", "交付边界与目标锁定", kind="doc"),
            _node("research", "资料情报与事实抓取", "research", "背景信息完整", kind="doc"),
            _node("execute", "核心任务执行与产出", "data" if "数" in low else "content", "核心产出满足要求", kind="doc"),
            _node("review", "合规审查与质检把关", "audit", "质量检验合格", kind="doc"),
            _node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"),
        ]
        name = f"{head}·专属工序流"
        summary = "根据需求特征动态编排交付边界、资料情报、核心推进与总装质检工序"

    return {"name": name, "kind": "adaptive", "summary": summary, "nodes": nodes}


def synthesize_adaptive_workflow(conn, text: str) -> dict:
    """AI 架构师需求自适应工作流动态拆解引擎。

    1. 结合任务需求内容，推导项目交付目标与交付物；
    2. 若 M0_INTENT_LLM != "0" 且模型可用（llm.has_key()），调用 llm.complete 让 AI 架构师输出结构化 JSON；
    3. 若模型不可用/离线/测试环境，调用智能自适应启发式拆解器动态生成 3 到 6 个严密前后工序节点；
    4. 确保最后一个节点强制为 deliver (role='platform', kind='deliver')；
    5. 生成 FLOW-ADAPTIVE-{hash} 模板并持久化到 flow_templates 表；
    6. 收集并返回 roles_needed 角色列表。
    """
    ensure(conn)
    from . import llm

    data = None
    how = "adaptive_architect"

    # 检查是否配置了模型调用
    if os.environ.get("M0_INTENT_LLM", "1") != "0" and llm.has_key():
        try:
            raw = llm.complete(
                ADAPTIVE_WORKFLOW_PROMPT.format(text=(text or "")[:800]),
                system=ADAPTIVE_WORKFLOW_SYSTEM,
                max_tokens=600,
                temperature=0.2,
                timeout=30.0,
            )
            parsed = _parse_json(raw)
            if parsed and isinstance(parsed.get("nodes"), list) and len(parsed["nodes"]) >= 2:
                data = parsed
                how = "adaptive_architect"
            elif parsed and float(parsed.get("confidence", 1.0) or 1.0) < 0.6:
                return {
                    "need_clarify": True,
                    "question": CLARIFY_QUESTION,
                    "flow_id": None,
                    "name": "",
                    "kind": "adaptive",
                    "nodes": [],
                    "roles": [],
                    "how": "llm",
                    "reason": str(parsed.get("reason") or "置信度过低"),
                    "task_type": str(parsed.get("task_type") or "unknown"),
                    "confidence": float(parsed.get("confidence", 0.0)),
                }
        except Exception as exc:
            log.info("adaptive workflow llm synthesis failed: %s", exc)
            data = None

    if not data:
        data = _heuristic_adaptive_workflow(text)

    name = str(data.get("name") or "自适应专属流水线")
    summary = str(data.get("summary") or "根据需求动态推导的端到端工序链")
    raw_nodes = data.get("nodes") or []

    # 规范化节点
    nodes = []
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        key = str(node.get("key") or f"stage_{len(nodes)+1}")
        if key == "deliver":
            continue
        title = str(node.get("title") or key)
        role = str(node.get("role") or PLATFORM)
        gate = str(node.get("gate") or f"{title}完成")
        kind = str(node.get("kind") or "doc")
        opt = bool(node.get("optional", False))
        nodes.append(_node(key, title, role, gate, kind=kind, optional=opt))

    # 最后一个节点强制为 deliver (role='platform', kind='deliver')
    nodes.append(_node("deliver", "总装交付与归档", PLATFORM, "全套物料归档", kind="deliver"))

    # 生成确定性模板 ID
    nodes_repr = json.dumps([{k: n[k] for k in ("key", "title", "role", "gate", "kind")} for n in nodes], ensure_ascii=False)
    flow_hash = hashlib.md5(f"{name}:{nodes_repr}".encode("utf-8")).hexdigest()[:10]
    flow_id = f"FLOW-ADAPTIVE-{flow_hash}"

    # 保存到 flow_templates 表
    conn.execute(
        "INSERT OR REPLACE INTO flow_templates (id, name, kind, description, nodes, enabled)"
        " VALUES (?, ?, 'adaptive', ?, ?, 1)",
        (flow_id, name, summary, json.dumps(nodes, ensure_ascii=False))
    )
    conn.commit()

    # 提取所需角色列表
    roles_needed = []
    for n in nodes:
        r = n.get("role")
        if r and r != PLATFORM and r not in roles_needed:
            roles_needed.append(r)

    return {
        "flow_id": flow_id,
        "name": name,
        "kind": "adaptive",
        "summary": summary,
        "nodes": nodes,
        "roles": roles_needed,
        "how": how,
        "reason": summary,
    }


def plan(conn, text: str, tags: list[str] | None = None) -> dict:
    """需求 → 流程规划。统一调用自适应工序拆解引擎，彻底取代死板固定的 4 大预设模板。"""
    ensure(conn)
    # v4.5：优先匹配显式指定或定制商业交付模板
    for cand_t in templates(conn, include_adaptive=True):
        if cand_t["id"] in text or f"【{cand_t['name']}】" in text or f"【{cand_t['id']}】" in text:
            nodes = [dict(n) for n in cand_t["nodes"]]
            roles_needed = []
            for node in nodes:
                if node.get("role") != PLATFORM and node.get("role") not in roles_needed:
                    roles_needed.append(node["role"])
            return {"flow_id": cand_t["id"], "name": cand_t["name"], "kind": cand_t["kind"],
                    "nodes": nodes, "roles": roles_needed, "how": "explicit_template",
                    "reason": f"命中定制商业交付模板：{cand_t['name']}", "task_type": cand_t["kind"],
                    "secondary": "", "confidence": 1.0}

    # 兼容内存只读预设模板显式调用
    for fid, cand_t in PRESETS.items():
        if fid in text or f"【{fid}】" in text or f"【{cand_t['name']}】" in text:
            nodes = [dict(n) for n in cand_t["nodes"]]
            roles_needed = [n["role"] for n in nodes if n.get("role") and n["role"] != PLATFORM]
            return {"flow_id": fid, "name": cand_t["name"], "kind": cand_t["kind"],
                    "nodes": nodes, "roles": roles_needed, "how": "explicit_template",
                    "reason": f"命中兼容预设模板：{cand_t['name']}", "task_type": cand_t["kind"],
                    "secondary": "", "confidence": 1.0}

    low = (text or "").lower()
    if not (text or "").strip():
        return {"need_clarify": True, "question": CLARIFY_QUESTION, "flow_id": None,
                "name": "", "kind": "adaptive", "nodes": [], "roles": [],
                "how": "clarify", "reason": "需求内容为空",
                "task_type": "clarify", "confidence": 0.0}

    # 统一调用需求自适应工序拆解
    try:
        return synthesize_adaptive_workflow(conn, text)
    except Exception as exc:
        log.warning("adaptive workflow synthesis failed, fallback to preset: %s", exc)
        tpl = template(conn, "FLOW-ANALYSIS") or PRESETS["FLOW-ANALYSIS"]
        nodes = [dict(n) for n in tpl["nodes"]]
        roles_needed = [n["role"] for n in nodes if n.get("role") and n["role"] != PLATFORM]
        return {
            "flow_id": "FLOW-ANALYSIS",
            "name": tpl["name"],
            "kind": tpl.get("kind", "analysis"),
            "nodes": nodes,
            "roles": roles_needed,
            "how": "fallback",
            "reason": "自适应拆解异常退避兜底",
        }


def instantiate(conn, pid: str, plan_or_id: dict | str) -> list[dict]:
    """按规划/模板生成项目阶段（幂等：已有阶段就不动）。"""
    ensure(conn)
    if conn.execute("SELECT 1 FROM project_stages WHERE project_id=? LIMIT 1",
                    (pid,)).fetchone():
        return nodes(conn, pid)
    if isinstance(plan_or_id, str):
        if plan_or_id == "adaptive":
            prow = conn.execute("SELECT requirement FROM projects WHERE id=?", (pid,)).fetchone()
            req_text = (prow["requirement"] if prow else "") or ""
            plan_obj = plan(conn, req_text)
            nodes_ = plan_obj["nodes"]
            flow_id = plan_obj["flow_id"]
        else:
            tpl = template(conn, plan_or_id)
            nodes_ = tpl["nodes"] if tpl else PRESETS["FLOW-ANALYSIS"]["nodes"]
            flow_id = plan_or_id
    else:
        nodes_ = plan_or_id["nodes"]
        flow_id = plan_or_id["flow_id"]
    has_wbs = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wbs_items'").fetchone())
    if not has_wbs:
        try:
            from . import pipeline
            pipeline.ensure(conn)
            has_wbs = True
        except Exception:
            pass
    has_wbs_stage = any(n["key"] == "wbs" for n in nodes_)
    for seq, node in enumerate(nodes_):
        conn.execute(
            "INSERT INTO project_stages (id, project_id, seq, key, title, role_tag, node_kind,"
            " gate_name) VALUES (?,?,?,?,?,?,?,?)",
            (f"S-{uuid.uuid4().hex[:12]}", pid, seq, node["key"], node["title"],
             node["role"], node.get("kind") or "doc", node.get("gate") or ""))
        if has_wbs and not has_wbs_stage:
            wbs_code = f"WBS-{seq+1:02d}"
            conn.execute("""INSERT OR IGNORE INTO wbs_items (id, project_id, code, title, role_tag, pack_id, state)
                            VALUES (?,?,?,?,?, 'standard', 'todo')""",
                         (f"WBS-{pid}-{seq+1}", pid, wbs_code, node["title"], node["role"]))
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


# ------------------------------------------------------------ 单阶段预算
def stage_budget(conn) -> int:
    """单阶段 token 预算：settings 表（界面可改）→ 环境变量 → 20000。

    每次开跑都现读，界面上调完下一个阶段立即生效，不用重启服务。
    """
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='flow_stage_budget'"
                           ).fetchone()
        if row is not None and str(row["value"]).strip():
            val = int(float(str(row["value"])))
            if FLOW_STAGE_BUDGET_MIN <= val <= FLOW_STAGE_BUDGET_MAX:
                return val
    except (sqlite3.Error, ValueError, TypeError):
        pass
    try:
        val = int(os.environ.get("M0_FLOW_STAGE_BUDGET", FLOW_STAGE_BUDGET_DEFAULT))
    except (TypeError, ValueError):
        val = FLOW_STAGE_BUDGET_DEFAULT
    return max(FLOW_STAGE_BUDGET_MIN, min(FLOW_STAGE_BUDGET_MAX, val))


def set_stage_budget(conn, value: int) -> int:
    """写入界面可改的预算值（5k–50 万），越界直接拒绝而不是悄悄夹断。"""
    val = int(value)
    if not FLOW_STAGE_BUDGET_MIN <= val <= FLOW_STAGE_BUDGET_MAX:
        raise ValueError("InvalidFlowBudget")
    conn.execute("INSERT INTO settings (key,value) VALUES ('flow_stage_budget',?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(val),))
    conn.commit()
    return val


# -------------------------------------------------------------------- 黑板
def blackboard_write(conn, *, pid: str, stage_key: str, role_tag: str, title: str,
                     content: str, kind: str = "deliverable",
                     file_path: str | None = None) -> str:
    if file_path:
        existing = conn.execute(
            "SELECT id FROM project_artifacts WHERE project_id=? AND file_path=?",
            (pid, file_path)).fetchone()
        if existing:
            return existing["id"]
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
    body = "\r\n".join(lines)
    source = "template"
    if llm_docs:
        try:
            from . import llm
            text = llm.complete(
                "把下面这些阶段交付压成一页中文汇总，保留关键结论与未决风险，不要复述全文：\r\n\r\n"
                + "\r\n\r\n".join(f"## {i['stage_key']} · {i['title']}\r\n{i['excerpt']}" for i in items),
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
                '{"cause":"一句话根因","fixes":["最多3条可执行修复"]}\r\n\r\n' + ctx,
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
    return "\r\n".join([
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
            f"项目 {pid} 的「{node['title']}」阶段。\r\n"
            f"验收口径：{node['gate']}\r\n\r\n原始需求：\r\n{(requirement or '')[:800]}\r\n\r\n"
            "用中文写这一阶段的正式交付文档：结论先行，写清来源/口径/未决问题，"
            "不要复述需求。",
            system=f"你是{node['title']}负责人，只交付与本阶段有关的结论。",
            max_tokens=1200)
        return text.strip() if text and text.strip() else None
    except Exception as exc:                     # noqa: BLE001
        log.info("stage doc llm failed: %s", exc)
        return None
