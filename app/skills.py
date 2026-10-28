"""技能：把"怎么做事"沉淀成可复用的指令包（v1.8）。

为什么不是向量知识库：
  技能要的是**可执行流程**，不是相似文档。模型真正需要的是"遇到 X 就按这几步做"，
  于是技能 = 名字 + 触发描述 + 指令正文（可含 {input} 占位）。
  取用时由 `use_skill` 把正文塞回上下文，模型自己照着执行 ——
  不额外起一次模型调用，省钱、可控、也方便人评审。

隔离：
  bot_id 为空 = 全局技能（所有 Bot 可用）；否则只属于那个 Bot。
  name 在同一归属内唯一。
"""

from __future__ import annotations

import secrets

from . import db
from . import events as ev

MAX_NAME = 40
MAX_DESC = 200
MAX_BODY = 8000

_READY_DBS: set[str] = set()


def ensure(conn) -> None:
    if db.db_path() in _READY_DBS:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            id          TEXT PRIMARY KEY,
            bot_id      TEXT,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL,
            use_count   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_owner_name"
        " ON skills(IFNULL(bot_id,''), name)"
    )
    conn.commit()
    _READY_DBS.add(db.db_path())


def _row_to_dict(row) -> dict:
    return dict(row)


def create(conn, *, bot_id: str | None, name: str, description: str,
           body: str) -> dict:
    ensure(conn)
    name = (name or "").strip()[:MAX_NAME]
    body = (body or "").strip()[:MAX_BODY]
    if not name or not body:
        raise ValueError("name 和 body 都不能为空")
    skill_id = "skl_" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO skills (id, bot_id, name, description, body, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (skill_id, bot_id, name, (description or "").strip()[:MAX_DESC],
         body, ev.now_iso()),
    )
    conn.commit()
    return get(conn, skill_id)


def get(conn, skill_id: str) -> dict | None:
    ensure(conn)
    row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_by_name(conn, name: str, *, bot_id: str | None = None) -> dict | None:
    """先找这个 Bot 自己的，再找全局的。同名时优先私有（bot 可以覆盖团队默认流程）。"""
    ensure(conn)
    if bot_id:
        row = conn.execute(
            "SELECT * FROM skills WHERE name=? AND bot_id=?", (name, bot_id)).fetchone()
        if row:
            return _row_to_dict(row)
    row = conn.execute(
        "SELECT * FROM skills WHERE name=? AND (bot_id IS NULL OR bot_id='')",
        (name,)).fetchone()
    return _row_to_dict(row) if row else None


def exists(conn, *, bot_id: str | None, name: str) -> bool:
    """同一归属内名称唯一：全局与私有可以同名（私有覆盖全局是设计意图，不是冲突）。"""
    ensure(conn)
    if bot_id:
        row = conn.execute("SELECT 1 FROM skills WHERE bot_id=? AND name=?",
                           (bot_id, name)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM skills WHERE (bot_id IS NULL OR bot_id='') AND name=?",
            (name,)).fetchone()
    return row is not None


def list_for(conn, *, bot_id: str | None = None, include_global: bool = True) -> list[dict]:
    ensure(conn)
    if bot_id and include_global:
        rows = conn.execute(
            "SELECT * FROM skills WHERE bot_id=? OR bot_id IS NULL OR bot_id=''"
            " ORDER BY COALESCE(bot_id,'') DESC, name", (bot_id,)).fetchall()
    elif bot_id:
        rows = conn.execute(
            "SELECT * FROM skills WHERE bot_id=? ORDER BY name", (bot_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
    return [_row_to_dict(r) for r in rows]


def update(conn, skill_id: str, *, name: str | None = None,
           description: str | None = None, body: str | None = None) -> dict | None:
    ensure(conn)
    if get(conn, skill_id) is None:
        return None
    fields: dict = {"updated_at": ev.now_iso()}
    if name is not None:
        fields["name"] = name.strip()[:MAX_NAME]
    if description is not None:
        fields["description"] = description.strip()[:MAX_DESC]
    if body is not None:
        fields["body"] = body.strip()[:MAX_BODY]
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE skills SET {assignments} WHERE id=?",
                 (*fields.values(), skill_id))
    conn.commit()
    return get(conn, skill_id)


def delete(conn, skill_id: str) -> bool:
    ensure(conn)
    cur = conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    conn.commit()
    return cur.rowcount > 0


def render(skill: dict, user_input: str | None = None) -> str:
    """把技能正文渲染成模型可执行的指令。{input} 是唯一占位符（故意只留一个，少就是多）。"""
    body = skill["body"]
    if "{input}" in body:
        body = body.replace("{input}", (user_input or "").strip())
    elif user_input:
        body = f"{body}\n\n本次输入：{user_input.strip()}"
    return body


def bump_use(skill_id: str) -> None:
    try:
        with db.get_conn() as conn:
            conn.execute("UPDATE skills SET use_count=use_count+1 WHERE id=?",
                         (skill_id,))
            conn.commit()
    except Exception:                                 # noqa: BLE001 - 计数失败不值得报错
        pass
