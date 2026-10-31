import sys, os, tempfile, pathlib, subprocess
sys.path.insert(0, '.')
from app.engine.runner_native import NativeKernel
from app.db import get_conn
from app import redline
from app.acceptance import l0_rules

with get_conn() as conn:
    with tempfile.TemporaryDirectory() as td:
        kernel = NativeKernel(conn, run_id='TEST-RUN', task_id='TEST-TASK', entity='LE-GROUP-HQ', actor='TEST-ACTOR', prompt='测试')
    kernel.proj_dir = pathlib.Path(td)
    kernel.execute_deterministic_build()

    print("Generated files:", [p.name for p in kernel.proj_dir.iterdir()])

    # Test running test_check.py
    py_res = subprocess.run([sys.executable, str(kernel.proj_dir / "test_check.py")], capture_output=True, text=True, cwd=td)
    print("test_check.py exit code:", py_res.returncode)
    print("test_check.py stdout:\n", py_res.stdout)
    if py_res.returncode != 0:
        print("test_check.py stderr:\n", py_res.stderr)

    # Test redline scan
    with get_conn() as conn:
        rules = redline.rules(conn, tenant_id='TENANT-HQ', entity='LE-GROUP-HQ')
        all_terms = []
        for r in rules:
            all_terms.extend(r['terms'])

        has_hit = False
        for f in kernel.proj_dir.iterdir():
            if f.is_file():
                txt = f.read_text(encoding='utf-8')
                copy = l0_rules.text_for_rule('banned', txt, str(f))
                hits = [t for t in all_terms if t in copy]
                if hits:
                    print(f"REDLINE HIT in {f.name}: {hits}")
                    has_hit = True
                else:
                    print(f"Clean: {f.name}")
        if not has_hit:
            print(">>> ALL DELIVERABLES 100% CLEAN OF REDLINE TERMS! <<<")
