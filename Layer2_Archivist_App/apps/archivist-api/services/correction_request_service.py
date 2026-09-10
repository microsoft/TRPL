"""Cosmos persistence for correction requests."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos import exceptions

from core.config import settings
from services.cosmos_service import get_cosmos_client
from services.correction_note_sanitize import (
    sanitize_optional_source_label,
    validate_and_sanitize_note,
)
from services.correction_request_source_registry import SourceRegistration

logger = logging.getLogger(__name__)

# Counter rows for intake rate limiting (same container as correction requests; excluded from list/patch).
DOC_KIND_INTAKE_RATE_LIMIT = "intake_rate_limit"

_SQL_NOT_RATE_LIMIT_DOC = (
    "(NOT IS_DEFINED(c.doc_kind) OR c.doc_kind != @dk)"
)

# Strict UUID v4 string (lowercase/uppercase hex accepted).
_RECORD_ID_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Stored in Cosmos (legacy-compatible)
_ST_OPEN = "open"
_ST_ADDRESSED = "addressed"
_ST_DISMISSED = "dismissed"

# Public API status strings
_UNREAD = "unread"
_REVIEWED = "reviewed"
_DISMISSED = "dismissed"


def _internal_status_from_filter(external: str) -> str:
    m = {
        _UNREAD: _ST_OPEN,
        "open": _ST_OPEN,
        _REVIEWED: _ST_ADDRESSED,
        "addressed": _ST_ADDRESSED,
        _DISMISSED: _ST_DISMISSED,
        "dismissed": _ST_DISMISSED,
        "all": "all",
    }
    return m.get(external, "")


def _normalize_patch_status(status: str) -> str:
    if status == "addressed":
        return _ST_ADDRESSED
    if status == "reviewed":
        return _ST_ADDRESSED
    if status == "dismissed":
        return _ST_DISMISSED
    raise ValueError("invalid_status")


def _external_status(raw: Optional[str]) -> str:
    if raw == _ST_OPEN:
        return _UNREAD
    if raw == _ST_ADDRESSED:
        return _REVIEWED
    if raw == _ST_DISMISSED:
        return _DISMISSED
    return _UNREAD


def _build_correction_request_create_item(
    *,
    doc_id: str,
    record_id: str,
    note_sanitized: str,
    source: SourceRegistration,
    caller_app_id: str,
    received_at: str,
    client_source: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Controlled Cosmos shape for new correction requests only.

    Never merge or spread raw HTTP / Pydantic payloads into this document — map each
    field explicitly from validated inputs and trusted auth (JWT-derived source, caller id).
    """
    doc: Dict[str, Any] = {
        "id": doc_id,
        "record_id": record_id,
        "note_sanitized": note_sanitized,
        "source_app_id": source.source_app_id,
        "source_display_name": source.display_name,
        "status": _ST_OPEN,
        "received_at": received_at,
        "caller_app_id": caller_app_id,
        "is_deleted": False,
    }
    if client_source:
        doc["client_source"] = client_source
    return doc


