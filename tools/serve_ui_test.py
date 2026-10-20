"""Serve the built UI using a separate synthetic database and workspace.

This intentionally enables dev-login only inside the isolated test process.
It never modifies .env, the live database, or the normal application service.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile


def _data_directory(parser, repo: Path, requested: Path | None) -> Path:
    target = (requested or Path(tempfile.mkdtemp(prefix="agent-os-ui-"))).resolve()
    if target == repo or target.is_relative_to(repo) or repo.is_relative_to(target):
        parser.error("The test directory must be outside the source repository and its ancestors.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        parser.error("The test directory must be new or empty; use a fresh directory for every run.")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Dedicated test directory; defaults to a new temporary directory")
    parser.add_argument("--port", type=int, default=8078)
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error("The test port must be between 1 and 65535.")
    if options.port == 8077:
        parser.error("Use a dedicated test port, not the normal service port 8077.")
    repo = Path(__file__).resolve().parents[1]
    target = _data_directory(parser, repo, options.data_dir)
    if not (repo / "web/dist/index.html").is_file():
        parser.error("Build the frontend first: npm --prefix web run build")
    for name in ("app", "tools", "scripts", "web/dist"):
        shutil.copytree(repo / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.environ.update({
        "DB_PATH": str(target / "data/ui-test.db"), "M0_WORKSPACE": str(target / "workspace"),
        "UI_SESSION_SECRET": secrets.token_hex(32), "UI_DEV_LOGIN": "1", "UI_CRON": "0",
        "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0", "OCTO_RUNNER": "0",
        "AUTO_APPROVE": "0", "EVAL_JUDGE": "0", "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
        "UI_RUNNER": "mock_acp", "RUNNER": "mock_acp", "M0_PIPELINE_EXEC": "0",
        "M0_SANDBOX": "none", "M0_SANDBOX_ACP": "0", "M0_HEARTBEAT_SECONDS": "0",
    })
    os.chdir(target)
    sys.path.insert(0, str(target))
    from app.db import init_db, get_conn
    from app import identity
    from app.ui import dispatcher, roles, store
    init_db()
    with get_conn() as conn:
        store.ensure(conn)
        roles.ensure(conn)
        dispatcher.ensure_ceo_chat(conn)
        existing = conn.execute("SELECT id FROM identities WHERE name='UI流程验证' AND state='active'").fetchone()
        identity_id = existing["id"] if existing else identity.issue(conn, name="UI流程验证", role="ceo")["id"]
        for role in ("ROLE-DEV", "ROLE-CONTENT", "ROLE-QA", "ROLE-DESIGN"):
            if not conn.execute("SELECT id FROM ai_employees WHERE role_id=?", (role,)).fetchone():
                roles.instantiate(conn, role)
    print(json.dumps({"test_url": f"http://localhost:{options.port}", "test_identity_id": identity_id,
                      "data_dir": str(target), "runner": "mock_acp"}), flush=True)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()
