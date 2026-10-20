"""Business-language error mapping (v3.1).

The boss should never see `GateNotPassed` or `build failed: 1 task(s)`.
Keep the machine codes in the audit/API, translate only at the edge.
"""
from __future__ import annotations

import re

MESSAGES = {
    "GateNotPassed": "审批未通过：项目已挂起，等待您的决策",
    "StageSkipped": "阶段顺序未满足：请先完成上一阶段",
    "build failed": "代码构建失败：请查看下方执行日志进行修复",
    "budget_exhausted_midturn": "预算不足：本次执行已中止，可以一键重试",
    "acp_timeout": "执行超时：任务已中止，可以一键重试",
    "acp_error": "执行异常：请查看执行日志，或一键重试",
    "tool_policy_violation": "权限校验未通过：操作已被安全策略拦截",
    "NotRetryable": "当前状态不能重试",
    "TaskNotFound": "任务不存在或无权访问",
    "ProjectNotFound": "项目不存在或无权访问",
    "RunNotFound": "运行记录不存在或无权访问",
    "TenantMismatch": "租户上下文不匹配，请重新登录",
    "EvalRejected": "评测未启动：请检查预算或基准用例",
    "NotLoggedIn": "登录已失效，请重新登录",
    "BadCredentials": "身份 ID 或密钥不正确",
    "EmptyMessage": "消息不能为空",
    "UnknownAction": "这个操作暂不支持",
    "BadAction": "操作参数不正确",
    "FileNotFound": "文件不存在或已被清理",
    "ChatNotFound": "会话不存在或无权访问",
    "McpServerNotFound": "MCP 工具不存在",
    "EmployeeNotFound": "员工不存在",
}

# Longest keys first so "build failed: 1 task(s)" gets the whole phrase replaced.
_KEYS = sorted(MESSAGES, key=len, reverse=True)


def humanize(value) -> str:
    text = str(value or "")
    for key in _KEYS:
        if key in text:
            text = text.replace(key, MESSAGES[key])
    return text


def human_error(exc: Exception, fallback: str = "执行失败：请查看执行日志") -> str:
    raw = str(exc or "")
    m = re.search(r"build failed:\s*(\d+)\s*task", raw)
    if m:
        return f"代码构建失败：请查看执行日志进行修复（失败 {m.group(1)} 个任务）"
    text = humanize(exc)
    if text and text != str(exc):
        return text
    raw = str(exc or "").strip()
    if not raw:
        return fallback
    if "timeout" in raw.lower():
        return MESSAGES["acp_timeout"]
    return f"{fallback}（{raw[:180]}）"
