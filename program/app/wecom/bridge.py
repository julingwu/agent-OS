"""Bridge between WeCom messages/cards and the OA shell API."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import pathlib
import threading
import uuid

log = logging.getLogger("wecom.bridge")

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from oa_client import OAClient  # noqa: E402


def panel_url() -> str:
    return os.environ.get("OA_PANEL_URL") or os.environ.get("OA_BASE", "http://127.0.0.1:8077")


# ------------------------------------------------------------------ identities
def identity_for(conn: sqlite3.Connection, wecom_userid: str) -> dict | None:
    row = conn.execute(
        """SELECT u.wecom_userid, u.identity_id, u.name, i.role, i.legal_entity_id,
                  i.secret, i.state
           FROM wecom_users u JOIN identities i ON i.id = u.identity_id
           WHERE u.wecom_userid = ? AND u.state = 'active' AND i.state = 'active'""",
        (wecom_userid,)).fetchone()
    return dict(row) if row else None


_TRANSPORT = None          # tests inject an in-process ASGI transport here


def use_transport(transport) -> None:
    """Call the shell in-process (tests) instead of over the network."""
    global _TRANSPORT
    _TRANSPORT = transport


def client_for(ident: dict) -> OAClient:
    base = os.environ.get("OA_BASE", "http://127.0.0.1:8077")
    return OAClient(base, ident["identity_id"], ident["secret"], transport=_TRANSPORT)


def bind_user(conn: sqlite3.Connection, wecom_userid: str, identity_id: str,
              name: str | None = None) -> dict:
    conn.execute(
        """INSERT INTO wecom_users (wecom_userid, identity_id, name, state)
           VALUES (?,?,?, 'active')
           ON CONFLICT(wecom_userid) DO UPDATE SET identity_id = excluded.identity_id,
                                                   name = excluded.name, state = 'active'""",
        (wecom_userid, identity_id, name))
    conn.commit()
    return {"wecom_userid": wecom_userid, "identity_id": identity_id}


def recipients_for(conn: sqlite3.Connection, *, entity: str, need_ceo: bool = False) -> list[str]:
    """Who gets the card: the entity's managers (plus the group CEO), or only CEO."""
    if need_ceo:
        rows = conn.execute(
            """SELECT u.wecom_userid FROM wecom_users u JOIN identities i ON i.id = u.identity_id
               WHERE u.state='active' AND i.state='active' AND i.role='ceo'""").fetchall()
        return [r[0] for r in rows]
    rows = conn.execute(
        """SELECT u.wecom_userid, i.role, i.legal_entity_id
           FROM wecom_users u JOIN identities i ON i.id = u.identity_id
           WHERE u.state='active' AND i.state='active' AND i.role IN ('manager','ceo')""").fetchall()
    out = []
    for r in rows:
        if r["role"] == "ceo" or r["legal_entity_id"] == entity:
            out.append(r["wecom_userid"])
    return out


# ------------------------------------------------------------------ cards
def _short(text, limit: int = 14) -> str:
    t = (text or "").replace(chr(10), " ").strip()
    return t if len(t) <= limit else t[:limit] + "…"


def _criteria_rows(task: dict, criteria: list[dict], limit: int = 6) -> list[dict]:
    """Evidence first: the chairman wants to see why each gate passed, not a count."""
    rows = []
    for c in criteria:
        if len(rows) >= limit:
            break
        state = c.get("state")
        if state == "na":
            continue
        mark = {"passed": "过", "failed": "不过是", "pending": "待"}.get(state, state or "?")
        note = c.get("note") or state or ""
        rows.append({"keyname": f"{c.get('acceptance_layer', '')} {_short(c.get('criterion'), 12)}",
                     "value": f"{mark}｜{_short(note, 34)}"})
    if not rows:
        rows = [{"keyname": "任务", "value": task["id"]},
                {"keyname": "实体", "value": task["legal_entity_id"]}]
    return rows


def _judge_button(task: dict, criterion: dict, user: str | None) -> dict:
    """Prefer a signed url: the click then works even when the app callback is
    unreachable (a quick tunnel changes hostname on every restart)."""
    return {"text": f"人工通过{criterion['seq']}", "style": 1, "key": f"judge:{criterion['id']}"}


