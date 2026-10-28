"""本地操作的护栏：目录白名单 + 审批预览（v1.9）。

为什么需要它：
  * 审批卡上原来只有 `path=..., content=...` 被截断的一行，用户看不到"到底会改什么"，
    点"允许"基本靠信任。预览要给出**将要发生的变化**，而不是参数原文。
  * 逐次审批防的是"意外"，防不了"范围失控"：模型完全可能写到 C:\\Windows 或 .ssh 目录。
    白名单是第二道闸：留空 = 不限制（保持旧行为），一旦配置，越界直接拒绝、连审批都不弹。

设计取舍：
  * 白名单只在服务端判断，不下发到 exe —— 客户端可以被绕过，策略必须收在能审计的一侧。
  * 路径比较前统一小写 + 反斜杠归一 + 去掉尾部分隔符，避免 C:\\Users\\me\\ 与 c:/users/me 判不相等。
"""

from __future__ import annotations

import json
import ntpath
import os

from . import db

SETTING_KEY = "local_allow_dirs"

# 这些工具的第一个路径参数需要过白名单
_PATH_TOOLS = {
    "local_list": "path",
    "local_read": "path",
    "local_write": "path",
    "local_exec": "cwd",
}

_EXCERPT_CHARS = 400


def allow_dirs() -> list[str]:
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_settings WHERE key=?", (SETTING_KEY,)
            ).fetchone()
    except Exception:                              # noqa: BLE001 - 读不到就当没配置
        return []
    if row is None:
        return []
    try:
        value = json.loads(row["value_json"])
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _norm(path: str) -> str:
    """归一化路径，容忍用户常见的粘贴错误：正斜杠、重复反斜杠、外层引号、尾部分隔符，
    并通过 ntpath.normpath 消除 '..' 等相对路径逃逸（服务端跑在 Linux 下也能准确解析 Windows 路径）。
    """
    p = (path or "").strip().strip('"').strip("'").replace("/", "\\")
    if not p:
        return ""
    # 先折叠重复分隔符再 normpath，彻底规避平台差异与目录穿越
    while "\\\\" in p:
        p = p.replace("\\\\", "\\")
    return ntpath.normpath(p).rstrip("\\").lower()


def _is_under(path: str, root: str) -> bool:
    target, base = _norm(path), _norm(root)
    if not target or not base:
        return False
    return target == base or target.startswith(base + "\\")


def check_path(tool: str, path: str | None) -> tuple[bool, str]:
    """返回 (是否允许, 拒绝原因)。白名单为空 = 不限制。"""
    roots = allow_dirs()
    if not roots or not path:
        return True, ""
    for root in roots:
        if _is_under(path, root):
            return True, ""
    return False, (f"路径不在允许目录内：{path}\n允许目录：{', '.join(roots)}\n"
                   "（设置 → 通用 → 本地允许目录）")


def _clip(text: str, limit: int = _EXCERPT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def build_preview(tool: str, args: dict) -> dict:
    """把参数翻译成"用户能看懂的将要发生的事"。只描述，不隐藏。"""
    if tool == "local_write":
        content = str(args.get("content") or "")
        return {"kind": "write", "path": args.get("path", ""),
                "bytes": len(content.encode("utf-8")),
                "lines": content.count("\n") + 1 if content else 0,
                "excerpt": _clip(content)}
    if tool == "local_exec":
        return {"kind": "exec", "cmd": _clip(str(args.get("cmd") or ""), 600),
                "cwd": args.get("cwd", ""),
                "timeout_s": args.get("timeout_s", 60)}
    if tool == "local_read":
        return {"kind": "read", "path": args.get("path", ""),
                "max_bytes": args.get("max_bytes", 200_000)}
    if tool == "local_list":
        return {"kind": "list", "path": args.get("path", "")}
    if tool == "local_open":
        return {"kind": "open", "target": args.get("target", "")}
    if tool == "local_type":
        text = str(args.get("text") or "")
        return {"kind": "type", "title": args.get("title", ""),
                "chars": len(text), "excerpt": _clip(text, 200)}
    if tool == "local_keys":
        return {"kind": "keys", "keys": args.get("keys", ""),
                "title": args.get("title", "")}
    if tool == "local_click":
        return {"kind": "click", "x": args.get("x"), "y": args.get("y"),
                "button": args.get("button", "left")}
    if tool == "local_close":
        return {"kind": "close", "title": args.get("title", "")}
    if tool == "local_apps":
        return {"kind": "apps", "query": args.get("query", "")}
    if tool == "local_focus":
        return {"kind": "focus", "title": args.get("title", "")}
    if tool == "local_windows":
        return {"kind": "windows"}
    if tool == "local_screenshot":
        return {"kind": "screenshot", "window_title": args.get("window_title", "")}
    return {"kind": "other", "args": {k: _clip(str(v), 120) for k, v in list(args.items())[:6]}}


def describe(tool: str, args: dict) -> str:
    """审批卡上那一行摘要（比原来的 `k=v, k=v` 更像人话）。"""
    preview = build_preview(tool, args)
    kind = preview["kind"]
    if kind == "write":
        return f"{preview['path']}（{preview['lines']} 行 / {preview['bytes']} 字节）"
    if kind == "exec":
        cwd = f"，目录 {preview['cwd']}" if preview["cwd"] else ""
        return f"{preview['cmd']}{cwd}"
    if kind in ("read", "list"):
        return str(preview.get("path") or "…")
    if kind == "open":
        return f"打开 {preview['target']}"
    if kind == "type":
        target = f"到「{preview['title']}」" if preview.get("title") else ""
        return f"输入 {preview['chars']} 个字符{target}：{preview['excerpt']}"
    if kind == "keys":
        return f"按 {preview['keys']}"
    if kind == "click":
        return f"点击 ({preview['x']}, {preview['y']}) {preview['button']}"
    if kind == "close":
        return f"关闭「{preview['title']}」"
    if kind == "focus":
        return f"切到「{preview['title']}」"
    if kind == "apps":
        return f"查找应用「{preview.get('query') or '全部'}」"
    if kind == "windows":
        return "列窗口"
    if kind == "screenshot":
        target = preview.get("window_title")
        return f"截屏（{target}）" if target else "截全屏"
    return os.path.basename(str(preview.get("args", "")))
