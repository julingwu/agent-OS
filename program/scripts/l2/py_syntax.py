"""L2: does the delivered Python file actually parse?"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    a = ap.parse_args()
    if not a.file:
        print(json.dumps({"ok": False, "reason": "no_file"}, ensure_ascii=False))
        return 0
    tmpdir = tempfile.mkdtemp(prefix="oa-pycheck-")
    try:
        py_compile.compile(a.file, cfile=os.path.join(tmpdir, "out.pyc"), doraise=True)
        print(json.dumps({"ok": True, "reason": None, "file": a.file}, ensure_ascii=False))
    except py_compile.PyCompileError as exc:
        msg = str(exc).replace("\n", " ")[:400]
        print(json.dumps({"ok": False, "reason": "syntax_error", "error": msg,
                          "file": a.file}, ensure_ascii=False))
    except OSError as exc:
        print(json.dumps({"ok": False, "reason": "unreadable", "error": str(exc),
                          "file": a.file}, ensure_ascii=False))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
