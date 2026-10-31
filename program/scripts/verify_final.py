import sys, os
sys.path.insert(0, os.path.abspath("."))
from app import db

with db.connect() as conn:
    pid = 'P-c2bc79721a8f'
    p = conn.execute('SELECT id, name, state FROM projects WHERE id=?', (pid,)).fetchone()
    print('PROJECT:', dict(p))
    
    stages = conn.execute('SELECT seq, key, title, state, started_at, ended_at FROM project_stages WHERE project_id=? ORDER BY seq', (pid,)).fetchall()
    print('\nSTAGES (ALL 9):')
    for s in stages:
        print(f"  [{s['seq']}] {s['key']:<10} | {s['state']:<8} | {s['started_at']} -> {s['ended_at']}")
        
    gates = conn.execute('SELECT stage_key, name, state, superseded FROM stage_gates WHERE project_id=? ORDER BY rowid', (pid,)).fetchall()
    print('\nGATES:')
    for g in gates:
        print(f"  {g['stage_key']:<10} | {g['state']:<8} | superseded={g['superseded']} | {g['name']}")
        
    runs = conn.execute('SELECT id, runner, state, usage_tokens FROM task_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?) AND usage_tokens > 0', (pid,)).fetchall()
    total_tokens = sum(r['usage_tokens'] for r in runs)
    print(f"\nTOTAL MODEL TOKENS CONSUMED: {total_tokens:,}")
    for r in runs:
        print(f"  {r['id']} | {r['runner']} | {r['state']} | {r['usage_tokens']:,} tokens")
