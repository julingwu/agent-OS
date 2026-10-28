"""签发一台客户端设备（exe / PWA）令牌 —— 只能在服务端本地执行。

用法：
    DB_PATH=/opt/agent-os/data/oa.db /opt/agent-os/venv/bin/python \
        scripts/issue_device.py --name my-exe --kind exe
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, devices  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="exe")
    ap.add_argument("--kind", default="exe", choices=["exe", "pwa", "wecom", "cli"])
    args = ap.parse_args()

    db.init_db()
    with db.get_conn() as conn:
        issued = devices.issue(conn, name=args.name, kind=args.kind)
    print(json.dumps(issued, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
