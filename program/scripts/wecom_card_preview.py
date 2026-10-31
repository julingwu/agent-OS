"""Render the mock outbox as a WeCom-looking preview page (docs/wecom_cards.html).

    python scripts/wecom_card_preview.py
"""
from __future__ import annotations

import html
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def esc(v) -> str:
    return html.escape(str(v or ""))


def card_html(card: dict, kind: str) -> str:
    rows = "".join(
        f'<div class="row"><span>{esc(r.get("keyname"))}</span><b>{esc(r.get("value"))}</b></div>'
        for r in card.get("horizontal_content_list", []))
    buttons = "".join(
        f'<button class="{"primary" if b.get("style") == 1 else "ghost"}">{esc(b.get("text"))}</button>'
        for b in card.get("button_list", []))
    title = esc(card.get("main_title", {}).get("title"))
    desc = esc(card.get("main_title", {}).get("desc"))
    source = esc(card.get("source", {}).get("desc"))
    return f"""
    <div class="msg">
      <div class="who">OA Agent <span class="muted">· {kind}</span></div>
      <div class="bubble">
        <div class="src">{source}</div>
        <div class="title">{title}</div>
        <div class="desc">{desc}</div>
        <div class="rows">{rows}</div>
        <div class="actions">{buttons}</div>
      </div>
    </div>"""


def main() -> int:
    conn = sqlite3.connect(ROOT / "data" / "oa.db")
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM wecom_outbox ORDER BY created_at DESC LIMIT 40")]
    task_card = decision_card = None
    for r in rows:
        body = json.loads(r["body"]) if isinstance(r["body"], str) else r["body"]
        key = json.dumps(body, ensure_ascii=False)
        if task_card is None and "accept:" in key:
            task_card = body
        if decision_card is None and "approve:" in key:
            decision_card = body
        if task_card and decision_card:
            break

    body = (card_html(task_card, "验收卡片") if task_card else "<p>还没有验收卡片</p>")
    body += (card_html(decision_card, "审批卡片") if decision_card else "<p>还没有审批卡片</p>")

    page = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>企业微信 · OA Agent 卡片预览</title>
<style>
 body{{margin:0;background:#ededed;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#111}}
 .wrap{{max-width:760px;margin:0 auto;padding:24px}}
 h1{{font-size:16px;color:#555;font-weight:600;margin:0 0 16px}}
 .msg{{margin-bottom:22px}}
 .who{{font-size:12px;color:#888;margin-bottom:6px}}
 .bubble{{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 2px rgba(0,0,0,.06)}}
 .src{{font-size:12px;color:#576b95;margin-bottom:6px}}
 .title{{font-size:16px;font-weight:700;margin-bottom:4px}}
 .desc{{font-size:13px;color:#666;margin-bottom:12px}}
 .row{{display:flex;justify-content:space-between;border-top:1px solid #f0f0f0;padding:7px 0;font-size:13px}}
 .row span{{color:#888}}
 .actions{{display:flex;gap:10px;margin-top:14px}}
 button{{flex:1;padding:8px 0;border-radius:6px;font:inherit;cursor:pointer}}
 button.primary{{background:#07c160;color:#fff;border:1px solid #07c160}}
 button.ghost{{background:#fff;color:#07c160;border:1px solid #c9e7d5}}
 .muted{{color:#aaa}}
</style></head><body><div class="wrap">
<h1>企业微信 · OA Agent 卡片（mock 渲染，内容取自本地 outbox）</h1>
{body}
</div></body></html>"""
    out = ROOT / "docs" / "wecom_cards.html"
    out.write_text(page, encoding="utf-8")
    print(f"written {out}  task_card={bool(task_card)} decision_card={bool(decision_card)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
