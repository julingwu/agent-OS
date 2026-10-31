"""Seed the Octo bridge demo: two extra bots + the octo_bots mapping.

Bots are normally created through BotFather in chat; for a reproducible demo we
insert the same rows BotFather would write (user + robot) straight into MySQL.
The two extra bots have no brain of their own — the bridge speaks for them, which
is exactly what a mock demo needs.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_env  # noqa: E402
from app.db import connect, init_db  # noqa: E402

load_env()

PM_BOT = "pmsim0000000000000000_bot"
HUMAN_UID = os.environ.get("OCTO_HUMAN_UID", "00000000000000000000000000000000")
EXTRA = [
    ("fe_bot_demo", "前端小前", "frontend", "ID-d2d0846432e64006"),
    ("be_bot_demo", "后端小后", "backend", "ID-ae38504179f041ac"),
]


def mysql(sql: str) -> None:
    env_file = ROOT / "vendor" / "octo-deployment" / "docker" / ".env"
    env = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines()
               if line.startswith("MYSQL_ROOT_PASSWORD="))
    cmd = ["docker", "exec", "-i", "octo-mysql-1", "mysql", "-uroot",
           f"-p{env['MYSQL_ROOT_PASSWORD']}", "octo"]
    p = subprocess.run(cmd, input=sql.encode(), capture_output=True)
    if p.returncode != 0:
        print(p.stderr.decode()[:400])
        raise SystemExit("mysql failed")


def main() -> int:
    init_db()
    conn = connect()
    rows = [(PM_BOT, os.environ.get("OCTO_BOT_TOKEN", ""), "小文", "pm",
             "ID-da610f30a4e94e0d")]
    for uid, name, role, ident in EXTRA:
        rows.append((uid, f"bf_demo_{secrets.token_hex(12)}", name, role, ident))

    for bot_uid, token, name, role, ident in rows:
        conn.execute(
            """INSERT INTO octo_bots (bot_uid, bot_token, display_name, role, identity_id)
               VALUES (?,?,?,?,?)
               ON CONFLICT(bot_uid) DO UPDATE SET bot_token=excluded.bot_token,
                 display_name=excluded.display_name, role=excluded.role,
                 identity_id=excluded.identity_id""",
            (bot_uid, token, name, role, ident))
    conn.commit()

    sql = []
    for bot_uid, token, name, role, ident in rows[1:]:
        sql.append(
            f"INSERT IGNORE INTO user (uid, name, username, robot, zone, phone, status) "
            f"VALUES ('{bot_uid}', '{name}', '{bot_uid}', 1, '0086', '', 1);")
        sql.append(
            f"INSERT IGNORE INTO robot (robot_id, token, username, bot_token, creator_uid, "
            f"description, app_id, placeholder, status) VALUES "
            f"('{bot_uid}', '{token}', '{bot_uid}', '{token}', '{HUMAN_UID}', "
            f"'{name}', '', '', 1);")
    mysql("\n".join(sql))

    print("bots in bridge:")
    for r in conn.execute("SELECT bot_uid, display_name, role, identity_id, budget_calls "
                          "FROM octo_bots ORDER BY role"):
        print("  ", dict(r))
    print("human uid:", HUMAN_UID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
