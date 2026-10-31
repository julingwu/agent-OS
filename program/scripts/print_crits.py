import sys, os
sys.path.insert(0, '.')
from app.db import get_conn
sys.stdout.reconfigure(encoding='utf-8')

with get_conn() as conn:
    crits = conn.execute("SELECT seq, criterion, check_type, check_ref, state, note FROM acceptance_criteria WHERE task_id='T-3314b4aa80a04106' ORDER BY seq").fetchall()
    for c in crits:
        print(f"[{c['seq']}] state={c['state']} type={c['check_type']} ref={c['check_ref']}\n   crit: {c['criterion']}\n   note: {c['note']}")
