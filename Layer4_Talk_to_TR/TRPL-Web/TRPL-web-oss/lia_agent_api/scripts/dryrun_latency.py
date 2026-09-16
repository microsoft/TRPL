# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Dry-run latency probe: first-token latency per question, no LiveKit.

Each question runs in its OWN fresh storys session (clean round-2 TTFT),
over the same /api/debate/start + WS path the worker uses.
"""
import asyncio
import json
import os
import statistics
import sys
import time

import aiohttp

BASE = os.getenv("LIA_API_BASE", "http://127.0.0.1:8010")
KEY = os.getenv("LIA_API_KEY", "")

# (category, question).  NOKB ~= chit-chat / opinion / light-personal (no KB).
QUESTIONS = [
    ("NOKB", "Hey, Mr. President, how are you?"),
    ("NOKB", "Wow, you have so much energy!"),
    ("NOKB", "Thanks, this is really fun."),
    ("NOKB", "What makes a good leader?"),
    ("NOKB", "Any advice for a young person starting out?"),
    ("NOKB", "Do you enjoy meeting visitors like me?"),
    ("KB", "Tell me about the coal strike of 1902."),
    ("KB", "What was the Badlands like?"),
    ("KB", "Tell me about your daughter Alice."),
    ("KB", "What happened at San Juan Hill?"),
    ("KB", "Tell me about the Rough Riders."),
    ("KB", "What did you do for conservation?"),
    ("KB", "Tell me about the Panama Canal."),
    ("KB", "What was trust-busting about?"),
    ("KB", "Tell me about your African safari."),
    ("KB", "What happened with the Bull Moose Party?"),
    ("KB", "Tell me about the Square Deal."),
    ("KB", "What was your relationship with Booker T. Washington?"),
    ("KB", "Tell me about the Great White Fleet."),
    ("KB", "What happened in the election of 1904?"),
    ("KB", "Tell me about your time as a rancher in Dakota."),
    ("KB", "What was the Nobel Peace Prize you won for?"),
    ("KB", "Tell me about mediating the Russo-Japanese War."),
    ("KB", "What did you write to Henry Cabot Lodge?"),
    ("KB", "Tell me about your father."),
    ("KB", "What happened during the Spanish-American War?"),
    ("KB", "Tell me about the national parks you created."),
    ("KB", "What was the Pure Food and Drug Act?"),
    ("KB", "Tell me about your hunting expeditions."),
    ("KB", "What happened at Kettle Hill?"),
]

TURN_TIMEOUT = 70.0


def _ws_url(http_base: str, ws_path: str) -> str:
    if ws_path.startswith("ws://") or ws_path.startswith("wss://"):
        return ws_path
    scheme = "wss" if http_base.startswith("https") else "ws"
    host = http_base.split("://", 1)[1]
    return f"{scheme}://{host}{ws_path}"


async def measure_one(http: aiohttp.ClientSession, question: str) -> dict:
    """Returns dict with ttft_ms, first_text, ok."""
    payload = {
        "scenario_id": "storys", "phases": ["storys"], "players": {},
        "visitor_mode": "adult", "wants_audio": False, "websocket_streaming": True,
    }
    async with http.post(f"{BASE}/api/debate/start", json=payload) as r:
        if r.status != 200:
            return {"ok": False, "err": f"start {r.status}"}
        data = await r.json()
    ws_url = _ws_url(BASE, data["ws_url"])

    async with http.ws_connect(ws_url, heartbeat=20) as ws:
        await ws.send_json({"type": "participant_joined", "participant_id": "probe"})

        # Drain the pre-greeting: wait until its utterance ends (stream_end).
        await _wait_greeting_end(ws)

        # Send the question; stamp t0 right before send.
        t0 = time.monotonic()
        await ws.send_json({
            "type": "participant_input",
            "spoken": {"participant_id": "probe", "text": question,
                       "utterance_id": "q-1"},
        })

        first_text = None
        ttft = None
        try:
            async with asyncio.timeout(TURN_TIMEOUT):
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    ev = json.loads(msg.data)
                    if ev.get("type") != "debate_output":
                        continue
                    text = ev.get("text") or ""
                    if text and ttft is None:
                        ttft = (time.monotonic() - t0) * 1000.0
                        first_text = text
                        break
        except (asyncio.TimeoutError, TimeoutError):
            return {"ok": False, "err": "timeout"}

        if ttft is None:
            return {"ok": False, "err": "no-token"}
        return {"ok": True, "ttft_ms": ttft, "first_text": first_text[:50]}


async def _wait_greeting_end(ws) -> None:
    """Consume messages until the first agent utterance ends."""
    async with asyncio.timeout(TURN_TIMEOUT):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            ev = json.loads(msg.data)
            if ev.get("type") != "debate_output":
                continue
            if ev.get("stream_end") or (
                not ev.get("streaming") and bool(ev.get("text"))
            ):
                return


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


async def main() -> int:
    headers = {"X-Api-Key": KEY} if KEY else {}
    rows = []
    async with aiohttp.ClientSession(headers=headers) as http:
        for i, (cat, q) in enumerate(QUESTIONS, 1):
            res = await measure_one(http, q)
            if res["ok"]:
                rows.append((cat, q, res["ttft_ms"], res["first_text"]))
                print(f"{i:2d}. [{cat:4s}] {res['ttft_ms']:7.0f} ms  "
                      f"| {q[:42]:42s} -> {res['first_text']!r}")
            else:
                rows.append((cat, q, None, res.get("err")))
                print(f"{i:2d}. [{cat:4s}] {'FAIL':>7s}     "
                      f"| {q[:42]:42s} -> {res.get('err')}")

    print("\n================ SUMMARY (first-token latency) ================")
    for cat in ("NOKB", "KB", "ALL"):
        vals = [t for c, _, t, _ in rows
                if t is not None and (cat == "ALL" or c == cat)]
        n_total = len([1 for c, _, _, _ in rows if cat == "ALL" or c == cat])
        if not vals:
            print(f"{cat:4s}: no successful samples ({n_total} attempted)")
            continue
        print(f"{cat:4s} (n={len(vals)}/{n_total}): "
              f"mean={statistics.mean(vals):6.0f}  median={statistics.median(vals):6.0f}  "
              f"p95={pct(vals,95):6.0f}  min={min(vals):6.0f}  max={max(vals):6.0f}  (ms)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
