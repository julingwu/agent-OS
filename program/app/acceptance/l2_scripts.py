"""L2: run a script from scripts/l2 in a child process with a hard timeout.

"Sandbox" here means: separate process, no inherited proxy env, fixed timeout,
JSON-only result on stdout. It is not an OS-level jail; see DESIGN_GAPS.md.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "l2"


class ScriptError(RuntimeError):
    pass


def _clean_env() -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_script(name: str, args: list[str], timeout: int = 30) -> dict:
    script = SCRIPT_DIR / name
    if not script.exists():
        raise ScriptError(f"script not found: {script}")
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_clean_env(),
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise ScriptError(f"{name} exit={proc.returncode}: {(proc.stderr or '').strip()[:400]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ScriptError(f"{name} returned non-JSON: {proc.stdout[:200]}") from exc


def evaluate(check_ref: str | None, file_path: str | None) -> dict:
    """check_ref: {"script":"word_count.py","min":500,"max":800} / {"script":"file_exists.py"}."""
    cfg = json.loads(check_ref or "{}")
    script = cfg.get("script", "word_count.py")
    if not file_path:
        return {"ok": False, "detail": "没有可检查的产出文件", "reason": "no_artifact"}
    if script == "word_count.py":
        r = run_script(script, ["--file", file_path, "--min", str(cfg.get("min", 0)),
                                "--max", str(cfg.get("max", 10**9))])
        r["detail"] = f"字数 {r.get('count')}（区间 {r.get('min')}-{r.get('max')}）"
        return r
    if script == "file_exists.py":
        r = run_script(script, ["--file", file_path, "--min-bytes", str(cfg.get("min_bytes", 1))])
        r["detail"] = f"文件 {'可用' if r.get('ok') else '不可用'}（{r.get('bytes', 0)} 字节）"
        return r
    if script == "py_syntax.py":
        r = run_script(script, ["--file", file_path])
        r["detail"] = "语法检查通过" if r.get("ok") else f"语法错误：{r.get('error', '')[:120]}"
        return r
    if script == "markdown_structure.py":
        r = run_script(script, ["--file", file_path, "--min-bytes", str(cfg.get("min_bytes", 200))])
        r["detail"] = "文档结构完整" if r.get("ok") else (r.get("error") or "文稿缺少一级或二级结构化标题（# 或 ##）")
        return r
    if script == "data_structure.py":
        r = run_script(script, ["--file", file_path, "--min-bytes", str(cfg.get("min_bytes", 200))])
        r["detail"] = "数据结构规范" if r.get("ok") else (r.get("error") or "未发现有效的数据结构或分析结论")
        return r
    return {"ok": False, "detail": f"未知脚本 {script}"}
