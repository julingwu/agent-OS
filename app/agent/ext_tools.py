"""Extended execution, process management, subagent delegation, and ecosystem tools for AgentOS.

Includes:
- run_exec: shell execution with 16k output cap & spillover files to .tool_outputs/
- Process supervisor wiring: process_start, process_list, process_logs, process_stop
- Planning & checklists: todo_write, todo_read (SQLite session persistence)
- Parallel subagent delegation: delegate_task (concurrent ThreadPoolExecutor workers)
- Self-evolution & Ecosystem:
  - skill_save, skill_delete (via app.skills)
  - routine_create, routine_delete (via app.routines)
  - publish_artifact (via app.artifacts + emit 'artifact.attached')
  - computer_screenshot, computer_action (via app.computer.screen)
  - mcp_list_tools, mcp_call (via JSON-RPC MCP clients)
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import mimetypes
import os
import pathlib
import secrets
import subprocess
import time
from typing import Any

import httpx

from .. import artifacts, db, llm, processes, routines, skills
from ..computer import screen
from . import tools

log = logging.getLogger("app.agent.ext_tools")

MAX_OUTPUT = 16000
BLOCKED = (
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero of=/dev/",
    ":(){ :|:& };:",
    "shutdown",
    "reboot",
    "> /dev/sda",
)


def _safe_decode(data: bytes | str | None) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 增强版 Exec 与 Spillover
def run_exec(cmd: str, cwd: str | None = None, timeout_s: int = 180) -> dict[str, Any]:
    """在 Linux/Windows 宿主环境中执行命令，支持长输出溢出文件保存（Spillover）。"""
    if not cmd.strip():
        raise tools.ToolError("empty command")
    low = cmd.lower()
    for bad in BLOCKED:
        if bad in low:
            raise tools.ToolError(f"blocked dangerous command pattern: {bad!r}")

    workdir = tools._jail(cwd)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=False,
            timeout=max(1, min(int(timeout_s), 900)),
        )
        full_stdout = _safe_decode(proc.stdout)
        full_stderr = _safe_decode(proc.stderr)

        spillover_path = None
        # 如果任一输出超过 16KB，自动保存完整输出到工作区 .tool_outputs/ 目录
        if len(full_stdout) > MAX_OUTPUT or len(full_stderr) > MAX_OUTPUT:
            outputs_dir = tools.WORKSPACE / ".tool_outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)
            spill_name = f"exec_{int(time.time())}_{secrets.token_hex(4)}.log"
            spill_file = outputs_dir / spill_name
            spill_content = (
                f"=== COMMAND ===\n{cmd}\n"
                f"=== EXIT CODE ===\n{proc.returncode}\n\n"
                f"=== STDOUT ===\n{full_stdout}\n\n"
                f"=== STDERR ===\n{full_stderr}\n"
            )
            spill_file.write_text(spill_content, encoding="utf-8")
            spillover_path = f".tool_outputs/{spill_name}"

        stdout_snippet = full_stdout[:MAX_OUTPUT]
        stderr_snippet = full_stderr[:MAX_OUTPUT]

        if spillover_path:
            notice = f"\n\n...[输出超长(已截断)，完整输出已存至 {spillover_path}，可用 fs_read 查看]"
            if len(full_stdout) > MAX_OUTPUT:
                stdout_snippet += notice
            if len(full_stderr) > MAX_OUTPUT:
                stderr_snippet += notice

        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout_snippet,
            "stderr": stderr_snippet,
            "spillover_file": spillover_path,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout_s}s"}
    except Exception as exc:
        return {"ok": False, "error": f"exec failed: {exc}"}


# ---------------------------------------------------------------- 后台进程管理
def process_start(cmd: str, name: str | None = None, cwd: str | None = None,
                  bot_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """启动一个持久化后台进程（服务端独立进程组，不受连接中断影响）。"""
    with db.get_conn() as conn:
        try:
            info = processes.start(
                conn,
                bot_id=bot_id or "bot_chief",
                cmd=cmd,
                name=name,
                cwd=cwd,
                session_id=session_id,
            )
            return {"ok": True, "process": info}
        except Exception as exc:
            return {"ok": False, "error": f"启动进程失败: {exc}"}


def process_list(bot_id: str | None = None, state: str | None = None) -> dict[str, Any]:
    """查看当前活跃或历史后台进程列表。"""
    with db.get_conn() as conn:
        try:
            items = processes.list_processes(conn, bot_id=bot_id, state=state, limit=50)
            return {"ok": True, "count": len(items), "processes": items}
        except Exception as exc:
            return {"ok": False, "error": f"列出进程失败: {exc}"}


def process_logs(proc_id: str, lines: int = 50) -> dict[str, Any]:
    """读取指定后台进程的末尾日志片段。"""
    with db.get_conn() as conn:
        try:
            tail = processes.log_tail(conn, proc_id, tail=max(1, min(int(lines or 50), 1000)))
            if tail is None:
                return {"ok": False, "error": f"进程不存在: {proc_id}"}
            return {"ok": True, "process_id": proc_id, "logs": tail.get("lines", []), "bytes": tail.get("bytes", 0)}
        except Exception as exc:
            return {"ok": False, "error": f"读取进程日志失败: {exc}"}


def process_stop(proc_id: str) -> dict[str, Any]:
    """停止指定的后台进程。"""
    with db.get_conn() as conn:
        try:
            res = processes.stop(conn, proc_id)
            if res is None:
                return {"ok": False, "error": f"进程不存在: {proc_id}"}
            return {"ok": True, "process": res}
        except Exception as exc:
            return {"ok": False, "error": f"停止进程失败: {exc}"}


# ---------------------------------------------------------------- 会话级 Todo / Checklist
def _ensure_todos_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_todos (
            session_id TEXT PRIMARY KEY,
            items_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def todo_write(todos: list[dict[str, Any]], session_id: str | None = None) -> dict[str, Any]:
    """持久化记录当前会话的任务清单（todo list）。"""
    sid = session_id or "default_session"
    cleaned = []
    for idx, item in enumerate(todos or []):
        cleaned.append({
            "id": item.get("id", idx + 1),
            "task": str(item.get("task") or item.get("content") or "").strip(),
            "status": str(item.get("status") or "pending").lower(),
        })

    with db.get_conn() as conn:
        _ensure_todos_table(conn)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            "INSERT OR REPLACE INTO session_todos (session_id, items_json, updated_at) VALUES (?, ?, ?)",
            (sid, json.dumps(cleaned, ensure_ascii=False), now),
        )
        conn.commit()
    return {"ok": True, "count": len(cleaned), "todos": cleaned}


def todo_read(session_id: str | None = None) -> dict[str, Any]:
    """读取当前会话的任务清单（todo list）。"""
    sid = session_id or "default_session"
    with db.get_conn() as conn:
        _ensure_todos_table(conn)
        row = conn.execute("SELECT items_json FROM session_todos WHERE session_id=?", (sid,)).fetchone()
        if not row:
            return {"ok": True, "count": 0, "todos": []}
        try:
            items = json.loads(row["items_json"])
            return {"ok": True, "count": len(items), "todos": items}
        except Exception:
            return {"ok": True, "count": 0, "todos": []}


# ---------------------------------------------------------------- 并行子任务委派 (delegate_task)
def _get_subagent_tool_specs() -> list[dict]:
    excluded = {
        "delegate_task", "ask_human", "routine_create", "routine_delete",
        "skill_save", "skill_delete", "computer_action",
    }
    return [
        s for s in tools.TOOL_SPECS
        if s.get("function", {}).get("name") not in excluded
        and not s.get("function", {}).get("name", "").startswith("local_")
    ]


def _run_single_subtask(task: dict[str, Any]) -> dict[str, Any]:
    tid = task.get("id") or secrets.token_hex(4)
    goal = str(task.get("goal") or "").strip()
    context = str(task.get("context") or "").strip()

    system_prompt = (
        "你是 AgentOS 的专业并发子代理（Subagent Worker）。\n"
        "你的任务是全神贯注于主代理分配给你的具体子目标。\n"
        "你可以调用工具（如 web_search 联网搜索、web_fetch 抓取网页、fs_read/fs_edit/glob_search/grep_search/doc_read 分析文档代码、exec 验证命令）。\n"
        "请进行严谨的调研、逻辑推演与综合，直接给出高质量、结构化的交付结果。\n"
        "回答必须实事求是、严禁编造虚假信息。"
    )
    user_prompt = f"【子任务目标】：{goal}\n【背景与上下文信息】：{context or '无'}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    subagent_tools = _get_subagent_tool_specs()
    max_steps = 6
    final_result = ""

    try:
        for step in range(max_steps):
            resp = llm.chat(messages, tools=subagent_tools, temperature=0.3)
            if not resp:
                break
            msg = resp.get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_result = (msg.get("content") or "").strip()
                break

            assistant_msg = {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
                "reasoning_content": msg.get("reasoning_content") or "",
            }
            messages.append(assistant_msg)

            for call in tool_calls:
                fn = call.get("function") or {}
                fn_name = fn.get("name") or "unknown"
                call_id = call.get("id") or f"sub_{step}"
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                try:
                    res = tools.dispatch(fn_name, args)
                except Exception as exc:
                    res = {"ok": False, "error": f"subagent tool error: {exc}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(res, ensure_ascii=False)[:12000],
                })

        if not final_result:
            summary_resp = llm.chat(
                messages + [{"role": "user", "content": "请综合上述工具调用与分析结果，直接给出最终结构化结论。"}],
                tools=None,
                temperature=0.2,
            )
            final_result = ((summary_resp or {}).get("message") or {}).get("content", "").strip()

        return {"id": tid, "goal": goal, "status": "completed", "result": final_result or "（子任务无输出）"}
    except Exception as exc:
        return {"id": tid, "goal": goal, "status": "failed", "error": str(exc)}


def delegate_task(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """将多个独立子任务并行委派给子代理（Subagent），并发数上限 4。"""
    if not tasks:
        return {"ok": False, "error": "tasks 列表不能为空"}

    max_workers = min(len(tasks), 4)
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_single_subtask, t): t for t in tasks}
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    # Sort results by original task order
    orig_order = {t.get("id", i): i for i, t in enumerate(tasks)}
    results.sort(key=lambda r: orig_order.get(r.get("id"), 999))

    return {
        "ok": True,
        "tasks_total": len(tasks),
        "results": results,
    }


# ---------------------------------------------------------------- 技能与例行任务管理
def skill_save(name: str, description: str, instructions: str,
               bot_id: str | None = None) -> dict[str, Any]:
    """保存或更新一个可复用技能。"""
    with db.get_conn() as conn:
        skills.ensure(conn)
        existing = skills.find_by_name(conn, name, bot_id=bot_id)
        if existing:
            updated = skills.update(
                conn,
                existing["id"],
                name=name,
                description=description,
                body=instructions,
            )
            return {"ok": True, "action": "updated", "skill": updated}
        created = skills.create(
            conn,
            bot_id=bot_id,
            name=name,
            description=description,
            body=instructions,
        )
        return {"ok": True, "action": "created", "skill": created}


def skill_delete(name_or_id: str, bot_id: str | None = None) -> dict[str, Any]:
    """删除一个技能。"""
    with db.get_conn() as conn:
        skills.ensure(conn)
        target = skills.get(conn, name_or_id)
        if not target:
            target = skills.find_by_name(conn, name_or_id, bot_id=bot_id)
        if not target:
            return {"ok": False, "error": f"未找到技能: {name_or_id}"}
        skills.delete(conn, target["id"])
        return {"ok": True, "deleted": target["name"], "id": target["id"]}


def routine_create(title: str, prompt: str, schedule: str,
                   bot_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """创建一个定时或周期性例行任务。"""
    with db.get_conn() as conn:
        try:
            item = routines.create(
                conn,
                bot_id=bot_id or "bot_chief",
                session_id=session_id or "ses_chief",
                title=title,
                prompt=prompt,
                schedule=schedule,
            )
            return {"ok": True, "routine": item}
        except Exception as exc:
            return {"ok": False, "error": f"创建例行任务失败: {exc}"}


def routine_delete(routine_id: str) -> dict[str, Any]:
    """删除一个例行任务。"""
    with db.get_conn() as conn:
        routines.ensure(conn)
        ok = routines.delete(conn, routine_id)
        return {"ok": ok, "routine_id": routine_id}


# ---------------------------------------------------------------- 成果产物发布 (publish_artifact)
def publish_artifact(path: str, name: str | None = None, mime: str | None = None, emit=None) -> dict[str, Any]:
    """将工作区内的任意文件发布为前端可预览/下载的产物（Artifact）。"""
    target = tools._jail(path)
    if not target.is_file():
        return {"ok": False, "error": f"文件不存在或不是普通文件: {path}"}

    data = target.read_bytes()
    guessed_mime, _ = mimetypes.guess_type(target.name)
    kind = mime or guessed_mime or "application/octet-stream"
    art_name = name or target.name

    try:
        art = artifacts.save_bytes(art_name, data, kind=kind)
        if emit:
            try:
                emit("artifact.attached", {
                    "artifact_id": art["id"],
                    "name": art["name"],
                    "kind": art["kind"],
                    "bytes": art["bytes"],
                    "url": art["url"],
                })
            except Exception:
                pass
        return {"ok": True, "artifact": art}
    except Exception as exc:
        return {"ok": False, "error": f"发布产物失败: {exc}"}


# ---------------------------------------------------------------- 电脑屏幕与桌面操作
def computer_screenshot(emit=None) -> dict[str, Any]:
    """截取当前服务器 X11 桌面图像并作为产物挂载。"""
    try:
        jpg_bytes = screen.grab_jpeg(max_w=1280, quality=7)
        art = artifacts.save_bytes(f"desktop_{int(time.time())}.jpg", jpg_bytes, kind="image/jpeg")
        if emit:
            try:
                emit("artifact.attached", {
                    "artifact_id": art["id"],
                    "name": art["name"],
                    "kind": art["kind"],
                    "bytes": art["bytes"],
                    "url": art["url"],
                })
            except Exception:
                pass
        return {"ok": True, "artifact": art}
    except Exception as exc:
        return {"ok": False, "error": f"桌面截图失败: {exc}"}


def computer_action(action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行服务器 X11 桌面控制动作（鼠标点击、按键、拖拽、输入）。"""
    p = params or {}
    act = (action or "").lower().strip()
    try:
        if act == "click":
            screen.input_click(int(p.get("x", 0)), int(p.get("y", 0)), int(p.get("button", 1)))
        elif act == "move":
            screen.input_move(int(p.get("x", 0)), int(p.get("y", 0)))
        elif act == "type":
            screen.input_type(str(p.get("text", "")))
        elif act == "key":
            screen.input_key(str(p.get("keys", "")))
        elif act == "drag":
            screen.input_drag(int(p.get("x1", 0)), int(p.get("y1", 0)), int(p.get("x2", 0)), int(p.get("y2", 0)))
        elif act == "probe":
            return {"ok": True, "status": screen.probe()}
        else:
            return {"ok": False, "error": f"不支持的桌面动作: {action}"}
        return {"ok": True, "action": act, "params": p}
    except Exception as exc:
        return {"ok": False, "error": f"执行桌面动作失败: {exc}"}


