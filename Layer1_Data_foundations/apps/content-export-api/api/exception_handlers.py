# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Global exception handlers with minimal JSON error bodies."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.public_errors import (
    log_internal_detail,
    merge_response_headers,
    public_error_payload,
    public_error_text,
)
from core.request_context import get_request_id

logger = logging.getLogger("content-export-api")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        logger.warning("Validation error request_id=%s errors=%s", rid, exc.errors())
        return JSONResponse(
            status_code=422,
            content={"error": public_error_text(422), "code": "VALIDATION_ERROR"},
            headers=merge_response_headers(None),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        # Azure App Service periodically probes /robots933456.txt to verify the app
        # is live. It will always 404 — suppress the warning to reduce App Insights noise.
        is_health_probe = (
            exc.status_code == 404
            and request.url.path in ("/robots933456.txt", "/robots.txt")
        )
        if not is_health_probe:
            log_internal_detail(exc.status_code, exc.detail, rid)
        return JSONResponse(
            status_code=exc.status_code,
            content=public_error_payload(exc.status_code, exc.detail),
            headers=merge_response_headers(dict(exc.headers) if exc.headers else None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        logger.exception("Unhandled exception request_id=%s", rid)
        return JSONResponse(
            status_code=500,
            content={
                "error": public_error_text(500),
                "code": "INTERNAL_ERROR",
                "message": (
                    "Unexpected server error. Share X-Request-ID with TRPL support "
                    "to locate the failure in logs."
                ),
            },
            headers=merge_response_headers(None),
        )
