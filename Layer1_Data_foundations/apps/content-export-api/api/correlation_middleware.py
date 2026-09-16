# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Assign X-Request-ID per request and log request lifecycle."""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.request_context import set_request_id

logger = logging.getLogger("content-export-api")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
        raw = (incoming or "").strip()
        rid = raw if raw and len(raw) <= 128 else str(uuid.uuid4())
        request.state.request_id = rid
        set_request_id(rid)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        except Exception:
            logger.exception(
                "Unhandled exception during request request_id=%s %s %s",
                rid,
                request.method,
                request.url.path,
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            log_msg = (
                "request_id=%s method=%s path=%s status=%s duration_ms=%.1f"
            )
            log_args = (rid, request.method, request.url.path, status_code, duration_ms)
            if status_code >= 500:
                logger.error(log_msg, *log_args)
            elif status_code >= 400:
                logger.warning(log_msg, *log_args)
            else:
                logger.info(log_msg, *log_args)
            set_request_id("")
