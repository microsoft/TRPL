#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Minimal downstream WebSocket consumer for the camera_platform event stack.

camera_platform (transport="ws", the default) HOSTS a WebSocket server and
pushes every event onto a consume-once FIFO stack. This script is the other
half: a downstream client that connects in and drains the stack, printing each
event. It is a reference / smoke-test stand-in for lia_agent_api's real
consumer.

Usage:
    python scripts/ws_consumer.py                      # ws://127.0.0.1:8765
    python scripts/ws_consumer.py ws://<host>:8765

Requires: pip install websockets
"""
import asyncio
import json
import sys

import websockets


async def consume(url: str) -> None:
    print(f"[ws_consumer] connecting to {url} ...")
    # Reconnect loop: the camera buffers events while we are away (up to its
    # stack bound), so a reconnect resumes from the front of the backlog.
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print("[ws_consumer] connected — draining event stack")
                async for frame in ws:
                    try:
                        event = json.loads(frame)
                    except json.JSONDecodeError:
                        print(f"[ws_consumer] non-JSON frame: {frame[:120]}")
                        continue
                    et = event.get("event_type", "?")
                    pid = event.get("payload", {})
                    print(f"[ws_consumer] {et:24} {json.dumps(pid, ensure_ascii=False)[:160]}")
        except (OSError, websockets.exceptions.WebSocketException) as e:
            print(f"[ws_consumer] disconnected ({type(e).__name__}); retrying in 2s")
            await asyncio.sleep(2.0)


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"
    try:
        asyncio.run(consume(url))
    except KeyboardInterrupt:
        print("\n[ws_consumer] bye")


if __name__ == "__main__":
    main()
