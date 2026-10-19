"""One-shot CDP probe: evaluate an expression in a WebView2/Chrome page."""
import asyncio
import json
import sys

import httpx
import websockets


async def main(port: int, expr: str) -> None:
    pages = httpx.get(f"http://127.0.0.1:{port}/json/list", trust_env=False).json()
    ws_url = pages[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Log.enable"}))
        await ws.send(json.dumps({
            "id": 3, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("id") == 3:
                res = msg.get("result", {}).get("result", {})
                print(json.dumps(res.get("value", res), ensure_ascii=False)[:4000])
                return
            if msg.get("method") in ("Log.entryAdded", "Runtime.exceptionThrown"):
                print("EVENT:", json.dumps(msg["params"], ensure_ascii=False)[:600])


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
