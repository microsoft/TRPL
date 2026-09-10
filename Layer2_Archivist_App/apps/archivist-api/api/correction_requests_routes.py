"""
Correction requests API.

Document metadata updates (routes.py) do not change correction-request lifecycle;
queue items are closed only via PATCH on this router (explicit dismiss / reviewed).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import AuthenticatedUser, get_current_user_or_mock
from api.intake_auth import IntakeCaller, get_correction_intake_caller
from models.correction_requests import (
    CorrectionRequestCreate,
    CorrectionRequestCreated,
    CorrectionRequestListResponse,
    CorrectionRequestOut,
    CorrectionRequestPatch,
)
from services.correction_intake_audit_log import emit_correction_intake_audit
from services.correction_intake_rate_limit import enforce_correction_intake_rate_limit
from services.correction_request_service import (
    document_to_out,
    get_correction_request_service,
    record_availability_by_ids,
    record_catalog_fields_by_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["correction-requests"])

_ALLOWED_LIST_FILTERS = frozenset(
    {
        "unread",
        "reviewed",
        "dismissed",
        "all",
        # legacy aliases
        "open",
        "addressed",
    }
)


def _actor_from_user(user: AuthenticatedUser) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.user_display,
    }


@router.post(
    "/correction-requests",
    response_model=CorrectionRequestCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_correction_request(
    body: CorrectionRequestCreate,
    caller: IntakeCaller = Depends(get_correction_intake_caller),
):
    rid_str = (body.record_id or "").strip()
    enforce_correction_intake_rate_limit(
        caller.entra_client_app_id,
        record_guid=rid_str or None,
    )
    svc = get_correction_request_service()
    try:
        doc = svc.create(
            record_id=body.record_id,
            note=body.note,
            source=caller.source,
            caller_app_id=caller.entra_client_app_id,
            optional_source_label=body.source,
        )
    except ValueError as exc:
        code = str(exc)
        _cid = caller.entra_client_app_id
        if code == "duplicate_request":
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=409,
            )
            raise HTTPException(
                status_code=409,
                detail={"message": "A correction request for this record and note already exists", "code": code},
            ) from exc
        if code == "record_not_found":
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=404,
            )
            raise HTTPException(
                status_code=404,
                detail={"message": "Record not found in document store", "code": code},
            ) from exc
        if code == "note_too_long":
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=422,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Note exceeds maximum length", "code": code},
            ) from exc
        if code in ("note_required", "note_invalid", "note_empty_after_sanitize"):
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=422,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Invalid correction note", "code": code},
            ) from exc
        if code == "record_id_invalid":
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=422,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Invalid record identifier (UUID v4 required)", "code": code},
            ) from exc
        if code in ("source_invalid", "source_too_long"):
            emit_correction_intake_audit(
                caller_app_id=_cid,
                record_guid=rid_str,
                outcome="rejected",
                reason=code,
                http_status=422,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Invalid source field", "code": code},
            ) from exc
        emit_correction_intake_audit(
            caller_app_id=_cid,
            record_guid=rid_str,
            outcome="rejected",
            reason="validation_error",
            http_status=422,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc)},
        ) from exc

    ts = doc["received_at"]
    emit_correction_intake_audit(
        caller_app_id=caller.entra_client_app_id,
        record_guid=doc.get("record_id"),
        outcome="accepted",
        reason="accepted",
        http_status=201,
    )
    logger.info(
        "Correction intake accepted id=%s record_id=%s source=%s",
        doc["id"],
        doc["record_id"],
        caller.source.source_app_id,
    )
    return CorrectionRequestCreated(
        request_id=doc["id"],
        record_id=doc["record_id"],
        status="unread",
        timestamp=ts,
        received_at=ts,
        source_app_id=doc["source_app_id"],
        source_display_name=doc["source_display_name"],
    )


@router.get("/correction-requests", response_model=CorrectionRequestListResponse)
async def list_correction_requests(
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="unread | reviewed | dismissed | all (default: unread). Legacy: open, addressed.",
    ),
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(10, ge=1, le=100),
    sort: str = Query(
        "desc",
        description="Sort by received_at: asc | desc",
    ),
    user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    _ = user
    effective = status_filter if status_filter else "unread"
    if effective not in _ALLOWED_LIST_FILTERS:
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid status filter", "code": "invalid_status"},
        )
    if limit not in (10, 20, 50, 100):
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid page size", "code": "invalid_limit"},
        )
    sort_norm = (sort or "desc").lower()
    if sort_norm not in ("asc", "desc"):
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid sort", "code": "invalid_sort"},
        )
    svc = get_correction_request_service()
    try:
        page = svc.list_by_status(
            effective,
            offset=offset,
            limit=limit,
            sort_desc=(sort_norm == "desc"),
        )
    except ValueError as exc:
        if str(exc) == "invalid_status_filter":
            raise HTTPException(
                status_code=400,
                detail={"message": "Invalid status filter", "code": "invalid_status"},
            ) from exc
        if str(exc) in ("invalid_offset", "invalid_limit"):
            raise HTTPException(
                status_code=400,
                detail={"message": "Invalid pagination parameters", "code": str(exc)},
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = page.get("rows") or []
    rids = [(doc.get("record_id") or "").strip() for doc in rows]
    avail_by_id = record_availability_by_ids(rids)
    catalog_by_id = record_catalog_fields_by_ids(rids)
    items: list[CorrectionRequestOut] = []
    for doc, rid in zip(rows, rids):
        avail = avail_by_id.get(rid, False) if rid else False
        cat = catalog_by_id.get(rid, {}) if rid else {}
        items.append(
            CorrectionRequestOut(
                **document_to_out(
                    doc,
                    record_available=avail,
                    record_title=cat.get("title"),
                    record_repository=cat.get("repository"),
                    record_collection=cat.get("collection"),
                )
            )
        )
    return CorrectionRequestListResponse(
        items=items,
        next_cursor=None,
        has_more=bool(page.get("has_more")),
        total=int(page.get("total") or 0),
        offset=offset,
        limit=limit,
        sort=sort_norm,
    )


@router.patch("/correction-requests/{request_id}", response_model=CorrectionRequestOut)
async def patch_correction_request(
    request_id: str,
    body: CorrectionRequestPatch,
    user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    svc = get_correction_request_service()
    actor = _actor_from_user(user)
    try:
        doc = svc.patch_status(
            item_id=request_id,
            new_status=body.status,
            actor=actor,
            dismissal_note=body.dismissal_note,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "not_found":
            raise HTTPException(status_code=404, detail="Correction request not found") from exc
        if msg == "conflict":
            raise HTTPException(
                status_code=409,
                detail="Request was modified concurrently. Please refresh and try again.",
            ) from exc
        if msg in ("invalid_transition", "invalid_status"):
            raise HTTPException(
                status_code=400,
                detail={"message": "Invalid status transition", "code": msg},
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rid = (doc.get("record_id") or "").strip()
    avail_map = record_availability_by_ids([rid]) if rid else {}
    avail = avail_map.get(rid, False)
    cat_map = record_catalog_fields_by_ids([rid]) if rid else {}
    cat = cat_map.get(rid, {})
    logger.info(
        "Correction request %s patched to %s by %s",
        request_id,
        body.status,
        user.email,
    )
    return CorrectionRequestOut(
        **document_to_out(
            doc,
            record_available=avail,
            record_title=cat.get("title"),
            record_repository=cat.get("repository"),
            record_collection=cat.get("collection"),
        )
    )
