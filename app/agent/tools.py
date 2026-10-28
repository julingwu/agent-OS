"""Agent 可调用的工具（v1：只做"这台电脑上真实能干的事"）。

安全边界：
  * 文件工具一律锁在 M0_WORKSPACE 里（路径逃逸直接拒绝）；
  * exec 以服务进程身份运行，默认 60s 超时、输出截断；
  * 高危命令模式直接拒绝（审批流在后续版本接入）。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

from ..computer import screen

WORKSPACE = pathlib.Path(os.environ.get("M0_WORKSPACE") or "/opt/agent-os/workspace")

MAX_OUTPUT = 8000
BLOCKED = ("rm -rf /", "mkfs", "dd if=/dev/zero of=/dev/", ":(){ :|:& };:",
           "shutdown", "reboot", "> /dev/sda")


class ToolError(RuntimeError):
    pass


def _jail(path: str | None) -> pathlib.Path:
    root = WORKSPACE.resolve()
    target = (root / (path or ".")).resolve()
    if target != root and root not in target.parents:
        raise ToolError(f"path escapes workspace: {path}")
    return target


def _run_exec(cmd: str, cwd: str | None = None, timeout_s: int = 180) -> dict:
    if not cmd.strip():
        raise ToolError("empty command")
    low = cmd.lower()
    for bad in BLOCKED:
        if bad in low:
            raise ToolError(f"blocked dangerous command pattern: {bad!r}")
    workdir = _jail(cwd)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(workdir),
                              capture_output=True, text=True,
                              timeout=max(1, min(int(timeout_s), 900)))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout_s}s"}
    stdout = (proc.stdout or "")[:MAX_OUTPUT]
    stderr = (proc.stderr or "")[:MAX_OUTPUT]
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": stdout, "stderr": stderr}


def _fs_list(path: str | None = None) -> dict:
    target = _jail(path)
    if not target.exists():
        return {"ok": False, "error": "not found"}
    if target.is_file():
        return {"ok": True, "type": "file", "path": str(target.relative_to(WORKSPACE)),
                "bytes": target.stat().st_size}
    items = []
    for child in sorted(target.iterdir())[:200]:
        items.append({"name": child.name,
                      "type": "dir" if child.is_dir() else "file",
                      "bytes": 0 if child.is_dir() else child.stat().st_size})
    return {"ok": True, "type": "dir", "path": str(target.relative_to(WORKSPACE) or "."),
            "items": items}


def _fs_read(path: str, max_bytes: int = 200_000) -> dict:
    target = _jail(path)
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    data = target.read_bytes()[:max(1, min(int(max_bytes), 1_000_000))]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "binary file (not utf-8)"}
    return {"ok": True, "path": str(target.relative_to(WORKSPACE)), "content": text}


def _fs_write(path: str, content: str) -> dict:
    target = _jail(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(target.relative_to(WORKSPACE)),
            "bytes": len(content.encode("utf-8"))}


def _computer_info() -> dict:
    return screen.probe()


def _ask_human(question: str, options: list[str] | None = None) -> dict:
    return {"status": "waiting_for_human", "question": question,
            "options": options or []}


def dispatch(name: str, args: dict, *, emit=None, bot_id: str | None = None,
             session_id: str | None = None) -> dict:
    """执行一次工具调用。未知工具抛 ToolError（由 loop 变成 tool.result 失败）。

    `emit(kind, payload)` 由 Agent 循环注入，本地电脑工具用它发审批卡。
    `bot_id` 用于"记忆"这类必须知道自己是谁的工具。
    `session_id` 用于会话级免审授权判定。
    """
    args = args or {}
    if name == "exec":
        return _run_exec(str(args.get("cmd", "")), args.get("cwd"), args.get("timeout_s", 180))
    if name == "fs_list":
        return _fs_list(args.get("path"))
    if name == "fs_read":
        return _fs_read(str(args.get("path", "")), args.get("max_bytes", 200_000))
    if name == "fs_write":
        return _fs_write(str(args.get("path", "")), str(args.get("content", "")))
    if name == "computer_info":
        return _computer_info()
    if name == "ask_human":
        return _ask_human(str(args.get("question", "")), args.get("options"))
    if name == "list_skills":
        return _list_skills(bot_id)
    if name == "use_skill":
        return _use_skill(bot_id, str(args.get("name") or "").strip(),
                          args.get("input"))
    if name == "list_routines":
        return _list_routines(bot_id)
    if name.startswith("local_"):
        return _local_tool(name, args, emit, session_id=session_id)
    if name == "remember":
        return _remember(bot_id, str(args.get("content", "")),
                         str(args.get("kind") or "fact"), args.get("importance", 1),
                         str(args.get("topic") or ""))
    if name == "recall":
        return _recall(bot_id, str(args.get("query", "")), int(args.get("limit") or 8))
    raise ToolError(f"unknown tool: {name}")


# ---------------------------------------------------------------- 长期记忆
def _remember(bot_id: str | None, content: str, kind: str, importance,
              topic: str = "") -> dict:
    from .. import bot_memory, db

    if not bot_id:
        return {"ok": False, "error": "缺少 bot 上下文，无法写入记忆"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "content 不能为空"}
    try:
        weight = int(importance or 1)
    except (TypeError, ValueError):
        weight = 1
    with db.get_conn() as conn:
        saved = bot_memory.add(conn, bot_id, content, kind=kind, importance=weight,
                               topic=topic)
        if saved is None:
            return {"ok": True, "saved": False,
                    "note": "这条记忆已存在（或同主题已有更新的值），未重复写入"}
        return {"ok": True, "saved": True, "memory_id": saved["id"],
                "content": saved["content"], "kind": saved["kind"]}


def _recall(bot_id: str | None, query: str, limit: int) -> dict:
    from .. import bot_memory, db

    if not bot_id:
        return {"ok": False, "error": "缺少 bot 上下文，无法检索记忆"}
    with db.get_conn() as conn:
        items = bot_memory.search(conn, bot_id, query, limit=max(1, min(limit, 20)))
    return {"ok": True, "count": len(items),
            "items": [{"content": i["content"], "kind": i["kind"],
                       "importance": i["importance"], "created_at": i["created_at"]}
                      for i in items]}


# ---------------------------------------------------------------- 技能 / 例行任务
def _list_skills(bot_id: str | None) -> dict:
    from .. import db, skills

    with db.get_conn() as conn:
        items = skills.list_for(conn, bot_id=bot_id)
    return {"ok": True, "count": len(items),
            "items": [{"name": i["name"], "description": i["description"],
                       "owner": "本 Bot" if i.get("bot_id") else "全局",
                       "use_count": i["use_count"]} for i in items],
            "hint": "要按某个技能干活时调用 use_skill(name=...)"}


def _use_skill(bot_id: str | None, name: str, user_input: str | None) -> dict:
    from .. import db, skills

    with db.get_conn() as conn:
        skill = skills.find_by_name(conn, name, bot_id=bot_id)
        if skill is None:
            available = [s["name"] for s in skills.list_for(conn, bot_id=bot_id)]
            return {"ok": False, "error": f"没有找到技能「{name}」",
                    "available": available[:20],
                    "hint": "先 list_skills 看有哪些可用"}
        body = skills.render(skill, user_input)
        skill_id = skill["id"]
    skills.bump_use(skill_id)
    return {"ok": True, "skill": skill["name"],
            "instructions": body,
            "hint": "按上面的指令执行，不要把这些指令原样复述给用户"}


def _list_routines(bot_id: str | None) -> dict:
    from .. import db, routines

    with db.get_conn() as conn:
        items = routines.list_routines(conn, bot_id=bot_id)
    return {"ok": True, "count": len(items),
            "items": [{"title": i["title"], "schedule": i["schedule_human"],
                       "enabled": i["enabled"], "next_run_at": i["next_run_at"],
                       "last_run_at": i["last_run_at"], "run_count": i["run_count"]}
                      for i in items]}


# ---------------------------------------------------------------- 本地电脑工具
def _local_policy() -> tuple[bool, bool]:
    """读设置：是否允许本地能力、是否需要逐次审批。"""
    import json

    from .. import db

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value_json FROM app_settings"
                " WHERE key IN ('local_enabled','local_approval')").fetchall()
        values = {r["key"]: json.loads(r["value_json"]) for r in rows}
    except Exception:  # noqa: BLE001 - 读不到设置按关闭处理
        return False, True
    return bool(values.get("local_enabled", False)), bool(values.get("local_approval", True))


# 本地工具的人话名 + 风险等级（审批卡上直接显示）
_LOCAL_RISK: dict[str, tuple[str, str]] = {
    "local_info": ("读取本机信息", "low"),
    "local_list": ("列出本机目录", "low"),
    "local_read": ("读取本机文件", "low"),
    "local_write": ("写入本机文件", "high"),
    "local_exec": ("在本机执行命令", "high"),
    "local_apps": ("查找本机应用", "low"),
    "local_windows": ("查看本机窗口列表", "low"),
    "local_open": ("在你电脑上打开应用/文件", "medium"),
    "local_focus": ("切换窗口到前台", "medium"),
    "local_type": ("在你的键盘上输入文字", "high"),
    "local_keys": ("在你的键盘上按键", "high"),
    "local_click": ("在你的屏幕上点击", "high"),
    "local_close": ("关闭本机窗口", "medium"),
    "local_screenshot": ("截取本机屏幕", "low"),
}


def _local_tool(name: str, args: dict, emit, session_id: str | None = None) -> dict:
    from .. import local_bridge

    enabled, need_approval = _local_policy()
    if not enabled:
        return {"ok": False,
                "error": "本地电脑能力未开启（设置 → 通用 → 允许访问本机）"}
    # 目录白名单：配置了就在服务端拦，越界连审批都不弹（策略必须收在可审计的一侧）
    from .. import local_policy

    path_field = local_policy._PATH_TOOLS.get(name)
    if path_field:
        allowed, reason = local_policy.check_path(name, args.get(path_field))
        if not allowed:
            return {"ok": False, "error": reason}
    device_id = local_bridge.first_device()
    if not device_id:
        return {"ok": False,
                "error": "没有在线的本地电脑：打开 AgentOS 桌面端并登录后会自动连接"}
    if need_approval:
        if session_id and local_bridge.is_session_approved(session_id):
            allowed = True
        else:
            label, risk = _LOCAL_RISK.get(name, (name, "medium"))
            # 摘要说人话 + 结构化的"将要发生什么"，让用户能判断，而不是盲点允许
            detail = local_policy.describe(name, args)[:600]
            preview = local_policy.build_preview(name, args)
            try:
                allowed = local_bridge.request_approval(
                    emit, action=label, detail=detail, risk=risk, preview=preview,
                    session_id=session_id)
            except TypeError:
                allowed = local_bridge.request_approval(
                    emit, action=label, detail=detail, risk=risk, preview=preview)
        if not allowed:
            return {"ok": False, "error": "用户拒绝了这次本地操作"}
    result = local_bridge.call(device_id, name, args)
    # 图片/二进制不上事件（8KB 上限），只发引用；界面按 url 拉取
    if isinstance(result, dict):
        for art in result.pop("_artifacts", None) or []:
            try:
                emit("artifact.attached", {
                    "artifact_id": art.get("id", ""),
                    "name": art.get("name") or "截图",
                    "kind": art.get("kind") or "image/png",
                    "bytes": int(art.get("bytes") or 0),
                    "url": art.get("url") or f"/v1/artifacts/{art.get('id', '')}",
                })
            except Exception:                        # noqa: BLE001 - 事件失败不该吞掉工具结果
                pass
    return result



# ---------------------------------------------------------------- OpenAI 工具 schema
TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "exec",
        "description": "在这台 Linux 服务器上执行 shell 命令，工作目录默认在 Bot 工作区。"
                       "用于跑脚本、装包、查看系统状态。返回 exit_code/stdout/stderr。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string", "description": "要执行的命令"},
            "cwd": {"type": "string", "description": "相对工作区的目录，可选"},
            "timeout_s": {"type": "integer", "description": "超时秒数，默认 60"},
        }, "required": ["cmd"]},
    }},
    {"type": "function", "function": {
        "name": "fs_list",
        "description": "列出 Bot 工作区里的文件或目录。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的路径，缺省为根"},
        }},
    }},
    {"type": "function", "function": {
        "name": "fs_read",
        "description": "读取工作区里的文本文件。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "fs_write",
        "description": "写入（覆盖）工作区里的文本文件，目录会自动创建。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "computer_info",
        "description": "查看当前电脑（桌面）状态：分辨率、采集是否正常。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "ask_human",
        "description": "需要用户做决定或提供信息时使用。调用后本轮结束，等用户回答。",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
        }, "required": ["question"]},
    }},
    # ---------------- 长期记忆 ----------------
    {"type": "function", "function": {
        "name": "remember",
        "description": "把一条值得长期记住的信息写进你自己的记忆（只属于你，不共享给其他 Bot）。"
                       "适合：用户的稳定偏好、项目事实、已拍板的决定。不要记寒暄和一次性细节。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "一句话，能脱离上下文看懂"},
            "topic": {"type": "string",
                      "enum": ["公司", "产品", "客户", "成员", "偏好", "回答风格",
                               "技术栈", "服务器", "流程", "其它"],
                      "description": "槽位，同槽位新值覆盖旧值"},
            "kind": {"type": "string", "enum": ["fact", "preference", "decision", "note"]},
            "importance": {"type": "integer", "description": "1 一般 / 2 重要 / 3 很重要"},
        }, "required": ["content"]},
    }},
    {"type": "function", "function": {
        "name": "recall",
        "description": "检索你自己的长期记忆。回答用户问题前不确定背景时先查一下。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        }, "required": ["query"]},
    }},
    # ---------------- 本地电脑（需要 exe 在线 + 用户审批） ----------------
    {"type": "function", "function": {
        "name": "local_info",
        "description": "查看用户本地电脑的系统信息（主机名、系统版本、用户名、家目录）。"
                       "本地能力需要在设置里开启。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "local_list",
        "description": "列出用户本地电脑某个目录的内容（只读）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径，如 C:\\\\Users\\\\me\\\\Documents"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "local_read",
        "description": "读取用户本地电脑上的一个文本文件（只读，最多 200KB）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "local_write",
        "description": "在用户本地电脑上写文件（会覆盖）。需要用户审批。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "local_exec",
        "description": "在用户本地电脑上执行命令（Windows 用 cmd /C，其他用 sh -c）。"
                       "需要用户审批，默认 60 秒超时。启动 GUI 程序请用 local_open，别用这个。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_s": {"type": "integer"},
        }, "required": ["cmd"]},
    }},
    # ---------------- 本地桌面操控（exe 提供，逐次审批） ----------------
    {"type": "function", "function": {
        "name": "local_apps",
        "description": "在用户电脑的开始菜单和桌面快捷方式里按名字找应用（支持中文名，如 微信、记事本）。"
                       "要打开应用前先用它拿到准确路径。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "应用名关键词，留空则列常用应用"},
            "limit": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "local_open",
        "description": "在用户电脑上打开应用、文件或网址（等价于双击）。可传 local_apps 拿到的 path，"
                       "也可直接传应用名（如 记事本）。返回是否成功以及当前前台窗口。",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "应用名 / 文件绝对路径 / URL"},
        }, "required": ["target"]},
    }},
    {"type": "function", "function": {
        "name": "local_windows",
        "description": "列出用户电脑上当前可见的窗口（标题、进程名、是否前台）。"
                       "用它确认应用真的打开了，或找到要操作的目标窗口。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "local_focus",
        "description": "把标题匹配的窗口切到前台并还原（操作某个应用前先切过去）。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "窗口标题片段，或进程名"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "local_type",
        "description": "向指定窗口输入文字（支持中文）。强烈建议带 title：会先聚焦再输入，"
                       "聚焦失败就不输入（避免把字打到别的窗口）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "title": {"type": "string", "description": "目标窗口标题片段，如 记事本"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "local_keys",
        "description": "按组合键，如 enter、esc、tab、ctrl+s、ctrl+shift+esc、alt+tab、win+r、f5。"
                       "用于确认、保存、切换、关闭对话框。",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "string", "description": "用 + 连接，例如 ctrl+s"},
            "title": {"type": "string", "description": "目标窗口标题片段（可选，带上更稳）"},
        }, "required": ["keys"]},
    }},
    {"type": "function", "function": {
        "name": "local_click",
        "description": "在用户屏幕的绝对坐标点击。坐标要来自用户描述或 local_windows 的屏幕尺寸，"
                       "盲点风险高，非必要不用。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"]},
        }, "required": ["x", "y"]},
    }},
    {"type": "function", "function": {
        "name": "local_close",
        "description": "关闭标题匹配的窗口（发正常关闭消息，不杀进程）。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "local_screenshot",
        "description": "截取用户本机屏幕（或指定窗口），存成图片挂到当前会话里，"
                       "用户能直接看到。返回尺寸/大小/前台窗口；你看不到像素内容，"
                       "判断界面状态请配合 local_windows 的标题与用户描述。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string",
                             "description": "可选：只截标题匹配的窗口；不传就截全屏"},
        }},
    }},
    # ---------------- 技能 / 例行任务 ----------------
    {"type": "function", "function": {
        "name": "list_skills",
        "description": "列出你可用和团队全局的技能（技能=别人沉淀好的做事流程）。"
                       "接到任务先看有没有对应技能，有就按它做，别自己另发明一套。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "use_skill",
        "description": "取回某个技能的完整执行指令，然后照着做。返回值里的 instructions 是给你的步骤，"
                       "不要原样复述给用户。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "技能名（list_skills 里看到的）"},
            "input": {"type": "string", "description": "本次要处理的具体内容，填到技能的 {input} 里"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "list_routines",
        "description": "查看你名下的例行任务（定时自动执行的任务）及下次运行时间。",
        "parameters": {"type": "object", "properties": {}},
    }},
]
