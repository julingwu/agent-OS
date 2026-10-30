"""会话级结构化黑板机制（Session Blackboard）——对标 Hermes 工业级断点接力。

解决核心问题：
  1. 记录会话核心任务目标（last_goal）；
  2. 记录已生成的交付物清单（artifacts: 研报、图表、脚本、数据）；
  3. 记录已完成的关键执行步骤（completed_steps）；
  4. 记录断点接力指引（next_step_hint）；
  5. 彻底解决用户发送「继续」时的失忆与重复执行，实现跨轮无缝断点接力。
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from datetime import datetime
from typing import Any

from . import tools

logger = logging.getLogger("app.agent.blackboard")

BLACKBOARD_DIR = (tools.WORKSPACE / ".blackboard").resolve()
try:
    BLACKBOARD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

CONTINUATION_PATTERN = re.compile(
    r"^\s*(继续|接着|接着做|接着往下|往下|继续执行|继续写|继续分析|下一?步|断点接力|断点|接着来|continue|go\s*on|proceed|next|resume)\s*[!！.。?？~～]*$",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def init_blackboard_schema(conn) -> None:
    """初始化黑板 SQLite 存储表（如果不存在）。"""
    if conn is None:
        return
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_blackboard (
                session_id TEXT PRIMARY KEY,
                last_goal TEXT DEFAULT '',
                artifacts_json TEXT DEFAULT '[]',
                completed_steps_json TEXT DEFAULT '[]',
                next_step_hint TEXT DEFAULT '',
                style_mode TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            """
        )
        conn.commit()
    except Exception as exc:
        logger.warning(f"[Blackboard] init schema failed: {exc}")

    try:
        conn.execute("ALTER TABLE session_blackboard ADD COLUMN style_mode TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass


def get_blackboard(conn, session_id: str) -> dict[str, Any]:
    """获取指定 session_id 的黑板状态，具备 DB 与本地落盘双重容错。"""
    if not session_id:
        return _empty_blackboard("")

    # 1. 尝试从 SQLite 读取
    if conn is not None:
        try:
            init_blackboard_schema(conn)
            row = conn.execute(
                "SELECT session_id, last_goal, artifacts_json, completed_steps_json, next_step_hint, style_mode, updated_at "
                "FROM session_blackboard WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                r = dict(row)
                try:
                    arts = json.loads(r.get("artifacts_json") or "[]")
                except Exception:
                    arts = []
                try:
                    steps = json.loads(r.get("completed_steps_json") or "[]")
                except Exception:
                    steps = []
                return {
                    "session_id": session_id,
                    "last_goal": r.get("last_goal") or "",
                    "artifacts": arts,
                    "completed_steps": steps,
                    "next_step_hint": r.get("next_step_hint") or "",
                    "style_mode": r.get("style_mode") or "",
                    "updated_at": r.get("updated_at") or "",
                }
        except Exception as exc:
            logger.debug(f"[Blackboard] read from db failed, fallback to file: {exc}")

    # 2. 从本地文件读取回退
    file_path = BLACKBOARD_DIR / f"{session_id}.json"
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "session_id": session_id,
                    "last_goal": data.get("last_goal") or "",
                    "artifacts": data.get("artifacts") or [],
                    "completed_steps": data.get("completed_steps") or [],
                    "next_step_hint": data.get("next_step_hint") or "",
                    "style_mode": data.get("style_mode") or "",
                    "updated_at": data.get("updated_at") or "",
                }
        except Exception as exc:
            logger.debug(f"[Blackboard] read file fallback failed: {exc}")

    return _empty_blackboard(session_id)


def save_blackboard(
    conn,
    session_id: str,
    *,
    last_goal: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    completed_steps: list[str] | None = None,
    next_step_hint: str | None = None,
    style_mode: str | None = None,
) -> dict[str, Any]:
    """更新并持久化会话黑板。"""
    if not session_id:
        return _empty_blackboard("")

    current = get_blackboard(conn, session_id)
    if last_goal is not None and last_goal.strip():
        current["last_goal"] = last_goal.strip()
    if style_mode is not None and style_mode.strip():
        current["style_mode"] = style_mode.strip()
    if artifacts is not None:
        # 路径去重
        existing_paths = {a.get("path") for a in current["artifacts"] if isinstance(a, dict)}
        for art in artifacts:
            if isinstance(art, dict) and art.get("path") and art["path"] not in existing_paths:
                current["artifacts"].append(art)
                existing_paths.add(art["path"])
    if completed_steps is not None:
        current["completed_steps"].extend([s for s in completed_steps if s and s not in current["completed_steps"]])
        # 保留最近 30 项
        current["completed_steps"] = current["completed_steps"][-30:]
    if next_step_hint is not None:
        current["next_step_hint"] = next_step_hint.strip()
    current["updated_at"] = _now()

    # 1. 写入 SQLite
    if conn is not None:
        try:
            init_blackboard_schema(conn)
            conn.execute(
                """
                INSERT INTO session_blackboard (session_id, last_goal, artifacts_json, completed_steps_json, next_step_hint, style_mode, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_goal = excluded.last_goal,
                    artifacts_json = excluded.artifacts_json,
                    completed_steps_json = excluded.completed_steps_json,
                    next_step_hint = excluded.next_step_hint,
                    style_mode = excluded.style_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    current["last_goal"],
                    json.dumps(current["artifacts"], ensure_ascii=False),
                    json.dumps(current["completed_steps"], ensure_ascii=False),
                    current["next_step_hint"],
                    current.get("style_mode") or "",
                    current["updated_at"],
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning(f"[Blackboard] db save error: {exc}")

    # 2. 写入文件备份
    try:
        BLACKBOARD_DIR.mkdir(parents=True, exist_ok=True)
        file_path = BLACKBOARD_DIR / f"{session_id}.json"
        file_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Blackboard] file save error: {exc}")

    return current


def record_tool_artifact_and_step(
    conn,
    session_id: str,
    tool_name: str,
    args: dict,
    result: dict,
) -> None:
    """在工具执行完毕后，自动嗅探产物并登记关键执行步骤。"""
    if not session_id or not isinstance(result, dict):
        return

    ok = bool(result.get("ok", True)) and not result.get("status") == "waiting_for_human"
    new_artifacts: list[dict[str, Any]] = []
    step_desc = ""

    if tool_name == "fs_write":
        path = str(result.get("path") or args.get("path") or args.get("file") or "").strip()
        if ok and path:
            ext = pathlib.Path(path).suffix.lower()
            kind = "code" if ext in (".py", ".js", ".ts", ".sh") else ("doc" if ext in (".md", ".txt", ".html") else "file")
            new_artifacts.append({"path": path, "kind": kind, "title": path, "created_at": _now()})
            step_desc = f"fs_write: 保存文件 {path}"
        else:
            step_desc = f"fs_write: 写入失败 ({result.get('error', '')[:40]})"

    elif tool_name == "render_chart":
        chart_path = str(result.get("chart_path") or "").strip()
        title = str(args.get("title") or "数据可视化图表")
        if ok and chart_path:
            new_artifacts.append({"path": chart_path, "kind": "chart", "title": title, "created_at": _now()})
            step_desc = f"render_chart: 生成可视化图表 '{title}' -> {chart_path}"
        else:
            step_desc = f"render_chart: 图表渲染完成"

    elif tool_name == "render_diagram":
        diag_path = str(result.get("diagram_path") or "").strip()
        title = str(args.get("title") or "系统架构图")
        if ok and diag_path:
            new_artifacts.append({"path": diag_path, "kind": "diagram", "title": title, "created_at": _now()})
            step_desc = f"render_diagram: 生成架构图 '{title}' -> {diag_path}"
        else:
            step_desc = f"render_diagram: 架构图生成完成"

    elif tool_name == "publish_artifact":
        path = str(result.get("path") or args.get("path") or "").strip()
        name = str(args.get("name") or path)
        if ok and path:
            new_artifacts.append({"path": path, "kind": "artifact", "title": name, "created_at": _now()})
            step_desc = f"publish_artifact: 发布核心产物 '{name}' -> {path}"
        else:
            step_desc = f"publish_artifact: 产物发布调用"

    elif tool_name == "web_search":
        q = str(args.get("query") or "")[:35]
        cnt = len(result.get("results") or []) if isinstance(result.get("results"), list) else 1
        step_desc = f"web_search: 检索 '{q}' (返回 {cnt} 条有效信源)"

    elif tool_name == "web_fetch":
        url = str(args.get("url") or "")[:45]
        step_desc = f"web_fetch: 抓取并解析网页 {url}"

    elif tool_name == "exec":
        cmd = str(args.get("cmd") or "")[:40]
        step_desc = f"exec: 执行 `{cmd}` ({'成功' if ok else '失败'})"

    elif tool_name in ("fs_read", "doc_read"):
        path = str(args.get("path") or args.get("file") or "")
        step_desc = f"{tool_name}: 查阅资料 {path}"

    elif tool_name == "fs_edit":
        path = str(args.get("path") or args.get("file") or "")
        step_desc = f"fs_edit: 局部修订 {path}"

    else:
        step_desc = f"{tool_name}: 调用 ({'成功' if ok else '失败'})"

    save_blackboard(
        conn,
        session_id,
        artifacts=new_artifacts if new_artifacts else None,
        completed_steps=[step_desc] if step_desc else None,
    )


def is_continuation_intent(user_text: str | None) -> bool:
    """判定用户输入是否为「继续」、「接着做」等断点接力意图。"""
    if not user_text:
        return False
    clean = user_text.strip()
    if CONTINUATION_PATTERN.match(clean):
        return True
    if clean in ("继续", "继续执行", "接着", "接着做", "接着往下做", "continue", "go on", "resume"):
        return True
    return False


def format_blackboard_prompt(board: dict[str, Any]) -> str:
    """生成注入 Prompt 的结构化任务黑板 / 断点进度看板。"""
    goal = board.get("last_goal") or "待定"
    artifacts = board.get("artifacts") or []
    completed_steps = board.get("completed_steps") or []
    next_hint = board.get("next_step_hint") or "从已有成果直接向下推进，杜绝重复第一步！"

    charts = [a["path"] for a in artifacts if isinstance(a, dict) and a.get("kind") in ("chart", "diagram")]
    docs = [a["path"] for a in artifacts if isinstance(a, dict) and a.get("kind") in ("doc", "artifact")]
    files = [a["path"] for a in artifacts if isinstance(a, dict) and a.get("kind") in ("code", "file")]

    art_lines: list[str] = []
    if charts:
        art_lines.append(f"  * 已就绪图表/架构图：{', '.join(charts)}")
    if docs:
        art_lines.append(f"  * 已就绪报告/文档：{', '.join(docs)}")
    if files:
        art_lines.append(f"  * 已就绪代码/数据：{', '.join(files)}")
    if not art_lines:
        art_text = "  * （暂无持久化产物文件）"
    else:
        art_text = "\n".join(art_lines)

    step_lines: list[str] = []
    for idx, s in enumerate(completed_steps[-10:], 1):
        step_lines.append(f"  {idx}. {s}")
    steps_text = "\n".join(step_lines) if step_lines else "  - （尚无历史执行步骤）"

    return (
        "【当前任务黑板 / 断点进度看板（Hermes Session Blackboard）】\n"
        f"- 核心任务目标：{goal}\n"
        "- 已生成产物清单（重要：以下文件已在工作区生成，严禁重复生成或重复爬取！）：\n"
        f"{art_text}\n"
        "- 之前已完成的关键执行步骤：\n"
        f"{steps_text}\n"
        f"- 断点接力指引：{next_hint}\n"
        "⚡ 注意：用户回复「继续」时，直接依据上述黑板已有成果往下做，严禁失忆，绝不从第一步重头再来！"
    )


def _empty_blackboard(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "last_goal": "",
        "artifacts": [],
        "completed_steps": [],
        "next_step_hint": "",
        "style_mode": "",
        "updated_at": _now(),
    }
