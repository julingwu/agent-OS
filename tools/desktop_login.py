"""Log into the desktop shell through its own page (CDP), without leaking the secret."""
import asyncio
import json
import sqlite3
import sys

import httpx
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9333


def creds():
    c = sqlite3.connect(r"C:\oaagent-m0\data\oa.db")
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT id, secret FROM identities WHERE role='ceo' AND state='active' LIMIT 1").fetchone()
    return r["id"], r["secret"]


async def main():
    ident, secret = creds()
    pages = httpx.get(f"http://127.0.0.1:{PORT}/json/list", trust_env=False).json()
    ws_url = pages[0]["webSocketDebuggerUrl"]
    expr = """
    (() => {
      const setV = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      const inputs = [...document.querySelectorAll('input')];
      if (inputs.length < 2) return 'no inputs';
      const fill = (el, v) => { setV.call(el, v); el.dispatchEvent(new Event('input', {bubbles:true})); };
      fill(inputs[0], %s);
      fill(inputs[1], %s);
      const btn = [...document.querySelectorAll('button')]
        .find(b => /进入控制台|登录/.test(b.textContent));
      if (!btn) return 'no button';
      btn.click();
      return 'clicked';
    })()
    """ % (json.dumps(ident), json.dumps(secret))

    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                                  "params": {"expression": expr, "returnByValue": True}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if msg.get("id") == 2:
                print("login:", msg.get("result", {}).get("result", {}).get("value"))
                break
        await asyncio.sleep(4)
        await ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {
            "expression": "JSON.stringify({text: document.body.innerText.slice(0,160), "
                          "chats: document.querySelectorAll('.chat-item, .list > *').length})",
            "returnByValue": True}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if msg.get("id") == 3:
                print("after:", msg.get("result", {}).get("result", {}).get("value"))
                break


asyncio.run(main())