# ---------------------------------------------------------------- MCP 协议集成
def mcp_list_tools(server_url: str | None = None) -> dict[str, Any]:
    """列出远程 MCP 服务器或已配置 MCP 服务的工具列表。"""
    if server_url == "stdio://homelab-unified":
        from .. import homelab_transport
        response = homelab_transport.rpc("tools/list")
        if not response.get("ok"):
            return response
        return {"ok": True, "server_url": server_url, "tools": response["result"].get("tools", [])}
    if server_url:
        try:
            req = {"jsonrpc": "2.0", "id": "tools-1", "method": "tools/list", "params": {}}
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                res = client.post(server_url, json=req)
                res.raise_for_status()
                data = res.json()
                tools_list = data.get("result", {}).get("tools", [])
                return {"ok": True, "server_url": server_url, "tools": tools_list}
        except Exception as exc:
            return {"ok": False, "error": f"查询 MCP 工具列表失败: {exc}"}

    # Query local database configured servers
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT id, name, url, tools_json, enabled FROM mcp_servers WHERE enabled=1").fetchall()
            servers = []
            for r in rows:
                tools_data = []
                if r["tools_json"]:
                    try:
                        tools_data = json.loads(r["tools_json"])
                    except Exception:
                        pass
                servers.append({
                    "id": r["id"],
                    "name": r["name"],
                    "url": r["url"],
                    "tools": tools_data,
                })
            return {"ok": True, "servers": servers}
    except Exception as exc:
        return {"ok": False, "error": f"读取 MCP 服务配置失败: {exc}"}


def mcp_call(tool_name: str, arguments: dict[str, Any], server_url: str | None = None) -> dict[str, Any]:
    """调用远程 MCP 工具并返回执行结果。"""
    target_url = server_url
    if not target_url:
        # Try to find server from DB
        try:
            with db.get_conn() as conn:
                rows = conn.execute("SELECT url, tools_json FROM mcp_servers WHERE enabled=1").fetchall()
                for r in rows:
                    if tool_name in (r["tools_json"] or ""):
                        target_url = r["url"]
                        break
        except Exception:
            pass

    if not target_url:
        return {"ok": False, "error": "未指定 server_url 且在已配置 MCP 服务中未找到对应工具"}

    if target_url == "stdio://homelab-unified":
        from .. import homelab_transport
        return homelab_transport.rpc("tools/call", {"name": tool_name, "arguments": arguments})

    req_body = {
        "jsonrpc": "2.0",
        "id": f"call-{int(time.time())}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    try:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(target_url, json=req_body)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"ok": False, "error": data["error"]}
            return {"ok": True, "result": data.get("result")}
    except Exception as exc:
        return {"ok": False, "error": f"调用远程 MCP 工具异常: {exc}"}
