#!/usr/bin/env python3
"""Verify Layer 4 cloud text generation and retained Layer 2 book retrieval."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from urllib.parse import quote

import aiohttp

from debate.services.book_search import BookSearchClient


QUERY = (
    "According to the fictional acceptance-test account The Juniper Basin "
    "Field Notes, in what year was the Blue Heron Compact made, what did it "
    "establish, and what reports were reviewed before it was named?"
)
EXPECTED_TITLE = "The Juniper Basin Field Notes"
EXPECTED_FACTS = ("blue heron compact", "juniper basin", "protected reserve")
TURN_TIMEOUT_SECONDS = 120


def _require(value: str, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


async def _verify_retained_book() -> None:
    client = BookSearchClient()
    try:
        results = await client.search(QUERY, top_k=5)
    finally:
        await client.close()

    retained = []
    for result in results:
        normalized_text = " ".join((result.get("text") or "").lower().split())
        if result.get("title") == EXPECTED_TITLE and all(
            fact in normalized_text for fact in EXPECTED_FACTS
        ):
            retained.append(result)
    if not retained:
        raise RuntimeError(
            "Layer 2 book search did not return the retained fictional acceptance record"
        )
    print("[ok] retained Layer 2 EPUB is queryable from Layer 4")


async def _next_reply(
    ws: aiohttp.ClientWebSocketResponse,
    *,
    timeout: int,
) -> str:
    chunks: list[str] = []
    while True:
        message = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if message.type == aiohttp.WSMsgType.ERROR:
            raise RuntimeError(f"WebSocket error: {ws.exception()}")
        if message.type in {
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        }:
            raise RuntimeError("WebSocket closed before the reply completed")
        if message.type != aiohttp.WSMsgType.TEXT:
            continue

        event: dict[str, Any] = json.loads(message.data)
        if event.get("type") == "session_complete":
            raise RuntimeError("Story session completed before producing a reply")
        if event.get("type") != "debate_output":
            continue
        text = event.get("text") or ""
        if text:
            chunks.append(text)
        if event.get("waiting_for_input") and (
            event.get("stream_end") or event.get("streaming") is not True
        ):
            return "".join(chunks).strip()


async def _verify_cloud_conversation(base: str, api_key: str) -> None:
    headers = {"X-Api-Key": api_key}
    timeout = aiohttp.ClientTimeout(total=TURN_TIMEOUT_SECONDS + 30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as http:
        async with http.get(f"{base}/healthz") as response:
            response.raise_for_status()
            health = await response.json()
        if health.get("runtime_mode") != "cloud":
            raise RuntimeError(f"Expected cloud runtime, received: {health}")
        providers = health.get("providers", {})
        if not providers.get("llm") or not providers.get("search"):
            raise RuntimeError(f"Required cloud providers are unavailable: {providers}")
        print("[ok] cloud LLM and Search configuration is active")

        payload = {
            "scenario_id": "storys",
            "phases": ["storys"],
            "players": {},
            "visitor_mode": "adult",
            "wants_audio": False,
            "websocket_streaming": True,
        }
        async with http.post(f"{base}/api/debate/start", json=payload) as response:
            response.raise_for_status()
            session = await response.json()

        ws_path = session["ws_url"]
        scheme = "wss" if base.startswith("https://") else "ws"
        host = base.split("://", 1)[1].rstrip("/")
        token = quote(_require(session.get("controller_token", ""), "controller_token"))
        ws_url = f"{scheme}://{host}{ws_path}?token={token}"
        async with http.ws_connect(ws_url, heartbeat=20) as ws:
            await ws.send_json(
                {"type": "participant_joined", "participant_id": "acceptance-visitor"}
            )
            greeting = await _next_reply(ws, timeout=TURN_TIMEOUT_SECONDS)
            if not greeting:
                raise RuntimeError("Story session did not produce its initial greeting")

            await ws.send_json(
                {
                    "type": "participant_input",
                    "spoken": {
                        "participant_id": "acceptance-visitor",
                        "text": QUERY,
                        "utterance_id": "layer4-cloud-acceptance",
                    },
                }
            )
            answer = await _next_reply(ws, timeout=TURN_TIMEOUT_SECONDS)

    normalized = " ".join(answer.lower().split())
    required_grounding = (
        "1908",
        "reserve",
        "forest",
    )
    if not all(term in normalized for term in required_grounding):
        raise RuntimeError(
            "Cloud story response did not contain retained EPUB facts: "
            f"{answer[:500]!r}"
        )
    print("[ok] authenticated WebSocket response used the retained EPUB facts")


async def run() -> None:
    await _verify_retained_book()
    base = os.getenv("LIA_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    api_keys = _require(os.getenv("CLIENT_API_KEYS", ""), "CLIENT_API_KEYS")
    api_key = api_keys.split(",", 1)[0].strip()
    await _verify_cloud_conversation(base, api_key)


def main() -> int:
    try:
        asyncio.run(run())
    except Exception as error:
        print(f"[fail] {error}", file=sys.stderr)
        return 1
    print("[done] Layer 4 cloud-text acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
