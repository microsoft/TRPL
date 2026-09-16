# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Minimal public error bodies for API responses (no stack traces or raw DB messages).

Clients receive {"error": "<short message>"}. Optional allowlisted keys on 409 for
known UX contracts (e.g. existing_document).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

from core.request_context import get_request_id

logger = logging.getLogger("archivist-api")

# Canonical messages — keep stable for callers
PUBLIC_ERROR_BY_STATUS: Dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    408: "Request timeout",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Invalid request",
    429: "Too many requests",
    500: "Internal server error",
    502: "Bad gateway",
    503: "Service unavailable",
    504: "Gateway timeout",
}


def public_error_text(status_code: int) -> str:
    return PUBLIC_ERROR_BY_STATUS.get(status_code, "Request failed")


def _safe_409_extras(detail: Any) -> Dict[str, Any]:
    """Allowlisted structured fields for 409 responses (logged in full server-side)."""
    if not isinstance(detail, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("existing_document", "current_job"):
        if key in detail:
            out[key] = detail[key]
    return out


def public_error_payload(status_code: int, detail: Any = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": public_error_text(status_code)}
    if status_code == 409:
        extras = _safe_409_extras(detail)
        body.update(extras)
    if isinstance(detail, str) and detail.strip():
        body["detail"] = detail.strip()
    return body


def merge_response_headers(exc_headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    rid = get_request_id()
    out: Dict[str, str] = {}
    if rid:
        out["X-Request-ID"] = rid
    if exc_headers:
        out.update(exc_headers)
    return out


def log_internal_detail(status_code: int, detail: Any, request_id: str) -> None:
    """Log full server-side detail; never send this blob to clients."""
    try:
        if isinstance(detail, (dict, list)):
            serialized = json.dumps(detail, default=str)[:8000]
        else:
            serialized = str(detail)[:8000]
    except Exception:
        serialized = "<unserializable>"
    logger.warning(
        "HTTP %s request_id=%s internal_detail=%s",
        status_code,
        request_id or "-",
        serialized,
    )
