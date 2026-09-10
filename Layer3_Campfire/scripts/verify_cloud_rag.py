#!/usr/bin/env python3
"""Run an authenticated cloud-RAG smoke test and require cited output."""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request


BACKEND_URL = os.getenv(
    "CAMPFIRE_SMOKE_BACKEND_URL", "http://127.0.0.1:8000"
).rstrip("/")
DEFAULT_PROMPT = (
    "What evidence in the available collection describes Theodore Roosevelt's "
    "approach to conservation?"
)


def main() -> int:
    api_key = os.getenv("RAG_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RAG_API_KEY is required")

    chat_id = f"cloud-smoke-{secrets.token_hex(6)}"
    user_id = f"cloud-smoke-{secrets.token_hex(6)}"
    payload = {
        "message": os.getenv("CAMPFIRE_SMOKE_PROMPT", DEFAULT_PROMPT),
        "chat_id": chat_id,
        "user_id": user_id,
        "mode": "research",
    }
    request = urllib.request.Request(
        f"{BACKEND_URL}/api/chat",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Cloud RAG smoke test returned HTTP {exc.code}: {detail}"
        ) from exc

    if body.get("type") != "final" or not str(body.get("text", "")).strip():
        raise RuntimeError(f"Cloud RAG smoke test did not return a final answer: {body}")
    citations = body.get("citations")
    if not isinstance(citations, list) or not citations:
        raise RuntimeError("Cloud RAG smoke test returned no citations")

    delete_request = urllib.request.Request(
        f"{BACKEND_URL}/api/chat-history/{user_id}/{chat_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(delete_request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Cloud RAG smoke cleanup returned HTTP {response.status}"
            )

    print(f"Cloud RAG smoke test passed with {len(citations)} citation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
