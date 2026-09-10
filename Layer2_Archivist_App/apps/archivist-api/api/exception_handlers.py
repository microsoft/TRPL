"""Global handlers: minimal JSON errors, full detail in logs with request id."""
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

logger = logging.getLogger("archivist-api")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        logger.warning("Validation error request_id=%s errors=%s", rid, exc.errors())
        return JSONResponse(
            status_code=422,
            content={"error": public_error_text(422)},
            headers=merge_response_headers(None),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        log_internal_detail(exc.status_code, exc.detail, rid)
        content = public_error_payload(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=merge_response_headers(dict(exc.headers) if exc.headers else None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or get_request_id()
        logger.exception("Unhandled exception request_id=%s", rid, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": public_error_text(500)},
            headers=merge_response_headers(None),
        )
