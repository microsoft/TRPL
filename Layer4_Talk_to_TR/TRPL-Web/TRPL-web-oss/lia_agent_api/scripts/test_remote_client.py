#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Sanity-test the lia_agent_api protocol from a remote machine.

  LIA_API_BASE=http://host:8010 LIA_API_KEY=xxxx \
    python test_remote_client.py --say "hello" --say "tell me about the badlands"

It POSTs /api/debate/start, opens the WebSocket, announces a
participant, sends each --say in sequence (waiting for TR's reply
between them), and prints streaming deltas as they arrive.

Exit code 0 on success, non-zero on any failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

try:
    import aiohttp
except ImportError:  # pragma: no cover
    print("error: aiohttp is required. pip install aiohttp", file=sys.stderr)
    sys.exit(2)


DEFAULT_TURN_TIMEOUT = 60.0  # seconds to wait for a full TR reply


def _ws_url_from_base(http_base: str, ws_path: str) -> str:
    scheme = "wss" if http_base.startswith("https") else "ws"
    host = http_base.split("://", 1)[1]
    return f"{scheme}://{host.rstrip('/')}{ws_path}"


async def run(
    base: str,
    api_key: str,
    turns: list[str],
    participant_id: str,
    visitor_mode: str,
    turn_timeout: float,
) -> int:
    headers = {"X-Api-Key": api_key} if api_key else {}
    async with aiohttp.ClientSession(headers=headers) as http:

        # 1. POST /start
        payload = {
            "scenario_id": "storys",
            "phases": ["storys"],
            "players": {},
            "visitor_mode": visitor_mode,
            "wants_audio": False,
            "websocket_streaming": True,
        }
        print(f"[POST] {base}/api/debate/start  payload={payload}")
        async with http.post(f"{base}/api/debate/start", json=payload) as r:
            if r.status != 200:
                body = await r.text()
                print(f"FAIL: /start {r.status}: {body[:500]}", file=sys.stderr)
                return 1
            data = await r.json()
        session_id = data["session_id"]
        ws_url = _ws_url_from_base(base, data["ws_url"])
        print(f"  session_id = {session_id}")
        print(f"  ws_url     = {ws_url}")

        # 2. WS connect
        controller_token = data.get("controller_token")
        if controller_token:
            separator = "&" if "?" in ws_url else "?"
            ws_url = f"{ws_url}{separator}token={controller_token}"

        async with http.ws_connect(ws_url, heartbeat=20) as ws:
            print(f"[WS]  connected")
            await ws.send_json({
                "type": "participant_joined",
                "participant_id": participant_id,
            })
            print(f"[WS]  → participant_joined({participant_id})")

            reader = asyncio.create_task(_consume(ws))

            # Wait for the pre-greeting to finish streaming so we don't
            # interrupt it.
            await _wait_for_utterance_end(reader, turn_timeout)

            for i, text in enumerate(turns, start=1):
                utt_in = f"u-in-{i}"
                print(f"\n[turn {i}] → user: {text!r}")
                await ws.send_json({
                    "type": "participant_input",
                    "spoken": {
                        "participant_id": participant_id,
                        "text": text,
                        "utterance_id": utt_in,
                    },
                })
                try:
                    await _wait_for_utterance_end(reader, turn_timeout)
                except asyncio.TimeoutError:
                    print(f"FAIL: no reply within {turn_timeout}s", file=sys.stderr)
                    reader.cancel()
                    return 1

            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
            await ws.close()
    print("\n[done]  OK")
    return 0


async def _consume(ws: "aiohttp.ClientWebSocketResponse"):
    """Read every WS message, print deltas, push utterance boundaries to
    a shared queue so the main loop can wait for each reply to finish."""
    current_utt: str | None = None
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            ev: dict[str, Any] = json.loads(msg.data)
        except json.JSONDecodeError:
            continue

        etype = ev.get("type")
        if etype == "debug_message":
            phase = ev.get("phase")
            content = (ev.get("content") or "").strip()
            if content and ("Transitioning" in content or "THREAT" in content):
                print(f"  [debug/{phase}] {content}")
            continue

        if etype != "debate_output":
            continue

        text = ev.get("text") or ""
        streaming = bool(ev.get("streaming"))
        stream_end = bool(ev.get("stream_end"))
        utt_id = ev.get("utterance_id") or ev.get("speaker") or "_"
        is_one_shot = not streaming and not stream_end and bool(text)

        if utt_id != current_utt and (text or is_one_shot):
            if current_utt is not None:
                print()  # newline after previous utterance
            print(f"  [TR/{utt_id}] ", end="", flush=True)
            current_utt = utt_id

        if text:
            print(text, end="", flush=True)

        if stream_end or is_one_shot:
            print()  # newline
            # notify waiter
            _END_QUEUE.put_nowait(utt_id)
            current_utt = None


_END_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()


async def _wait_for_utterance_end(reader: asyncio.Task, timeout: float) -> None:
    """Block until the next utterance ends (stream_end or one-shot)."""
    done, pending = await asyncio.wait(
        [asyncio.create_task(_END_QUEUE.get()), reader],
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not done:
        raise asyncio.TimeoutError()
    for t in done:
        if t is reader:
            # reader died — re-raise its exception if any
            exc = t.exception()
            if exc:
                raise exc
            return
        t.result()  # consume the queue item
    for t in pending:
        # keep the reader running; cancel only our own get() task
        if t is not reader:
            t.cancel()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--base",
        default=os.getenv("LIA_API_BASE", "http://127.0.0.1:8010"),
        help="lia_agent_api base URL (env: LIA_API_BASE)",
    )
    p.add_argument(
        "--api-key",
        default=os.getenv("LIA_API_KEY", ""),
        help="X-Api-Key header value (env: LIA_API_KEY)",
    )
    p.add_argument(
        "--say",
        action="append",
        default=[],
        help="User utterance to send. Repeatable; sent in order.",
    )
    p.add_argument(
        "--participant-id",
        default="visitor",
        help="participant_id to announce and send inputs as",
    )
    p.add_argument(
        "--visitor-mode",
        default="adult",
        choices=["adult", "child"],
        help="audience tone (default: adult)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TURN_TIMEOUT,
        help=f"per-turn timeout in seconds (default: {DEFAULT_TURN_TIMEOUT})",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    turns = args.say or [
        "Hello Mr. Roosevelt!",
        "Tell me about the Badlands.",
    ]
    try:
        return asyncio.run(
            run(
                base=args.base.rstrip("/"),
                api_key=args.api_key,
                turns=turns,
                participant_id=args.participant_id,
                visitor_mode=args.visitor_mode,
                turn_timeout=args.timeout,
            )
        )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
