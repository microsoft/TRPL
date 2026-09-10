"""Content export API — read-only FastAPI service."""
from fastapi import FastAPI

from api.correlation_middleware import CorrelationIdMiddleware
from api.exception_handlers import register_exception_handlers
from api.routes import health, records
from core.logging_config import configure_azure_monitor, configure_logging, get_logger
from models.schemas import HealthResponse
logger = get_logger("content-export-api")

app = FastAPI(
    title="Data Foundations — Approved Records API",
    description=(
        "Pull-based, read-only export of archivist-approved records "
        "(metadata + OCR / visual description) within an approval date range.\n\n"
        "**Swagger:** click **Authorize** (top right), enter your `X-API-Key` value, "
        "then **Authorize** again. Record endpoints require the key; `/health` does not."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={"persistAuthorization": True},
)

register_exception_handlers(app)
configure_azure_monitor(app)
logger.info("Content export API starting")

app.add_middleware(CorrelationIdMiddleware)

_V1 = "/api/v1"
app.include_router(health.router, prefix=_V1)
app.include_router(records.router, prefix=_V1)


@app.get("/health", include_in_schema=False, response_model=HealthResponse)
async def health_root() -> HealthResponse:
    """Bare /health for App Service probes (also at /api/v1/health)."""
    return await health.run_health_check()


@app.get("/")
async def root():
    return {
        "message": "Content Export API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": f"{_V1}/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=True)
