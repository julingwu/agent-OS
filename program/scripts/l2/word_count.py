"""Count text length: CJK characters + Latin/digit words, whitespace excluded.

Byte length is never used: a 500-character Chinese article is ~1500 bytes and
would be judged as "too long" by any byte-based rule.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD = re.compile(r"[A-Za-z0-9]+")


def count(text: str) -> int:
    stripped = re.sub(r"\s+", "", text)
    return len(CJK.findall(stripped)) + len(WORD.findall(stripped))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--min", type=int, default=0)
    ap.add_argument("--max", type=int, default=10**9)
    a = ap.parse_args()
    if not a.file:
        print(json.dumps({"error": "no --file"}, ensure_ascii=False))
        return 2
    try:
        with open(a.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    n = count(text)
    print(json.dumps(
        {"count": n, "min": a.min, "max": a.max, "ok": a.min <= n <= a.max,
         "file": a.file, "chars_raw": len(text)},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
