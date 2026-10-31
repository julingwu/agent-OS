"""L2: markdown/html deliverable must exist, >= min_bytes, and contain markdown headings."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--min-bytes", type=int, default=200)
    a = ap.parse_args()
    if not a.file or not os.path.isfile(a.file):
        print(json.dumps({"ok": False, "reason": "missing", "error": f"文件不存在: {a.file}"}, ensure_ascii=False))
        return 0
    size = os.path.getsize(a.file)
    if size < a.min_bytes:
        print(json.dumps({"ok": False, "bytes": size, "reason": "too_small",
                          "error": f"文件大小不足（当前 {size} 字节 < 要求 {a.min_bytes} 字节）"}, ensure_ascii=False))
        return 0
    try:
        with open(a.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(json.dumps({"ok": False, "reason": "unreadable", "error": str(exc)}, ensure_ascii=False))
        return 0

    has_md_heading = bool(re.search(r"^\s*#{1,3}\s+\S+", text, re.MULTILINE))
    has_html_heading = bool(re.search(r"<h[1-3][^>]*>.*?</h[1-3]>", text, re.I | re.DOTALL))
    ok = has_md_heading or has_html_heading

    reason = None if ok else "missing_headings"
    error = None if ok else "文稿缺少一级或二级结构化标题（# 或 ##）"
    print(json.dumps({"ok": ok, "bytes": size, "has_headings": ok, "reason": reason, "error": error, "file": a.file}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
