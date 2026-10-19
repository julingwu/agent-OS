"""Issue / list / revoke API identities.

    python tools/auth_admin.py issue --name ceo-01 --role ceo
    python tools/auth_admin.py list
    python tools/auth_admin.py revoke --id ID-xxxx

The secret is printed exactly once. Store it in .env (gitignored) or hand it to
the agent; the database keeps the key because HMAC needs it to verify.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import identity  # noqa: E402
from app.db import connect, init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue")
    p_issue.add_argument("--name", required=True)
    p_issue.add_argument("--role", default="agent",
                         choices=["system", "ceo", "manager", "agent"])
    p_issue.add_argument("--legal-entity-id", default="LE-GROUP-HQ")

    p_list = sub.add_parser("list")
    p_list.add_argument("--all", action="store_true")

    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--id", required=True)

    a = ap.parse_args()
    init_db()
    conn = connect()
    try:
        if a.cmd == "issue":
            out = identity.issue(conn, name=a.name, role=a.role,
                                 legal_entity_id=a.legal_entity_id)
            conn.commit()
            print(f"id={out['id']}\nrole={out['role']}\nsecret={out['secret']}")
            print("\n# add to .env (never commit):")
            print(f"OA_ID={out['id']}\nOA_SECRET={out['secret']}")
        elif a.cmd == "list":
            sql = "SELECT id, name, role, state, created_at FROM identities"
            if not a.all:
                sql += " WHERE state = 'active'"
            for r in conn.execute(sql + " ORDER BY created_at"):
                print(f"{r['id']}  {r['role']:<8} {r['state']:<8} {r['name']}")
        elif a.cmd == "revoke":
            identity.revoke(conn, a.id)
            conn.commit()
            print(f"revoked {a.id}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
