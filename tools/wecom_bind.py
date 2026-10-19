"""Bind a WeCom UserID to an OA identity (so messages act as that identity).

    python tools/wecom_bind.py --user ZhangSan --identity ID-da610f30a4e94e0d
    python tools/wecom_bind.py --list
    python tools/wecom_bind.py --unbind --user ZhangSan
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import connect, init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="企微 UserID（通讯录里那个）")
    ap.add_argument("--identity", help="系统身份 ID（ID-…）")
    ap.add_argument("--name", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--unbind", action="store_true")
    a = ap.parse_args()

    init_db()
    conn = connect()
    try:
        if a.list:
            for r in conn.execute(
                    """SELECT u.wecom_userid, u.name, u.identity_id, i.role, i.legal_entity_id
                       FROM wecom_users u JOIN identities i ON i.id = u.identity_id
                       ORDER BY u.wecom_userid"""):
                print(f"{r['wecom_userid']:<20} {r['identity_id']:<22} {r['role']:<8} "
                      f"{r['legal_entity_id']:<14} {r['name'] or ''}")
            return 0
        if not a.user:
            print("--user is required", file=sys.stderr)
            return 2
        if a.unbind:
            conn.execute("UPDATE wecom_users SET state='inactive' WHERE wecom_userid=?", (a.user,))
            conn.commit()
            print(f"unbound {a.user}")
            return 0
        if not a.identity:
            print("--identity is required", file=sys.stderr)
            return 2
        row = conn.execute("SELECT id, role FROM identities WHERE id=? AND state='active'",
                           (a.identity,)).fetchone()
        if row is None:
            print(f"unknown identity {a.identity}", file=sys.stderr)
            return 1
        conn.execute(
            """INSERT INTO wecom_users (wecom_userid, identity_id, name, state)
               VALUES (?,?,?,'active')
               ON CONFLICT(wecom_userid) DO UPDATE SET identity_id=excluded.identity_id,
                 name=excluded.name, state='active'""", (a.user, a.identity, a.name))
        conn.commit()
        print(f"bound {a.user} -> {a.identity} ({row['role']})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
