# -*- coding: utf-8 -*-
"""Collect a factual inventory of the Agent OS codebase for the architecture document."""
import ast, collections, json, os, pathlib, re, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("UI_CRON", "0")

out = {}

# ---------------------------------------------------------------- API surface
from app.main import app
spec = app.openapi()
paths = spec["paths"]
by_tag = collections.OrderedDict()
for p, ops in sorted(paths.items()):
    for m, op in ops.items():
        tags = op.get("tags") or ["(none)"]
        by_tag.setdefault(tags[0], []).append(f"{m.upper():6s} {p}")
out["api"] = {"total_paths": len(paths),
              "by_tag": {k: sorted(v) for k, v in by_tag.items()}}

# ---------------------------------------------------------------- modules
mods = []
for f in sorted((ROOT / "app").rglob("*.py")):
    if "__pycache__" in str(f):
        continue
    rel = f.relative_to(ROOT).as_posix()
    src = f.read_text(encoding="utf-8", errors="replace")
    try:
        doc = (ast.get_docstring(ast.parse(src)) or "").strip().split("\n")[0]
    except SyntaxError:
        doc = "(syntax error)"
    mods.append({"path": rel, "loc": src.count("\n") + 1, "doc": doc[:110]})
out["modules"] = mods
out["loc_app"] = sum(m["loc"] for m in mods)

# ---------------------------------------------------------------- frontend
fe = []
for f in sorted((ROOT / "web" / "src").glob("*")):
    if f.suffix in (".jsx", ".js", ".css"):
        n = f.read_text(encoding="utf-8", errors="replace").count("\n") + 1
        fe.append({"file": f.name, "loc": n})
out["frontend"] = fe

# ---------------------------------------------------------------- db
db = sqlite3.connect(str(ROOT / "data" / "oa.db"))
tables = [r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
counts = {}
for t in tables:
    try:
        counts[t] = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception:
        counts[t] = -1
out["db"] = {"tables": len(tables), "counts": counts,
             "triggers": [r[0] for r in db.execute(
                 "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")]}
stats = {}
for sql, key in [
    ("SELECT state, COUNT(*) n FROM tasks GROUP BY state", "tasks"),
    ("SELECT state, COUNT(*) n FROM task_runs GROUP BY state", "runs"),
    ("SELECT state, COUNT(*) n FROM projects GROUP BY state", "projects"),
    ("SELECT state, COUNT(*) n FROM org_experiences GROUP BY state", "experiences"),
]:
    stats[key] = {r[0]: r[1] for r in db.execute(sql)}
out["db"]["states"] = stats

# ---------------------------------------------------------------- tests
tests = []
for f in sorted((ROOT / "tests").glob("test_*.py")):
    src = f.read_text(encoding="utf-8", errors="replace")
    tests.append({"file": f.name, "tests": len(re.findall(r"^def test_", src, re.M)),
                  "loc": src.count("\n") + 1})
out["tests"] = tests
out["test_total"] = sum(t["tests"] for t in tests)

# ---------------------------------------------------------------- env keys
env_keys = []
envp = ROOT / ".env"
if envp.exists():
    for line in envp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            env_keys.append(line.split("=", 1)[0])
out["env_keys"] = env_keys

(ROOT / "docs" / "inventory.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("modules", len(mods), "loc_app", out["loc_app"])
print("api paths", len(paths), "tags", len(by_tag))
print("tables", len(tables), "triggers", len(out["db"]["triggers"]))
print("test files", len(tests), "test cases", out["test_total"])
print("wrote docs/inventory.json")
