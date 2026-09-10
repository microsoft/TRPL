"""Unauthenticated health endpoints for AGW probes."""
import logging

from fastapi import APIRouter, HTTPException

from core.config import settings
from models.schemas import HealthResponse
from services import cosmos_service

router = APIRouter(tags=["Health"])
logger = logging.getLogger("content-export-api")


async def run_health_check() -> HealthResponse:
    checks: dict[str, str] = {}

    if settings.environment != "local":
        cosmos_ok, cosmos_error = cosmos_service.check_cosmos_readiness()
        checks["cosmos"] = "ok" if cosmos_ok else f"unavailable: {cosmos_error}"
        if not cosmos_ok:
            logger.error("Health check failed: cosmos unavailable (%s)", cosmos_error)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "Cosmos DB is not reachable.",
                },
            )
    else:
        checks["cosmos"] = "skipped (local)"

    return HealthResponse(checks=checks)


@router.get("/health", response_model=HealthResponse)
async def health_v1() -> HealthResponse:
    return await run_health_check()
