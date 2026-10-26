import sys, os
sys.path.insert(0, '.')
from app.db import get_conn

tid = sys.argv[1] if len(sys.argv) > 1 else 'T-3314b4aa80a04106'
with get_conn() as conn:
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    print('Task:', dict(task) if task else None)
    runs = conn.execute('SELECT * FROM task_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 3', (tid,)).fetchall()
    for r in runs:
        print('Run:', dict(r))
    crits = conn.execute('SELECT * FROM acceptance_criteria WHERE task_id=?', (tid,)).fetchall()
    print('Crits:')
    for c in crits:
        print(' ', dict(c))
    arts = conn.execute('SELECT a.* FROM artifacts a JOIN task_runs r ON r.id=a.run_id WHERE r.task_id=?', (tid,)).fetchall()
    print('Artifacts count:', len(arts))
    for a in arts:
        print(' ', dict(a))
