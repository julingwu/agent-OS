"""One sentence -> a task plus the six acceptance criteria of v2 §7."""
from __future__ import annotations

import json
import os
import re
import sqlite3

from ..audit import new_id, write_audit


def _target_words(text: str) -> tuple[int, int]:
    """Read the requested length; honour an explicit range like '400-600 字'."""
    t = text or ""
    rng = re.search(r"(\d+)\s*(?:-|~|—|–|到|至)\s*(\d+)\s*字", t)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    m = re.search(r"(\d+)\s*字", t)
    if not m:
        return 600, 1500                     # v1.5 默认区间
    lo = int(m.group(1))                     # 明确说了字数就照办
    return lo, min(1500, max(lo + 200, int(round(lo * 1.6))))

TEMPLATE = [
    dict(
        criterion="产出文件存在、可读且非空",
        check_type="script_exec",
        check_ref=lambda lo, hi: json.dumps({"script": "file_exists.py", "min_bytes": 100,
                                             "name": ".md"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="正文 {lo}-{hi} 字（CJK 字符 + 英文单词，去空白）",
        check_type="script_exec",
        check_ref=lambda lo, hi: json.dumps({"script": "word_count.py", "min": lo, "max": hi}),
        # v1.5：字数只做提示，不再阻断（600-1500 直接交人工），也不再触发自动扩写
        is_blocking=0, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="覆盖主题关键词（至少 2 组）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: needle,      # needle 这里传关键词 JSON
        # 关键词只是启发式提示，不该一票否决；真正的质量闸是广告法与人工调性
        is_blocking=0, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="不含广告法禁用词（最好/最佳/第一品牌/绝对/100%/根治/治愈…）",
        check_type="rule_auto",
        check_ref=lambda lo, hi: "banned",
        is_blocking=1, evidence_required=1, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="品牌调性：语气亲切、不夸大功效（人工判定，须附原文片段）",
        check_type="human_check",
        check_ref=lambda lo, hi: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
    dict(
        criterion="配图版权合规（M0 未启用）",
        check_type="human_check",
        check_ref=lambda lo, hi: None,
        is_blocking=0, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
        state="na",
    ),
]

# A code task is judged on the artifact being a parseable, on-target script —
# not on word count or brand tone.
TEMPLATE_CODE = [
    dict(
        criterion="产出脚本存在、可读且非空（≥200 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle: json.dumps({"script": "file_exists.py", "min_bytes": 200,
                                                     "name": ".py"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="Python 语法检查通过（py_compile）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle: json.dumps({"script": "py_syntax.py", "name": ".py"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="脚本确实发起了网络请求（urllib / requests / httpx / aiohttp / yfinance 等）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: ("contains:" + needle if needle.startswith("http")
                                          else "contains_any:urllib|requests|httpx|aiohttp|"
                                               "yfinance|akshare|socket"),
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="代码可读、无破坏性操作（人工：无删文件/写系统目录/硬编码密钥）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
    dict(
        criterion="交付说明清楚（顶部注释写明用法与依赖，人工判定）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=0, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
]

TEMPLATE_FINANCE = [
    dict(
        criterion="财务审核单存在且非空（≥200 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle: json.dumps({"script": "file_exists.py", "min_bytes": 200}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="审核单写明科目与金额（含“科目”“金额”字样）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:科目|金额",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="给出合计/结论（含“合计”“总计”“结论”之一）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:合计|总计|结论",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="金额与凭证一致、科目归类正确（人工复核，须附原文片段）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
]

TEMPLATE_REVIEW = [
    dict(
        criterion="评审报告 review.md 存在且非空（≥50 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle: json.dumps(
            {"script": "file_exists.py", "min_bytes": 50, "name": "review.md"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="评审报告给出明确结论（结论 / 通过 / 不通过）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:结论|通过|不通过",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="评审意见具体、可执行（人工：至少一条能落地的修改建议）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
]

REVIEW_HINT = re.compile(r"(审查|评审|review)", re.I)
CODE_HINT = re.compile(r"(脚本|代码|爬取|抓取|下载|解析|python|script|crawl|scrape|\.py\b|开发一个|实现一个|写个|写一个|计算器|工具|程序|函数|算法)", re.I)
URL_RE = re.compile(r"https?://[^\s，。；;\"'）)]+")
WEB_HINT = re.compile(r"(落地页|网页|页面|官网|首页|index\.html|html|前端界面|banner)", re.I)
# A script can itself emit HTML. Generic words such as “代码” or “写一个”
# are insufficient to override a web task; an explicit script/file comes first.
SCRIPT_DELIVERABLE_HINT = re.compile(
    r"脚本|python\s*(?:3(?:\.\d+)?\s*)?(?:script|program|程序)|"
    r"(?<![A-Za-z0-9_/\\.-])[A-Za-z_][A-Za-z0-9_.-]*\.py(?![A-Za-z0-9_])", re.I)


FINANCE_HINT = re.compile(r"(财务|报销|对账|核算|费用|发票|审核单)", re.I)


def task_type_of(text: str) -> str:
    text = text or ""
    if REVIEW_HINT.search(text):
        return "review_write"
    if FINANCE_HINT.search(text):
        return "finance_write"
    web = WEB_HINT.search(text)
    script = SCRIPT_DELIVERABLE_HINT.search(text)
    if script and (web is None or script.start() < web.start()):
        return "code_write"
    if web:
        return "web_write"
    return "code_write" if CODE_HINT.search(text) else "content_write"


TEMPLATE_WEB = [
    dict(
        criterion="产出 HTML 落地页文件、可读且非空（≥2000 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle: json.dumps({"script": "file_exists.py", "min_bytes": 2000,
                                                     "name": ".html"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="页面结构完整（含 <html 根节点）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:<html|<!DOCTYPE|<!doctype",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="自带样式（内联 <style> 或外链 <link）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:<style|<link",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="含可点击的行动按钮（<button / 立即购买 / 抢购）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "contains_any:<button|立即|抢购|购买",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="不含广告法禁用词（最好/最佳/第一品牌/绝对/100%/根治/治愈…）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle: "banned",
        is_blocking=1, evidence_required=1, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="页面视觉与文案质量（人工判定，须附页面片段）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
    dict(
        criterion="移动端适配检查（M0 未启用）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle: None,
        is_blocking=0, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
        state="na",
    ),
]

TEMPLATE_RESEARCH = [
    dict(
        criterion="调研/文稿交付物存在、可读且非空（≥200 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle=None: json.dumps({"script": "file_exists.py", "min_bytes": 200, "name": ".md"}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="文档结构完整（包含一级或二级结构化标题 # 或 ##）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle=None: json.dumps({"script": "markdown_structure.py", "min_bytes": 200}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="不含广告法禁用词（最好/最佳/第一品牌/绝对/100%/根治/治愈…）",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle=None: "banned",
        is_blocking=1, evidence_required=1, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="调研结论详实、事实逻辑严密（人工判定，须附原文片段）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle=None: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
]

TEMPLATE_ANALYST = [
    dict(
        criterion="数据分析报告存在且非空（≥200 字节）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle=None: json.dumps({"script": "file_exists.py", "min_bytes": 200}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="数据结构规范（有效 JSON/CSV 或包含数据表格与数值分析结论）",
        check_type="script_exec",
        check_ref=lambda lo, hi, needle=None: json.dumps({"script": "data_structure.py", "min_bytes": 200}),
        is_blocking=1, evidence_required=1, acceptance_layer="L2", is_deterministic=1,
    ),
    dict(
        criterion="数据分析结论明确且有数据论据支撑",
        check_type="rule_auto",
        check_ref=lambda lo, hi, needle=None: "contains_any:分析|结论|数据|指标|趋势|比例|汇总|同比|环比",
        is_blocking=1, evidence_required=0, acceptance_layer="L0", is_deterministic=1,
    ),
    dict(
        criterion="指标口径统一、数值具备业务可解释性（人工复核）",
        check_type="human_check",
        check_ref=lambda lo, hi, needle=None: None,
        is_blocking=1, evidence_required=1, acceptance_layer="L1", is_deterministic=0,
    ),
]

# 本地脚本（不联网）：不应该拿「必须发网络请求」去卡它 —— FizzBuzz 也会被判 failed。
TEMPLATE_CODE_LOCAL = [t for t in TEMPLATE_CODE if "网络请求" not in t["criterion"]]

TEMPLATE_BY_KEY = {
    "content": TEMPLATE,
    "code": TEMPLATE_CODE,
    "code_local": TEMPLATE_CODE_LOCAL,
    "review": TEMPLATE_REVIEW,
    "finance": TEMPLATE_FINANCE,
    "web": TEMPLATE_WEB,
    "research": TEMPLATE_RESEARCH,
    "analyst": TEMPLATE_ANALYST,
    "data": TEMPLATE_ANALYST,
}

NET_WORDS = ("抓取", "爬虫", "下载", "接口", "api", "请求", "汇率", "股价", "行情", "http", "url")

# 主题词表：criterion 现在按题材取词组，不再写死护肤（商单题材一变就全挂）
KEYWORD_DOMAINS = [
    (("护肤", "保湿", "修护", "精华", "面霜", "敏感肌"),
     [["保湿", "补水", "水润"], ["修护", "屏障", "修复"], ["抗老", "抗衰"], ["敏感肌", "舒缓"]]),
    (("大模型", "人工智能", "ai", "智能体", "降本", "增效", "数字化", "算法"),
     [["大模型", "模型", "ai"], ["降本", "成本", "增效", "效率"], ["案例", "实践", "落地"]]),
    (("公众号", "宣传", "文案", "推文", "软文", "营销", "推广"),
     [["宣传", "推广", "营销"], ["公众号", "文章", "推文"], ["案例", "数据", "效果"]]),
    (("双十一", "促销", "电商", "带货", "直播"),
     [["双十一", "大促", "促销"], ["转化", "下单", "成交"], ["预算", "投放", "roi"]]),
]


def keyword_groups(text: str) -> list[list[str]]:
    low = (text or "").lower()
    for words, groups in KEYWORD_DOMAINS:
        if any(w in low for w in words):
            return groups
    return [["说明", "方案"], ["步骤", "流程"], ["结果", "交付"]]


# v5.3 TASK-4.1：role_tag → (template, task_type, pack) 的推断表。
# 只在调用方**没有**声明 pack_id（或声明的 pack 解析不到）时才使用。
ROLE_PACKS = {
    "research": (TEMPLATE_RESEARCH, "research_write", "research-v1"),
    "researcher": (TEMPLATE_RESEARCH, "research_write", "research-v1"),
    "data": (TEMPLATE_ANALYST, "analyst_write", "analyst-v1"),
    "analyst": (TEMPLATE_ANALYST, "analyst_write", "analyst-v1"),
    "content": (TEMPLATE_RESEARCH, "content_write", "content-v1"),
    "product": (TEMPLATE_RESEARCH, "content_write", "content-v1"),
    "dev": (TEMPLATE_CODE_LOCAL, "code_write", "code-local-v1"),
    "ops": (TEMPLATE_CODE_LOCAL, "code_write", "code-local-v1"),
    "qa": (TEMPLATE_REVIEW, "review_write", "review-v1"),
    "audit": (TEMPLATE_REVIEW, "review_write", "review-v1"),
    "legal": (TEMPLATE_REVIEW, "review_write", "review-v1"),
}


def _pack_by_id(conn: sqlite3.Connection, pack_id: str | None):
    """显式声明的 pack 解析：别名或 packs 表 active 记录命中才算数，否则 None。"""
    if not pack_id:
        return None
    if pack_id in ("research-v1", "research"):
        return TEMPLATE_RESEARCH, "research_write", pack_id
    if pack_id in ("analyst-v1", "data-v1"):
        return TEMPLATE_ANALYST, "analyst_write", pack_id
    prow = conn.execute("SELECT * FROM packs WHERE id = ? AND state = 'active'",
                        (pack_id,)).fetchone()
    if prow is None:
        return None
    # code-local-v1 与 code-v1 共用 template='code'，但判据不同：本地脚本不查联网
    template = (TEMPLATE_CODE_LOCAL if pack_id == "code-local-v1"
                else TEMPLATE_BY_KEY[prow["template"]])
    return template, f"{prow['template']}_write", pack_id


def generate(conn: sqlite3.Connection, text: str, legal_entity_id: str = "LE-GROUP-HQ",
             pack_id: str | None = None, tenant_id: str | None = None,
             role_tag: str | None = None) -> dict:
    from ..tenancy import checked_entity_tenant
    tenant_id = checked_entity_tenant(conn, legal_entity_id, tenant_id)
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    lo, hi = _target_words(text)
    ttype = task_type_of(text)
    # v5.3 TASK-4.1：优先级 = 显式 pack_id（解析成功）> role_tag 推断 > 文本启发式。
    # 旧顺序反了：pipeline.execute_build 同时传 pack_id=web-v1 与 role_tag=dev，
    # role 分支抢先命中，把「页面交付」判成了「python 脚本交付」（py_syntax 误杀）。
    resolved = _pack_by_id(conn, pack_id)
    if resolved is not None:
        template, ttype, pack = resolved
        tpl_override, pack_source = True, "explicit_pack"
    elif role_tag and role_tag.lower() in ROLE_PACKS:
        template, ttype, pack = ROLE_PACKS[role_tag.lower()]
        tpl_override, pack_source = True, "role_tag"
    else:
        tpl_override, pack_source = False, "heuristic"
    if not tpl_override and pack_id:
        # 显式声明了却解析不到，且 role_tag 也给不出替代：保持旧的严格报错。
        raise ValueError(f"unknown pack {pack_id}")
    if not tpl_override:
        template = {"code_write": TEMPLATE_CODE, "review_write": TEMPLATE_REVIEW,
                    "finance_write": TEMPLATE_FINANCE,
                    "web_write": TEMPLATE_WEB}.get(ttype, TEMPLATE)
        pack = {"code_write": "code-v1", "review_write": "review-v1",
                "finance_write": "finance-v1", "web_write": "web-v1"}.get(ttype, "content-v1")
        if ttype == "code_write" and not any(w in text.lower() for w in NET_WORDS):
            template = TEMPLATE_CODE_LOCAL
            pack = "code-local-v1"
    needle = (URL_RE.search(text).group(0) if URL_RE.search(text) else "requests")
    if ttype == "content_write":
        # 内容类：把主题关键词组随判据一起存下来，验收时按这篇的题材判
        needle = "keywords_json:" + json.dumps(
            {"n": 2, "groups": keyword_groups(text)}, ensure_ascii=False)
    task_id = new_id("T")
    conn.execute(
        """INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title, state, pack_id)
           VALUES (?,?,?,?,?, 'todo', ?)""",
        (task_id, legal_entity_id, tenant_id, ttype, text[:120], pack),
    )
    created = []
    for seq, tpl in enumerate(template, start=1):
        cid = new_id("C")
        try:
            ref = tpl["check_ref"](lo, hi, needle)
        except TypeError:
            ref = tpl["check_ref"](lo, hi)
        conn.execute(
            """INSERT INTO acceptance_criteria
               (id, legal_entity_id, task_id, criterion, check_type, check_ref, is_blocking,
                evidence_required, acceptance_layer, state, is_deterministic, seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, legal_entity_id, task_id,
             tpl["criterion"].format(lo=lo, hi=hi, needle=needle),
             tpl["check_type"], ref, tpl["is_blocking"],
             tpl["evidence_required"], tpl["acceptance_layer"], tpl.get("state", "pending"),
             tpl["is_deterministic"], seq),
        )
        created.append(cid)
    write_audit(
        conn,
        legal_entity_id=legal_entity_id,
        verb="task_created",
        task_id=task_id,
        layer="system",
        action="create_task",
        target=task_id,
        payload={"text": text, "word_range": [lo, hi], "task_type": ttype, "needle": needle,
                 "pack_id": pack, "pack_source": pack_source, "role_tag": role_tag},
        response={"criteria_count": len(created), "pack": pack},
    )
    return {
        "task_id": task_id,
        "state": "todo",
        "title": text[:120],
        "task_type": ttype,
        "pack_id": pack,
        "pack_source": pack_source,
        "word_range": [lo, hi],
        "criteria": criteria_of(conn, task_id),
    }


def criteria_of(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id = ? ORDER BY seq", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def budget_limit() -> int:
    return int(os.environ.get("M0_BUDGET_LIMIT_TOKENS", "500000"))
