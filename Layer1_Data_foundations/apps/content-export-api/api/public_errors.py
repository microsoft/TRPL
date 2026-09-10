"""Minimal public error bodies for API responses."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.request_context import get_request_id

logger = logging.getLogger("content-export-api")

PUBLIC_ERROR_BY_STATUS: Dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    422: "Invalid request",
    429: "Too many requests",
    500: "Internal server error",
    503: "Service unavailable",
}


def public_error_text(status_code: int) -> str:
    return PUBLIC_ERROR_BY_STATUS.get(status_code, "Request failed")


def public_error_payload(status_code: int, detail: Any = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": public_error_text(status_code)}
    if isinstance(detail, dict) and "code" in detail:
        body["code"] = detail["code"]
        if "message" in detail:
            body["message"] = detail["message"]
    return body


def log_internal_detail(status_code: int, detail: Any, request_id: Optional[str]) -> None:
    logger.warning(
        "HTTP %s request_id=%s detail=%s",
        status_code,
        request_id or get_request_id(),
        detail,
    )


def merge_response_headers(extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    headers = {"X-Request-ID": get_request_id() or ""}
    if extra:
        headers.update({k: v for k, v in extra.items() if k.lower() != "content-length"})
    return {k: v for k, v in headers.items() if v}
