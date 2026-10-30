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

WORKSPACE = pathlib.Path(os.environ.get("M0_WORKSPACE") or "/opt/agent-os/workspace").resolve()

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


def _safe_decode(data: bytes | str | None) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _run_exec(cmd: str, cwd: str | None = None, timeout_s: int = 180) -> dict:
    from . import ext_tools
    return ext_tools.run_exec(cmd, cwd=cwd, timeout_s=timeout_s)


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


def _fs_read(path: str, max_bytes: int = 200_000, start_line: int | None = None,
             end_line: int | None = None) -> dict:
    from . import doc_tools
    return doc_tools.fs_read(path, start_line=start_line, end_line=end_line, max_bytes=max_bytes)


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
        from . import ext_tools
        return ext_tools.run_exec(str(args.get("cmd", "")), args.get("cwd"), args.get("timeout_s", 180))
    if name == "fs_list":
        return _fs_list(args.get("path"))
    if name == "fs_read":
        from . import doc_tools
        return doc_tools.fs_read(str(args.get("path", "")), start_line=args.get("start_line"),
                                 end_line=args.get("end_line"), max_bytes=args.get("max_bytes", 200_000))
    if name == "fs_write":
        return _fs_write(str(args.get("path", "")), str(args.get("content", "")))
    if name == "fs_edit":
        from . import doc_tools
        return doc_tools.fs_edit(str(args.get("path", "")), str(args.get("old_text", "")),
                                 str(args.get("new_text", "")), bool(args.get("replace_all", False)))
    if name == "glob_search":
        from . import doc_tools
        return doc_tools.glob_search(str(args.get("pattern", "*")), str(args.get("path", ".")))
    if name == "grep_search":
        from . import doc_tools
        return doc_tools.grep_search(str(args.get("pattern", "")), str(args.get("path", ".")),
                                     str(args.get("file_glob", "*")), int(args.get("context_lines", 1)),
                                     int(args.get("max_matches", 80)))
    if name == "doc_read":
        from . import doc_tools
        return doc_tools.doc_read(str(args.get("path", "")), max_chars=args.get("max_chars", 30000),
                                  sheet=args.get("sheet"))
    if name == "web_search":
        from . import web_tools
        return web_tools.web_search(
            str(args.get("query", "")),
            search_type=str(args.get("search_type") or "all"),
            max_results=int(args.get("max_results") or args.get("count") or 8)
        )
    if name == "web_fetch":
        from . import web_tools
        return web_tools.web_fetch(
            str(args.get("url", "")),
            max_chars=int(args.get("max_chars", 25000)),
            render_js=bool(args.get("render_js", False))
        )
    if name == "browser_open":
        from . import web_tools
        return web_tools.browser_open(str(args.get("url", "")), wait_ms=int(args.get("wait_ms", 2500)),
                                      screenshot=bool(args.get("screenshot", False)),
                                      selector=args.get("selector"),
                                      max_chars=int(args.get("max_chars", 20000)), emit=emit)
    if name == "process_start":
        from . import ext_tools
        return ext_tools.process_start(str(args.get("cmd", "")), name=args.get("name"), cwd=args.get("cwd"),
                                       bot_id=bot_id, session_id=session_id)
    if name == "process_list":
        from . import ext_tools
        return ext_tools.process_list(bot_id=bot_id, state=args.get("state"))
    if name == "process_logs":
        from . import ext_tools
        return ext_tools.process_logs(str(args.get("proc_id", "")), lines=int(args.get("lines", 50)))
    if name == "process_stop":
        from . import ext_tools
        return ext_tools.process_stop(str(args.get("proc_id", "")))
    if name == "todo_write":
        from . import ext_tools
        return ext_tools.todo_write(args.get("todos", []), session_id=session_id)
    if name == "todo_read":
        from . import ext_tools
        return ext_tools.todo_read(session_id=session_id)
    if name == "delegate_task":
        from . import ext_tools
        return ext_tools.delegate_task(args.get("tasks", []))
    if name == "skill_save":
        from . import ext_tools
        return ext_tools.skill_save(str(args.get("name", "")), str(args.get("description", "")),
                                    str(args.get("instructions", "")), bot_id=bot_id)
    if name == "skill_delete":
        from . import ext_tools
        return ext_tools.skill_delete(str(args.get("name_or_id", "")), bot_id=bot_id)
    if name == "routine_create":
        from . import ext_tools
        return ext_tools.routine_create(str(args.get("title", "")), str(args.get("prompt", "")),
                                        str(args.get("schedule", "")), bot_id=bot_id, session_id=session_id)
    if name == "routine_delete":
        from . import ext_tools
        return ext_tools.routine_delete(str(args.get("routine_id", "")))
    if name == "publish_artifact":
        from . import ext_tools
        return ext_tools.publish_artifact(str(args.get("path", "")), name=args.get("name"),
                                          mime=args.get("mime"), emit=emit)
    if name == "render_chart":
        from . import ext_tools
        return ext_tools.render_chart(
            title=str(args.get("title") or "数据图表"),
            chart_type=str(args.get("chart_type") or "bar"),
            data=args.get("data"),
            python_code=args.get("python_code"),
            theme=str(args.get("theme") or "modern"),
            emit=emit,
        )
    if name == "computer_screenshot":
        from . import ext_tools
        return ext_tools.computer_screenshot(emit=emit)
    if name == "computer_action":
        from . import ext_tools
        return ext_tools.computer_action(str(args.get("action", "")), args.get("params"))
    if name == "mcp_list_tools":
        from . import ext_tools
        return ext_tools.mcp_list_tools(args.get("server_url"))
    if name == "mcp_call":
        from . import ext_tools
        return ext_tools.mcp_call(str(args.get("tool_name", "")), args.get("arguments", {}),
                                  server_url=args.get("server_url"))
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
    if name == 'local_exec':
        args = dict(args)
        args['timeout_s'] = max(1, min(int(args.get('timeout_s') or 60), 150))
        result = local_bridge.call(device_id, name, args, timeout=args['timeout_s'] + 10)
    else:
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
        "description": "读取工作区里的文件。支持文本分段/行号切片读取与自动解析各类 Office/PDF 文档。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的路径"},
            "start_line": {"type": "integer", "description": "起始行号（1-indexed，可选）"},
            "end_line": {"type": "integer", "description": "结束行号（1-indexed，可选）"},
            "max_bytes": {"type": "integer", "description": "最大读取字节数限制，默认 200,000"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "fs_edit",
        "description": "外科手术式精确替换工作区文本文件中的内容。old_text 须在目标文件中精准匹配且唯一。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "old_text": {"type": "string", "description": "需要被替换的原文内容（精准匹配空格缩进）"},
            "new_text": {"type": "string", "description": "替换后的新内容"},
            "replace_all": {"type": "boolean", "description": "若为 true 则替换所有匹配项，默认 false"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "glob_search",
        "description": "在工作区内根据通配符模式（如 *.py, **/*.json）快速递归搜索文件路径。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "通配符模式，如 *.py 或 **/*.md"},
            "path": {"type": "string", "description": "相对工作区的起始搜索目录，默认 ."},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "grep_search",
        "description": "在工作区文本文件中搜索正则表达式或文本片段，返回行号与上下文前后行。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "要搜索的正则表达式或文本模式"},
            "path": {"type": "string", "description": "相对工作区的搜索目录，默认 ."},
            "file_glob": {"type": "string", "description": "文件类型过滤通配符，如 *.py 或 *.json，默认 *"},
            "context_lines": {"type": "integer", "description": "匹配行前后展示的上下文行数，默认 1"},
            "max_matches": {"type": "integer", "description": "最大匹配条数限制，默认 80"},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "doc_read",
        "description": "解析各类文档、表格、幻灯片与压缩包内容：PDF, Word(.docx), Excel(.xlsx), PPTX, CSV, HTML, JSON, ZIP/TAR。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "max_chars": {"type": "integer", "description": "最大提取字符数，默认 30000"},
            "sheet": {"type": "string", "description": "Excel 工作表名称（可选，默认第一张表）"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "实时多引擎联网搜索（国内必应、360、搜狗级联，获取最新资讯、行业事实与技术文档）。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
            "search_type": {"type": "string", "enum": ["all", "news", "web"], "description": "搜索类型：all 全部 / news 新闻时效 / web 网页，默认 all"},
            "count": {"type": "integer", "description": "返回结果条数，默认 8 条"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "抓取目标 URL 网页并提取为排版规整的高质量 Markdown 正文（自动剥离广告导航并处理重定向）。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "目标网页的完整 HTTP/HTTPS 链接"},
            "max_chars": {"type": "integer", "description": "提取正文的最大字符数，默认 25000"},
            "render_js": {"type": "boolean", "description": "是否强制使用无头浏览器完整渲染 JS，默认 false"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "在服务端启动无头 Chromium 浏览器完整渲染动态 JS 页面，支持 DOM 提取与网页全屏截图挂载。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "目标网页的完整 HTTP/HTTPS 链接"},
            "wait_ms": {"type": "integer", "description": "等待页面 JS 加载渲染时间（毫秒），默认 2500"},
            "screenshot": {"type": "boolean", "description": "是否同时截取全屏并挂载为图片产物，默认 false"},
            "max_chars": {"type": "integer", "description": "DOM 提取文字上限，默认 20000"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "process_start",
        "description": "在后台以独立进程组启动长期运行的守护任务（如本地开发服务、耗时数据计算），不阻塞当前交互。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string", "description": "要启动的后台命令"},
            "name": {"type": "string", "description": "进程标识名（可选）"},
            "cwd": {"type": "string", "description": "相对工作区的工作目录（可选）"},
        }, "required": ["cmd"]},
    }},
    {"type": "function", "function": {
        "name": "process_list",
        "description": "查看当前活跃或历史后台进程状态与 PID。",
        "parameters": {"type": "object", "properties": {
            "state": {"type": "string", "description": "按状态筛选：running / exited / failed / stopped，缺省查全部"},
        }},
    }},
    {"type": "function", "function": {
        "name": "process_logs",
        "description": "查看指定后台进程的最新输出日志片段。",
        "parameters": {"type": "object", "properties": {
            "proc_id": {"type": "string", "description": "进程 ID（如 proc_xxxx）"},
            "lines": {"type": "integer", "description": "返回末尾行数，默认 50 行"},
        }, "required": ["proc_id"]},
    }},
    {"type": "function", "function": {
        "name": "process_stop",
        "description": "主动停止/杀死指定的后台运行进程。",
        "parameters": {"type": "object", "properties": {
            "proc_id": {"type": "string", "description": "进程 ID（如 proc_xxxx）"},
        }, "required": ["proc_id"]},
    }},
    {"type": "function", "function": {
        "name": "todo_write",
        "description": "持久化更新本会话的任务步骤清单（Checklist），保持长任务与复杂规划的状态对齐。",
        "parameters": {"type": "object", "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "task": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["task", "status"],
                },
                "description": "任务清单项列表",
            },
        }, "required": ["todos"]},
    }},
    {"type": "function", "function": {
        "name": "todo_read",
        "description": "读取当前会话的任务步骤清单，查看未完成或已完成的进度情况。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "delegate_task",
        "description": "将多个独立子任务并行分派给并发子代理（Subagent），最多支持 4 线程并发执行，聚合返回成果。",
        "parameters": {"type": "object", "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "goal": {"type": "string"},
                        "context": {"type": "string"},
                    },
                    "required": ["goal"],
                },
                "description": "子任务列表（每个任务包含 goal 目标及 context 上下文）",
            },
        }, "required": ["tasks"]},
    }},
    {"type": "function", "function": {
        "name": "skill_save",
        "description": "将当前总结出的优质工作流程或专业指令固化保存为新技能（自演化沉淀）。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "技能名称，如 深度行业调研流程"},
            "description": {"type": "string", "description": "技能触发场景说明"},
            "instructions": {"type": "string", "description": "技能执行详细指令（支持 {input} 占位符）"},
        }, "required": ["name", "description", "instructions"]},
    }},
    {"type": "function", "function": {
        "name": "skill_delete",
        "description": "删除不再需要的技能。",
        "parameters": {"type": "object", "properties": {
            "name_or_id": {"type": "string", "description": "技能名称或 ID"},
        }, "required": ["name_or_id"]},
    }},
    {"type": "function", "function": {
        "name": "routine_create",
        "description": "为当前 Bot 创建定时或周期性自动执行任务（例行任务），如每日巡检或定时早报。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "任务标题"},
            "prompt": {"type": "string", "description": "触发时让 Bot 执行的指令提示词"},
            "schedule": {"type": "string", "description": "调度表达式，如 interval:30m, interval:2h, daily:09:00"},
        }, "required": ["title", "prompt", "schedule"]},
    }},
    {"type": "function", "function": {
        "name": "routine_delete",
        "description": "删除指定的例行任务。",
        "parameters": {"type": "object", "properties": {
            "routine_id": {"type": "string", "description": "例行任务 ID（如 rtn_xxxx）"},
        }, "required": ["routine_id"]},
    }},
    {"type": "function", "function": {
        "name": "publish_artifact",
        "description": "将工作区内的报告、图表或二进制文件发布为可供用户下载和直接在会话中预览的产物（Artifact）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "工作区内的文件路径"},
            "name": {"type": "string", "description": "展示文件名（可选）"},
            "mime": {"type": "string", "description": "MIME 类型（可选，默认根据后缀自动探测）"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "render_chart",
        "description": "专业数据图表生成工具。支持柱状图(bar)、趋势折线图(line)、环形饼图(pie)、散点图(scatter)、面积图(area)、多维雷达图(radar)及自定义 Matplotlib 代码(custom_code)。"
                       "自动应用 Hermes/DSH 专业研报级高雅暗调/彭博金融/学术主题配色，支持中文回退字体与 300 DPI 产物高清发布。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "图表主标题，如 2026年业务营收趋势"},
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "pie", "scatter", "area", "radar", "custom_code"],
                "description": "图表类型：bar(柱状/对比), line(折线趋势), pie(饼图/环形), scatter(散点), area(面积), radar(雷达), custom_code(自定义代码)",
            },
            "data": {
                "type": "object",
                "description": "图表数据对象。支持 categories 标签列表与 series 数据列表（包含 name 与 values），如 {'categories': ['Q1','Q2'], 'series': [{'name': '营收', 'values': [100, 150]}]}",
            },
            "python_code": {
                "type": "string",
                "description": "当 chart_type 为 custom_code 时执行的自定义 Matplotlib/Seaborn 绘图代码（局部隔离运行，已预注入 output_path, plt, np 变量）",
            },
            "theme": {
                "type": "string",
                "enum": ["modern", "financial", "academic"],
                "description": "研报风格主题：modern(现代科技暗调/蓝紫绿金), financial(彭博金融/高对比暗调), academic(经典学术黑白灰/纯白洁净)，默认 modern",
            },
        }, "required": ["title", "chart_type"]},
    }},
    {"type": "function", "function": {
        "name": "computer_screenshot",
        "description": "截取当前服务器 X11 桌面图像并作为产物在聊天会话中直接展示给用户。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "computer_action",
        "description": "在服务器 X11 桌面上执行键鼠动作（click/move/type/key/drag/probe）。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["click", "move", "type", "key", "drag", "probe"]},
            "params": {"type": "object", "description": "动作参数（坐标 x/y、按键 keys、输入 text 等）"},
        }, "required": ["action"]},
    }},
    {"type": "function", "function": {
        "name": "mcp_list_tools",
        "description": "查看已配置的 MCP 服务工具或指定远程 MCP Server 支持的所有工具能力。",
        "parameters": {"type": "object", "properties": {
            "server_url": {"type": "string", "description": "可选的 MCP 服务 HTTP 端点 URL"},
        }},
    }},
    {"type": "function", "function": {
        "name": "mcp_call",
        "description": "通过标准 MCP 协议调用外部服务器工具。",
        "parameters": {"type": "object", "properties": {
            "tool_name": {"type": "string", "description": "MCP 工具名称"},
            "arguments": {"type": "object", "description": "传入工具的参数字典"},
            "server_url": {"type": "string", "description": "可选的 MCP 服务 HTTP 端点 URL"},
        }, "required": ["tool_name", "arguments"]},
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
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】查看用户本地电脑的系统信息（主机名、系统版本、用户名、家目录）。"
                       "本地能力需要在设置里开启。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "local_list",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】列出用户本地电脑某个目录的内容（只读）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径，如 C:\\\\Users\\\\me\\\\Documents"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "local_read",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】读取用户本地电脑上的一个文本文件（只读，最多 200KB）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "local_write",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】在用户本地电脑上写文件（会覆盖）。需要用户审批。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "local_exec",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】在用户本地电脑上执行命令（Windows 用 cmd /C，其他用 sh -c）。"
                       "需要用户审批，默认 60 秒超时，最多 150 秒。Windows 命令使用 cmd 语法；"
                       "复杂 Python/PowerShell 脚本应先 local_write 写文件再执行，避免多层引号。"
                       "启动 GUI 程序请用 local_open，别用这个。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_s": {"type": "integer"},
        }, "required": ["cmd"]},
    }},
    # ---------------- 本地桌面操控（exe 提供，逐次审批） ----------------
    {"type": "function", "function": {
        "name": "local_apps",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】在用户电脑的开始菜单和桌面快捷方式里按名字找应用（支持中文名，如 微信、记事本）。"
                       "要打开应用前先用它拿到准确路径。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "应用名关键词，留空则列常用应用"},
            "limit": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "local_open",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】在用户电脑上打开应用、文件或网址（等价于双击）。可传 local_apps 拿到的 path，"
                       "也可直接传应用名（如 记事本）。返回是否成功以及当前前台窗口。",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "应用名 / 文件绝对路径 / URL"},
        }, "required": ["target"]},
    }},
    {"type": "function", "function": {
        "name": "local_windows",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】列出用户电脑上当前可见的窗口（标题、进程名、是否前台）。"
                       "用它确认应用真的打开了，或找到要操作的目标窗口。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "local_focus",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】把标题匹配的窗口切到前台并还原（操作某个应用前先切过去）。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "窗口标题片段，或进程名"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "local_type",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】向指定窗口输入文字（支持中文）。强烈建议带 title：会先聚焦再输入，"
                       "聚焦失败就不输入（避免把字打到别的窗口）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "title": {"type": "string", "description": "目标窗口标题片段，如 记事本"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "local_keys",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】按组合键，如 enter、esc、tab、ctrl+s、ctrl+shift+esc、alt+tab、win+r、f5。"
                       "用于确认、保存、切换、关闭对话框。",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "string", "description": "用 + 连接，例如 ctrl+s"},
            "title": {"type": "string", "description": "目标窗口标题片段（可选，带上更稳）"},
        }, "required": ["keys"]},
    }},
    {"type": "function", "function": {
        "name": "local_click",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】在用户屏幕的绝对坐标点击。坐标要来自用户描述或 local_windows 的屏幕尺寸，"
                       "盲点风险高，非必要不用。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"]},
        }, "required": ["x", "y"]},
    }},
    {"type": "function", "function": {
        "name": "local_close",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】关闭标题匹配的窗口（发正常关闭消息，不杀进程）。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "local_screenshot",
        "description": "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】截取用户本机屏幕（或指定窗口），存成图片挂到当前会话里，"
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
