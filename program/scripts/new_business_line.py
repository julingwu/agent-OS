"""一条命令开一条业务线：法人主体 + 预算池 + 员工（含模型） + 私聊会话。

用法：
    python scripts/new_business_line.py --id LE-AD --name 广告事业部 \
        --pool 200000 --model deepseek-chat \
        --member "小广:文案:pack=content-v1" --member "小图:设计:model=deepseek-chat"

说明：
  * 预算池 = budgets 里 identity_id / task_id 都为 NULL 的那一行（实体级共享池）。
  * 每个任务在执行时再按池剩余额度切分，agent 之间不共享单任务额度。
  * 幂等：主体、员工、池子已存在则跳过（`--reset-pool` 可覆盖池额度）。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import secrets

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import get_conn, init_db          # noqa: E402
from app.ui import store                     # noqa: E402


def _slug(name: str, entity: str = "") -> str:
    """ID 里带业务线，避免「小文」在两条线里撞成同一个人。"""
    return hashlib.sha1(f"{entity}:{name}".encode("utf-8")).hexdigest()[:6].upper()


def ensure_entity(conn, eid: str, name: str) -> None:
    if conn.execute("SELECT 1 FROM legal_entities WHERE id=?", (eid,)).fetchone():
        return
    conn.execute(
        """INSERT INTO legal_entities (id, name, type, parent_entity_id, governance_mode,
                                       pack_policy, data_boundary, budget_mode)
           VALUES (?,?,'subsidiary','LE-GROUP-HQ','subsidiary','follow_group','isolated','pooled')""",
        (eid, name))


def ensure_pool(conn, eid: str, tokens: int, reset: bool) -> None:
    row = conn.execute(
        """SELECT id, limit_tokens FROM budgets
           WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
           ORDER BY rowid DESC LIMIT 1""", (eid,)).fetchone()
    if row and not reset:
        print(f"  pool: 已存在 {row['limit_tokens']} tokens")
        return
    if row:
        conn.execute("UPDATE budgets SET limit_tokens=?, state='active' WHERE id=?",
                     (tokens, row["id"]))
        print(f"  pool: 重置为 {tokens} tokens")
    else:
        conn.execute(
            """INSERT INTO budgets (id, legal_entity_id, identity_id, task_id,
                                    limit_amount, limit_tokens, used_tokens, state)
               VALUES (?,?,NULL,NULL,0,?,0,'active')""",
            ("BUD-" + secrets.token_hex(6), eid, tokens))
        print(f"  pool: 新建 {tokens} tokens")


def ensure_member(conn, eid: str, spec: str, default_model: str) -> None:
    parts = spec.split(":")
    name = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "专员"
    model, pack = default_model, "content-v1"
    for extra in parts[2:]:
        if extra.startswith("model="):
            model = extra.split("=", 1)[1]
        elif extra.startswith("pack="):
            pack = extra.split("=", 1)[1]

    slug = _slug(name, eid)
    pos_id, emp_id = f"POS-{slug}", f"AI-{slug}"
    if conn.execute("SELECT 1 FROM ai_employees WHERE id=?", (emp_id,)).fetchone():
        print(f"  member: {name} 已存在 ({emp_id})")
        return
    conn.execute("""INSERT OR IGNORE INTO positions (id, legal_entity_id, title, pack_id, state)
                    VALUES (?,?,?,?, 'active')""", (pos_id, eid, title, pack))
    conn.execute("""INSERT INTO ai_employees
                    (id, legal_entity_id, name, position_id, model, soul, focus, status, preset)
                    VALUES (?,?,?,?,?,?,?,'online','standard')""",
                 (emp_id, eid, name, pos_id, model,
                  f"你是{title}，按判据交付，不夸大、不越权。", "按判据交付"))
    conn.execute("""INSERT INTO identities
                    (id, name, role, legal_entity_id, secret, scopes, state, employee_id)
                    VALUES (?,?,'agent',?,?, '["task:create","task:read","accept:judge"]',
                            'active', ?)""",
                 ("ID-" + secrets.token_hex(8), name, eid, secrets.token_hex(16), emp_id))
    store.get_or_create_dm(conn, emp_id, name, eid)
    print(f"  member: {name} / {title} / model={model} / pack={pack} -> {emp_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="主体 ID，如 LE-AD")
    ap.add_argument("--name", required=True, help="主体名称，如 广告事业部")
    ap.add_argument("--pool", type=int, default=100_000, help="token 预算池")
    ap.add_argument("--model", default="deepseek-chat", help="该业务线默认模型")
    ap.add_argument("--member", action="append", default=[],
                    help='员工，格式 "名字:职位[:model=..][:pack=..]"，可重复')
    ap.add_argument("--reset-pool", action="store_true", help="覆盖已有池额度")
    args = ap.parse_args()

    init_db()
    with get_conn() as conn:
        store.ensure(conn)
        print(f"业务线 {args.id} / {args.name}")
        ensure_entity(conn, args.id, args.name)
        ensure_pool(conn, args.id, args.pool, args.reset_pool)
        for m in args.member:
            ensure_member(conn, args.id, m, args.model)
        conn.commit()

        left = conn.execute(
            """SELECT limit_tokens - used_tokens AS left FROM budgets
               WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
               ORDER BY rowid DESC LIMIT 1""", (args.id,)).fetchone()
        n = conn.execute("SELECT COUNT(*) n FROM ai_employees WHERE legal_entity_id=?",
                         (args.id,)).fetchone()["n"]
        print(f"完成：员工 {n} 名，池剩余 {left['left'] if left else 0} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
