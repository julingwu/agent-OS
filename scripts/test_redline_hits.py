import sys, os, pathlib
sys.path.insert(0, '.')
from app.acceptance import l0_rules
from app.db import get_conn
from app import redline

with get_conn() as conn:
    rules = redline.rules(conn, tenant_id='TENANT-HQ', entity='LE-GROUP-HQ')
    print('Total rules:', len(rules))
    all_terms = []
    for r in rules:
        all_terms.extend(r['terms'])
    print('Rule terms:', set(all_terms))

    for fname in ['index.html', 'test_check.py', 'README.md', 'api_schema.json']:
        fpath = pathlib.Path(f'C:/Users/juling.wu.ext/AppData/Local/oaagent-m0/workspace/ID-da610f30a4e94e0d/T-3314b4aa80a04106/{fname}')
        if fpath.is_file():
            text = fpath.read_text(encoding='utf-8')
            copy = l0_rules.text_for_rule('banned', text, str(fpath))
            hits = [t for t in all_terms if t in copy]
            print(f'{fname}: hits={hits}')
