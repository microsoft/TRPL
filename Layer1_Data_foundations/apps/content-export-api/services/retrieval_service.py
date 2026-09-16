# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Approved Records retrieval — approval-status + approval-date-range queries.

The external API exposes approval semantics (approved_from/approved_to params,
approved_at/approved_by/unapproved_at in responses). Internally these are backed
by fields the Archivist
pipeline already populates, so no new fields or backfill are required:

    approved   = archivist_status = 'published',          keyed on published_at
    unapproved = archivist_status IN ('pending','reviewed'), keyed on unpublished_at

Note: records in the transient 'publishing' state (approved, search-indexing not
yet complete) appear once they reach 'published'. 'failed' records are excluded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from fastapi import HTTPException

from core.config import (
    APPROVED_STATUSES,
    UNAPPROVED_STATUSES,
    settings,
)
from models.schemas import (
    ApprovedQueryEcho,
    ApprovedRecord,
    ApprovedRecordsResponse,
    IncludeContent,
    UnapprovedQueryEcho,
    UnapprovedRecord,
    UnapprovedRecordsResponse,
)
from services import cosmos_service, pagination, record_shaper

# Cosmos fields that already carry the approval/unapproval timestamps.
APPROVED_DATE_FIELD = "c.published_at"
UNAPPROVED_DATE_FIELD = "c.unpublished_at"


def _status_in_clause(statuses: Tuple[str, ...]) -> str:
    # Filter on the raw archivist_status (stored lowercase by the pipeline) so the
    # composite indexes on (archivist_status, published_at/unpublished_at) are used.
    # Wrapping the path in LOWER() would force a cross-partition scan + sort.
    quoted = ", ".join(f"'{s}'" for s in statuses)
    return f"c.archivist_status IN ({quoted})"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_range(start: datetime, end: datetime) -> None:
    if start > end:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_DATE_RANGE", "message": "from must be <= to."},
        )
    if (end - start) > timedelta(days=settings.max_date_range_days):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DATE_RANGE_TOO_LARGE",
                "message": f"Date range may not exceed {settings.max_date_range_days} days.",
            },
        )


def _effective_page_size(page_size: int) -> int:
    return max(1, min(page_size, settings.list_max_limit))


def get_approved_records(
    *,
    approved_from: datetime,
    approved_to: datetime,
    include_content: IncludeContent,
    page_size: int,
    continuation_token: str | None,
) -> ApprovedRecordsResponse:
    _validate_range(approved_from, approved_to)
    limit = _effective_page_size(page_size)
    skip = pagination.decode_token(continuation_token)

    where = (
        f"{_status_in_clause(APPROVED_STATUSES)} "
        f"AND IS_DEFINED({APPROVED_DATE_FIELD}) "
        f"AND {APPROVED_DATE_FIELD} >= @from AND {APPROVED_DATE_FIELD} <= @to"
    )
    parameters: List[Dict[str, Any]] = [
        {"name": "@from", "value": _iso(approved_from)},
        {"name": "@to", "value": _iso(approved_to)},
    ]

    docs, total = cosmos_service.query_offset(
        where,
        offset=skip,
        limit=limit,
        order_by=f"{APPROVED_DATE_FIELD} ASC",
        parameters=parameters,
    )

    records: List[ApprovedRecord] = [
        record_shaper.shape_record(doc, include_content) for doc in docs
    ]
    has_more = (skip + len(docs)) < total
    next_token = pagination.encode_token(skip + limit) if has_more else None

    return ApprovedRecordsResponse(
        query=ApprovedQueryEcho(
            approved_from=_iso(approved_from),
            approved_to=_iso(approved_to),
            include_content=include_content,
            page_size=limit,
        ),
        total_count=total,
        records=records,
        continuation_token=next_token,
        has_more=has_more,
    )


def get_unapproved_records(
    *,
    unapproved_from: datetime,
    unapproved_to: datetime,
    page_size: int,
    continuation_token: str | None,
) -> UnapprovedRecordsResponse:
    _validate_range(unapproved_from, unapproved_to)
    limit = _effective_page_size(page_size)
    skip = pagination.decode_token(continuation_token)

    where = (
        f"{_status_in_clause(UNAPPROVED_STATUSES)} "
        f"AND IS_DEFINED({UNAPPROVED_DATE_FIELD}) "
        f"AND {UNAPPROVED_DATE_FIELD} >= @from AND {UNAPPROVED_DATE_FIELD} <= @to"
    )
    parameters: List[Dict[str, Any]] = [
        {"name": "@from", "value": _iso(unapproved_from)},
        {"name": "@to", "value": _iso(unapproved_to)},
    ]

    docs, total = cosmos_service.query_offset(
        where,
        offset=skip,
        limit=limit,
        order_by=f"{UNAPPROVED_DATE_FIELD} ASC",
        parameters=parameters,
        projection="c.id, c.record_id, c.unpublished_at, c.archivist_status",
    )

    records = [record_shaper.shape_unapproved(doc) for doc in docs]
    has_more = (skip + len(docs)) < total
    next_token = pagination.encode_token(skip + limit) if has_more else None

    return UnapprovedRecordsResponse(
        query=UnapprovedQueryEcho(
            unapproved_from=_iso(unapproved_from),
            unapproved_to=_iso(unapproved_to),
            page_size=limit,
        ),
        total_count=total,
        records=records,
        continuation_token=next_token,
        has_more=has_more,
    )


def get_record(record_id: str, *, include_content: IncludeContent) -> ApprovedRecord:
    """Single record by source-system id, regardless of approval status (Section 4.2)."""
    doc = cosmos_service.get_record_by_id(record_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Record '{record_id}' not found."},
        )
    return record_shaper.shape_record(doc, include_content)