def task_card(task: dict, criteria: list[dict], counts: dict,
              user: str | None = None) -> dict:
    desc = (f"{counts.get('passed', 0)} 已过 / {counts.get('pending', 0)} 待定 / "
            f"{counts.get('failed', 0)} 不过")
    return {
        "card_type": "button_interaction",
        # WeCom rejects a template_card without a unique task_id (errcode 42014)
        "task_id": uuid.uuid4().hex,
        "source": {"desc": "OA Agent · 验收", "desc_color": 1},
        "main_title": {"title": f"待验收：{task.get('title') or task['id']}",
                       "desc": f"业务包 {task.get('pack_id') or '-'}　{desc}"},
        "horizontal_content_list": _criteria_rows(task, criteria),
        "button_list": ([{"text": "验收", "style": 1, "key": f"accept:{task['id']}"}]
                        + [_judge_button(task, c, user)
                           for c in criteria
                           if c.get("check_type") == "human_check"
                           and c.get("state") == "pending"][:3]
                        + [{"text": "打开详情", "style": 2,
                            "key": f"detail:{task['id']}"}])[:6],
        "card_action": {"type": 1, "url": f"{panel_url()}/?task_id={task['id']}"},
    }


def sign_card(task: dict, criteria: list[dict], counts: dict, user: str) -> dict:
    """text_notice card: tapping anywhere opens the signed page.

    A button_interaction card cannot carry a url button (WeCom 42039), and a
    keyed button needs the app callback — which a rotating quick tunnel breaks.
    """
    from . import sign_page

    base = (os.environ.get("WECOM_CALLBACK_URL") or "").rsplit("/wecom/callback", 1)[0]
    target = next((c for c in criteria if c.get("check_type") == "human_check"
                   and c.get("state") == "pending"), None)
    url = (sign_page.make_url(base, task["id"], target["id"], user) if target and base
           else f"{panel_url()}/?task_id={task['id']}")
    rows = [r for r in _criteria_rows(task, criteria, limit=6)]
    return {
        "card_type": "text_notice",
        "source": {"desc": "OA Agent · 待签字", "desc_color": 1},
        "main_title": {"title": "点开签字：" + (task.get("title") or task["id"])[:38],
                       "desc": f"{counts.get('passed', 0)} 已过 / {counts.get('pending', 0)} 待定"},
        "horizontal_content_list": rows,
        "jump_list": [{"type": 1, "title": "打开签字页", "url": url}],
        "card_action": {"type": 1, "url": url},
    }


def decision_card(dec: dict) -> dict:
    amount = f"{float(dec.get('amount') or 0):,.0f} {dec.get('currency') or ''}".strip()
    return {
        "card_type": "button_interaction",
        "task_id": uuid.uuid4().hex,
        "source": {"desc": "OA Agent · 审批", "desc_color": 2},
        "main_title": {"title": f"待审批：{dec.get('title')}", "desc": f"金额 {amount}"},
        "horizontal_content_list": [
            {"keyname": "决策包", "value": dec["id"]},
            {"keyname": "规则", "value": f"{dec.get('matched_rule') or '-'} {dec.get('filter_reason') or ''}"},
            {"keyname": "申请原因", "value": (dec.get("summary") or "-")[:60]},
        ],
        "button_list": [
            {"text": "同意", "style": 1, "key": f"approve:{dec['id']}"},
            {"text": "驳回", "style": 3, "key": f"reject:{dec['id']}"},
            {"text": "打开", "style": 2, "key": f"detail:decision:{dec['id']}"},
        ],
        "card_action": {"type": 1, "url": panel_url()},
    }


