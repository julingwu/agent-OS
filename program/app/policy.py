"""Tool-call policy for ACP runs.

ACP gives no per-tool approval request, but every `tool_call` notification does
carry the target path *before* the tool finishes. That is enough for the shell
to say "no" and cancel the session; the Low-integrity sandbox is the backstop
for whatever still slips through.

Policy (deliberately small and explainable):
  * file-writing tools must target a path inside the task workspace
  * tools listed in M0_DENY_TOOLS are refused outright (comma separated)
  * everything else is allowed and only counted
"""
from __future__ import annotations

import os
import pathlib

WRITE_TOOLS = {"write", "edit", "write_file", "create_file", "apply_patch", "patch"}


def denied_tools() -> set[str]:
    raw = os.environ.get("M0_DENY_TOOLS", "")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def _inside(path: str, root: pathlib.Path) -> bool:
    """Relative paths are relative to the task workspace, not to this process.

    DSH is launched with the workspace as its cwd, so a bare `article.md` lands
    inside it — resolving it against the server's cwd would deny a legal write.
    """
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def evaluate(update: dict, workspace: pathlib.Path) -> dict:
    """Return {'action': 'allow'|'deny', 'reason': str, 'target': str|None}."""
    tool = (update.get("title") or "").split()[0].lower() if update.get("title") else ""
    if not tool:
        tool = (update.get("kind") or "").lower()
    inputs = update.get("rawInput") or {}
    target = inputs.get("file_path") or inputs.get("path")
    command = inputs.get("command") or inputs.get("cmd")

    if tool in denied_tools():
        return {"action": "deny", "reason": f"tool '{tool}' is on the deny list",
                "target": target}

    if tool in WRITE_TOOLS and target:
        if not _inside(str(target), workspace):
            return {"action": "deny",
                    "reason": f"write outside the task workspace: {target}", "target": str(target)}

    if command:
        # shell tools stay allowed (the sandbox denies the write anyway), but the
        # command is returned so the caller can put it in the audit trail
        return {"action": "allow", "reason": "shell command (sandbox enforced)",
                "target": None, "command": str(command)[:300]}

    return {"action": "allow", "reason": "", "target": target}
