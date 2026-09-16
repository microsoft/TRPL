# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pure helpers for Durable content-source cursor state."""

from __future__ import annotations

import hashlib
import inspect
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from .contracts import (
    ContentSourceError,
    ContentSourceErrorCode,
)

SYNC_QUERY_STATE_VERSION = 1
_CURSOR_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_SYNC_QUERY_STATE_FIELDS = {
    "version",
    "cursor",
    "page_size",
    "collection_ids",
    "date_from",
    "date_to",
    "seen_cursor_fingerprints",
}


def _optional_total(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def cursor_fingerprint(cursor: str) -> str:
    """Return a non-reversible cursor value suitable for persisted cycle state."""

    return hashlib.sha256(cursor.encode("utf-8")).hexdigest()


def _utc_bound(value: object, field_name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError as exc:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            f"{field_name} must be an ISO-8601 datetime",
            details={"field": field_name},
        ) from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            f"{field_name} must include a UTC offset",
            details={"field": field_name},
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_sync_query_state(
    state: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Validate and normalize the JSON state required to replay one sync query."""

    if not isinstance(state, Mapping):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source query state must be an object",
        )
    if require_complete:
        missing = sorted(_SYNC_QUERY_STATE_FIELDS.difference(state))
        if missing:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "The persisted content-source query state is incomplete",
                details={"missing_fields": missing},
            )

    version = state.get("version", SYNC_QUERY_STATE_VERSION)
    if version != SYNC_QUERY_STATE_VERSION:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source query state version is unsupported",
            details={"version": version},
        )

    cursor = state.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source cursor must be a non-empty string",
        )

    page_size = state.get("page_size")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 500
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source page size must be between 1 and 500",
            details={"page_size": page_size},
        )

    collection_ids = state.get("collection_ids")
    if not isinstance(collection_ids, (list, tuple)) or any(
        not isinstance(value, str) or not value.strip() for value in collection_ids
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source collection filter must be a string array",
        )
    normalized_collections = list(dict.fromkeys(collection_ids))

    date_from = _utc_bound(state.get("date_from"), "date_from")
    date_to = _utc_bound(state.get("date_to"), "date_to")
    if (date_from is None) != (date_to is None):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source query must retain both date bounds or neither",
        )
    if (
        date_from is not None
        and datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        > datetime.fromisoformat(date_to.replace("Z", "+00:00"))
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted content-source date range is invalid",
        )

    fingerprints = state.get("seen_cursor_fingerprints")
    if not isinstance(fingerprints, (list, tuple)) or any(
        not isinstance(value, str) or not _CURSOR_FINGERPRINT.fullmatch(value)
        for value in fingerprints
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The persisted cursor-chain context is invalid",
        )
    normalized_fingerprints = set(fingerprints)
    if cursor is not None:
        normalized_fingerprints.add(cursor_fingerprint(cursor))

    return {
        "version": SYNC_QUERY_STATE_VERSION,
        "cursor": cursor,
        "page_size": page_size,
        "collection_ids": normalized_collections,
        "date_from": date_from,
        "date_to": date_to,
        "seen_cursor_fingerprints": sorted(normalized_fingerprints),
    }


def sync_query_state_from_failure(failure: Mapping[str, Any]) -> dict[str, Any]:
    """Load only complete failure state so a retry can never widen its filters."""

    query_state = failure.get("query_state") if isinstance(failure, Mapping) else None
    if not isinstance(query_state, Mapping):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "The sync failure has no complete persisted query state",
            details={"missing_query_state": True},
        )
    return normalize_sync_query_state(query_state, require_complete=True)


def sync_query_activity_params(
    query_state: Mapping[str, Any],
    *,
    instance_id: Optional[str] = None,
) -> dict[str, Any]:
    """Convert persisted query state into one activity invocation without filter loss."""

    normalized = normalize_sync_query_state(query_state, require_complete=True)
    params = {
        "collection_ids": normalized["collection_ids"],
        "cursor": normalized["cursor"],
        "limit": normalized["page_size"],
        "date_from": normalized["date_from"],
        "date_to": normalized["date_to"],
        "seen_cursor_fingerprints": normalized["seen_cursor_fingerprints"],
    }
    if instance_id is not None:
        params["instance_id"] = instance_id
    return params


def advance_opaque_cursor(
    current_cursor: Optional[str],
    next_cursor: object,
    seen_cursors: Sequence[str] = (),
) -> tuple[Optional[str], list[str]]:
    """Validate one adapter-owned cursor transition and retain cycle-detection state."""

    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not next_cursor.strip()
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            "The content-source adapter returned an invalid continuation cursor",
        )

    seen = set(seen_cursors)
    if current_cursor is not None:
        seen.add(current_cursor)
    if next_cursor is not None and next_cursor in seen:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            "The content-source adapter repeated a continuation cursor",
            details={"cursor_repeated": True},
        )
    if next_cursor is not None:
        seen.add(next_cursor)
    return next_cursor, sorted(seen)


def advance_persisted_cursor(
    current_cursor: Optional[str],
    next_cursor: object,
    seen_cursor_fingerprints: Sequence[str] = (),
) -> tuple[Optional[str], list[str]]:
    """Advance a cursor using only non-reversible persisted chain context."""

    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not next_cursor.strip()
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            "The content-source adapter returned an invalid continuation cursor",
        )
    seen = set(seen_cursor_fingerprints)
    if current_cursor is not None:
        seen.add(cursor_fingerprint(current_cursor))
    if next_cursor is not None:
        fingerprint = cursor_fingerprint(next_cursor)
        if fingerprint in seen:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                "The content-source adapter repeated a continuation cursor",
                details={"cursor_repeated": True},
            )
        seen.add(fingerprint)
    return next_cursor, sorted(seen)


def advance_sync_query_state(
    query_state: Mapping[str, Any],
    page_result: object,
) -> dict[str, Any]:
    """Advance one retry page while retaining the exact original query filters."""

    normalized = normalize_sync_query_state(query_state, require_complete=True)
    if not isinstance(page_result, Mapping) or not page_result.get("success"):
        error = page_result.get("error") if isinstance(page_result, Mapping) else None
        if not isinstance(error, Mapping):
            error = {
                "code": ContentSourceErrorCode.SOURCE_FAILURE.value,
                "message": str(error or "The content-source page activity failed"),
                "retryable": True,
                "details": {},
            }
        return {"status": "error", "error": dict(error), "query_state": normalized}

    failed = int(page_result.get("failed", 0) or 0)
    if failed:
        error = page_result.get("error")
        if not isinstance(error, Mapping):
            error = {
                "code": "storage_failure",
                "message": "The retried content-source page was not stored completely",
                "retryable": True,
                "details": {"failed_records": failed},
            }
        return {"status": "error", "error": dict(error), "query_state": normalized}

    try:
        next_cursor, fingerprints = advance_persisted_cursor(
            normalized["cursor"],
            page_result.get("next_cursor"),
            normalized["seen_cursor_fingerprints"],
        )
    except ContentSourceError as exc:
        return {
            "status": "error",
            "error": exc.as_dict(),
            "query_state": normalized,
        }

    next_state = dict(normalized)
    next_state["cursor"] = next_cursor
    next_state["seen_cursor_fingerprints"] = fingerprints
    return {
        "status": "completed" if next_cursor is None else "continue",
        "query_state": next_state,
    }


async def _invoke(callback: Callable[..., Any], *args: object) -> Any:
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


async def retry_sync_failure_chain(
    failure: Mapping[str, Any],
    fetch_page: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
    checkpoint: Callable[[Mapping[str, Any], Optional[Mapping[str, Any]]], Any],
    complete: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Drain a failed cursor chain and complete it only after its final page succeeds."""

    try:
        query_state = sync_query_state_from_failure(failure)
    except ContentSourceError as exc:
        return {
            "success": False,
            "status": "error",
            "error": exc.as_dict(),
            "pages_processed": 0,
            "successful": 0,
            "failed": 0,
        }

    pages_processed = 0
    successful = 0
    failed = 0
    while True:
        try:
            page_result = await _invoke(fetch_page, query_state)
        except ContentSourceError as exc:
            page_result = {"success": False, "error": exc.as_dict()}
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            page_result = {
                "success": False,
                "error": {
                    "code": ContentSourceErrorCode.SOURCE_FAILURE.value,
                    "message": "The content-source retry activity failed unexpectedly",
                    "retryable": True,
                    "details": {"exception_type": type(exc).__name__},
                },
            }

        if isinstance(page_result, Mapping):
            successful += int(page_result.get("successful", 0) or 0)
            failed += int(page_result.get("failed", 0) or 0)
        transition = advance_sync_query_state(query_state, page_result)
        if transition["status"] == "error":
            error = transition["error"]
            try:
                persisted = await _invoke(checkpoint, query_state, error)
                if persisted is False:
                    raise RuntimeError("checkpoint returned false")
            except Exception as exc:  # pragma: no cover - defensive storage boundary
                error = {
                    "code": "storage_failure",
                    "message": "The content-source retry state could not be persisted",
                    "retryable": True,
                    "details": {
                        "exception_type": type(exc).__name__,
                        "retry_error": error,
                    },
                }
            return {
                "success": False,
                "status": "error",
                "error": error,
                "query_state": query_state,
                "pages_processed": pages_processed,
                "successful": successful,
                "failed": failed,
            }

        pages_processed += 1
        next_state = transition["query_state"]
        if transition["status"] == "completed":
            try:
                completed = await _invoke(complete, next_state)
                if completed is False:
                    raise RuntimeError("completion returned false")
            except Exception as exc:
                return {
                    "success": False,
                    "status": "error",
                    "error": {
                        "code": "storage_failure",
                        "message": "The completed content-source retry could not be recorded",
                        "retryable": True,
                        "details": {"exception_type": type(exc).__name__},
                    },
                    "query_state": query_state,
                    "pages_processed": pages_processed,
                    "successful": successful,
                    "failed": failed,
                }
            return {
                "success": True,
                "status": "completed",
                "query_state": next_state,
                "pages_processed": pages_processed,
                "successful": successful,
                "failed": failed,
            }

        try:
            persisted = await _invoke(checkpoint, next_state, None)
            if persisted is False:
                raise RuntimeError("checkpoint returned false")
        except Exception as exc:
            return {
                "success": False,
                "status": "error",
                "error": {
                    "code": "storage_failure",
                    "message": "The content-source retry checkpoint could not be persisted",
                    "retryable": True,
                    "details": {"exception_type": type(exc).__name__},
                },
                "query_state": query_state,
                "pages_processed": pages_processed,
                "successful": successful,
                "failed": failed,
            }
        query_state = next_state


def advance_sync_orchestration(
    state: Mapping[str, Any],
    page_result: object,
) -> dict[str, Any]:
    """Apply one page result to JSON-compatible Durable orchestration state."""

    processed_so_far = int(state.get("total_processed_so_far", 0) or 0)
    failed_so_far = int(state.get("total_failed_so_far", 0) or 0)
    pages_so_far = int(state.get("pages_processed_so_far", 0) or 0)

    if not isinstance(page_result, Mapping) or not page_result.get("success"):
        error = (
            page_result.get("error")
            if isinstance(page_result, Mapping)
            else None
        )
        if not isinstance(error, Mapping):
            error = {
                "code": ContentSourceErrorCode.SOURCE_FAILURE.value,
                "message": str(error or "The content-source page activity failed"),
                "retryable": False,
                "details": {},
            }
        return {
            "status": "error",
            "error": dict(error),
            "total_processed_so_far": processed_so_far,
            "total_failed_so_far": failed_so_far,
            "pages_processed_so_far": pages_so_far,
            "advisory_total": _optional_total(state.get("advisory_total")),
        }

    processed = processed_so_far + int(page_result.get("successful", 0) or 0)
    failed = failed_so_far + int(page_result.get("failed", 0) or 0)
    pages = pages_so_far + 1
    advisory_total = _optional_total(page_result.get("reported_total"))
    if advisory_total is None:
        advisory_total = _optional_total(state.get("advisory_total"))

    legacy_fingerprints = list(state.get("seen_cursor_fingerprints", ()))
    legacy_fingerprints.extend(
        cursor_fingerprint(value)
        for value in state.get("seen_cursors", ())
        if isinstance(value, str)
    )
    try:
        next_cursor, seen_cursor_fingerprints = advance_persisted_cursor(
            state.get("cursor"),
            page_result.get("next_cursor"),
            legacy_fingerprints,
        )
    except ContentSourceError as exc:
        return {
            "status": "error",
            "error": exc.as_dict(),
            "total_processed_so_far": processed,
            "total_failed_so_far": failed,
            "pages_processed_so_far": pages,
            "advisory_total": advisory_total,
        }

    summary = {
        "total_processed_so_far": processed,
        "total_failed_so_far": failed,
        "pages_processed_so_far": pages,
        "advisory_total": advisory_total,
    }
    if next_cursor is None:
        return {
            "status": "completed_with_failures" if failed else "completed",
            **summary,
        }

    next_state = dict(state)
    next_state.pop("seen_cursors", None)
    next_state.update(
        {
            "cursor": next_cursor,
            "seen_cursor_fingerprints": seen_cursor_fingerprints,
            **summary,
        }
    )
    return {"status": "continue", "next_state": next_state, **summary}
