"""Kernel Router: Pluggable Dual-Engine Kernel & Automatic Failover.

Modes:
- "auto_failover" (default): High-reliability NativeKernel + graceful failover
- "native": In-process NativeKernel (zero child-process conflicts, ultra fast)
- "dsh": DeepSeek Harness CLI ACP engine (runner_acp.py)
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from .. import telemetry
from . import dsh_adapter as base
from . import runner_native

log = logging.getLogger("kernel_router")

_CURRENT_MODE = os.environ.get("M0_KERNEL_MODE", "auto_failover")


def get_kernel_mode() -> str:
    global _CURRENT_MODE
    return _CURRENT_MODE


def set_kernel_mode(mode: str) -> str:
    global _CURRENT_MODE
    clean = (mode or "").strip().lower()
    if clean in ("auto_failover", "native", "dsh"):
        _CURRENT_MODE = clean
        os.environ["M0_KERNEL_MODE"] = clean
    return _CURRENT_MODE


def get_kernel_status() -> dict[str, Any]:
    mode = get_kernel_mode()
    dsh_avail = os.path.isfile(str(base.DSH_CLI)) if hasattr(base, "DSH_CLI") else False
    active = "NativeKernel" if mode in ("native", "auto_failover") else "DSH-ACP"
    return {
        "mode": mode,
        "available_modes": ["auto_failover", "native", "dsh"],
        "active_engine": active,
        "dsh_available": dsh_avail,
        "host_protection": "STRICT_PID_ONLY"
    }


def _has_high_risk_tools(conn: sqlite3.Connection, task_id: str | None) -> bool:
    if not task_id:
        return False
    try:
        role_tag = None
        wrow = conn.execute("SELECT role_tag FROM wbs_items WHERE task_id=?", (task_id,)).fetchone()
        if wrow and wrow["role_tag"]:
            role_tag = wrow["role_tag"]
        if not role_tag:
            row = conn.execute("SELECT owner_employee_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row and row["owner_employee_id"]:
                erow = conn.execute(
                    "SELECT r.tag FROM ai_employees e JOIN role_templates r ON r.id=e.role_id WHERE e.id=?",
                    (row["owner_employee_id"],)
                ).fetchone()
                if erow:
                    role_tag = erow["tag"]
        from .. import tools_acl
        tools = tools_acl.tools_for(conn, role_tag) if role_tag else set()
        high_risk = {"db_exec", "file_delete", "shell_exec"}
        return bool(tools & high_risk)
    except Exception:
        return False


def execute_kernel(conn: sqlite3.Connection, run_id: str, *, task_id: str,
                   entity: str, actor: str, prompt: str, runner: str = "auto",
                   resume_session: str | None = None, est_tokens: int = 120000,
                   timeout: int = 300, hb_interval: int = 15,
                   pause_at_tokens: int | None = None) -> dict:
    # 1. runner == "mock_acp": 强制走 Mock ACP 流程，严禁路由至 Native
    if runner == "mock_acp":
        log.info("[%s] Routing directly to Mock ACP runner", run_id)
        from . import runner_acp
        return runner_acp.execute_acp(
            conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
            prompt=prompt, runner="mock_acp", resume_session=resume_session,
            est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
            pause_at_tokens=pause_at_tokens
        )

    # 2. runner in ("acp", "dsh"): 强制拉起 ACP/DSH 通道
    if runner in ("acp", "dsh"):
        import shutil
        if not shutil.which("dsh"):
            log.warning("[%s] DSH CLI not found on system, falling back to NativeKernel", run_id)
            return runner_native.execute_native(
                conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
                prompt=prompt, runner="native", resume_session=resume_session,
                est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
                pause_at_tokens=pause_at_tokens
            )
        log.info("[%s] Routing directly to ACP runner (runner=%s)", run_id, runner)
        from . import runner_acp
        return runner_acp.execute_acp(
            conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
            prompt=prompt, runner=runner, resume_session=resume_session,
            est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
            pause_at_tokens=pause_at_tokens
        )

    # 3. runner == "native": 强制走 NativeKernel
    if runner == "native":
        log.info("[%s] Routing directly to NativeKernel", run_id)
        return runner_native.execute_native(
            conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
            prompt=prompt, runner="native", resume_session=resume_session,
            est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
            pause_at_tokens=pause_at_tokens
        )

    # 4. runner in ("auto", "auto_failover"): 智能路由
    high_risk = _has_high_risk_tools(conn, task_id)
    prefer_dsh = os.environ.get("M0_PREFER_DSH", "0") == "1"

    if high_risk or prefer_dsh:
        try:
            log.info("[%s] High risk tools (%s) or M0_PREFER_DSH (%s), routing to ACP",
                     run_id, high_risk, prefer_dsh)
            from . import runner_acp
            res = runner_acp.execute_acp(
                conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
                prompt=prompt, runner="acp", resume_session=resume_session,
                est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
                pause_at_tokens=pause_at_tokens
            )
            if res.get("ok"):
                return res
            log.warning("[%s] ACP returned non-ok result, failing over to NativeKernel...", run_id)
        except Exception as exc:
            log.warning("[%s] ACP exception %s, triggering failover to NativeKernel...", run_id, exc)

        telemetry.emit(
            "KERNEL_FAILOVER",
            run_id=run_id,
            message="[KERNEL FAILOVER] 检测到外部 CLI 异常或超时，已自动无缝热切换至 Native 内核保障流水线连续运行"
        )

    log.info("[%s] Routing to NativeKernel (default)", run_id)
    return runner_native.execute_native(
        conn, run_id=run_id, task_id=task_id, entity=entity, actor=actor,
        prompt=prompt, runner="native", resume_session=resume_session,
        est_tokens=est_tokens, timeout=timeout, hb_interval=hb_interval,
        pause_at_tokens=pause_at_tokens
    )
