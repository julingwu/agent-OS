"""产线文档的模型生成层（v1.6）。

每个函数先生成 prompt，调用 `llm.complete`；失败返回 None，调用方回落模板。
产物都带「由 deepseek-chat 生成 · 时间」脚注，方便审计追溯。
"""
from __future__ import annotations

import datetime
import json

from . import llm
from .role_alignment import boundary_for

FOOT = "\n\n---\n_由 {model} 生成 · {ts} · 项目 {pid}_"


def _foot(pid: str) -> str:
    return FOOT.format(model=llm.MODEL,
                       ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                       pid=pid)


def intake(pid: str, requirement: str) -> str | None:
    text = llm.complete(
        f"""你是数字集团的项目助理。把下面这条需求整理成《需求登记》，中文 Markdown：
需求原文：{requirement}
要求包含：1) 一句话目标 2) 目标用户 3) 可衡量指标(≥3 条) 4) 明确不做什么 5) 待澄清问题(≥3 条)。
不要空话，每条都要具体。""",
        system="你写的是给管理层看的需求文档，简洁、具体、可验收。")
    return (text + _foot(pid)) if text else None


def charter(pid: str, requirement: str) -> str | None:
    text = llm.complete(
        f"""为项目写《立项书》（中文 Markdown，900-1400 字）：
需求：{requirement}

必须包含这些章节，内容要具体到能执行：
## 背景与问题
## 目标与成功指标（可量化）
## 范围（做什么 / 不做什么）
## 方案要点（技术路线 2-3 条并给取舍理由）
## 里程碑与排期（按 立项→研讨→拆解→实现→测试→验收→发布→复盘）
## 预算与资源
## 风险与应对（≥5 条，每条给触发条件与应对动作）
## 否决点（什么情况下必须停）""",
        system="你是资深产品负责人（CPO），写过大量上线项目的立项书。不要模板套话。",
        max_tokens=2400)
    return (text + _foot(pid)) if text else None


def valid_statement(data) -> bool:
    """A veto can be cleared only by a complete, explicit reviewer decision."""
    return (isinstance(data, dict)
            and all(isinstance(data.get(key), str) and data[key].strip()
                    for key in ("question", "risk", "stance"))
            and data["stance"] in {"同意", "有条件同意", "反对"})


def workshop_statement(pid: str, requirement: str, role: str, title: str) -> dict | None:
    """让某个角色给出质询与风险（JSON）。"""
    raw = llm.complete(
        f"""项目需求：{requirement}
你是{title}（角色 {role}），参加立项评审会。请只输出 JSON：
{{"question": "一句尖锐的质询（针对方案/预算/边界）",
  "risk": "一条具体风险（含触发条件与后果）",
  "stance": "同意" 或 "有条件同意" 或 "反对"}}
要求：质询要具体到能被回答，风险要能被验证。""",
        system="你是严格的评审专家，不说客套话。\n" + boundary_for([role]),
        max_tokens=300, temperature=0.6)
    if not raw:
        return None
    try:
        import re
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception:                          # noqa: BLE001
        return None
    if not valid_statement(data):
        return None
    data["role"], data["title"] = role, title
    return data


def minutes(pid: str, requirement: str, statements: list[dict]) -> str | None:
    body = "\n".join(f"- {s['title']}：质询「{s['question']}」；风险「{s['risk']}」；立场：{s['stance']}"
                     for s in statements)
    text = llm.complete(
        f"""把下面这场评审会整理成《研讨会纪要》（中文 Markdown，600-1000 字）：
项目：{requirement}

各角色发言：
{body}

结构：
# 研讨会纪要
## 议题与背景
## 各方质询（逐条保留，不要合并）
## 风险清单（表格：风险 / 触发条件 / 影响 / 应对 / 责任角色）
## 修订结论（方案改了什么）
## 表决结果（逐角色：同意/有条件同意/反对 + 条件）
## 未决事项""",
        system="你是会议记录员，忠实记录，不美化分歧。", max_tokens=1800)
    return (text + _foot(pid)) if text else None


def wbs(pid: str, requirement: str, codes: list[tuple[str, str, str]]) -> str | None:
    body = "\n".join(f"- {c}｜{t}｜角色 {r}" for c, t, r in codes)
    text = llm.complete(
        f"""为下面项目写《WBS 工作分解》（中文 Markdown）：
需求：{requirement}
固定任务（不要增删编号）：
{body}

要求：
# WBS 工作分解
## 拆解原则（为什么这样切）
## 任务表（编号 / 任务 / 角色 / 交付物 / 验收标准 / 预估工作量）
## 依赖与 DAG（说明谁必须等谁，画 ASCII 图）
## 关键路径与风险缓冲
## 排期（按天，给出起止）""",
        system="你是资深项目经理，WBS 要能直接派活。", max_tokens=1600)
    return (text + _foot(pid)) if text else None


def release(pid: str, requirement: str, artifacts: list[str]) -> str | None:
    text = llm.complete(
        f"""为项目写《发布清单》（中文 Markdown）：
需求：{requirement}
已有产物：{", ".join(artifacts) or "代码、测试记录"}

要求：
# 发布清单
## 上线前检查（勾选框列表，≥10 条，含配置/密钥/数据备份）
## 灰度与回滚方案（分步骤，含判定阈值）
## 监控与告警（指标 + 阈值 + 处理人）
## 用户通知文案（一句话 + 一段）
## 发布后 24h 观察项""",
        system="你是 SRE，清单必须可勾选、可回滚。", max_tokens=1500)
    return (text + _foot(pid)) if text else None


def retro(pid: str, requirement: str, facts: dict) -> str | None:
    text = llm.complete(
        f"""为项目写《复盘报告》（中文 Markdown，700-1100 字）：
需求：{requirement}
过程事实：{json.dumps(facts, ensure_ascii=False)}

结构：
# 复盘
## 结果对照（目标 vs 实际）
## 做对了什么（≤3 条，给证据）
## 出了什么问题（≤3 条，给根因，不要归咎个人）
## 改进行动（每条：动作 / 负责人 / 截止 / 验证方式）
## 可复用的经验（写进流程的哪一步）""",
        system="你是务实的复盘主持人，只写能落地的改进。", max_tokens=1600)
    return (text + _foot(pid)) if text else None
