"""Render the audit Markdown as a self-contained HTML page (no deps).

    python scripts/audit_html.py docs/audit_T-xxxx.md
"""
from __future__ import annotations

import html
import pathlib
import re
import sys

CSS = """
:root{--bg:#0f1115;--panel:#151922;--line:#262b36;--fg:#e6e6e6;--muted:#8b93a1;--accent:#8ab4f8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"PingFang SC",Segoe UI,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:36px 22px 80px}
h1{font-size:24px;margin:0 0 18px;letter-spacing:.02em}
h2{font-size:14px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);
   margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
p{margin:6px 0;color:var(--muted)}
p strong{color:var(--fg)}
ul{margin:8px 0 8px 18px;padding:0;color:var(--muted)}
table{width:100%;border-collapse:collapse;margin:10px 0 4px;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--line);padding:7px 9px;white-space:nowrap}
td{border-bottom:1px solid #1d222c;padding:7px 9px;vertical-align:top;word-break:break-word}
tr:hover td{background:#12161e}
td:first-child{color:#a9b4c4;font-variant-numeric:tabular-nums;white-space:nowrap}
code{background:#1b2029;padding:1px 5px;border-radius:4px;font-size:12.5px}
.tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11.5px;background:#22303f;color:#9dc6ff}
"""


def inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def convert(md: str) -> str:
    out: list[str] = []
    in_table = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if re.fullmatch(r"\|[\s:\-|]+\|", line):
            continue
        if line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("- "):
            out.append(f"<p>{inline(line[2:])}</p>")
        elif line.strip() == "":
            continue
        else:
            out.append(f"<p>{inline(line)}</p>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def main() -> int:
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "")
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    md = src.read_text(encoding="utf-8")
    title = md.splitlines()[0].lstrip("# ").strip() if md else "审计时间轴"
    dest = src.with_suffix(".html")
    dest.write_text(
        f"""<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><div class=wrap>{convert(md)}</div></body></html>""", encoding="utf-8")
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
