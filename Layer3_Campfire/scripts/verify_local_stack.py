#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Verify the Layer 3 degraded local stack without Azure dependencies."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import time
from typing import Callable, TypeVar
import urllib.error
from urllib.parse import urlsplit
import urllib.request

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = (
    SCRIPT_ROOT / "backend" if (SCRIPT_ROOT / "backend").is_dir() else SCRIPT_ROOT
)
sys.path.insert(0, str(BACKEND_DIR))

from chatbot.chat_history import ChatHistoryManager  # noqa: E402
from common_config import REDIS_URL_CHAT_STORE  # noqa: E402
from models import ChatMessageItem  # noqa: E402
from redis.asyncio import Redis  # noqa: E402


BACKEND_URL = os.getenv(
    "CAMPFIRE_VERIFY_BACKEND_URL", "http://127.0.0.1:8000"
).rstrip("/")
FRONTEND_URL = os.getenv(
    "CAMPFIRE_VERIFY_FRONTEND_URL", "http://layer3-frontend:3000"
).rstrip("/")
WAIT_TIMEOUT_SECONDS = float(os.getenv("CAMPFIRE_VERIFY_WAIT_SECONDS", "120"))
ProbeResult = TypeVar("ProbeResult")


def _load_api_key() -> str:
    api_key = os.getenv("RAG_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RAG_API_KEY is required")
    return api_key


def _request(
    path: str,
    *,
    api_key: str | None = None,
    method: str = "GET",
    expected_status: int = 200,
    json_body: object | None = None,
) -> object:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BACKEND_URL}{path}",
        method=method,
        headers=headers,
        data=data,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    if status != expected_status:
        raise RuntimeError(
            f"{method} {path} returned HTTP {status}, expected {expected_status}: "
            f"{body.decode('utf-8', errors='replace')}"
        )
    return json.loads(body) if body else None


def _wait_for(name: str, probe: Callable[[], ProbeResult]) -> ProbeResult:
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return probe()
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(
        f"{name} did not become ready within {WAIT_TIMEOUT_SECONDS:g} seconds"
    ) from last_error


