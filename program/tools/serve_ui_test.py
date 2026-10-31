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
    parser.add_argument("--dist-dir", type=Path, help="Optional separately built frontend directory")
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error("The test port must be between 1 and 65535.")
    if options.port == 8077:
        parser.error("Use a dedicated test port, not the normal service port 8077.")
    repo = Path(__file__).resolve().parents[1]
    target = _data_directory(parser, repo, options.data_dir)
    dist = (options.dist_dir or repo / "web/dist").resolve()
    if not (dist / "index.html").is_file():
        parser.error("Build the frontend first: npm --prefix web run build")
    for name in ("app", "tools", "scripts", "web/dist"):
        shutil.copytree(dist if name == "web/dist" else repo / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.environ.update({
        "DB_PATH": str(target / "data/ui-test.db"), "M0_WORKSPACE": str(target / "workspace"),
        "UI_SESSION_SECRET": secrets.token_hex(32), "UI_DEV_LOGIN": "1", "UI_CRON": "0",
        "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0", "OCTO_RUNNER": "0",
        "WECOM_AUTORUN": "0", "WECOM_AUTORUN_RUNNER": "mock", "MCP_MAIL_DELIVERY": "outbox",
        "AUTO_APPROVE": "0", "EVAL_JUDGE": "0", "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
        "UI_RUNNER": "mock_acp", "RUNNER": "mock_acp", "M0_PIPELINE_EXEC": "0",
        "M0_SANDBOX": "none", "M0_SANDBOX_ACP": "0", "M0_HEARTBEAT_SECONDS": "0",
    })
    for key in list(os.environ):
        if key.startswith(("SMTP_", "MCP_MAIL_", "EMPLOYEE_")) and key != "MCP_MAIL_DELIVERY":
            os.environ.pop(key, None)
    os.chdir(target)
    sys.path.insert(0, str(target))
    from app.db import init_db, get_conn
    from app import identity
    from app.engine import task_generator
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
        conn.execute("INSERT INTO tenants (id,name) VALUES ('TENANT-UI-BETA','隔离租户')")
        conn.execute("""INSERT INTO legal_entities (id,name,type,parent_entity_id,tenant_id)
            VALUES ('LE-UI-BETA','隔离租户组织','subsidiary','LE-GROUP-HQ','TENANT-UI-BETA')""")
        conn.execute("""INSERT INTO legal_entities (id,name,type,parent_entity_id,tenant_id)
            VALUES ('LE-UI-SUB','总部子公司','subsidiary','LE-GROUP-HQ','TENANT-HQ')""")
        tenant_identity_id = identity.issue(
            conn, name="隔离租户 CEO", role="ceo", legal_entity_id="LE-UI-BETA")["id"]
        subsidiary_identity_id = identity.issue(
            conn, name="总部子公司 CEO", role="ceo", legal_entity_id="LE-UI-SUB")["id"]
        beta_employee = roles.instantiate(conn, "ROLE-CONTENT", "LE-UI-BETA")
        conn.execute("UPDATE ai_employees SET name='隔离租户员工' WHERE id=?", (beta_employee,))
        conn.execute("UPDATE chats SET title='隔离租户员工' WHERE employee_id=?", (beta_employee,))
        task_generator.generate(conn, "隔离租户专属任务", legal_entity_id="LE-UI-BETA")
        task_generator.generate(conn, "总部专属任务", legal_entity_id="LE-GROUP-HQ")
        conn.commit()
    print(json.dumps({"test_url": f"http://localhost:{options.port}", "test_identity_id": identity_id,
                      "test_tenant_identity_id": tenant_identity_id,
                      "test_subsidiary_identity_id": subsidiary_identity_id,
                      "data_dir": str(target), "runner": "mock_acp"}), flush=True)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()
