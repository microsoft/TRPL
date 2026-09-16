# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Local E2E check: POST correction intake + GET list (uses Cosmos from .env.local).

Run from repo root or this directory:
  python scripts/e2e_correction_requests_local.py

Requires: ENVIRONMENT=local, CORRECTION_INTAKE_LOCAL_ENABLED=true,
CORRECTION_INTAKE_LOCAL_KEY set, and valid COSMOS_DB_* credentials.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Ensure app imports resolve
_api_root = Path(__file__).resolve().parent.parent
os.chdir(_api_root)
sys.path.insert(0, str(_api_root))

load_dotenv(".env.local")
# Synthetic UUIDs are not in Cosmos unless you opt in; unset env defaults to skip existence check.
if os.getenv("CORRECTION_INTAKE_REQUIRE_RECORD_EXISTS", "").strip() == "":
    os.environ["CORRECTION_INTAKE_REQUIRE_RECORD_EXISTS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from app import app  # noqa: E402


def main() -> int:
    key = os.getenv("CORRECTION_INTAKE_LOCAL_KEY", "")
    if not key:
        print("Set CORRECTION_INTAKE_LOCAL_KEY in .env.local", file=sys.stderr)
        return 1
    if os.getenv("ENVIRONMENT", "").lower() != "local":
        print("Set ENVIRONMENT=local for intake header bypass", file=sys.stderr)
        return 1

    rid = str(uuid.uuid4())
    client = TestClient(app)
    post = client.post(
        "/api/v1/correction-requests",
        json={
            "recordId": rid,
            "note": "E2E script: intake + list.",
            "source": "ReadingRoom",
        },
        headers={"X-Local-Correction-Intake-Key": key},
    )
    if post.status_code != 201:
        print("POST failed:", post.status_code, post.text, file=sys.stderr)
        return 2
    body = post.json()
    req_id = body["request_id"]
    print("Created:", req_id, "record_id:", body.get("record_id"))

    get = client.get("/api/v1/correction-requests?status=unread&limit=100")
    if get.status_code != 200:
        print("GET failed:", get.status_code, get.text, file=sys.stderr)
        return 3
    items = get.json().get("items") or []
    if not any(x.get("id") == req_id for x in items):
        print("New item not in unread list", file=sys.stderr)
        return 4
    print("List OK: unread count", len(items))
    print("Done. Open http://localhost:5173/correction-requests (API must proxy to same Cosmos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
