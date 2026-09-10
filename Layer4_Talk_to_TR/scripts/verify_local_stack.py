#!/usr/bin/env python3
"""Verify the provider-free Layer 4 local stack."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import secrets
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
BRAIN_URL = "http://127.0.0.1:8010"
TOKEN_URL = "http://127.0.0.1:8002"
BRAIN_ENV = (
    ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-Web"
    / "TRPL-web-oss"
    / "lia_agent_api"
    / ".env.local"
)
TOKEN_ENV = (
    ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-Web"
    / "TRPL-web-oss"
    / "webapp-tokenserver"
    / ".env.local"
)


def _env_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key == name and value:
            return value
    raise RuntimeError(f"{name} is missing from {path}")


def _request(
    base_url: str,
    path: str,
    *,
    api_key: str | None = None,
    admin_token: str | None = None,
    method: str = "GET",
    expected_status: int = 200,
    json_body: object | None = None,
) -> object:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-Api-Key"] = api_key
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}",
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
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8")


def _websocket_handshake(
    path: str, *, expect_success: bool
) -> tuple[socket.socket, bytearray]:
    client = socket.create_connection(("127.0.0.1", 8010), timeout=10)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    headers = [
        f"GET {path} HTTP/1.1",
        "Host: 127.0.0.1:8010",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    client.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += client.recv(4096)
    status = response.split(b"\r\n", 1)[0]
    expected = b"101" if expect_success else b"403"
    if expected not in status:
        client.close()
        raise RuntimeError(f"Unexpected WebSocket handshake status: {status!r}")
    _, _, buffered = response.partition(b"\r\n\r\n")
    return client, bytearray(buffered)


def _send_websocket_text(client: socket.socket, value: object) -> None:
    payload = json.dumps(value).encode("utf-8")
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
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    client.sendall(bytes(header) + mask + masked)


def _receive_websocket_json(client: socket.socket, buffered: bytearray) -> dict:
    def receive_exact(length: int) -> bytes:
        data = b""
        if buffered:
            take = min(length, len(buffered))
            data = bytes(buffered[:take])
            del buffered[:take]
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
    return json.loads(payload)


def main() -> int:
    api_key = _env_value(BRAIN_ENV, "CLIENT_API_KEYS")
    admin_token = _env_value(TOKEN_ENV, "ADMIN_TOKEN")

    expected_providers = {"llm": False, "search": False, "speech": False}
    brain_health = _request(BRAIN_URL, "/healthz")
    if brain_health != {
        "status": "ok",
        "runtime_mode": "deterministic",
        "providers": expected_providers,
    }:
        raise RuntimeError(f"Unexpected brain health response: {brain_health}")

    token_health = _request(TOKEN_URL, "/healthz")
    if token_health != {
        "status": "ok",
        "runtime_mode": "deterministic",
        "providers": {"livekit": False},
    }:
        raise RuntimeError(f"Unexpected token-server health response: {token_health}")
    _request(TOKEN_URL, "/")
    _request(TOKEN_URL, "/api/token", expected_status=503)
    _request(
        TOKEN_URL,
        "/admin/api/prompts",
        admin_token=admin_token,
        expected_status=503,
    )

    _request(
        BRAIN_URL,
        "/api/debate/start",
        method="POST",
        json_body={},
        expected_status=401,
    )
    _request(
        BRAIN_URL,
        "/api/debate/start",
        api_key="invalid-local-key",
        method="POST",
        json_body={},
        expected_status=401,
    )
    started = _request(
        BRAIN_URL,
        "/api/debate/start",
        api_key=api_key,
        method="POST",
        json_body={},
    )
    if not isinstance(started, dict):
        raise RuntimeError(f"Unexpected session response: {started}")

    session_id = started["session_id"]
    ws_path = started["ws_url"]
    invalid_ws, _ = _websocket_handshake(
        f"{ws_path}?token=invalid-local-token",
        expect_success=False,
    )
    invalid_ws.close()
    token = urllib.parse.quote(started["controller_token"], safe="")
    websocket, buffered = _websocket_handshake(
        f"{ws_path}?token={token}",
        expect_success=True,
    )
    try:
        startup_events = [
            _receive_websocket_json(websocket, buffered) for _ in range(2)
        ]
        runtime_events = [
            event for event in startup_events if event.get("type") == "runtime_status"
        ]
        if (
            len(runtime_events) != 1
            or runtime_events[0].get("providers") != expected_providers
        ):
            raise RuntimeError(f"Missing deterministic runtime event: {startup_events}")

        _send_websocket_text(websocket, {"type": "ping"})
        if _receive_websocket_json(websocket, buffered) != {"type": "pong"}:
            raise RuntimeError("WebSocket ping did not return pong")

        event_id = f"local-fixture-{secrets.token_hex(6)}"
        camera_result = _request(
            BRAIN_URL,
            "/api/camera/events",
            api_key=api_key,
            method="POST",
            json_body={
                "event_id": event_id,
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "local_fixture",
                "event_type": "BATCH_INVITE",
                "payload": {"fixture": "fictional-no-media"},
                "session_id": session_id,
            },
        )
        if not isinstance(camera_result, dict) or camera_result.get(
            "status"
        ) != "accepted":
            raise RuntimeError(f"Camera event was not accepted: {camera_result}")
        routed = _receive_websocket_json(websocket, buffered)
        if (
            routed.get("type") != "camera_event_received"
            or routed.get("event_id") != event_id
            or routed.get("session_id") != session_id
        ):
            raise RuntimeError(f"Camera event was not routed to WebSocket: {routed}")
    finally:
        websocket.close()

    print(
        "Layer 4 local verification passed: health, API and WebSocket auth, "
        "deterministic session transport, fictional camera replay, static UI, "
        "and explicit provider-unavailable responses."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
