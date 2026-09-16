# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Approved Records API endpoints (content source sync API design, Section 4.2)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from api.dependencies import ContentExportCaller, get_content_export_caller
from core.config import settings
from models.schemas import (
    ApprovedRecord,
    ApprovedRecordsResponse,
    IncludeContent,
    UnapprovedRecordsResponse,
)
from services import retrieval_service

router = APIRouter(tags=["Approved Records"])

_page_size = Query(
    default=settings.list_default_limit,
    ge=1,
    le=settings.list_max_limit,
    description="Records per page.",
)
_include_content = Query(
    default="inline",
    description="Content delivery mode: inline, reference, or none.",
)


@router.get("/approved-records", response_model=ApprovedRecordsResponse)
async def list_approved_records(
    approved_from: datetime = Query(..., description="Start of approval date range (inclusive)."),
    approved_to: datetime = Query(..., description="End of approval date range (inclusive)."),
    include_content: IncludeContent = _include_content,
    page_size: int = _page_size,
    continuation_token: Optional[str] = Query(default=None),
    _caller: ContentExportCaller = Depends(get_content_export_caller),
) -> ApprovedRecordsResponse:
    return retrieval_service.get_approved_records(
        approved_from=approved_from,
        approved_to=approved_to,
        include_content=include_content,
        page_size=page_size,
        continuation_token=continuation_token,
    )


@router.get("/unapproved-records", response_model=UnapprovedRecordsResponse)
async def list_unapproved_records(
    unapproved_from: datetime = Query(..., description="Start of unapproval date range (inclusive)."),
    unapproved_to: datetime = Query(..., description="End of unapproval date range (inclusive)."),
    page_size: int = _page_size,
    continuation_token: Optional[str] = Query(default=None),
    _caller: ContentExportCaller = Depends(get_content_export_caller),
) -> UnapprovedRecordsResponse:
    return retrieval_service.get_unapproved_records(
        unapproved_from=unapproved_from,
        unapproved_to=unapproved_to,
        page_size=page_size,
        continuation_token=continuation_token,
    )


@router.get("/records/{record_id}", response_model=ApprovedRecord)
async def get_record(
    record_id: str = Path(..., description="Source system record identifier (GUID)."),
    include_content: IncludeContent = _include_content,
    _caller: ContentExportCaller = Depends(get_content_export_caller),
) -> ApprovedRecord:
    return retrieval_service.get_record(record_id, include_content=include_content)