def _frontend_health() -> None:
    with urllib.request.urlopen(f"{FRONTEND_URL}/api/health", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Frontend health returned HTTP {response.status}")


async def _redis_ping() -> None:
    redis_client = Redis.from_url(REDIS_URL_CHAT_STORE)
    try:
        if not await redis_client.ping():
            raise RuntimeError("Redis did not return PONG")
    finally:
        await redis_client.aclose()


async def _write_history_through_application(
    user_id: str,
    chat_id: str,
) -> tuple[str, str, str]:
    redis_client = Redis.from_url(REDIS_URL_CHAT_STORE)
    message_key = f"user:{user_id}:chatmessages:{chat_id}"
    chats_key = f"user:{user_id}:chats"
    metadata_key = f"user:{user_id}:chatmeta:{chat_id}"
    try:
        manager = ChatHistoryManager(redis_client)
        await manager.add_messages(
            chat_id,
            user_id,
            [
                ChatMessageItem(
                    role="user",
                    text="Layer 3 local Redis round trip",
                    timestamp=time.time(),
                )
            ],
        )
        await manager.set_chat_metadata(chat_id, user_id, mode="research")

        ttls = [
            await redis_client.ttl(message_key),
            await redis_client.ttl(chats_key),
            await redis_client.ttl(metadata_key),
        ]
        if any(ttl <= 0 for ttl in ttls):
            raise RuntimeError(f"Application history keys are missing TTLs: {ttls}")
        return message_key, chats_key, metadata_key
    finally:
        await redis_client.aclose()


async def _cleanup_history(*keys: str) -> None:
    redis_client = Redis.from_url(REDIS_URL_CHAT_STORE)
    try:
        await redis_client.delete(*keys)
    finally:
        await redis_client.aclose()


def _websocket_handshake(api_key: str | None) -> socket.socket:
    backend = urlsplit(BACKEND_URL)
    host = backend.hostname or "127.0.0.1"
    port = backend.port or (443 if backend.scheme == "https" else 80)
    client = socket.create_connection((host, port), timeout=10)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    headers = [
        "GET /ws/chat HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if api_key:
        headers.append(f"Authorization: Bearer {api_key}")
    client.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += client.recv(4096)
    status = response.split(b"\r\n", 1)[0]
    expected = b"101" if api_key else b"403"
    if expected not in status:
        client.close()
        raise RuntimeError(f"Unexpected WebSocket handshake status: {status!r}")
    return client


def _send_websocket_text(client: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask = secrets.token_bytes(4)
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(0x80 | len(payload))
    elif len(payload) < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", len(payload)))
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    client.sendall(bytes(header) + mask + masked)


def _receive_websocket_text(client: socket.socket) -> str:
    def receive_exact(length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = client.recv(length - len(data))
            if not chunk:
                raise RuntimeError("WebSocket closed before returning a response")
            data += chunk
        return data

    first, second = receive_exact(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", receive_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", receive_exact(8))[0]
    payload = receive_exact(length)
    if first & 0x0F == 0x8:
        raise RuntimeError("WebSocket closed before returning a response")
    return payload.decode("utf-8")


def main() -> int:
    api_key = _load_api_key()
    _wait_for("Redis", lambda: asyncio.run(_redis_ping()))
    health = _wait_for("backend", lambda: _request("/healthz"))
    expected_health = {
        "status": "ok",
        "mode": "degraded",
        "rag_available": False,
        "redis": "ok",
    }
    if not isinstance(health, dict) or any(
        health.get(key) != value for key, value in expected_health.items()
    ):
        raise RuntimeError(f"Unexpected backend health response: {health}")

    _wait_for("frontend", _frontend_health)

    frontend_chat = urllib.request.Request(
        f"{FRONTEND_URL}/api/chat",
        method="POST",
        data=json.dumps(
            {
                "message": "local frontend proxy probe",
                "sessionId": f"verify-{secrets.token_hex(6)}",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(frontend_chat, timeout=20) as response:
        frontend_stream = response.read().decode("utf-8")
        local_cookie = response.headers.get("Set-Cookie", "")
    if response.status != 200 or "degraded local mode" not in frontend_stream:
        raise RuntimeError(
            f"Unexpected frontend chat proxy response: {frontend_stream}"
        )
    if not local_cookie.startswith("rr_uid=") or "Secure" in local_cookie:
        raise RuntimeError(f"Unexpected local user cookie: {local_cookie}")
    cookie_value = local_cookie.split(";", 1)[0]
    frontend_history = urllib.request.Request(
        f"{FRONTEND_URL}/api/chat-history/ignored-user-id",
        headers={"Cookie": cookie_value},
    )
    with urllib.request.urlopen(frontend_history, timeout=10) as response:
        if response.status != 200 or response.headers.get("Set-Cookie"):
            raise RuntimeError("Frontend did not preserve the signed local user cookie")

    _request("/api/chat-history/local-user", expected_status=401)
    _request("/api/chat-history/local-user", api_key=api_key)
    unavailable = _request(
        "/api/chat",
        api_key=api_key,
        method="POST",
        expected_status=503,
        json_body={"message": "local availability probe"},
    )
    if not isinstance(unavailable, dict) or "degraded local mode" not in unavailable.get(
        "detail", ""
    ):
        raise RuntimeError(f"Unexpected degraded HTTP response: {unavailable}")

    unauthenticated = _websocket_handshake(None)
    unauthenticated.close()
    authenticated = _websocket_handshake(api_key)
    try:
        _send_websocket_text(
            authenticated,
            json.dumps(
                {
                    "message": "local availability probe",
                    "chat_id": "local-probe-chat",
                    "user_id": "local-probe-user",
                }
            ),
        )
        response = json.loads(_receive_websocket_text(authenticated))
        end_marker = _receive_websocket_text(authenticated)
        if response.get("type") != "error" or "degraded local mode" not in response.get(
            "error", ""
        ):
            raise RuntimeError(f"Unexpected degraded WebSocket response: {response}")
        if end_marker != "[END]":
            raise RuntimeError(f"Unexpected WebSocket end marker: {end_marker!r}")
    finally:
        authenticated.close()

    user_id = f"verify-{secrets.token_hex(6)}"
    chat_id = f"verify-{secrets.token_hex(6)}"
    history_keys = asyncio.run(_write_history_through_application(user_id, chat_id))
    try:
        chats = _request(f"/api/chat-history/{user_id}", api_key=api_key)
        if (
            not isinstance(chats, list)
            or not chats
            or chats[0].get("chat_id") != chat_id
            or chats[0].get("mode") != "research"
        ):
            raise RuntimeError(f"Unexpected user chat history response: {chats}")

        messages = _request(
            f"/api/chat-history/{user_id}/{chat_id}/messages",
            api_key=api_key,
        )
        if (
            not isinstance(messages, list)
            or not messages
            or messages[0]["text"] != "Layer 3 local Redis round trip"
        ):
            raise RuntimeError(f"Unexpected chat history response: {messages}")
        _request(
            f"/api/chat-history/{user_id}/{chat_id}",
            api_key=api_key,
            method="DELETE",
        )
        if _request(
            f"/api/chat-history/{user_id}/{chat_id}/messages",
            api_key=api_key,
        ):
            raise RuntimeError("Chat history was not deleted")
        if _request(f"/api/chat-history/{user_id}", api_key=api_key):
            raise RuntimeError("Deleted chat remains in the user chat index")
    finally:
        asyncio.run(_cleanup_history(*history_keys))

    print(
        "Layer 3 local verification passed: frontend/backend health, HTTP and "
        "WebSocket auth, degraded RAG signaling, and Redis chat history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
