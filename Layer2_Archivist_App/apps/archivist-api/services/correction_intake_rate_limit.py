# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Hourly intake rate limit using the same Cosmos container as correction requests.

Counter documents use doc_kind=intake_rate_limit, id=rate-limit-{caller}-{YYYYMMDDHH},
partition key /id (same as queue items). List/patch queries exclude these rows.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from azure.cosmos import exceptions
from fastapi import HTTPException

from core.config import settings
from services.correction_intake_audit_log import emit_correction_intake_audit
from services.correction_request_service import (
    DOC_KIND_INTAKE_RATE_LIMIT,
    get_correction_request_service,
)

logger = logging.getLogger(__name__)

_TTL_SECONDS = 7200
_MAX_RETRIES = 12

_RE_ID_UNSAFE = re.compile(r"[/\\?#]")


def _hour_bucket_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


def _seconds_until_next_hour_utc() -> int:
    now = datetime.now(timezone.utc)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(1, int((next_hour - now).total_seconds()))


def _rate_limit_document_id(caller_app_id: str, bucket: str) -> str:
    safe = _RE_ID_UNSAFE.sub("_", (caller_app_id or "").strip())
    return f"rate-limit-{safe}-{bucket}"


def _container():
    return get_correction_request_service()._container()


def enforce_correction_intake_rate_limit(
    caller_app_id: str,
    record_guid: Optional[str] = None,
) -> None:
    """
    Increment per-(caller, hour) counter in correction-requests container.

    Disabled when CORRECTION_INTAKE_RATE_LIMIT_ENABLED is false.
    On Cosmos errors, fail-open (allow) after logging.
    """
    if not settings.correction_intake_rate_limit_enabled:
        return
    key = (caller_app_id or "").strip()
    if not key:
        return

    max_per = settings.correction_intake_rate_limit_max_per_hour
    bucket = _hour_bucket_utc()
    doc_id = _rate_limit_document_id(key, bucket)

    try:
        _increment_or_create(
            _container(),
            item_id=doc_id,
            partition_key=doc_id,
            caller_app_id=key,
            hour_bucket=bucket,
            max_per=max_per,
            record_guid=record_guid,
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Correction intake rate limit failed; allowing request (fail-open)")


def _increment_or_create(
    container,
    *,
    item_id: str,
    partition_key: str,
    caller_app_id: str,
    hour_bucket: str,
    max_per: int,
    record_guid: Optional[str] = None,
) -> None:
    for _ in range(_MAX_RETRIES):
        try:
            doc = container.read_item(item=item_id, partition_key=partition_key)
            count = int(doc.get("count", 0))
            etag: Optional[str] = doc.get("_etag")
            new_count = count + 1
            if new_count > max_per:
                emit_correction_intake_audit(
                    caller_app_id=caller_app_id,
                    record_guid=record_guid,
                    outcome="rejected",
                    reason="rate_limit_exceeded",
                    http_status=429,
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "message": "Too many correction requests for this application in the current hour",
                        "code": "rate_limit_exceeded",
                        "limit": max_per,
                        "window": hour_bucket,
                    },
                    headers={"Retry-After": str(_seconds_until_next_hour_utc())},
                )
            doc["count"] = new_count
            doc["ttl"] = _TTL_SECONDS
            if etag:
                container.replace_item(item=item_id, body=doc, if_match=etag)
            else:
                container.replace_item(item=item_id, body=doc)
            return
        except exceptions.CosmosResourceNotFoundError:
            try:
                new_doc = {
                    "id": item_id,
                    "doc_kind": DOC_KIND_INTAKE_RATE_LIMIT,
                    "caller_app_id": caller_app_id,
                    "hour_bucket": hour_bucket,
                    "count": 1,
                    "ttl": _TTL_SECONDS,
                }
                container.create_item(body=new_doc)
                return
            except exceptions.CosmosResourceExistsError:
                continue
        except exceptions.CosmosAccessConditionFailedError:
            continue

    logger.warning(
        "Rate limit increment failed after %s retries; allowing intake", _MAX_RETRIES
    )