class CorrectionRequestService:
    """CRUD and queries for the correction-requests container."""

    def __init__(self) -> None:
        self._container_name = settings.cosmos_db_correction_requests_container_name

    def _container(self):
        if not settings.cosmos_db_database_name:
            raise ValueError("COSMOS_DB_DATABASE_NAME is not configured")
        if not self._container_name:
            raise ValueError("COSMOS_DB_CORRECTION_REQUESTS_CONTAINER_NAME is not configured")
        client = get_cosmos_client()
        db = client.get_database_client(settings.cosmos_db_database_name)
        return db.get_container_client(self._container_name)

    def create(
        self,
        record_id: str,
        note: str,
        source: SourceRegistration,
        caller_app_id: str,
        optional_source_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        rid = (record_id or "").strip()
        if not rid:
            raise ValueError("record_id_invalid")
        if not _RECORD_ID_UUID_V4.fullmatch(rid):
            raise ValueError("record_id_invalid")
        if settings.correction_intake_require_record_exists:
            if not record_exists_for_archivist(rid):
                raise ValueError("record_not_found")

        note_sanitized, err = validate_and_sanitize_note(note)
        if err:
            raise ValueError(err)

        client_source = ""
        if optional_source_label:
            src_clean, src_err = sanitize_optional_source_label(optional_source_label)
            if src_err:
                raise ValueError(src_err)
            client_source = src_clean

        doc_id = str(uuid.uuid4())
        now = _utc_now_iso()
        doc = _build_correction_request_create_item(
            doc_id=doc_id,
            record_id=rid,
            note_sanitized=note_sanitized,
            source=source,
            caller_app_id=caller_app_id,
            received_at=now,
            client_source=client_source or None,
        )

        # Prevent duplicate intake. We consider a request duplicate if an existing
        # non-dismissed item exists for the same record_id + sanitized note + source app.
        # (Dismissed items are treated as closed; re-intake is allowed.)
        try:
            query = (
                "SELECT TOP 1 c.id FROM c WHERE c.record_id = @rid "
                "AND c.note_sanitized = @note AND c.source_app_id = @sid "
                "AND c.status != @dismissed AND "
                "(NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false) AND "
                f"{_SQL_NOT_RATE_LIMIT_DOC}"
            )
            params = [
                {"name": "@rid", "value": rid},
                {"name": "@note", "value": note_sanitized},
                {"name": "@sid", "value": source.source_app_id},
                {"name": "@dismissed", "value": _ST_DISMISSED},
                {"name": "@dk", "value": DOC_KIND_INTAKE_RATE_LIMIT},
            ]
            existing = list(
                self._container().query_items(
                    query=query,
                    parameters=params,
                    enable_cross_partition_query=True,
                )
            )
            if existing:
                raise ValueError("duplicate_request")
        except ValueError:
            raise
        except Exception:
            # Duplicate prevention should never break intake; fail open on query errors.
            logger.exception("Duplicate check failed; proceeding with intake")

        self._container().create_item(body=doc)
        logger.info("Created correction request %s for record %s", doc_id, rid)
        return doc

    def get_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._container().read_item(item=item_id, partition_key=item_id)
        except exceptions.CosmosResourceNotFoundError:
            return None

    def list_by_status(
        self,
        status_filter: str,
        *,
        offset: int = 0,
        limit: int = 50,
        sort_desc: bool = True,
    ) -> Dict[str, Any]:
        internal = _internal_status_from_filter(status_filter)
        if not internal:
            raise ValueError("invalid_status_filter")
        if offset < 0:
            raise ValueError("invalid_offset")
        if limit < 1:
            raise ValueError("invalid_limit")
        container = self._container()

        dk_param = {"name": "@dk", "value": DOC_KIND_INTAKE_RATE_LIMIT}
        if internal == "all":
            query = f"SELECT * FROM c WHERE {_SQL_NOT_RATE_LIMIT_DOC}"
            params = [dk_param]
        elif internal == _ST_DISMISSED:
            query = (
                f"SELECT * FROM c WHERE c.status = @st AND {_SQL_NOT_RATE_LIMIT_DOC}"
            )
            params = [{"name": "@st", "value": internal}, dk_param]
        else:
            query = (
                "SELECT * FROM c WHERE c.status = @st AND "
                "(NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false) AND "
                f"{_SQL_NOT_RATE_LIMIT_DOC}"
            )
            params = [{"name": "@st", "value": internal}, dk_param]

        items = container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
        rows = list(items)
        rows.sort(
            key=lambda x: (x.get("received_at") or "", x.get("id") or ""),
            reverse=sort_desc,
        )
        total = len(rows)
        page = rows[offset : offset + limit]
        has_more = offset + len(page) < total
        return {
            "rows": page,
            "total": total,
            "has_more": has_more,
        }

    def patch_status(
        self,
        item_id: str,
        new_status: str,
        actor: Dict[str, Any],
        dismissal_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = _normalize_patch_status(new_status)
        doc = self.get_by_id(item_id)
        if not doc:
            raise ValueError("not_found")
        if doc.get("doc_kind") == DOC_KIND_INTAKE_RATE_LIMIT:
            raise ValueError("not_found")
        etag = doc.get("_etag")
        current = doc.get("status")
        if current != _ST_OPEN:
            raise ValueError("invalid_transition")
        now = _utc_now_iso()
        if target == _ST_DISMISSED:
            doc["status"] = _ST_DISMISSED
            doc["is_deleted"] = True
            doc["dismissed_at"] = now
            doc["dismissed_by"] = actor
            if dismissal_note is not None:
                doc["dismissal_note"] = dismissal_note.strip() or None
        elif target == _ST_ADDRESSED:
            doc["status"] = _ST_ADDRESSED
            doc["is_deleted"] = False
            doc["addressed_at"] = now
            doc["addressed_by"] = actor
        else:
            raise ValueError("invalid_status")
        options = {}
        if etag:
            options["if_match"] = etag
        try:
            self._container().replace_item(item=item_id, body=doc, **options)
        except exceptions.CosmosAccessConditionFailedError:
            raise ValueError("conflict") from None
        logger.info("Correction request %s -> %s", item_id, target)
        return doc


def document_to_out(
    doc: Dict[str, Any],
    record_available: bool,
    *,
    record_title: Optional[str] = None,
    record_repository: Optional[str] = None,
    record_collection: Optional[str] = None,
) -> Dict[str, Any]:
    """Shape for CorrectionRequestOut (public status names)."""
    raw_status = doc.get("status", _ST_OPEN)
    ext = _external_status(raw_status)
    received = doc.get("received_at", "")
    client_src = doc.get("client_source") or ""
    display = doc.get("source_display_name", "")
    source_col = client_src if client_src else display
    return {
        "id": doc["id"],
        "record_id": doc.get("record_id", ""),
        "note": doc.get("note_sanitized", ""),
        "source": source_col,
        "status": ext,
        "timestamp": received,
        "received_at": received,
        "is_deleted": bool(doc.get("is_deleted", False)),
        "source_app_id": doc.get("source_app_id", ""),
        "source_display_name": doc.get("source_display_name", ""),
        "record_available": record_available,
        "caller_app_id": doc.get("caller_app_id"),
        "dismissed_at": doc.get("dismissed_at"),
        "reviewed_at": doc.get("addressed_at"),
        "dismissed_by": doc.get("dismissed_by"),
        "reviewed_by": doc.get("addressed_by"),
        "dismissal_note": doc.get("dismissal_note"),
        "record_title": record_title,
        "record_repository": record_repository,
        "record_collection": record_collection,
    }


def record_exists_for_archivist(record_id: str) -> bool:
    """True if the main OCR document exists (same check as review open)."""
    rid = (record_id or "").strip()
    if not rid:
        return False
    existing = record_availability_by_ids([rid])
    return existing.get(rid, False)


def record_catalog_fields_by_ids(record_ids: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    """
    For each record id (document id or c.record_id), return title / repository / collection
    from the main OCR container in one cross-partition query.
    """
    from services.cosmos_service import get_cosmos_service

    unique_nonempty = list(
        dict.fromkeys((rid or "").strip() for rid in record_ids if (rid or "").strip())
    )
    if not unique_nonempty:
        return {}
    try:
        return get_cosmos_service().get_record_catalog_fields_batch(unique_nonempty)
    except Exception:
        logger.exception("batch record catalog fields failed")
        return {}


def record_availability_by_ids(record_ids: List[str]) -> Dict[str, bool]:
    """
    Map stripped record_id -> exists in main document store.
    One Cosmos query for all unique non-empty ids.
    """
    from services.cosmos_service import get_cosmos_service

    unique_nonempty = list(
        dict.fromkeys((rid or "").strip() for rid in record_ids if (rid or "").strip())
    )
    if not unique_nonempty:
        return {}

    try:
        svc = get_cosmos_service()
        existing = svc.get_existing_document_ids(unique_nonempty)
    except Exception:
        logger.exception("batch record availability check failed")
        return {uid: False for uid in unique_nonempty}

    return {uid: (uid in existing) for uid in unique_nonempty}


_correction_service: Optional[CorrectionRequestService] = None


def get_correction_request_service() -> CorrectionRequestService:
    global _correction_service
    if _correction_service is None:
        _correction_service = CorrectionRequestService()
    return _correction_service
