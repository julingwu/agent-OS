"""Capture the desktop webview via CDP (Page.captureScreenshot) —真窗口内容，不受遮挡."""
import asyncio, base64, json, sys
import httpx, websockets

async def main(port: int, out: str) -> None:
    pages = httpx.get(f"http://127.0.0.1:{port}/json/list", trust_env=False).json()
    ws_url = [p for p in pages if p.get("type") == "page"][0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot",
                                  "params": {"format": "png", "captureBeyondViewport": False}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if msg.get("id") == 2:
                open(out, "wb").write(base64.b64decode(msg["result"]["data"]))
                print("saved", out)
                return

asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
