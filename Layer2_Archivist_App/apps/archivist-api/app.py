"""Main FastAPI application entry point for the Archivist API."""
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from api.correlation_middleware import CorrelationIdMiddleware
from api.correction_intake_dependencies import assert_correction_intake_http
from api.correction_requests_routes import router as correction_requests_router
try:
    from api.digital_items_routes import router as digital_items_router
except Exception:
    # Keep core API available even when optional digital-items dependencies are missing
    # or DataFoundations path resolution fails at import time in packaged deployments.
    digital_items_router = None
from api.exception_handlers import register_exception_handlers
from api.public_errors import log_internal_detail, merge_response_headers, public_error_text
from api.routes import router
from core.logging_config import configure_logging, configure_azure_monitor, get_logger
from core.request_context import get_request_id
from services.correction_intake_audit_log import emit_correction_intake_audit

# Configure logging first (before any other operations)
configure_logging("archivist-api")
logger = get_logger("archivist-api")

app = FastAPI(
    title="Archivist API",
    description="API for managing and retrieving document metadata from CosmosDB",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

register_exception_handlers(app)

# Configure Azure Application Insights (if connection string is set)
configure_azure_monitor(app)
logger.info("Archivist API starting up")


class CorrectionIntakeMiddleware(BaseHTTPMiddleware):
    """Enforce JSON Content-Type and body size for POST /api/v1/correction-requests before body read."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            path = request.url.path.rstrip("/")
            if path.endswith("/correction-requests"):
                try:
                    assert_correction_intake_http(request)
                except HTTPException as exc:
                    detail = exc.detail
                    reason = (
                        detail.get("code")
                        if isinstance(detail, dict)
                        else "correction_intake_http_error"
                    )
                    emit_correction_intake_audit(
                        caller_app_id=None,
                        record_guid=None,
                        outcome="rejected",
                        reason=str(reason),
                        http_status=exc.status_code,
                    )
                    rid = get_request_id()
                    log_internal_detail(exc.status_code, exc.detail, rid)
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"error": public_error_text(exc.status_code)},
                        headers=merge_response_headers(
                            dict(exc.headers) if exc.headers else None
                        ),
                    )
        return await call_next(request)


# Configure CORS based on environment
# In local development, allow localhost. In other environments, use specific frontend URL
environment = os.getenv("ENVIRONMENT", "production").lower()
frontend_url = os.getenv("FRONTEND_URL", "")


def parse_origins(origins_value: str) -> list[str]:
    return [origin.strip().rstrip("/") for origin in origins_value.split(",") if origin.strip()]


configured_frontend_origins = parse_origins(frontend_url)

if environment == "local":
    # Local development: Allow local development servers
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    # Add frontend URL if specified
    if configured_frontend_origins:
        allowed_origins.extend(configured_frontend_origins)
else:
    # All other environments (development, staging, production): Only allow specific frontend URL
    if not configured_frontend_origins:
        raise ValueError(
            "FRONTEND_URL environment variable must be set when ENVIRONMENT is not 'local'"
        )
    allowed_origins = configured_frontend_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "If-Match",
        "X-MS-TOKEN-AAD-ACCESS-TOKEN",
        "X-MS-TOKEN-AAD-ID-TOKEN",
        "X-ID-Token",
        "X-Request-ID",
        "X-Correlation-ID",
    ],
    expose_headers=["ETag", "Retry-After", "X-Request-ID"],
)
app.add_middleware(CorrectionIntakeMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(router, prefix="/api/v1")
app.include_router(correction_requests_router, prefix="/api/v1")
if digital_items_router is not None:
    app.include_router(digital_items_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint providing API information."""
    logger.debug("Root endpoint accessed")
    return {
        "message": "Archivist API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    logger.debug("Health check endpoint accessed")
    return {
        "status": "healthy",
        "service": "archivist-api",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Archivist API in development mode")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
