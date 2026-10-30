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
import io
import json
import logging
import mimetypes
import os
import pathlib
import re
import secrets
import subprocess
import tempfile
import textwrap
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
    safe_env = tools.sanitize_env()

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=False,
            env=safe_env,
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
    workdir = tools._jail(cwd)
    workdir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        try:
            info = processes.start(
                conn,
                bot_id=bot_id or "bot_chief",
                cmd=cmd,
                name=name,
                cwd=str(workdir),
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


def todo_write(todos: list[Any], session_id: str | None = None) -> dict[str, Any]:
    """持久化记录当前会话的任务清单（todo list）。"""
    sid = session_id or "default_session"
    cleaned = []
    for idx, raw_item in enumerate(todos or []):
        if isinstance(raw_item, str):
            item = {"task": str(raw_item)}
        elif isinstance(raw_item, dict):
            item = raw_item
        else:
            item = {"task": str(raw_item)}
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
    goal = str(task.get("goal") or task.get("task") or "").strip()
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


def delegate_task(tasks: list[Any]) -> dict[str, Any]:
    """将多个独立子任务并行委派给子代理（Subagent），并发数上限 4。"""
    if not tasks:
        return {"ok": False, "error": "tasks 列表不能为空"}

    normalized_tasks = []
    for i, t in enumerate(tasks):
        if isinstance(t, str):
            normalized_tasks.append({"task": t, "id": i + 1, "goal": t})
        elif isinstance(t, dict):
            td = dict(t)
            if "goal" not in td and "task" in td:
                td["goal"] = td["task"]
            if "id" not in td:
                td["id"] = i + 1
            normalized_tasks.append(td)
        else:
            st = str(t)
            normalized_tasks.append({"task": st, "id": i + 1, "goal": st})

    tasks = normalized_tasks

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
        # 如果发布的文件是非图片类型（MIME 不是 image/*），自动生成伴生预览图
        if not kind.startswith("image/"):
            try:
                preview_png = artifacts.generate_doc_preview(art["name"], kind, data)
                preview_path = artifacts.root() / f"{art['id']}.preview.png"
                preview_path.write_bytes(preview_png)
            except Exception as pe:
                log.warning("生成伴生预览图失败 (%s): %s", art["id"], pe)
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


# ---------------------------------------------------------------- 专业数据图表渲染 (render_chart)
CHART_THEMES = {
    "modern": {
        "fig_bg": "#0F172A",
        "ax_bg": "#1E293B",
        "card_bg": "#1E293B",
        "card_border": "#3B82F6",
        "text": "#F8FAFC",
        "subtext": "#94A3B8",
        "grid": "#334155",
        "spine": "#475569",
        "arrow": "#38BDF8",
        "palette": ["#6366F1", "#06B6D4", "#10B981", "#F59E0B", "#EC4899", "#8B5CF6", "#F43F5E", "#3B82F6"],
    },
    "financial": {
        "fig_bg": "#0A0E17",
        "ax_bg": "#121826",
        "card_bg": "#1A2234",
        "card_border": "#FF6B00",
        "text": "#F8FAFC",
        "subtext": "#9CA3AF",
        "grid": "#1F2937",
        "spine": "#374151",
        "arrow": "#00D2FF",
        "palette": ["#FF6B00", "#00D2FF", "#00E676", "#FFD600", "#FF3D71", "#9D4EDD", "#00F5D4", "#F72585"],
    },
    "academic": {
        "fig_bg": "#FFFFFF",
        "ax_bg": "#F8FAFC",
        "card_bg": "#FFFFFF",
        "card_border": "#2563EB",
        "text": "#0F172A",
        "subtext": "#475569",
        "grid": "#E2E8F0",
        "spine": "#94A3B8",
        "arrow": "#2563EB",
        "palette": ["#2563EB", "#475569", "#0D9488", "#DC2626", "#D97706", "#7C3AED", "#0284C7", "#64748B"],
    },
}


def _parse_chart_data(data: dict | None) -> tuple[list[str], list[dict[str, Any]]]:
    if not data or not isinstance(data, dict):
        return [], []

    categories: list[str] = []
    for k in ("categories", "labels", "x", "keys"):
        if k in data and isinstance(data[k], list):
            categories = [str(x) for x in data[k]]
            break

    series_list: list[dict[str, Any]] = []
    if "series" in data and isinstance(data["series"], list):
        for idx, s in enumerate(data["series"]):
            if isinstance(s, dict):
                s_name = str(s.get("name") or f"系列 {idx + 1}")
                s_vals = s.get("values") if "values" in s else s.get("data", [])
                series_list.append({"name": s_name, "values": list(s_vals or [])})
            elif isinstance(s, (int, float)):
                series_list.append({"name": f"系列 {idx + 1}", "values": [s]})
    elif "series" in data and isinstance(data["series"], dict):
        s = data["series"]
        series_list.append({"name": str(s.get("name") or "数值"), "values": list(s.get("values") or s.get("data") or [])})
    elif "values" in data and isinstance(data["values"], list):
        series_list.append({"name": "数值", "values": list(data["values"])})
    elif "items" in data and isinstance(data["items"], list):
        cats = []
        vals = []
        for it in data["items"]:
            if isinstance(it, dict):
                cats.append(str(it.get("name") or it.get("label") or ""))
                vals.append(it.get("value") or it.get("val") or 0)
        categories = cats
        series_list.append({"name": "数值", "values": vals})

    if not categories and series_list and series_list[0]["values"]:
        categories = [f"项目 {i+1}" for i in range(len(series_list[0]["values"]))]

    return categories, series_list


def render_chart(
    title: str,
    chart_type: str,
    data: dict | None = None,
    python_code: str | None = None,
    theme: str = "modern",
    emit=None,
) -> dict:
    """专业数据图表生成工具。
    
    支持图表类型：bar, line, pie, scatter, area, radar, custom_code。
    支持主题：modern (高雅暗调/科技现代), financial (彭博/金融研报风格), academic (经典学术/黑白灰精炼)。
    高分辨率输出 300 DPI 并自动发布为产物。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Serif CJK SC",
        "Microsoft YaHei",
        "SimHei",
        "sans-serif",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    t_key = (theme or "modern").lower().strip()
    t_cfg = CHART_THEMES.get(t_key) or CHART_THEMES["modern"]
    text_color = t_cfg["text"]
    spine_color = t_cfg["spine"]
    grid_color = t_cfg["grid"]
    palette = t_cfg["palette"]

    ctype = (chart_type or "bar").lower().strip()

    try:
        if ctype == "custom_code":
            if not python_code or not python_code.strip():
                return {"ok": False, "error": "custom_code 模式下必须提供 python_code 参数"}
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name
            try:
                scope = {
                    "plt": plt,
                    "matplotlib": matplotlib,
                    "np": np,
                    "data": data,
                    "title": title,
                    "theme": theme,
                    "output_path": output_path,
                }
                try:
                    import seaborn as sns
                    scope["sns"] = sns
                except ImportError:
                    pass
                exec(python_code, scope)
                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    plt.savefig(output_path, dpi=300, bbox_inches="tight")
                png_bytes = pathlib.Path(output_path).read_bytes()
            finally:
                try:
                    os.remove(output_path)
                except Exception:
                    pass
                plt.close("all")
        elif ctype == "radar":
            categories, series_list = _parse_chart_data(data)
            if not categories:
                categories = ["指标 A", "指标 B", "指标 C", "指标 D", "指标 E"]
            N = len(categories)
            angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
            angles += angles[:1]

            fig = plt.figure(figsize=(9, 8), facecolor=t_cfg["fig_bg"])
            ax = fig.add_subplot(111, polar=True, facecolor=t_cfg["ax_bg"])
            ax.set_title(title, fontsize=16, fontweight="bold", pad=24, color=text_color)

            if not series_list:
                series_list = [{"name": "数值", "values": [50] * N}]

            for i, s in enumerate(series_list):
                vals = [float(v) for v in s["values"]]
                if len(vals) < N:
                    vals += [0.0] * (N - len(vals))
                vals = vals[:N]
                vals += vals[:1]
                color = palette[i % len(palette)]
                ax.plot(angles, vals, linewidth=2.4, linestyle="solid", label=s["name"], color=color)
                ax.fill(angles, vals, color=color, alpha=0.25)

            ax.set_xticks(angles[:-1])
            ax.set_xticklabels(categories, color=text_color, fontsize=11)
            ax.tick_params(colors=text_color)
            ax.grid(color=grid_color, alpha=0.45, linestyle="--")
            ax.spines["polar"].set_color(spine_color)
            if len(series_list) > 1:
                ax.legend(loc="upper right", bbox_to_anchor=(1.2, 1.1), facecolor=t_cfg["ax_bg"], edgecolor=spine_color, labelcolor=text_color)

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
            png_bytes = buf.getvalue()
            plt.close("all")
        elif ctype == "pie":
            categories, series_list = _parse_chart_data(data)
            vals = []
            labels = []
            if series_list and series_list[0]["values"]:
                vals = [float(v) for v in series_list[0]["values"]]
                labels = categories[:len(vals)] if categories else [f"项 {i+1}" for i in range(len(vals))]
            elif categories:
                vals = [1.0] * len(categories)
                labels = categories
            else:
                vals = [40, 35, 25]
                labels = ["项目 A", "项目 B", "项目 C"]

            fig, ax = plt.subplots(figsize=(9, 7), facecolor=t_cfg["fig_bg"])
            ax.set_facecolor(t_cfg["fig_bg"])
            ax.set_title(title, fontsize=16, fontweight="bold", pad=16, color=text_color)

            wedges, texts, autotexts = ax.pie(
                vals,
                labels=labels,
                autopct="%1.1f%%",
                pctdistance=0.75,
                startangle=140,
                colors=[palette[i % len(palette)] for i in range(len(vals))],
                wedgeprops=dict(width=0.45, edgecolor=t_cfg["fig_bg"], linewidth=2.5),
                textprops=dict(color=text_color, fontsize=11),
            )
            for autotext in autotexts:
                autotext.set_color("#FFFFFF")
                autotext.set_fontsize(10)
                autotext.set_weight("bold")

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
            png_bytes = buf.getvalue()
            plt.close("all")
        else:
            # bar, line, scatter, area
            categories, series_list = _parse_chart_data(data)
            if not series_list:
                series_list = [{"name": "数据指标", "values": [10, 20, 30, 40]}]
            if not categories:
                categories = [f"第 {i+1} 组" for i in range(len(series_list[0]["values"]))]

            fig, ax = plt.subplots(figsize=(10, 6), facecolor=t_cfg["fig_bg"])
            ax.set_facecolor(t_cfg["ax_bg"])
            ax.set_title(title, fontsize=16, fontweight="bold", pad=16, color=text_color)
            ax.tick_params(colors=text_color, labelsize=10)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color(spine_color)
            ax.spines["bottom"].set_color(spine_color)
            ax.grid(True, linestyle="--", alpha=0.35, color=grid_color)

            x = np.arange(len(categories))

            if ctype == "bar":
                n_series = len(series_list)
                if n_series == 1:
                    vals = [float(v) for v in series_list[0]["values"]]
                    bars = ax.bar(x, vals, width=0.52, color=palette[0], edgecolor=spine_color, linewidth=0.8, alpha=0.92)
                    for b in bars:
                        h = b.get_height()
                        ax.annotate(f"{h:g}", xy=(b.get_x() + b.get_width() / 2, h), xytext=(0, 4),
                                    textcoords="offset points", ha="center", va="bottom", color=text_color, fontsize=10)
                else:
                    width = 0.8 / max(1, n_series)
                    for i, s in enumerate(series_list):
                        vals = [float(v) for v in s["values"]]
                        offset = (i - n_series / 2 + 0.5) * width
                        bars = ax.bar(x + offset, vals, width=width, label=s["name"], color=palette[i % len(palette)],
                                      edgecolor=spine_color, linewidth=0.8, alpha=0.92)
                        for b in bars:
                            h = b.get_height()
                            ax.annotate(f"{h:g}", xy=(b.get_x() + b.get_width() / 2, h), xytext=(0, 3),
                                        textcoords="offset points", ha="center", va="bottom", color=text_color, fontsize=8)
                    ax.legend(facecolor=t_cfg["ax_bg"], edgecolor=spine_color, labelcolor=text_color, fontsize=10)
                ax.set_xticks(x)
                ax.set_xticklabels(categories, rotation=25 if any(len(c) > 4 for c in categories) else 0, color=text_color)

            elif ctype == "line":
                for i, s in enumerate(series_list):
                    vals = [float(v) for v in s["values"]]
                    color = palette[i % len(palette)]
                    ax.plot(x, vals, marker="o", markersize=6, linewidth=2.5, label=s["name"], color=color)
                    if len(vals) <= 15:
                        for xi, yi in zip(x, vals):
                            ax.annotate(f"{yi:g}", xy=(xi, yi), xytext=(0, 6), textcoords="offset points",
                                        ha="center", va="bottom", color=color, fontsize=9)
                ax.set_xticks(x)
                ax.set_xticklabels(categories, rotation=25 if any(len(c) > 4 for c in categories) else 0, color=text_color)
                if len(series_list) > 1:
                    ax.legend(facecolor=t_cfg["ax_bg"], edgecolor=spine_color, labelcolor=text_color, fontsize=10)

            elif ctype == "scatter":
                if "x" in data and "y" in data:
                    xs = [float(v) for v in data["x"]]
                    ys = [float(v) for v in data["y"]]
                    ax.scatter(xs, ys, color=palette[0], s=80, alpha=0.88, edgecolors=spine_color)
                else:
                    for i, s in enumerate(series_list):
                        vals = [float(v) for v in s["values"]]
                        xs = np.arange(len(vals))
                        color = palette[i % len(palette)]
                        ax.scatter(xs, vals, color=color, s=80, alpha=0.88, label=s["name"], edgecolors=spine_color)
                    if len(series_list) > 1:
                        ax.legend(facecolor=t_cfg["ax_bg"], edgecolor=spine_color, labelcolor=text_color, fontsize=10)
                    if categories:
                        ax.set_xticks(x)
                        ax.set_xticklabels(categories, color=text_color)

            elif ctype == "area":
                for i, s in enumerate(series_list):
                    vals = [float(v) for v in s["values"]]
                    color = palette[i % len(palette)]
                    ax.plot(x, vals, color=color, linewidth=2.4, label=s["name"])
                    ax.fill_between(x, vals, alpha=0.32, color=color)
                ax.set_xticks(x)
                ax.set_xticklabels(categories, rotation=25 if any(len(c) > 4 for c in categories) else 0, color=text_color)
                if len(series_list) > 1:
                    ax.legend(facecolor=t_cfg["ax_bg"], edgecolor=spine_color, labelcolor=text_color, fontsize=10)

            else:
                plt.close("all")
                return {"ok": False, "error": f"不支持的图表类型: {chart_type}"}

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
            png_bytes = buf.getvalue()
            plt.close("all")

        # 保存为产物
        art = artifacts.save_bytes(name=f"{title}.png", data=png_bytes, kind="image/png")
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

        return {
            "ok": True,
            "artifact_id": art["id"],
            "url": art["url"],
            "name": art["name"],
            "markdown": f"![{title}]({art['url']})",
            "summary": f"已生成专业图表【{title}】并发布为产物",
        }
    except Exception as exc:
        log.exception("render_chart failed: %s", exc)
        return {"ok": False, "error": f"图表生成失败: {exc}"}
    finally:
        plt.close("all")


# ---------------------------------------------------------------- 专业架构与业务流程图渲染 (render_diagram)
def _clean_diagram_code(code: str) -> list[str]:
    lines = []
    for line in (code or "").strip().splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        if line.lower() in ("graph td", "graph lr", "graph tb", "flowchart td", "flowchart lr", "sequencediagram", "autonumber"):
            continue
        lines.append(line)
    return lines


def _render_diagram_sequence(title: str, lines: list[str], t_cfg: dict) -> bytes:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    participants = []
    messages = []
    part_re = re.compile(r"^participant\s+([a-zA-Z0-9_\u4e00-\u9fa5]+)(?:\s+as\s+(.+))?$", re.IGNORECASE)
    msg_re = re.compile(r"^([a-zA-Z0-9_\u4e00-\u9fa5]+)\s*(-{1,2}>{1,2})\s*([a-zA-Z0-9_\u4e00-\u9fa5]+)\s*:\s*(.*)$")

    alias_map = {}
    for line in lines:
        pm = part_re.match(line)
        if pm:
            pid = pm.group(1).strip()
            plabel = (pm.group(2) or pid).strip()
            if pid not in participants:
                participants.append(pid)
            alias_map[pid] = plabel
            continue

        mm = msg_re.match(line)
        if mm:
            src = mm.group(1).strip()
            arrow = mm.group(2).strip()
            dst = mm.group(3).strip()
            text = mm.group(4).strip()
            is_dashed = "--" in arrow
            if src not in participants:
                participants.append(src)
            if dst not in participants:
                participants.append(dst)
            messages.append({"src": src, "dst": dst, "text": text, "dashed": is_dashed})

    if not participants:
        participants = ["Client", "Server"]
    if not messages:
        messages = [{"src": participants[0], "dst": participants[-1], "text": "交互请求", "dashed": False}]

    n_part = len(participants)
    n_msg = len(messages)

    fig_w = max(10, n_part * 2.8)
    fig_h = max(6, 2.5 + n_msg * 0.9)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=t_cfg["fig_bg"])
    ax.set_facecolor(t_cfg["fig_bg"])
    ax.set_xlim(-0.5, n_part - 0.5)
    ax.set_ylim(-0.5, n_msg + 2.0)
    ax.axis("off")

    ax.text(
        (n_part - 1) / 2.0, n_msg + 1.5, title,
        ha="center", va="center", color=t_cfg["text"], fontsize=16, fontweight="bold",
    )

    y_top = n_msg + 0.6
    y_bot = 0.2
    p_x = {p: i for i, p in enumerate(participants)}
    palette = t_cfg["palette"]

    for i, p in enumerate(participants):
        x = i
        label = alias_map.get(p, p)
        color = palette[i % len(palette)]
        bbox = FancyBboxPatch(
            (x - 0.55, y_top - 0.25), 1.1, 0.5,
            boxstyle="round,pad=0.08,rounding_size=0.1",
            facecolor=t_cfg["card_bg"], edgecolor=color, linewidth=2.0, zorder=3,
        )
        ax.add_patch(bbox)
        ax.text(x, y_top, label, ha="center", va="center", color=t_cfg["text"], fontsize=11, fontweight="bold", zorder=4)

        ax.plot([x, x], [y_top - 0.25, y_bot], color=t_cfg["spine"], linestyle="--", linewidth=1.5, zorder=1)

        bbox_bot = FancyBboxPatch(
            (x - 0.55, y_bot - 0.35), 1.1, 0.45,
            boxstyle="round,pad=0.08,rounding_size=0.1",
            facecolor=t_cfg["card_bg"], edgecolor=color, linewidth=1.5, zorder=3,
        )
        ax.add_patch(bbox_bot)
        ax.text(x, y_bot - 0.12, label, ha="center", va="center", color=t_cfg["subtext"], fontsize=10, zorder=4)

    for idx, msg in enumerate(messages):
        y = y_top - 0.7 - idx * 0.85
        x_src = p_x[msg["src"]]
        x_dst = p_x[msg["dst"]]
        dashed = msg["dashed"]
        color = t_cfg["arrow"] if not dashed else palette[2]
        style = "--" if dashed else "-"

        dx = x_dst - x_src
        offset = 0.08 if dx > 0 else -0.08
        ax.annotate(
            "",
            xy=(x_dst - offset, y),
            xytext=(x_src + offset, y),
            arrowprops=dict(
                arrowstyle="-|>",
                color=color,
                linewidth=1.8,
                linestyle=style,
                mutation_scale=16,
            ),
            zorder=2,
        )

        x_mid = (x_src + x_dst) / 2.0
        label_text = f"{idx + 1}. {msg['text']}"
        ax.text(
            x_mid, y + 0.18, label_text,
            ha="center", va="bottom",
            color=t_cfg["text"], fontsize=9.5, fontweight="normal",
            bbox=dict(boxstyle="round,pad=0.2", facecolor=t_cfg["fig_bg"], edgecolor=t_cfg["grid"], linewidth=0.8),
            zorder=4,
        )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
    plt.close("all")
    return buf.getvalue()


def _parse_flowchart_tokens(lines: list[str]) -> tuple[dict[str, dict], list[dict]]:
    nodes = {}
    edges = []

    def _extract_node(token: str) -> tuple[str, str, str]:
        token = token.strip()
        m = re.match(r"^([a-zA-Z0-9_\u4e00-\u9fa5]+)\{([^}]+)\}$", token)
        if m:
            return m.group(1).strip(), m.group(2).strip(), "diamond"
        m = re.match(r"^([a-zA-Z0-9_\u4e00-\u9fa5]+)\[([^\]]+)\]$", token)
        if m:
            return m.group(1).strip(), m.group(2).strip(), "rect"
        m = re.match(r"^([a-zA-Z0-9_\u4e00-\u9fa5]+)\(([^)]+)\)$", token)
        if m:
            return m.group(1).strip(), m.group(2).strip(), "round"
        raw = token.strip("[](){}\"' ")
        return raw, raw, "rect"

    for line in lines:
        if "-->" in line or "->" in line:
            edge_label = ""
            if "-->|" in line:
                m_edge = re.search(r"-->\|([^|]+)\|\s*", line)
                if m_edge:
                    edge_label = m_edge.group(1).strip()
                    parts = line.split(m_edge.group(0))
                    left_tok, right_tok = parts[0], parts[1]
                else:
                    parts = line.split("-->")
                    left_tok, right_tok = parts[0], parts[1]
            elif "--" in line and "-->" in line:
                m_edge = re.search(r"--\s*([^-]+?)\s*-->", line)
                if m_edge:
                    edge_label = m_edge.group(1).strip()
                    parts = re.split(r"--\s*[^-]+?\s*-->", line)
                    left_tok, right_tok = parts[0], parts[1]
                else:
                    parts = line.split("-->")
                    left_tok, right_tok = parts[0], parts[1]
            elif "-->" in line:
                parts = line.split("-->")
                left_tok, right_tok = parts[0], parts[1]
            else:
                parts = line.split("->")
                left_tok, right_tok = parts[0], parts[1]
                if ":" in right_tok:
                    rt_parts = right_tok.split(":", 1)
                    right_tok = rt_parts[0]
                    edge_label = rt_parts[1].strip()

            src_id, src_label, src_shape = _extract_node(left_tok)
            dst_id, dst_label, dst_shape = _extract_node(right_tok)

            if src_id not in nodes:
                nodes[src_id] = {"id": src_id, "label": src_label, "shape": src_shape}
            elif src_label != src_id and nodes[src_id]["label"] == src_id:
                nodes[src_id]["label"] = src_label
                nodes[src_id]["shape"] = src_shape

            if dst_id not in nodes:
                nodes[dst_id] = {"id": dst_id, "label": dst_label, "shape": dst_shape}
            elif dst_label != dst_id and nodes[dst_id]["label"] == dst_id:
                nodes[dst_id]["label"] = dst_label
                nodes[dst_id]["shape"] = dst_shape

            edges.append({"src": src_id, "dst": dst_id, "label": edge_label})
        else:
            nid, nlabel, nshape = _extract_node(line)
            if nid and nid not in nodes:
                nodes[nid] = {"id": nid, "label": nlabel, "shape": nshape}

    return nodes, edges


def _render_diagram_flowchart(title: str, lines: list[str], t_cfg: dict) -> bytes:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Polygon
    import networkx as nx
    import numpy as np

    nodes, edges = _parse_flowchart_tokens(lines)
    if not nodes:
        nodes = {
            "A": {"id": "A", "label": "开始", "shape": "round"},
            "B": {"id": "B", "label": "执行处理", "shape": "rect"},
            "C": {"id": "C", "label": "完成", "shape": "round"},
        }
        edges = [{"src": "A", "dst": "B", "label": ""}, {"src": "B", "dst": "C", "label": ""}]

    G = nx.DiGraph()
    for nid, n in nodes.items():
        G.add_node(nid, **n)
    for e in edges:
        G.add_edge(e["src"], e["dst"], label=e.get("label", ""))

    roots = [n for n in G.nodes if G.in_degree(n) == 0]
    if not roots:
        roots = [list(G.nodes)[0]]

    node_layers = {}
    visited = set()
    queue = [(r, 0) for r in roots]
    for r in roots:
        node_layers[r] = 0

    while queue:
        curr, depth = queue.pop(0)
        visited.add(curr)
        for succ in G.successors(curr):
            succ_depth = max(node_layers.get(succ, 0), depth + 1)
            node_layers[succ] = succ_depth
            if succ not in visited:
                queue.append((succ, succ_depth))

    for n in G.nodes:
        if n not in node_layers:
            node_layers[n] = 0

    max_layer = max(node_layers.values()) if node_layers else 0
    layers_dict: dict[int, list[str]] = {}
    for n, l in node_layers.items():
        layers_dict.setdefault(l, []).append(n)

    pos = {}
    for l_idx, n_list in layers_dict.items():
        y = (max_layer - l_idx) * 1.8
        count = len(n_list)
        for i, n in enumerate(n_list):
            x = (i - (count - 1) / 2.0) * 3.2
            pos[n] = (x, y)

    all_x = [p[0] for p in pos.values()]
    all_y = [p[1] for p in pos.values()]
    x_min, x_max = min(all_x) - 2.5, max(all_x) + 2.5
    y_min, y_max = min(all_y) - 1.2, max(all_y) + 1.8

    fig_w = max(11, (x_max - x_min) * 0.95)
    fig_h = max(7, (y_max - y_min) * 1.1)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=t_cfg["fig_bg"])
    ax.set_facecolor(t_cfg["fig_bg"])
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis("off")

    ax.text(
        (x_min + x_max) / 2.0, y_max - 0.4, title,
        ha="center", va="center", color=t_cfg["text"], fontsize=16, fontweight="bold",
    )

    palette = t_cfg["palette"]
    card_w, card_h = 2.2, 0.9

    for idx, (nid, n_info) in enumerate(nodes.items()):
        if nid not in pos:
            continue
        x, y = pos[nid]
        shape = n_info.get("shape", "rect")
        label = n_info.get("label", nid)
        wrapped_label = textwrap.fill(label, width=12)
        color = palette[idx % len(palette)]

        if shape == "diamond":
            d_pts = np.array([
                [x, y + card_h * 0.65],
                [x + card_w * 0.65, y],
                [x, y - card_h * 0.65],
                [x - card_w * 0.65, y],
            ])
            poly = Polygon(d_pts, closed=True, facecolor=t_cfg["card_bg"], edgecolor=color, linewidth=2.0, zorder=3)
            ax.add_patch(poly)
            ax.text(x, y, wrapped_label, ha="center", va="center", color=t_cfg["text"], fontsize=10, fontweight="bold", zorder=4)
        else:
            rounding = 0.4 if shape == "round" else 0.12
            bbox = FancyBboxPatch(
                (x - card_w / 2.0, y - card_h / 2.0), card_w, card_h,
                boxstyle=f"round,pad=0.08,rounding_size={rounding}",
                facecolor=t_cfg["card_bg"], edgecolor=color, linewidth=2.0, zorder=3,
            )
            ax.add_patch(bbox)
            ax.text(x, y, wrapped_label, ha="center", va="center", color=t_cfg["text"], fontsize=10.5, fontweight="bold", zorder=4)

    for e in edges:
        src, dst = e["src"], e["dst"]
        if src not in pos or dst not in pos:
            continue
        x1, y1 = pos[src]
        x2, y2 = pos[dst]
        label = e.get("label", "")
        dy = y2 - y1
        dx = x2 - x1
        rad = 0.15 if (dy >= 0 and abs(dx) < 0.1) else 0.0

        ax.annotate(
            "",
            xy=(x2, y2 + (card_h * 0.55 if dy < 0 else -card_h * 0.55)),
            xytext=(x1, y1 - (card_h * 0.55 if dy < 0 else -card_h * 0.55)),
            arrowprops=dict(
                arrowstyle="-|>",
                color=t_cfg["arrow"],
                linewidth=1.8,
                mutation_scale=15,
                connectionstyle=f"arc3,rad={rad}",
            ),
            zorder=2,
        )

        if label:
            mx = (x1 + x2) / 2.0 + (0.3 if dx == 0 else 0.0)
            my = (y1 + y2) / 2.0
            ax.text(
                mx, my, label,
                ha="center", va="center",
                color=t_cfg["text"], fontsize=9,
                bbox=dict(boxstyle="round,pad=0.2", facecolor=t_cfg["fig_bg"], edgecolor=t_cfg["grid"], linewidth=0.8),
                zorder=5,
            )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
    plt.close("all")
    return buf.getvalue()


def _render_diagram_architecture(title: str, lines: list[str], t_cfg: dict) -> bytes:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    layers = []
    current_layer = None
    explicit_arrows = []

    for line in lines:
        if "-->" in line or "->" in line:
            parts = re.split(r"-{1,2}>", line)
            if len(parts) >= 2:
                explicit_arrows.append((parts[0].strip(), parts[1].strip()))
            continue

        m_layer = re.match(r"^(?:Layer|层级|架构层|Tier|层)\s*[:：]?\s*(.+)$", line, re.IGNORECASE)
        if m_layer:
            layer_raw = m_layer.group(1).strip()
            m_comp = re.search(r"\[(.*?)\]", layer_raw)
            if m_comp:
                l_title = layer_raw[:m_comp.start()].strip()
                comps = [c.strip() for c in m_comp.group(1).split(",") if c.strip()]
                layers.append({"title": l_title or "架构分层", "components": comps})
                current_layer = None
            else:
                current_layer = {"title": layer_raw, "components": []}
                layers.append(current_layer)
            continue

        if current_layer is not None:
            comps = [c.strip() for c in re.split(r"[,，|、]", line) if c.strip()]
            current_layer["components"].extend(comps)
        else:
            comps = [c.strip() for c in re.split(r"[,，|、]", line) if c.strip()]
            if comps:
                layers.append({"title": "核心模块", "components": comps})

    layers = [l for l in layers if l["components"]]
    if not layers:
        layers = [
            {"title": "01 接入层 (Client)", "components": ["Web Portal", "Mobile iOS/Android", "小程序端"]},
            {"title": "02 网关与安全 (Gateway & Security)", "components": ["Kong API 网关", "WAF 防火墙", "OAuth 2.0 鉴权"]},
            {"title": "03 核心业务微服务 (Microservices)", "components": ["用户中心服务", "订单支付引擎", "智能调度服务", "审计中心"]},
            {"title": "04 数据基础设施 (Data Infrastructure)", "components": ["MySQL 集群", "Redis 缓存", "Kafka 消息管道", "ElasticSearch"]},
        ]

    n_layers = len(layers)
    max_comps = max(len(l["components"]) for l in layers)

    fig_w = max(12, max_comps * 3.4)
    fig_h = max(8, n_layers * 2.6 + 1.8)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=t_cfg["fig_bg"])
    ax.set_facecolor(t_cfg["fig_bg"])
    ax.set_xlim(-1, fig_w + 1)
    ax.set_ylim(-0.5, fig_h + 0.5)
    ax.axis("off")

    ax.text(
        fig_w / 2.0, fig_h - 0.5, title,
        ha="center", va="center", color=t_cfg["text"], fontsize=17, fontweight="bold",
    )

    palette = t_cfg["palette"]
    card_centers = {}

    for l_idx, layer in enumerate(layers):
        y_top = fig_h - 1.5 - l_idx * 2.5
        y_bot = y_top - 1.9
        l_color = palette[l_idx % len(palette)]

        container = FancyBboxPatch(
            (0.5, y_bot), fig_w - 1.0, 1.9,
            boxstyle="round,pad=0.1,rounding_size=0.15",
            facecolor=t_cfg["ax_bg"], edgecolor=t_cfg["spine"], linewidth=1.2,
            linestyle="--", alpha=0.9, zorder=1,
        )
        ax.add_patch(container)

        ax.text(
            1.0, y_top - 0.22, layer["title"],
            ha="left", va="center", color=l_color, fontsize=11, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor=t_cfg["fig_bg"], edgecolor=l_color, linewidth=1.2),
            zorder=3,
        )

        comps = layer["components"]
        n_c = len(comps)
        c_width = min(2.8, (fig_w - 3.0) / max(1, n_c))
        spacing = (fig_w - 2.5 - n_c * c_width) / max(1, n_c + 1)

        for c_idx, c_name in enumerate(comps):
            cx = 1.8 + (c_idx + 0.5) * c_width + c_idx * spacing
            cy = y_bot + 0.75
            card_centers[c_name] = (cx, cy)

            card = FancyBboxPatch(
                (cx - c_width / 2.0, cy - 0.45), c_width, 0.9,
                boxstyle="round,pad=0.08,rounding_size=0.1",
                facecolor=t_cfg["card_bg"], edgecolor=l_color, linewidth=1.8, zorder=3,
            )
            ax.add_patch(card)

            wrapped_name = textwrap.fill(c_name, width=14)
            ax.text(
                cx, cy, wrapped_name,
                ha="center", va="center", color=t_cfg["text"], fontsize=10, fontweight="bold", zorder=4,
            )

        if l_idx < n_layers - 1:
            next_y_top = fig_h - 1.5 - (l_idx + 1) * 2.5
            mid_x = fig_w / 2.0
            ax.annotate(
                "",
                xy=(mid_x, next_y_top + 0.05),
                xytext=(mid_x, y_bot - 0.05),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=t_cfg["arrow"],
                    linewidth=2.0,
                    mutation_scale=16,
                ),
                zorder=2,
            )

    for src, dst in explicit_arrows:
        if src in card_centers and dst in card_centers:
            x1, y1 = card_centers[src]
            x2, y2 = card_centers[dst]
            ax.annotate(
                "",
                xy=(x2, y2),
                xytext=(x1, y1),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=t_cfg["arrow"],
                    linewidth=1.8,
                    mutation_scale=15,
                    connectionstyle="arc3,rad=0.1",
                ),
                zorder=5,
            )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
    plt.close("all")
    return buf.getvalue()


def render_diagram(
    title: str,
    diagram_type: str,
    code: str,
    theme: str = "modern",
    emit=None,
) -> dict[str, Any]:
    """专业架构师与产品经理图表绘制工具。
    
    支持图表类型：architecture (系统架构图), flowchart (业务流程图), sequence (时序交互图), custom_code (自定义代码)。
    高分辨率输出 300 DPI 并自动发布为产物。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import networkx as nx

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Serif CJK SC",
        "Microsoft YaHei",
        "SimHei",
        "DejaVu Sans",
        "sans-serif",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    t_key = (theme or "modern").lower().strip()
    t_cfg = CHART_THEMES.get(t_key) or CHART_THEMES["modern"]
    dtype = (diagram_type or "architecture").lower().strip()

    try:
        if dtype == "custom_code" or (code and ("plt." in code or "import matplotlib" in code)):
            if not code or not code.strip():
                return {"ok": False, "error": "custom_code 模式下必须提供 code 代码"}
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name
            try:
                scope = {
                    "plt": plt,
                    "matplotlib": matplotlib,
                    "np": np,
                    "nx": nx,
                    "title": title,
                    "theme": theme,
                    "t_cfg": t_cfg,
                    "output_path": output_path,
                }
                exec(code, scope)
                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor=t_cfg["fig_bg"])
                png_bytes = pathlib.Path(output_path).read_bytes()
            finally:
                try:
                    os.remove(output_path)
                except Exception:
                    pass
                plt.close("all")
        elif dtype in ("sequence", "seq", "时序图", "顺序图"):
            lines = _clean_diagram_code(code)
            png_bytes = _render_diagram_sequence(title, lines, t_cfg)
        elif dtype in ("flowchart", "flow", "流程图", "状态机"):
            lines = _clean_diagram_code(code)
            png_bytes = _render_diagram_flowchart(title, lines, t_cfg)
        else:
            lines = _clean_diagram_code(code)
            png_bytes = _render_diagram_architecture(title, lines, t_cfg)

        # 保存为产物
        art = artifacts.save_bytes(name=f"{title}.png", data=png_bytes, kind="image/png")
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

        return {
            "ok": True,
            "artifact_id": art["id"],
            "url": art["url"],
            "name": art["name"],
            "markdown": f"![{title}]({art['url']})",
            "summary": f"已生成专业架构/流程图【{title}】并发布为产物",
        }
    except Exception as exc:
        log.exception("render_diagram failed: %s", exc)
        return {"ok": False, "error": f"架构/流程图生成失败: {exc}"}
    finally:
        plt.close("all")


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
