"""默认 Bot 与会话（幂等种子）。

第一个 Bot 叫 Chief：和 Grok Bot 的"主 Bot"同构 —— 它负责管其他 Bot 和群聊。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

DEFAULT_BOT_ID = "bot_chief"
DEFAULT_SESSION_ID = "ses_chief"

_BUILTIN_APPS = [
    ("wecom", "企业微信", "手机派活、审批与卡片推送", "channel"),
    ("email", "邮件", "草稿审阅后发送，不自动外发", "app"),
    ("browser", "浏览器", "服务器桌面上的网页自动化与截图", "tool"),
    ("mcp", "自定义 MCP", "接入外部工具服务器", "mcp"),
]

_CHIEF_PERSONA = (
    "你是 Chief，用户的首席 AI 助手，负责统筹其他 Bot 和群聊。"
    "说话简短、给结论、给证据；不确定就说不确定。"
    "涉及外发、付款、删除等不可逆动作时先请求批准。"
)

_BUILTIN_SKILLS = [
    (
        "本地通道自检",
        "自检本地电脑连通性、当前前台窗口与屏幕分辨率",
        """【本地通道自检执行标准】
1. 首先调用 local_info 检查当前在线的本地设备信息（核查主机名、当前系统用户名与工作目录）；若失败或报无在线设备，立即停止并明确提示用户打开 AgentOS 桌面端。
2. 调用 local_windows 获取当前桌面可见窗口列表（取前 10 个），核对前台激活窗口（foreground）与主屏幕分辨率。
3. 综合核验结果，按以下结构简要输出：
   - 本地设备：[主机名] / 用户 [用户名]
   - 屏幕状态：[分辨率] / 当前前台窗口 [标题]
   - 通道判定：本地桥双向通信正常，具备文件读写、命令执行与桌面操控能力。""",
    ),
    (
        "系统健康自检",
        "检查例行定时任务健康度、是否有失败任务与下一次调度时间",
        """【系统与定时任务健康自检标准】
1. 调用 list_routines 查看所有已注册的例行任务：
   - 关注是否有 status=failed 的异常任务或报错记录；
   - 核对下次调度时间 next_run_at 是否按计划递增；
   - 检查任务已执行次数 run_count。
2. 若有异常任务，调用相关日志或历史记录排查失败根因。
3. 按结构输出自检报告：
   - 任务总数：[N] 项（启用 [X] / 停用 [Y]）；
   - 健康状况：若有失败，明确指出「任务名、最近报错、建议动作」；若全部正常，列出下一次即将触发的任务和时间点。""",
    ),
    (
        "桌面窗口操控",
        "标准化的本地桌面窗口查找、启动、聚焦置顶与安全键入流程",
        """【桌面窗口安全操控标准流程】
1. 【查找应用】：若用户指定了软件名（如“记事本”、“微信”），先调用 local_apps(query=目标) 获取精准的可执行文件或快捷方式名称；切勿凭空编造不存在的路径。
2. 【启动与聚焦】：
   - 若目标未在运行，调用 local_open(target=应用标识) 启动；
   - 调用 local_focus(title=窗口关键词) 抢夺前台焦点，并核验返回结果中的 foreground_now 是否确实匹配目标窗口；若未成功置顶，绝不盲目输入。
3. 【安全键入/按键】：
   - 输入文字调用 local_type(text=..., title=窗口关键词)；
   - 触发快捷键调用 local_keys(keys=..., title=窗口关键词)；
   - 每一步操作必须附带 title 参数做双重防串焦保护。
4. 【回执留存】：操作完成后，调用 local_screenshot(window_title=窗口关键词) 截取窗口当前状态，留存凭据供用户复核。""",
    ),
    (
        "周报三段式",
        "按 Done / Blockers / Next 三段式规范输出结构化周报",
        """【周报三段式输出标准】
1. 【真实事实收集】：先结合本次会话事实，必要时调用 list_routines 查看定时巡检成果，禁止编造未发生的进展。
2. 【按三段式结构规范输出】：
   ### 一、 本周交付与核心进展（Done）
   - 按业务产出罗列已完成项（每项包括：做了什么 + 验证结果 + 对照指标）。
   ### 二、 遇到的阻碍与风险（Blockers & Risks）
   - 真实陈述未完成项、潜在隐患或技术卡点，并说明当前原因。
   ### 三、 下周计划与决策建议（Next Steps）
   - 列出高优先级的 2~3 个具体动作，附带建议负责人或时间预估。
3. 语言简练客观，结论先行。""",
    ),
    (
        "第一性原理决策分析",
        "基于物理与系统约束的方案对比矩阵与确定性决策输出",
        """【第一性原理决策分析标准】
1. 【明确核心目标与物理/系统约束】：首先界定本次决策的底线指标（性能、可靠性、开发周期、维护成本、安全）。
2. 【方案对比矩阵】：对比至少两种可行路径，按「实现原理 - 核心优势 - 代价与隐患 - 适用场景」四维度列出 Markdown 表格。
3. 【主观结论与推荐路径】：不给模糊模棱两可的回答，明确给出你的首选推荐理由，并给出可落地的第一步行动。""",
    ),
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def ensure(conn: sqlite3.Connection) -> None:
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO bots (id, name, role, persona, model, status, created_at)"
        " VALUES (?,?,?,?,?,'idle',?)",
        (DEFAULT_BOT_ID, "Chief", "chief", _CHIEF_PERSONA, None, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, kind, title, admin_bot_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (DEFAULT_SESSION_ID, "direct", "和 Chief 的对话", DEFAULT_BOT_ID, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO session_members (session_id, member_id, role, joined_at)"
        " VALUES (?,?,?,?)",
        (DEFAULT_SESSION_ID, DEFAULT_BOT_ID, "admin", now),
    )
    for app_id, name, desc, kind in _BUILTIN_APPS:
        conn.execute(
            "INSERT OR IGNORE INTO apps (id, name, description, kind, connected)"
            " VALUES (?,?,?,?,?)",
            (app_id, name, desc, kind, 1 if app_id == "browser" else 0),
        )
    try:
        from .. import skills as _skills
        _skills.ensure(conn)
        for name, desc, body in _BUILTIN_SKILLS:
            if not _skills.exists(conn, bot_id=None, name=name):
                _skills.create(conn, bot_id=None, name=name, description=desc, body=body)
    except Exception as exc:                               # noqa: BLE001
        print(f"[seed] skills seed skipped: {exc}")
