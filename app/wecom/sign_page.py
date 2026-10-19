"""A signed link that lets the chairman sign a human criterion from his phone.

WeCom template-card buttons need the app's callback URL to be reachable from
WeCom's servers. A quick tunnel changes hostname on every restart, which breaks
that silently. A url-type button avoids the callback entirely: the link itself
carries an HMAC over (task, criterion, user, expiry), so a tap opens a page that
can sign the criterion and show the result.

The signature is the authorisation: only the bridge can mint links, and they
expire.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..db import connect
from . import bridge

html_escape = html.escape

router = APIRouter(tags=["wecom"])

LINK_TTL = int(os.environ.get("WECOM_LINK_TTL", "86400"))


def _secret() -> bytes:
    return (os.environ.get("WECOM_SIGN_SECRET") or os.environ.get("WECOM_TOKEN") or "dev").encode()


def sign(params: dict[str, str]) -> str:
    msg = "|".join([params["task"], params["criterion"], params["user"], params["exp"]])
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def make_url(base: str, task: str, criterion: str, user: str) -> str:
    params = {"task": task, "criterion": criterion, "user": user,
              "exp": str(int(time.time()) + LINK_TTL)}
    params["sig"] = sign(params)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base.rstrip('/')}/wecom/sign?{qs}"


def _verify(task: str, criterion: str, user: str, exp: str, sig: str) -> None:
    if not exp.isdigit() or int(exp) < time.time():
        raise HTTPException(410, detail={"code": "LinkExpired"})
    expected = sign({"task": task, "criterion": criterion, "user": user, "exp": exp})
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(403, detail={"code": "BadLinkSignature"})


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
 body{{margin:0;padding:16px 14px 40px;background:#f2f2f7;font:16px/1.55 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1c1c1e;-webkit-text-size-adjust:100%}}
 h1{{font-size:19px;margin:0 0 6px}} .sub{{color:#8e8e93;font-size:13px;margin-bottom:14px;word-break:break-all}}
 .card{{background:#fff;border-radius:12px;padding:4px 14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 .row{{display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid #f0f0f2;font-size:14px}}
 .row:last-child{{border-bottom:0}}
 .row span:first-child{{flex:1 1 auto;min-width:0;word-break:break-word}}
 .row span:last-child{{flex:0 0 auto;white-space:nowrap}}
 .item{{padding:10px 0;border-bottom:1px solid #f0f0f3}}
 .item:last-child{{border-bottom:0}}
 .txt{{font-size:14.5px;line-height:1.5;word-break:break-word}}
 .badge{{display:inline-block;margin-top:4px;font-size:12px;font-weight:600;padding:2px 8px;border-radius:10px}}
 .badge.ok{{background:#e6f6ec;color:#0a7d33}} .badge.wait{{background:#fff4e0;color:#a35a00}}
 .note{{color:#8e8e93;font-size:12px;margin-top:4px;word-break:break-word}}
 body{{overflow-x:hidden}}
 .ok{{color:#0a7d33;font-weight:600}} .wait{{color:#b26a00;font-weight:600}}
 button{{width:100%;padding:16px;border:0;border-radius:12px;background:#0a84ff;color:#fff;font-size:17px;font-weight:600;font-family:inherit;margin-top:4px}}
 .done{{background:#0a7d33}} .muted{{color:#8e8e93;font-size:12px;margin-top:10px;word-break:break-all}}
</style>
<h1>{title}</h1>
{body}
<p class=muted>此链接由 OA Agent 签发，仅用于本次人工判据签字，24 小时内有效。</p>
""")


@router.get("/wecom/sign", response_class=HTMLResponse)
def sign_page(task: str, criterion: str, user: str, exp: str, sig: str):
    _verify(task, criterion, user, exp, sig)
    with connect() as conn:
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
        if t is None:
            raise HTTPException(404, detail={"code": "TaskNotFound"})
        rows = conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task,)).fetchall()
        target = next((r for r in rows if r["id"] == criterion), None)
        if target is None:
            raise HTTPException(404, detail={"code": "CriterionNotFound"})

        lines = []
        for r in rows:
            if r["state"] == "na":
                continue
            mark = {"passed": "✅ 通过", "failed": "❌ 不过", "pending": "⏳ 待签"}.get(
                r["state"], r["state"])
            cls = "ok" if r["state"] == "passed" else ("wait" if r["state"] == "pending" else "")
            lines.append(f"<div class=item><div class=txt>{html_escape(r['acceptance_layer'] or '')} "
                         f"{html_escape((r['criterion'] or '')[:30])}</div>"
                         f"<div class='badge {cls}'>{mark}</div>"
                         + (f"<div class=note>证据：{html_escape((r['note'] or '')[:80])}</div>"
                            if r["note"] else "")
                         + "</div>")
        qs = (f"task={task}&criterion={criterion}&user={user}"
              f"&exp={exp}&sig={sig}")
        body = (f"<div class=sub>任务 {task}　业务包 {t['pack_id']}　状态 {t['state']}</div>"
                f"<div class=card>{''.join(lines)}</div>"
                f"<form method=post action='/wecom/sign?{qs}'>"
                "<button type=submit>确认通过这条判据</button></form>")
    return _page("人工判据签字", body)


@router.post("/wecom/sign", response_class=HTMLResponse)
def sign_submit(task: str, criterion: str, user: str, exp: str, sig: str):
    _verify(task, criterion, user, exp, sig)
    with connect() as conn:
        reply = bridge.handle_action(conn, user, f"judge:{criterion}")
        t = conn.execute("SELECT state FROM tasks WHERE id=?", (task,)).fetchone()
        state = t["state"] if t else "?"
    body = (f"<div class=card><div class=row><span>结果</span><span class=ok>已记录</span></div>"
            f"<div class=row><span>任务状态</span><span>{state}</span></div></div>"
            f"<div class=card style='white-space:pre-wrap;font-size:14px'>{reply}</div>")
    return _page("签字完成", body)
