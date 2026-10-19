"""L2: the deliverable must exist on disk, be readable and be non-empty."""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--min-bytes", type=int, default=1)
    a = ap.parse_args()
    if not a.file or not os.path.isfile(a.file):
        print(json.dumps({"ok": False, "reason": "missing", "file": a.file}, ensure_ascii=False))
        return 0
    size = os.path.getsize(a.file)
    ok = size >= a.min_bytes
    try:
        with open(a.file, encoding="utf-8", errors="strict") as fh:
            fh.read(4096)
        readable = True
    except (OSError, UnicodeDecodeError):
        readable = False
    print(json.dumps(
        {"ok": ok and readable, "bytes": size, "readable": readable,
         "reason": None if (ok and readable) else ("empty" if not ok else "not-utf8"),
         "file": a.file},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
