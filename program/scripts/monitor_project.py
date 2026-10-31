import sys
import os
import time
sys.path.insert(0, os.path.abspath("."))
from app.db import get_conn

sys.stdout.reconfigure(encoding='utf-8')

pid = sys.argv[1] if len(sys.argv) > 1 else 'P-4802aa93b035'

with get_conn() as conn:
    p = conn.execute('SELECT id, name, state, current_seq FROM projects WHERE id=?', (pid,)).fetchone()
    print('Project:', dict(p) if p else 'None')
    stages = conn.execute('SELECT seq, key, title, state, started_at, ended_at FROM project_stages WHERE project_id=? ORDER BY seq', (pid,)).fetchall()
    print('\nStages:')
    for s in stages:
        print(f"[{s['seq']}] {s['key']} ({s['title']}): {s['state']} (start={s['started_at']}, end={s['ended_at']})")

    gates = conn.execute('SELECT stage_key, name, kind, state, note, superseded FROM stage_gates WHERE project_id=? ORDER BY rowid', (pid,)).fetchall()
    print(f"\nGates count: {len(gates)}")
    for g in gates:
        print(f"  [{g['stage_key']}] {g['name']}: {g['state']} (superseded={g['superseded']}) note={g['note']}")

    runs = conn.execute('SELECT id, task_id, state, usage_tokens, runner, started_at, ended_at, error FROM task_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?)', (pid,)).fetchall()
    print(f"\nTask Runs count: {len(runs)}")
    for r in runs:
        print(f"  {r['id']}: state={r['state']}, tokens={r['usage_tokens']}, runner={r['runner']}, err={r['error']}")

    wbs = conn.execute('SELECT code, title, state, role_tag FROM wbs_items WHERE project_id=? ORDER BY seq', (pid,)).fetchall()
    print(f"\nWBS Items count: {len(wbs)}")
    for w in wbs:
        print(f"  [{w['code']}] {w['title']}: {w['state']} ({w['role_tag']})")
