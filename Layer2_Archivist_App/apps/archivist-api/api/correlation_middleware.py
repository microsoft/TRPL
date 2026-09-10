"""Assign X-Request-ID / correlation id per request for logging and support."""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.request_context import set_request_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Outermost: accept or generate X-Request-ID, attach to context and response."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
        raw = (incoming or "").strip()
        if raw and len(raw) <= 128:
            rid = raw
        else:
            rid = str(uuid.uuid4())
        request.state.request_id = rid
        set_request_id(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            set_request_id("")
