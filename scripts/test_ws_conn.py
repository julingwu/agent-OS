import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://127.0.0.1:8077/ws/metacorp"
    async with websockets.connect(uri) as ws:
        print(f"Connected to {uri} successfully!")
        # receive greeting or first broadcast if any, or ping
        await ws.send(json.dumps({"type": "PING"}))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print("Received from /ws/metacorp:", msg)
        except asyncio.TimeoutError:
            print("Ping sent; connection active (no immediate reply, normal for broadcast hub).")

if __name__ == "__main__":
    asyncio.run(test_ws())