def criteria_counts(conn: sqlite3.Connection, task_id: str) -> tuple[list[dict], dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id = ? ORDER BY seq", (task_id,))]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return rows, counts


# ------------------------------------------------------------------ autorun
def autorun_enabled() -> bool:
    return os.environ.get("WECOM_AUTORUN", "1") == "1"


def _run_bg(task_id: str, ident: dict, pack_id: str | None) -> None:
    """Spawn + execute off the callback thread: WeCom wants its ack in 5 seconds."""
    try:
        client = client_for(ident)
        run = client.post("/verbs/spawn_run", json={"task_id": task_id, "run_seq": 1}).json()
        budget = int(os.environ.get("WECOM_AUTORUN_BUDGET")
                     or (45000 if (pack_id or "").startswith("code") else 30000))
        client.post(f"/runs/{run['run_id']}/execute",
                    json={"runner": os.environ.get("WECOM_AUTORUN_RUNNER", "acp"),
                          "budget_tokens": budget})
        log.info("autorun finished for %s", task_id)
    except Exception as exc:                       # noqa: BLE001
        log.warning("autorun failed for %s: %s", task_id, exc)


# ------------------------------------------------------------------ inbound text
def handle_text(conn: sqlite3.Connection, wecom_userid: str, text: str) -> str:
    """A sentence from WeCom becomes a task; the reply says what was created."""
    ident = identity_for(conn, wecom_userid)
    if ident is None:
        return ("这个企微账号还没有绑定 OA 身份。在服务器上执行：\n"
                "python tools/wecom_bind.py --user <企微UserID> --identity <ID-...>")
    text = (text or "").strip()
    if not text:
        return "说一句话我就建任务，例如：写一篇 500 字的秋季护肤短文"
    if len(text) > 500:
        return "太长了，一句话说清任务目标就行（500 字以内）。"

    client = client_for(ident)
    r = client.post("/tasks/from_sentence", json={"text": text})
    if r.status_code >= 400:
        return f"建任务失败：{r.status_code} {r.text[:200]}"
    task = r.json()
    blocking = sum(1 for c in task["criteria"] if c.get("is_blocking"))
    lines = [f"已建任务 {task['task_id']}",
             f"业务包：{task['pack_id']}　判据 {len(task['criteria'])} 条（阻塞 {blocking}）"]
    for c in task["criteria"][:6]:
        lines.append(f"· [{c.get('acceptance_layer')}] {c['criterion'][:40]}")
    lines.append(f"详情：{panel_url()}/?task_id={task['task_id']}")
    if autorun_enabled():
        threading.Thread(target=_run_bg,
                         args=(task["task_id"], ident, task.get("pack_id")),
                         daemon=True).start()
        lines.append("已开工，跑完自动把验收卡片推给你。")
    return "\n".join(lines)


# ------------------------------------------------------------------ card actions
def handle_action(conn: sqlite3.Connection, wecom_userid: str, key: str) -> str:
    ident = identity_for(conn, wecom_userid)
    if ident is None:
        return "这个企微账号还没有绑定 OA 身份，无法操作。"

    parts = key.split(":")
    action = parts[0] if parts else ""
    client = client_for(ident)

    try:
        if action == "accept" and len(parts) >= 2:
            tid = parts[1]
            r = client.post(f"/tasks/{tid}/acceptance/run")
            if r.status_code >= 400:
                return f"验收失败：{r.status_code} {r.text[:180]}"
            res = r.json()
            pending = sum(1 for x in res["results"] if x["state"] == "pending")
            return (f"{tid} 验收结果：{res['verdict']}（任务 {res['task_state']}）"
                    + (f"\n还有 {pending} 条人工判据待签，去面板处理。" if pending else ""))

        if action in ("approve", "reject") and len(parts) >= 2:
            did = parts[1]
            r = client.post(f"/decisions/{did}/decide",
                            json={"approved": action == "approve",
                                  "note": "企微卡片审批"})
            if r.status_code == 403:
                return "你没有这个决策包的审批权限（需要 CEO）。"
            if r.status_code >= 400:
                return f"审批失败：{r.status_code} {r.text[:180]}"
            d = r.json()
            return f"决策 {did} 已{'同意' if action == 'approve' else '驳回'}，状态 {d.get('state')}。"

        if action == "judge" and len(parts) >= 2:
            cid = parts[1]
            row = conn.execute(
                """SELECT c.*, t.id AS task_id FROM acceptance_criteria c
                   JOIN tasks t ON t.id = c.task_id WHERE c.id = ?""", (cid,)).fetchone()
            if row is None:
                return f"找不到判据 {cid}"
            art = conn.execute(
                """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id = a.run_id
                   WHERE r.task_id = ? ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1""",
                (row["task_id"],)).fetchone()
            snippet = None
            if art:
                try:
                    snippet = pathlib.Path(art["file_path"]).read_text(
                        encoding="utf-8", errors="replace")[:200]
                except OSError:
                    snippet = None
            r = client.post(f"/criteria/{cid}/check",
                            json={"passed": True, "note": "企微卡片人工通过",
                                  "evidence_snippet": snippet})
            if r.status_code >= 400:
                return f"人工判定失败：{r.status_code} {r.text[:160]}"
            acc = client.post(f"/tasks/{row['task_id']}/acceptance/run")
            verdict = acc.json().get("verdict") if acc.status_code < 400 else acc.status_code
            left = conn.execute(
                """SELECT COUNT(*) FROM acceptance_criteria WHERE task_id = ?
                   AND check_type = 'human_check' AND state = 'pending'""",
                (row["task_id"],)).fetchone()[0]
            tail = f"还有 {left} 条人工判据。" if left else "全部判据完成。"
            return f"判据 {row['seq']} 已通过，验收：{verdict}。{tail}"

        if action == "detail":
            if len(parts) >= 3 and parts[1] == "decision":
                return f"决策详情：{panel_url()}/（工作台·董事长看板）\n决策包 {parts[2]}"
            if len(parts) >= 2:
                return f"{parts[1]} 详情：{panel_url()}/?task_id={parts[1]}"

        return f"不认识的操作：{key}"
    except Exception as exc:            # noqa: BLE001 - always answer the user
        return f"操作失败：{type(exc).__name__}: {exc}"
