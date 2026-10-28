"""假本地设备：模拟 exe 的本地桥，用于端到端验证（不属于产品代码）。

连接 /v1/device，应答 local_* 工具调用，返回可识别的假数据。
"""

from __future__ import annotations

import asyncio
import json
import ssl
import sys

import websockets


async def main(base: str, token: str) -> None:
    url = base.replace("https://", "wss://").replace("http://", "ws://") + f"/v1/device?token={token}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with websockets.connect(url, ssl=ctx, max_size=4_000_000) as ws:
        await ws.send(json.dumps({"t": "hello", "caps": ["exec", "fs", "info"]}))
        print("fake-device connected", flush=True)
        while True:
            raw = await ws.recv()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("t") != "tool_call":
                continue
            tool = msg.get("tool")
            args = msg.get("args") or {}
            print("tool_call", tool, json.dumps(args, ensure_ascii=False)[:200], flush=True)
            if tool == "local_info":
                result = {"ok": True, "os": "windows", "arch": "x86_64",
                          "hostname": "FAKE-DESKTOP", "username": "tester",
                          "home": "C:/Users/tester"}
            elif tool == "local_list":
                result = {"ok": True, "path": args.get("path", ""),
                          "items": [{"name": "demo.txt", "type": "file", "bytes": 12}]}
            elif tool == "local_read":
                result = {"ok": True, "path": args.get("path", ""),
                          "content": "hello from fake local computer"}
            elif tool == "local_write":
                result = {"ok": True, "path": args.get("path", ""),
                          "bytes": len(str(args.get("content", "")))}
            elif tool == "local_exec":
                result = {"ok": True, "exit_code": 0,
                          "stdout": f"fake-stdout: {args.get('cmd', '')}",
                          "stderr": ""}
            else:
                result = {"ok": False, "error": f"unknown tool {tool}"}
            await ws.send(json.dumps({"t": "tool_result",
                                      "call_id": msg.get("call_id"),
                                      "ok": bool(result.get("ok")),
                                      "result": result}))


if __name__ == "__main__":
    base, token = sys.argv[1], sys.argv[2]
    asyncio.run(main(base, token))
