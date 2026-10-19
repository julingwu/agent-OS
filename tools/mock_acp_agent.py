"""Mock ACP agent: same JSON-RPC surface as `dsh --profile acp`, no model call.

Used by tests and as a zero-cost runner for the ACP path. State (session id and
turn history) lives in `.mock_acp_state.json` inside the cwd so session/resume
works across processes exactly like the real agent's persisted session.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import sys
import threading
import time

STATE = ".mock_acp_state.json"
ARTICLE = pathlib.Path(__file__).resolve().parents[1] / "tools" / "mock_agent.py"


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def main() -> int:
    inbox: "queue.Queue[dict]" = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                inbox.put(json.loads(line))
            except json.JSONDecodeError:
                inbox.put({"__raw__": line})
        inbox.put({"__eof__": True})

    threading.Thread(target=reader, daemon=True).start()

    state_path = pathlib.Path.cwd() / STATE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {"session_id": None, "turns": []}

    sid = state.get("session_id")
    overspend = os.environ.get("MOCK_ACP_OVERSPEND") == "1"
    cancelled = False

    while True:
        try:
            msg = inbox.get(timeout=0.2)
        except queue.Empty:
            continue
        if msg.get("__eof__"):
            return 0
        method = msg.get("method")
        rid = msg.get("id")
        if method == "session/cancel":
            cancelled = True
            continue
        if method == "initialize" and rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1,
                "agentInfo": {"name": "mock-acp", "version": "0.0.1"},
                "agentCapabilities": {"sessionCapabilities": {"close": {}, "list": {}, "resume": {}}},
                "authMethods": []}})
        elif method == "session/new" and rid is not None:
            sid = os.environ.get("MOCK_SESSION_ID") or f"sess-mock-{int(time.time() * 1000)}"
            state["session_id"] = sid
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid, "configOptions": []}})
        elif method == "session/resume" and rid is not None:
            sid = (msg.get("params") or {}).get("sessionId") or state.get("session_id")
            state["session_id"] = sid
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": [], "resumed": True}})
        elif method == "session/prompt" and rid is not None:
            params = msg.get("params") or {}
            sid = params.get("sessionId") or sid
            text = "".join(b.get("text", "") for b in params.get("prompt") or [])
            state.setdefault("turns", []).append(text[:200])
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            cancelled = False            # 新的一轮：上一轮的 cancel 不该影响续跑

            if os.environ.get("MOCK_ACP_ESCAPE_TOOL") == "1":
                notify("session/update", {"sessionId": sid, "update": {
                    "sessionUpdate": "tool_call", "toolCallId": "evil1", "title": "write",
                    "kind": "other", "status": "in_progress",
                    "rawInput": {"file_path": str(pathlib.Path("..", "escaped.txt").resolve()),
                                 "content": "nope"}}})
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    try:
                        m = inbox.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if m.get("method") == "session/cancel":
                        cancelled = True
                        break
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"stopReason": "cancelled" if cancelled else "end_turn"}})
                continue

            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "agent_thought_chunk", "messageId": "m1",
                "content": {"type": "text", "text": "按模板写短文并落盘。"}}})
            base_usage = int(state.get("usage", 0))
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "usage_update", "used": base_usage + 420, "size": 1000000}})
            if cancelled or overspend:
                notify("session/update", {"sessionId": sid, "update": {
                    "sessionUpdate": "usage_update", "used": base_usage + 999999,
                    "size": 1000000}})
                # give the client a chance to send session/cancel
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    try:
                        m = inbox.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if m.get("method") == "session/cancel":
                        cancelled = True
                        break
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"stopReason": "cancelled" if cancelled else "end_turn"}})
                continue

            target_name = os.environ.get("MOCK_ACP_TARGET")
            if target_name:
                target = pathlib.Path(target_name)
                if not target.is_absolute():
                    target = pathlib.Path(os.environ.get("MOCK_WORKSPACE")
                                          or pathlib.Path.cwd()) / target
                body = os.environ.get("MOCK_ACP_TEXT") or f"mock rbac drill\n"
            else:
                article = pathlib.Path(os.environ.get("MOCK_WORKSPACE") or pathlib.Path.cwd())
                article.mkdir(parents=True, exist_ok=True)
                target = article / "article.md"
                body = _article_text()
            target.parent.mkdir(parents=True, exist_ok=True)
            # 通知先于落盘：闸门有机会在写入发生前拦下（真实工具的语义）
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "tool_call", "toolCallId": "c1", "title": "write",
                "kind": "other", "status": "in_progress",
                "rawInput": {"file_path": str(target), "content": body}}})
            deadline = time.time() + 0.8
            while time.time() < deadline:
                try:
                    m = inbox.get(timeout=0.1)
                except queue.Empty:
                    continue
                if m.get("method") == "session/cancel":
                    cancelled = True
                    break
            if cancelled:
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
                continue
            target.write_text(body, encoding="utf-8")
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                "content": [{"type": "content", "content": {"type": "text",
                                                            "text": f"<path>{target}</path>"}}]}})
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "usage_update", "used": base_usage + 980,
                "size": 1000000}})
            state["usage"] = base_usage + 980
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk", "messageId": "m2",
                "content": {"type": "text", "text": f"DONE turns={len(state['turns'])}"}}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"unsupported: {method}"}})
    return 0


def _article_text() -> str:
    """Reuse the exact article the headless mock writes, so L0/L2 verdicts match."""
    text = ARTICLE.read_text(encoding="utf-8")
    start = text.index('ARTICLE = """') + len('ARTICLE = """')
    end = text.index('"""', start)
    return text[start:end]


if __name__ == "__main__":
    sys.exit(main())
