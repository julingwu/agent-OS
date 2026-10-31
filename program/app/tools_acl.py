"""Tool ACL：角色绑定的工具在执行期真正拦截。

规则：
  * 每个角色在 `role_templates.tools` 里声明可用工具；
  * `db_query` 只允许只读语句（SELECT / PRAGMA / WITH ... SELECT），
    出现 UPDATE / DELETE / DROP / ALTER / INSERT / ATTACH 直接拒绝；
  * `code_exec` 只允许在沙箱工作区内落盘（由 sandbox 层保证，这里只做声明校验）；
  * 越权 → raise PermissionError，并写 `tool_denied` 审计。
"""
from __future__ import annotations

import re
import sqlite3

from .audit import write_audit

READONLY_START = re.compile(r"^\s*(select|with|pragma|explain)\b", re.I)
WRITE_TOKEN = re.compile(r"\b(insert|update|delete|drop|alter|attach|detach|replace|create|truncate)\b", re.I)

DEFAULT_TOOLS = {
    "dev": {"code_exec", "file_write"},
    "qa": {"code_exec"},
    "content": {"file_write"},
    "design": {"file_write"},
    "data": {"db_query", "file_write"},
    "translate": {"file_write"},
    "product": {"file_write", "db_query"},
    "architect": {"code_exec", "file_write"},
    "audit": {"db_query", "file_write"},
    "ops": {"code_exec", "file_write"},
    "growth": {"file_write", "db_query"},
    "legal": {"file_write"},
}


def tools_for(conn: sqlite3.Connection, role_tag: str | None) -> set[str]:
    if not role_tag:
        return set()
    from .ui import roles as _roles
    _roles.ensure(conn)
    row = conn.execute("SELECT tools FROM role_templates WHERE tag=?", (role_tag,)).fetchone()
    if row and row["tools"]:
        return {t.strip() for t in row["tools"].split(",") if t.strip()}
    return DEFAULT_TOOLS.get(role_tag, set())


def check(conn: sqlite3.Connection, *, role_tag: str | None, tool: str,
          sql: str | None = None, entity: str = "LE-GROUP-HQ",
          target: str | None = None, raise_on_deny: bool = True) -> bool:
    """返回 True=允许；False/抛错=拒绝，并写审计。"""
    allowed = tools_for(conn, role_tag)
    reason = None
    if tool not in allowed:
        reason = f"role {role_tag or '-'} 未绑定工具 {tool}"
    elif tool == "db_query" and sql:
        stmt = sql.strip()
        if not READONLY_START.match(stmt) or WRITE_TOKEN.search(stmt):
            reason = "db_query 只允许只读语句（拒绝 UPDATE/DELETE/DROP 等）"

    if reason is None:
        return True

    write_audit(conn, legal_entity_id=entity, verb="tool", task_id=None, layer="system",
                action="tool_denied", target=target or tool,
                payload={"role": role_tag, "tool": tool, "sql": (sql or "")[:200]},
                response={"reason": reason})
    conn.commit()
    if raise_on_deny:
        raise PermissionError(f"ToolDenied: {reason}")
    return False
