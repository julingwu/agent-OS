import os
import sys
import sqlite3
import json
import urllib.request
import subprocess
from pathlib import Path

ROOT = Path(r"C:\oaagent-m0")
sys.path.insert(0, str(ROOT))

def section(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)

def main():
    print(">>> Agent OS (C:\\oaagent-m0) Comprehensive Health & System Inspection <<<")

    # 1. Environment & Directories
    section("1. ENVIRONMENT & KEY PATHS")
    env_file = ROOT / ".env"
    db_file = ROOT / "data" / "oa.db"
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    web_dist = ROOT / "web" / "dist" / "index.html"
    
    print(f"Project Root     : {ROOT} (exists: {ROOT.exists()})")
    print(f".env File        : {env_file} (exists: {env_file.exists()})")
    print(f"Database         : {db_file} (exists: {db_file.exists()}, size: {db_file.stat().st_size if db_file.exists() else 0} bytes)")
    print(f"Virtualenv Python: {venv_python} (exists: {venv_python.exists()})")
    print(f"Web Dist index   : {web_dist} (exists: {web_dist.exists()})")

    # 2. Database Integrity Check
    section("2. DATABASE INTEGRITY & METRICS (data/oa.db)")
    if db_file.exists():
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # PRAGMA integrity_check
        integrity = cur.execute("PRAGMA integrity_check").fetchall()
        print(f"PRAGMA integrity_check : {[r[0] for r in integrity]}")
        
        # PRAGMA foreign_key_check
        fk_violations = cur.execute("PRAGMA foreign_key_check").fetchall()
        print(f"Foreign Key Violations : {len(fk_violations)}")
        if fk_violations:
            for fkv in fk_violations[:5]:
                print(f"   Violation: {fkv}")

        # Table counts
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        print(f"Total Tables ({len(tables)}): {', '.join(sorted(tables))}")

        # Budgets
        print("\nTenant Budgets Summary:")
        budgets = cur.execute("SELECT count(*), sum(limit_tokens), sum(used_tokens) FROM budgets").fetchone()
        print(f"  Total budget records: {budgets[0]}, Total limit: {budgets[1]:,}, Total used: {budgets[2]:,}")

        # Recent Projects
        print("\nRecent Projects:")
        p_cols = [c[1] for c in cur.execute("PRAGMA table_info(projects)").fetchall()]
        print(f"  Projects columns: {p_cols}")
        projects = cur.execute("SELECT * FROM projects ORDER BY created_at DESC LIMIT 5").fetchall()
        for p in projects:
            print(f"  [{p['id']}] '{p['name']}' | State: {p['state']} | Created: {p['created_at']}")
            # Stages for this project
            stages = cur.execute("SELECT seq, key, title, state, started_at, ended_at FROM project_stages WHERE project_id=? ORDER BY seq", (p['id'],)).fetchall()
            stage_summary = " -> ".join([f"{s['key']}:{s['state']}" for s in stages])
            print(f"     Stages ({len(stages)}): {stage_summary}")
            
            # Runs for this project
            runs = cur.execute("SELECT id, task_id, runner, state, usage_tokens FROM task_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?)", (p['id'],)).fetchall()
            total_toks = sum(r['usage_tokens'] or 0 for r in runs)
            print(f"     Task Runs ({len(runs)}): Total Tokens Consumed = {total_toks:,}")

        conn.close()

    # 3. Kernel & Dual-Engine Verification
    section("3. DUAL-ENGINE KERNEL ARCHITECTURE")
    native_py = ROOT / "app" / "engine" / "runner_native.py"
    router_py = ROOT / "app" / "engine" / "kernel_router.py"
    acp_py = ROOT / "app" / "engine" / "runner_acp.py"
    dsh_adapter_py = ROOT / "app" / "engine" / "dsh_adapter.py"
    print(f"Native Kernel (`runner_native.py`) : {'OK' if native_py.exists() else 'MISSING'}")
    print(f"Kernel Router (`kernel_router.py`) : {'OK' if router_py.exists() else 'MISSING'}")
    print(f"ACP Runner    (`runner_acp.py`)    : {'OK' if acp_py.exists() else 'MISSING'}")
    print(f"DSH Adapter   (`dsh_adapter.py`)   : {'OK' if dsh_adapter_py.exists() else 'MISSING'}")

    try:
        from app.engine.kernel_router import get_kernel_mode, set_kernel_mode
        print(f"Current Kernel Mode Configured   : {get_kernel_mode()}")
    except Exception as e:
        print(f"Kernel mode import check error   : {e}")

    # 4. Deliverables Inspection for Latest Project
    section("4. PROJECT DELIVERABLES INSPECTION")
    ws_projects = ROOT / "workspace" / "projects"
    if ws_projects.exists():
        subdirs = sorted([d for d in ws_projects.iterdir() if d.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)
        print(f"Projects in workspace/projects/: {len(subdirs)}")
        if subdirs:
            latest = subdirs[0]
            print(f"Latest Project Directory: {latest.name}")
            files = list(latest.iterdir())
            for f in sorted(files, key=lambda x: x.name):
                print(f"  - {f.name:<30} ({f.stat().st_size:,} bytes)")
                
            test_check = latest / "test_check.py"
            if test_check.exists():
                print(f"\nRunning deliverable test ({test_check.name})...")
                res = subprocess.run([str(venv_python), str(test_check)], cwd=str(latest), capture_output=True, text=True)
                print(f"Test returncode: {res.returncode}")
                print(res.stdout.strip())
                if res.stderr:
                    print(f"Stderr: {res.stderr.strip()}")

    # 5. Frontend Build Verification
    section("5. FRONTEND ASSETS & UI INTEGRITY")
    deck_jsx = ROOT / "web" / "src" / "CyberFactorioDeck.jsx"
    drawer_jsx = ROOT / "web" / "src" / "CyberStageDrawer.jsx"
    app_jsx = ROOT / "web" / "src" / "App.jsx"
    dist_dir = ROOT / "web" / "dist"
    
    print(f"CyberFactorioDeck.jsx: {'OK' if deck_jsx.exists() else 'MISSING'} ({deck_jsx.stat().st_size if deck_jsx.exists() else 0} bytes)")
    print(f"CyberStageDrawer.jsx : {'OK' if drawer_jsx.exists() else 'MISSING'} ({drawer_jsx.stat().st_size if drawer_jsx.exists() else 0} bytes)")
    print(f"App.jsx              : {'OK' if app_jsx.exists() else 'MISSING'}")
    
    if dist_dir.exists():
        assets = list((dist_dir / "assets").glob("*"))
        print(f"Built Assets in web/dist/assets/ ({len(assets)} files):")
        for a in assets:
            print(f"  - {a.name} ({a.stat().st_size:,} bytes)")

    # 6. Service Listener Check
    section("6. SERVICE LISTENER CHECK (Port 8077)")
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8077/health", timeout=2)
        status = req.getcode()
        body = req.read().decode("utf-8")
        print(f"Service on 8077 is ONLINE! HTTP {status} -> {body}")
    except Exception as e:
        print(f"Service on 8077 is currently NOT running: {e}")

    section("HEALTH CHECK SUMMARY COMPLETED")

if __name__ == "__main__":
    main()
