# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API key authentication for outbound retrieval."""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from core.config import settings

logger = logging.getLogger(__name__)

# scheme_name keeps Swagger to a single "ApiKeyAuth" entry (not "APIKeyHeader").
api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyAuth",
    auto_error=False,
    description="Shared API key from TRPL. Click **Authorize** (top right) and paste the key.",
)


class ContentExportCaller:
    def __init__(self, org_id: str, display_name: str):
        self.org_id = org_id
        self.display_name = display_name


def _validate_api_key(provided: Optional[str]) -> None:
    expected = settings.outbound_api_key
    if not expected:
        if settings.environment == "local":
            logger.warning("OUTBOUND_API_KEY is not set; allowing request in local mode.")
            return
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": "API key authentication is not configured.",
            },
        )

    if not provided or not secrets.compare_digest(provided.strip(), expected):
        logger.warning("API key authentication failed (missing or invalid key)")
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing API key."},
        )


async def get_content_export_caller(
    x_api_key: Optional[str] = Security(api_key_header),
) -> ContentExportCaller:
    _validate_api_key(x_api_key)
    return ContentExportCaller(
        org_id=settings.outbound_caller_org_id,
        display_name=settings.outbound_caller_display_name,
    )
