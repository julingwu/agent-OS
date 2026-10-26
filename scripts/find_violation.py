import sys, os, pathlib
sys.path.insert(0, '.')
from app.db import get_conn

with get_conn() as conn:
    arts = conn.execute("SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id WHERE r.task_id='T-3314b4aa80a04106'").fetchall()
    for a in arts:
        p = pathlib.Path(a['file_path'])
        if p.is_file():
            txt = p.read_text(encoding='utf-8', errors='ignore')
            if '100%' in txt:
                print('Found 100% in:', p)
                for line in txt.splitlines():
                    if '100%' in line:
                        print('  line:', line)
