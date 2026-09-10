"""Opaque continuation tokens for offset-based pagination.

A token is the base64 encoding of {"skip": N} — matching the example token in
the content source sync API design (Section 5.1), which decodes to {"skip": 50}.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import HTTPException


def encode_token(skip: int) -> str:
    raw = json.dumps({"skip": int(skip)}, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_token(token: Optional[str]) -> int:
    """Return the skip offset from a continuation token (0 when absent)."""
    if not token:
        return 0
    try:
        raw = base64.b64decode(token.encode("ascii"))
        skip = int(json.loads(raw)["skip"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CONTINUATION_TOKEN", "message": "Malformed continuation_token."},
        ) from exc
    if skip < 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CONTINUATION_TOKEN", "message": "continuation_token offset must be >= 0."},
        )
    return skip
