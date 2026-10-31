"""L2: data deliverable must exist, >= min_bytes, and contain valid JSON, CSV, or structured data tables."""
from __future__ import annotations

import argparse
import csv
import io
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

    # 1. Check if valid JSON with content
    is_json = False
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, (dict, list)) and len(parsed) > 0:
            is_json = True
    except Exception:
        pass

    # 2. Check if valid CSV
    is_csv = False
    if not is_json:
        try:
            reader = csv.reader(io.StringIO(text.strip()))
            rows = list(reader)
            if len(rows) >= 2 and all(len(r) >= 2 for r in rows[:2]):
                is_csv = True
        except Exception:
            pass

    # 3. Check if Markdown table or structured analysis with numbers
    has_table = bool(re.search(r"\|.+\|.+\|", text))
    has_numbers = bool(re.search(r"\d+(?:\.\d+)?%?", text))
    has_analysis = any(w in text for w in ("分析", "结论", "指标", "趋势", "比例", "数据", "总计", "平均"))

    ok = is_json or is_csv or (has_table and has_numbers) or (has_analysis and has_numbers)
    reason = None if ok else "invalid_data_format"
    error = None if ok else "未发现有效的结构化数据（JSON/CSV/Markdown表格）或包含数值的分析结论"
    print(json.dumps({"ok": ok, "bytes": size, "reason": reason, "error": error, "file": a.file}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
