"""API routes for document management and retrieval."""

# pylint:disable = too-many-locals

import json
import asyncio
import copy
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal, NoReturn
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse, quote

from azure.core.exceptions import AzureError
from azure.servicebus.exceptions import ServiceBusError
from fastapi import APIRouter, HTTPException, Query, Body, Header, Depends, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.dependencies import AuthenticatedUser, get_current_user_or_mock
from services.audit_service import (
    AuditService,
    create_field_changes,
)
from services.blob_service import (
    BlobAuthenticationError,
    BlobDownloadError,
    BlobNotFoundError,
    add_sas_token_to_url,
    download_blob_bytes,
    download_blob_text,
    get_container_name,
    strip_sas_token_from_url,
    upload_text_to_blob_url,
    upload_binary_to_blob,
    _parse_blob_storage_url,
    _parse_local_blob_storage_url,
)
from services.cosmos_service import (
    DocumentVersionConflictError,
    _find_asset_index,
    get_cosmos_service,
)
from services.status_helpers import normalize_archivist_status
from services.statistics_service import get_statistics_service
from services.service_bus import send_to_queue
from services.pipeline_service import get_pipeline_service
from services.publish_batch_service import get_publish_batch_service
from services.periodic_run_history_service import (
    record_periodic_sync_started,
    record_periodic_sync_finished,
    list_periodic_sync_runs,
    get_periodic_sync_run,
)
from services.periodic_schedule_service import (
    upsert_schedule,
    list_schedules,
    get_schedule,
    delete_schedule,
    list_due_schedules,
    mark_schedule_after_trigger,
    resolve_schedule_ingestion_calendar_window,
    _parse_iso_dt as _parse_schedule_iso_dt,
)
from services.field_mapping_service import get_field_mapping_service
from services.epub_document_service import get_epub_document_service
from core.config import settings

from services.blob_service import download_json_from_blob, upload_json_to_blob
from models.schemas import (
    AssetDetail,
    DocumentMetadata,
    DocumentList,
    CollectionSummariesResponse,
    RepositoryStatisticsResponse,
    RepositoryStatisticsSummary,
    RepositoryCollectionStatisticsSummary,
    CollectionDetailsResponse,
    RecordIdsResponse,
    DashboardStatistics,
    EpubDocument,
    EpubDocumentList,
    EpubUploadResponse,
    EpubSectionSelection,
    EpubFilterConfig,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _raise_document_save_value_error(e: ValueError) -> NoReturn:
    """Map etag/version conflicts to 409; other validation errors stay 400."""
    if isinstance(e, DocumentVersionConflictError):
        raise HTTPException(status_code=409, detail=str(e)) from e
    msg = str(e)
    if "modified by another user" in msg.lower():
        raise HTTPException(status_code=409, detail=msg) from e
    raise HTTPException(status_code=400, detail=msg) from e


# Request models
class DocumentUpdateRequest(BaseModel):
    """Request model for updating document metadata."""

    metadata: Optional[Dict[str, Any]] = None
    archivist_notes: Optional[str] = None
    archivist_status: Optional[str] = None
    archivist_approval_method: Optional[str] = None
    visual_detailed_description_flexible: Optional[str] = None
    correlation_id: Optional[str] = None
    # user_id and user_display removed - now come from authentication dependency

    @field_validator("archivist_status", mode="before")
    @classmethod
    def _normalize_archivist_status_request(cls, v):
        if v is None:
            return None
        return normalize_archivist_status(v)


class OcrUpdateRequest(BaseModel):
    """Request model for updating OCR text."""

    ocr_text: str
    correlation_id: Optional[str] = None
    # user_id and user_display removed - now come from authentication dependency


class ReconcileStuckPublishingRequest(BaseModel):
    """Admin: move documents stuck in publishing past threshold (default: back to reviewed)."""

    threshold_minutes: int = Field(20, ge=1, le=1440)
    action: Literal["failed", "reviewed"] = "reviewed"


class DocumentIdsRequest(BaseModel):
    """Request model for batch document processing."""

    document_ids: List[str]
    correlation_id: Optional[str] = None


class AuditHistoryResponse(BaseModel):
    """Response model for audit history."""

    entity_id: str
    entries: List[Dict[str, Any]]
    count: int


class BlobContentRequest(BaseModel):
    """Request body for private blob proxy."""

    blob_url: str


_ASSET_BLOB_URL_FIELDS = (
    "blob_url",
    "blob_thumbnail_url",
    "thumbnail_url",
    "file_url",
)


def _sign_asset_blob_urls(document_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Append fresh SAS tokens to asset blob URLs before returning to clients."""
    asset_details = document_dict.get("asset_details", [])
    if not asset_details or not isinstance(asset_details, list):
        return document_dict

    for asset in asset_details:
        if not isinstance(asset, dict):
            continue
        for field in _ASSET_BLOB_URL_FIELDS:
            value = asset.get(field)
            if value:
                asset[field] = add_sas_token_to_url(value)
    return document_dict


async def _async_identity(value: Any) -> Any:
    return value


async def _build_document_response(document_dict: Dict[str, Any]) -> DocumentMetadata:
    """Sign asset URLs and hydrate blob-backed text fields."""
    document_dict = _sign_asset_blob_urls(document_dict)

    asset_details = document_dict.get("asset_details")
    if isinstance(asset_details, list) and asset_details:
        document_dict["asset_details"] = await asyncio.gather(
            *[
                hydrate_asset_ocr_text(asset) if isinstance(asset, dict) else _async_identity(asset)
                for asset in asset_details
            ]
        )

    if document_dict.get("visual_description_possible") == "Y":
        document_dict = await hydrate_visual_description(document_dict)
    return DocumentMetadata(**document_dict)


async def _stream_blob_content_response(blob_url: str) -> Response:
    clean_url = strip_sas_token_from_url(blob_url.strip())
    is_cloud_blob = _parse_blob_storage_url(clean_url) is not None
    is_local_blob = _parse_local_blob_storage_url(clean_url) is not None
    if not is_cloud_blob and not is_local_blob:
        raise HTTPException(
            status_code=400,
            detail="blob_url must target Azure Blob Storage or the configured local emulator",
        )

    try:
        data, content_type = await download_blob_bytes(clean_url)
    except BlobNotFoundError as exc:
        logger.warning("blob-content not found: %s", clean_url[:160])
        raise HTTPException(status_code=404, detail="Blob not found") from exc
    except BlobAuthenticationError as exc:
        logger.error(
            "blob-content access denied for %s — verify Archivist API MI has "
            "Storage Blob Data Reader + Storage Blob Delegator on the DF storage account "
            "(ARCHIVIST_API_PRINCIPAL_ID + datafoundations provision)",
            clean_url[:160],
        )
        raise HTTPException(status_code=403, detail="Blob access denied") from exc
    except (BlobDownloadError, ValueError) as exc:
        logger.exception("Failed to stream blob content: %s", clean_url[:120])
        raise HTTPException(status_code=502, detail="Failed to read blob content") from exc

    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/blob-content")
async def post_blob_content(
    body: BlobContentRequest,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Stream blob bytes to authenticated clients (preferred).
    """
    del current_user
    return await _stream_blob_content_response(body.blob_url.strip())


@router.get("/blob-content")
async def get_blob_content(
    blob_url: str = Query(..., min_length=1, description="Azure Blob Storage HTTPS URL"),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Stream blob bytes to authenticated clients (legacy GET).

    Prefer POST /blob-content with a JSON body; some WAF policies strip the `url` query param.
    """
    del current_user
    return await _stream_blob_content_response(blob_url)


async def hydrate_asset_ocr_text(asset: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hydrates OCR text for a single asset by downloading the OCR blob.

    Downloads flexible (working) and original OCR blobs when URLs are present.
    If the flexible blob is missing or empty, falls back to original text so the
    UI does not show stale inline content while the blob URL points at an empty file.
    """
    if not isinstance(asset, dict):
        return asset

    ocr_result = asset.get("ocr_result", {})
    if not isinstance(ocr_result, dict):
        return asset

    ocr_res = asset.setdefault("ocr_result", {})
    ocr_url = ocr_result.get("ocr_text_flexible_blob_url")
    ocr_original_blob_url = ocr_result.get("ocr_text_original_blob_url")

    original_content = ""
    if ocr_original_blob_url:
        try:
            original_content = await download_blob_text(ocr_original_blob_url) or ""
        except (OSError, asyncio.TimeoutError, BlobDownloadError, BlobNotFoundError) as e:
            logger.warning(
                "Failed to download original OCR blob for asset %s: %s",
                asset.get("asset_id") or asset.get("id"),
                str(e),
            )
    ocr_res["ocr_text_original"] = original_content

    flexible_content = ""
    if ocr_url:
        try:
            flexible_content = await download_blob_text(ocr_url) or ""
        except (OSError, asyncio.TimeoutError, BlobDownloadError, BlobNotFoundError) as e:
            logger.warning(
                "Failed to download flexible OCR blob for asset %s: %s",
                asset.get("asset_id") or asset.get("id"),
                str(e),
            )

    if not flexible_content.strip() and original_content.strip():
        flexible_content = original_content

    ocr_res["ocr_text"] = flexible_content

    if ocr_url or ocr_original_blob_url:
        logger.debug(
            "Hydrated OCR text for asset %s", asset.get("asset_id") or asset.get("id")
        )
    return asset


async def _hydrate_audit_blob_reference(value: str) -> str:
    """Resolve legacy audit values that stored blob URLs instead of inline text."""
    if not isinstance(value, str):
        return value if value is not None else ""
    clean = value.strip()
    if not clean.startswith("https://") or ".blob.core.windows.net" not in clean:
        return value
    try:
        text = await download_blob_text(strip_sas_token_from_url(clean))
        return text if text else "(empty)"
    except BlobNotFoundError:
        return "(content not found)"
    except (BlobDownloadError, BlobAuthenticationError, OSError, asyncio.TimeoutError) as exc:
        logger.warning("Could not hydrate audit blob reference: %s", exc)
        return "(content unavailable)"


async def _hydrate_audit_field(field: Dict[str, Any], key: str) -> None:
    value = field.get(key, "")
    if (
        isinstance(value, str)
        and value.startswith("https://")
        and ".blob.core.windows.net" in value
    ):
        field[key] = await _hydrate_audit_blob_reference(value)


async def hydrate_visual_description(document: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hydrates visual description text for a document by downloading from blob URLs.

    Downloads the blob content referenced by:
    - 'visual_detailed_description_original_blob_url' -> 'visual_detailed_description_original'
    - 'visual_detailed_description_flexible_blob_url' -> 'visual_detailed_description_flexible'

    Args:
        document (Dict[str, Any]): Document dictionary to hydrate.

    Returns:
        Dict[str, Any]: The same document dict with visual description text fields populated.
    """
    if not isinstance(document, dict):
        return document

    # Download original visual description
    original_blob_url = document.get("visual_detailed_description_original_blob_url")
    if original_blob_url:
        try:
            original_content = await download_blob_text(original_blob_url)
            document["visual_detailed_description_original"] = original_content or ""
            logger.debug("Hydrated original visual description for document %s", document.get("id"))
        except (BlobNotFoundError, BlobDownloadError, BlobAuthenticationError, OSError, asyncio.TimeoutError, UnicodeDecodeError) as e:
            logger.warning(
                "Failed to download original visual description for document %s: %s",
                document.get("id"),
                e,
            )
            document["visual_detailed_description_original"] = ""
    else:
        document["visual_detailed_description_original"] = ""

    # Download flexible visual description
    flexible_blob_url = document.get("visual_detailed_description_flexible_blob_url")
    if flexible_blob_url:
        try:
            flexible_content = await download_blob_text(flexible_blob_url)
            document["visual_detailed_description_flexible"] = flexible_content or ""
            logger.debug("Hydrated flexible visual description for document %s", document.get("id"))
        except (BlobNotFoundError, BlobDownloadError, BlobAuthenticationError, OSError, asyncio.TimeoutError, UnicodeDecodeError) as e:
            logger.warning(
                "Failed to download flexible visual description for document %s: %s",
                document.get("id"),
                e,
            )
            document["visual_detailed_description_flexible"] = ""
    else:
        document["visual_detailed_description_flexible"] = ""

    return document


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/auth/me")
async def get_current_user_info(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get current authenticated user information (BFF endpoint).

    This endpoint extracts the user information from the Azure AD token
    and returns it to the frontend. The token is validated by Azure App Service
    Easy Auth before reaching this endpoint.

    This follows the Backend-for-Frontend (BFF) pattern where the backend
    has access to the x-ms-token-* headers that are not available to the
    client-side JavaScript.

    Returns:
        User information including user_id, email, name, and display_name
    """
    return {"authenticated": True, "user": current_user.to_dict()}


@router.get("/items/{item_id}")
async def read_item(item_id: int):
    """Legacy endpoint for testing."""
    return {"item_id": item_id, "name": f"Item {item_id}"}


@router.get("/documents", response_model=DocumentList, response_model_exclude_none=True)
async def get_all_documents(
    order_by: str = Query(default="created_at"),
    descending: bool = Query(default=True),
    page_number: Optional[int] = Query(default=1),
    page_size: Optional[int] = Query(default=20),
    status: Optional[str] = None,
    repository: Optional[str] = None,
    collection: Optional[str] = None,
    resource_type: Optional[str] = None,
    source: Optional[str] = None,
    creator: Optional[str] = None,
    recipient: Optional[str] = None,
    min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    max_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    title_contains: Optional[str] = None,
    exclude_errors: Optional[bool] = Query(default=None),
    exclude_zero_assets: Optional[bool] = Query(default=None),
):
    """
    Get documents from the CosmosDB container with pagination and filtering support.
    Args:
        order_by: Field to order by (default: created_at)
        descending: Sort in descending order (default: True)
        page_number: Page number for pagination (default: 1)
        page_size: Number of items per page (default: 20)
        status: Filter by status (pending, processing, completed, failed)
        repository: Filter by repository name
        collection: Filter by collection name
        resource_type: Filter by resource type name
        source: Filter by Digital Item Publisher (source)
        creator: Filter by Creator name
        recipient: Filter by Recipient name
        min_confidence: Filter by minimum OCR confidence score (0.0 to 1.0)
        max_confidence: Filter by maximum OCR confidence score (0.0 to 1.0)
        title_contains: Filter documents where title contains this text (case-insensitive)
        exclude_errors: If True, exclude documents where any pipeline stage has error/failed status
        exclude_zero_assets: If True, exclude documents with no assets (asset_details array is empty or missing)
    Returns:
        List of documents with count, continuation token, and has_more flag
    """
    try:
        logger.info("Request for documents page=%s size=%s", page_number, page_size)

        cosmos_service = get_cosmos_service()

        # Validate confidence range
        if min_confidence is not None and max_confidence is not None:
            if min_confidence > max_confidence:
                raise ValueError("min_confidence cannot be greater than max_confidence")

        documents, total_matching = cosmos_service.get_all_documents(
            order_by=order_by,
            descending=descending,
            page_number=page_number,
            page_size=page_size,
            status_filter=status,
            repository_filter=repository,
            collection_filter=collection,
            resource_type_filter=resource_type,
            source_filter=source,
            creator_filter=creator,
            recipient_filter=recipient,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            title_contains=title_contains,
            exclude_errors=exclude_errors,
            exclude_zero_assets=exclude_zero_assets,
        )

        logger.info(
            "Retrieved %d documents (page), total_matching=%d",
            len(documents),
            total_matching,
        )
        return DocumentList(documents=documents, count=total_matching)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve documents: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/documents/ingest/query", response_model=Dict[str, Any])
async def approve_documents(
    status: Optional[str] = None,
    repository: Optional[str] = None,
    collection: Optional[str] = None,
    resource_type: Optional[str] = None,
    min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    max_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    title_contains: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Approve documents matching the specified filters and trigger data ingestion.

    This endpoint builds a Cosmos DB query from the provided filters and triggers
    the data ingestion HTTP function for batch processing.

    Note: Only one bulk ingestion job can run at a time. If a job is already running,
    this endpoint will return a 409 Conflict error.

    Args:
        status: Filter by status (pending, processing, completed, failed,
            published, publishing, reviewed)
        repository: Filter by repository name
        collection: Filter by collection name
        resource_type: Filter by resource type name
        min_confidence: Filter by minimum OCR confidence score (0.0 to 1.0)
        max_confidence: Filter by maximum OCR confidence score (0.0 to 1.0)
        title_contains: Filter documents where title contains this text (case-insensitive)
        current_user: Authenticated user (from dependency)

    Returns:
        Dictionary with success status, message, job_id, and orchestration URLs
    """
    try:
        # Check if a bulk ingestion job is already running
        pipeline_service = get_pipeline_service()
        current_job = pipeline_service.get_bulk_ingestion_status()
        if current_job.get("has_job") and current_job.get("status") in ("Pending", "Running"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "A bulk ingestion job is already running",
                    "current_job": {
                        "job_type": current_job.get("job_type"),
                        "status": current_job.get("status"),
                        "started_by": current_job.get("started_by"),
                        "started_at": current_job.get("started_at"),
                        "processed_documents": current_job.get("processed_documents"),
                        "total_documents": current_job.get("total_documents")
                    },
                    "message": "Please wait for the current job to complete or cancel it first."
                }
            )

        # Validate confidence range
        if min_confidence is not None and max_confidence is not None:
            if min_confidence > max_confidence:
                raise ValueError("min_confidence cannot be greater than max_confidence")

        # Build WHERE clause and parameters using cosmos_service method
        cosmos_service = get_cosmos_service()
        query, where_clause, parameters = cosmos_service.build_query_filters(
            status_filter=status,
            repository_filter=repository,
            collection_filter=collection,
            resource_type_filter=resource_type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            title_contains=title_contains,
        )

        # Build the final query - add the 'not Published' condition and exclude invalid records
        # Exclude records that:
        # - Have no assets (asset_count is 0 or null/undefined)
        # - Don't have completed original_file_status
        # - Don't have completed ocr_processing_status
        # Note: 'query' already includes the WHERE clause from build_query_filters
        exclusion_conditions = [
            "(NOT IS_DEFINED(c.archivist_status) OR c.archivist_status = null OR "
            "c.archivist_status = '' OR NOT IS_STRING(c.archivist_status) OR "
            "LOWER(c.archivist_status) != 'published')",
            "(IS_DEFINED(c.asset_count) AND IS_NUMBER(c.asset_count) AND c.asset_count > 0)",
            "(IS_DEFINED(c.original_file_status) AND IS_STRING(c.original_file_status) AND "
            "c.original_file_status = 'completed')",
            "(IS_DEFINED(c.ocr_processing_status) AND IS_STRING(c.ocr_processing_status) AND "
            "c.ocr_processing_status = 'completed')"
        ]
        exclusion_clause = " AND ".join(exclusion_conditions)
        
        portal_pred = cosmos_service.portal_publish_date_sql_predicate()
        if where_clause:
            # WHERE clause exists, append with AND
            final_query = f"{query} AND {exclusion_clause} AND {portal_pred}"
        else:
            # No WHERE clause, add one
            final_query = f"{query} WHERE {exclusion_clause} AND {portal_pred}"

        excluded_trc_ids: List[str] = []
        excluded_trc_total = 0
        excluded_trc_truncated = False
        try:
            excluded_trc_ids, excluded_trc_total, excluded_trc_truncated = (
                cosmos_service.list_record_ids_matching_ingest_but_missing_trc(
                    query=query,
                    where_clause=where_clause,
                    exclusion_clause=exclusion_clause,
                    portal_pred=portal_pred,
                    parameters=parameters,
                )
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Could not list records excluded for missing TRC: %s", str(exc))

        excluded_trc_payload = {
            "excluded_missing_trc_record_ids": excluded_trc_ids,
            "excluded_missing_trc_total": excluded_trc_total,
            "excluded_missing_trc_ids_truncated": excluded_trc_truncated,
        }

        logger.info(
            "Bulk ingest final_query (first 500 chars): %s",
            final_query[:500],
        )
        logger.info("Bulk ingest parameters: %s", parameters)

        # Call HTTP trigger - Azure Function creates the job record with management URLs
        try:
            result = await _trigger_azure_function(
                "data-ingest",
                {
                    "job_id": "bulk_ingestion",
                    "filter": {
                        "query": final_query,
                        "parameters": parameters if parameters else []
                    },
                    "operation_type": "bulk",
                    "started_by": current_user.user_display
                },
                function_app="data_ingest"
            )
            
            # Check if no documents were found (Azure Function returned early)
            if result.get("instance_id") is None:
                logger.info(
                    "No documents found for bulk ingestion with filters: "
                    "status=%s, repository=%s, collection=%s",
                    status, repository, collection
                )
                return {
                    "success": True,
                    "message": result.get("message", "No documents found matching the filter"),
                    "job_id": None,
                    "instance_id": None,
                    "status_url": None,
                    "terminate_url": None,
                    "suspend_url": None,
                    "resume_url": None,
                    "total_documents": result.get("total_documents", 0),
                    "query": final_query,
                    "parameters": parameters,
                    **excluded_trc_payload,
                }
            
            logger.info(
                "Bulk ingestion started for user %s with filters: "
                "status=%s, repository=%s, collection=%s (instance_id: %s)",
                current_user.user_display,
                status,
                repository,
                collection,
                result.get("instance_id")
            )
            
            return {
                "success": True,
                "message": "Bulk ingestion started successfully",
                "job_id": "bulk_ingestion",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url"),
                "query": final_query,
                "parameters": parameters,
                **excluded_trc_payload,
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start bulk ingestion: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start bulk ingestion: {str(trigger_error)}"
            ) from trigger_error

    except HTTPException:
        raise
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to approve documents: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/search-index/restore", response_model=Dict[str, Any])
async def restore_search_index(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Restore the AI Search index by re-indexing all published documents.

    This endpoint triggers the HTTP function to re-index all documents where 
    archivist_status is 'Published' into Azure AI Search.

    Note: Uses the same job as bulk ingestion (only one ingestion job at a time).
    Batch size and parallelism are configured via environment variables on the Function App.

    Args:
        current_user: Authenticated user (from dependency)

    Returns:
        Dictionary with success status, orchestration URLs, and job info
    """
    logger.info("Restore search index request from user: %s", current_user.user_display)
    
    try:
        pipeline_service = get_pipeline_service()
        
        # Check if any ingestion job is already running (bulk, retry, restore share same job)
        current_status = pipeline_service.get_bulk_ingestion_status()
        if current_status.get("has_job") and current_status.get("status") in ("Running", "Pending", "Suspended"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "An ingestion job is already running",
                    "operation_type": current_status.get("operation_type", "unknown"),
                    "status": current_status.get("status"),
                    "started_by": current_status.get("started_by"),
                    "started_at": current_status.get("started_at"),
                    "message": "Please wait for the current job to complete or cancel it first."
                }
            )

        # Call HTTP trigger for reindex (uses same job as bulk ingestion)
        try:
            result = await _trigger_azure_function(
                "data-ingest",
                {
                    "action": "reindex",
                    "started_by": current_user.user_display
                },
                function_app="data_ingest"
            )
            
            logger.info(
                "Search index restore started by user %s (instance_id: %s)",
                current_user.user_display,
                result.get("instance_id")
            )
            
            return {
                "success": True,
                "message": "Search index restore initiated. Published documents will be re-indexed.",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url")
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start search index restore: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start search index restore: {str(trigger_error)}"
            ) from trigger_error

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to initiate search index restore: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to initiate search index restore: {str(e)}"
        ) from e


class JobStatusUpdate(BaseModel):
    """Request model for job status actions."""
    action: str  # "status", "update", "cancel"
    # For "update" action:
    status: Optional[str] = None  # Durable Functions status
    processed_documents: Optional[int] = None
    total_documents: Optional[int] = None
    error_message: Optional[str] = None


# =============================================================================
# Unified Ingestion Job Management (bulk, retry failed, restore index)
# All three operations share the same job ID since they're the same flow
# =============================================================================


@router.post("/ingestion/job")
async def manage_bulk_ingestion_job(
    request: JobStatusUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Manage bulk ingestion job with a single endpoint.
    
    Actions:
    - "status": Get current job status
    - "update": Update job status from orchestration polling
    - "cancel": Cancel the running job
    
    Args:
        request: Action request with optional parameters
        current_user: Authenticated user
        
    Returns:
        Job status or action result
    """
    try:
        pipeline_service = get_pipeline_service()
        action = request.action.lower()
        
        # ACTION: Get status
        if action == "status":
            return pipeline_service.get_bulk_ingestion_status()
        
        # ACTION: Update status (from UI polling)
        if action == "update":
            result = pipeline_service.update_bulk_ingestion_job(
                status=request.status,
                processed_documents=request.processed_documents,
                total_documents=request.total_documents,
                error_message=request.error_message
            )
            if result:
                return {"success": True, "job": result}
            return {"success": False, "message": "Job not found"}
        
        # ACTION: Cancel job
        if action == "cancel":
            current_job = pipeline_service.get_bulk_ingestion_status()
            if not current_job.get("has_job"):
                raise HTTPException(status_code=404, detail="No bulk ingestion job found")
            
            if current_job.get("status") not in ("Pending", "Running", "Suspended"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel job in '{current_job.get('status')}' status"
                )
            
            result = pipeline_service.cancel_bulk_ingestion_job()
            if not result.get("success"):
                raise HTTPException(status_code=400, detail=result.get("message", "Failed to cancel"))
            
            # Terminate orchestration
            terminate_url = result.get("terminate_url")
            if terminate_url:
                try:
                    terminate_url = _prepare_orchestration_action_url(
                        terminate_url,
                        f"Cancelled by {current_user.user_display}",
                    )
                    await _call_durable_management_url("POST", terminate_url)
                    logger.info("Orchestration terminated by %s", current_user.user_display)
                except httpx.RequestError as e:
                    logger.warning("Failed to terminate orchestration: %s", str(e))
            
            return {"success": True, "message": "Job cancelled", "cancelled_by": current_user.user_display}
        
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to manage bulk ingestion job: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# Retry Failed Documents - Uses Bulk Ingestion Flow
# =============================================================================


@router.post("/ingestion/retry-failed", response_model=Dict[str, Any])
async def retry_failed_documents(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Retry processing of all documents with archivist_status = 'Failed'.

    Note: Uses the same job as bulk ingestion (only one ingestion job at a time).
    Batch size and parallelism are configured via environment variables on the Function App.

    Args:
        current_user: Authenticated user (from dependency)

    Returns:
        Dictionary with success status, orchestration URLs, and job info
    """
    logger.info("Retry failed request from user: %s", current_user.user_display)
    
    try:
        pipeline_service = get_pipeline_service()
        
        # Check if any ingestion job is already running (bulk, retry, restore share same job)
        current_status = pipeline_service.get_bulk_ingestion_status()
        if current_status.get("has_job") and current_status.get("status") in ("Running", "Pending", "Suspended"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "An ingestion job is already running",
                    "operation_type": current_status.get("operation_type", "unknown"),
                    "status": current_status.get("status"),
                    "started_by": current_status.get("started_by"),
                    "started_at": current_status.get("started_at"),
                    "message": "Please wait for the current job to complete or cancel it first."
                }
            )

        # Check how many failed documents exist
        failed_count = pipeline_service.get_failed_documents_count()
        if failed_count == 0:
            return {
                "success": True,
                "message": "No failed documents to retry",
                "total_documents": 0
            }

        # Call HTTP trigger with filter for failed documents
        try:
            result = await _trigger_azure_function(
                "data-ingest",
                {
                    "job_id": "bulk_ingestion",
                    "filter": {
                        "query": "SELECT c.id FROM c WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')",
                        "parameters": []
                    },
                    "operation_type": "bulk",  # Use bulk to enable AI metadata fallback
                    "started_by": current_user.user_display
                },
                function_app="data_ingest"
            )
            
            logger.info(
                "Retry failed started by user %s (instance_id: %s)",
                current_user.user_display,
                result.get("instance_id")
            )
            
            return {
                "success": True,
                "message": f"Retry job initiated for {failed_count} failed documents.",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url"),
                "total_documents": failed_count
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start retry failed job: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start retry failed job: {str(trigger_error)}"
            ) from trigger_error

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to initiate retry failed job: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to initiate retry failed job: {str(e)}"
        ) from e


@router.get("/ingestion/failed-count", response_model=Dict[str, Any])
async def get_failed_documents_count(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get the count of documents with archivist_status = 'Failed'.
    
    Returns:
        Dictionary with failed_count
    """
    try:
        pipeline_service = get_pipeline_service()
        count = pipeline_service.get_failed_documents_count()
        return {"failed_count": count}
    except Exception as e:
        logger.exception("Failed to get failed documents count: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ingestion/failed-errors", response_model=Dict[str, Any])
async def get_failed_errors_summary(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get unique error messages from failed documents with counts.
    
    Returns:
        Dictionary with errors, counts, and pagination info
    """
    try:
        pipeline_service = get_pipeline_service()
        result = pipeline_service.get_failed_documents_errors_summary(
            page=page,
            page_size=page_size
        )
        return result
    except Exception as e:
        logger.exception("Failed to get failed errors summary: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ingestion/failed-documents", response_model=Dict[str, Any])
async def get_failed_documents(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    error: Optional[str] = Query(None, description="Filter by specific error message"),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get paginated list of failed documents.
    Optionally filter by a specific error message.
    
    Returns:
        Dictionary with documents and pagination info
    """
    try:
        pipeline_service = get_pipeline_service()
        
        if error:
            result = pipeline_service.get_failed_documents_by_error(
                error_text=error,
                page=page,
                page_size=page_size
            )
        else:
            result = pipeline_service.get_failed_documents(
                page=page,
                page_size=page_size
            )
        return result
    except Exception as e:
        logger.exception("Failed to get failed documents: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# Unpublish Documents - Unpublish documents by user and date
# =============================================================================


class UnpublishRequest(BaseModel):
    """Request model for unpublishing documents."""

    published_by: str
    published_date: str  # YYYY-MM-DD format


class UnpublishByFiltersRequest(BaseModel):
    """Unpublish all published documents in a collection matching optional filters (no status field — always published)."""

    repository: str = Field(..., min_length=1)
    collection: str = Field(..., min_length=1)
    resource_type: Optional[str] = None
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    title_contains: Optional[str] = None


@router.post("/ingestion/unpublish", response_model=Dict[str, Any])
async def unpublish_documents(
    request: UnpublishRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Unpublish documents that were published by a specific user on a specific date.
    
    This will:
    1. Find all documents where published_by = user AND published_at starts with published_date
    2. Set their archivist_status back to 'Pending'
    3. Delete them from the search index
    
    Args:
        request: Request body containing published_by (user) and published_date (YYYY-MM-DD)
        current_user: Authenticated user (from dependency)
    
    Returns:
        Dictionary with success status, orchestration URLs, and job info
    """
    try:
        # Check if unpublish job is already running
        pipeline_service = get_pipeline_service()
        current_status = pipeline_service.get_unpublish_status()
        if current_status.get("has_job") and current_status.get("status") in ("Running", "Pending", "Suspended"):
            raise HTTPException(
                status_code=409,
                detail="An unpublish job is already running. Please wait for it to complete."
            )

        # Call HTTP trigger for unpublish
        try:
            result = await _trigger_azure_function(
                "unpublish-documents",
                {
                    "published_by": request.published_by,
                    "published_date": request.published_date,
                    "started_by": current_user.user_display
                },
                function_app="data_ingest"
            )
            
            logger.info(
                "Unpublish started by user %s for documents published by %s on %s (instance_id: %s)",
                current_user.user_display,
                request.published_by,
                request.published_date,
                result.get("instance_id")
            )
            
            # Handle case where no documents found
            if not result.get("instance_id"):
                return {
                    "success": True,
                    "message": result.get("message", "No documents found matching criteria."),
                    "total_documents": result.get("total_documents", 0)
                }
            
            return {
                "success": True,
                "message": f"Unpublish job initiated for documents published by {request.published_by} on {request.published_date}",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url")
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start unpublish job: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start unpublish job: {str(trigger_error)}"
            ) from trigger_error
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to initiate unpublish job: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to initiate unpublish job: {str(e)}"
        ) from e


@router.post("/ingestion/unpublish/by-filters", response_model=Dict[str, Any])
async def unpublish_documents_by_filters(
    request: UnpublishByFiltersRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Unpublish published documents scoped to a repository/collection, optionally filtered by
    resource type, OCR confidence band, and title search. Status is always *published*.
    """
    try:
        if request.min_confidence is not None and request.max_confidence is not None:
            if request.min_confidence > request.max_confidence:
                raise ValueError("min_confidence cannot be greater than max_confidence")

        pipeline_service = get_pipeline_service()
        current_status = pipeline_service.get_unpublish_status()
        if current_status.get("has_job") and current_status.get("status") in (
            "Running",
            "Pending",
            "Suspended",
        ):
            raise HTTPException(
                status_code=409,
                detail="An unpublish job is already running. Please wait for it to complete.",
            )

        cosmos_service = get_cosmos_service()
        _, where_clause, parameters = cosmos_service.build_query_filters(
            status_filter="published",
            repository_filter=request.repository,
            collection_filter=request.collection,
            resource_type_filter=request.resource_type,
            min_confidence=request.min_confidence,
            max_confidence=request.max_confidence,
            title_contains=request.title_contains,
        )
        if not where_clause:
            logger.error(
                "unpublish by filters produced empty WHERE (repository=%s, collection=%s)",
                request.repository,
                request.collection,
            )
            raise HTTPException(
                status_code=500,
                detail="Internal error: could not build a scoped unpublish query. Refusing to run without filters.",
            )
        id_query = f"SELECT c.id FROM c {where_clause}"

        try:
            result = await _trigger_azure_function(
                "unpublish-documents",
                {
                    "started_by": current_user.user_display,
                    "published_by": "(collection filters)",
                    "published_date": "bulk-by-filters",
                    "filter": {
                        "query": id_query,
                        "parameters": parameters if parameters else [],
                    },
                },
                function_app="data_ingest",
            )

            logger.info(
                "Unpublish by filters started by %s (repository=%s, collection=%s, instance_id=%s)",
                current_user.user_display,
                request.repository,
                request.collection,
                result.get("instance_id"),
            )

            if not result.get("instance_id"):
                return {
                    "success": True,
                    "message": result.get(
                        "message", "No published documents match the selected filters."
                    ),
                    "total_documents": result.get("total_documents", 0),
                }

            return {
                "success": True,
                "message": "Unpublish job started for published documents matching the selected filters",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url"),
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start unpublish-by-filters job: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start unpublish job: {str(trigger_error)}",
            ) from trigger_error

    except HTTPException:
        raise
    except ValueError as e:
        logger.exception("Invalid unpublish-by-filters request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to initiate unpublish-by-filters job: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to initiate unpublish job: {str(e)}"
        ) from e


@router.post("/ingestion/unpublish/job", response_model=Dict[str, Any])
async def manage_unpublish_job(
    action: str = Body(..., embed=True),
    status: Optional[str] = Body(None),
    error_message: Optional[str] = Body(None),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Manage unpublish job - get status, update, or cancel.
    
    Args:
        action: The action to perform ("status", "update", "cancel")
        status: New status when action is "update"
        error_message: Error message when updating with failed status
        current_user: Authenticated user (from dependency)
    
    Returns:
        Dictionary with job status or action result
    """
    pipeline_service = get_pipeline_service()
    
    if action == "status":
        return pipeline_service.get_unpublish_status()
    
    elif action == "update":
        if not status:
            raise HTTPException(status_code=400, detail="status is required for update action")
        result = pipeline_service.update_unpublish_status(
            status=status,
            error_message=error_message
        )
        if result:
            return {"success": True, "job": result}
        raise HTTPException(status_code=404, detail="No unpublish job found")
    
    elif action == "cancel":
        result = pipeline_service.cancel_unpublish_job()
        if result:
            return {"success": True, "message": "Unpublish job cancelled", "job": result}
        raise HTTPException(status_code=404, detail="No unpublish job found to cancel")
    
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}. Use 'status', 'update', or 'cancel'")


@router.get("/ingestion/publishers", response_model=Dict[str, Any])
async def get_publishers_list(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get list of users who have published documents with their publication dates.
    
    Returns a list of unique publishers with the dates they published documents.
    Used for the unpublish UI to populate dropdowns.
    
    Returns:
        Dictionary with list of publishers and their publication dates
    """
    try:
        cosmos_service = get_cosmos_service()
        publisher_list = cosmos_service.get_publishers_list()
        return {"publishers": publisher_list}
        
    except Exception as e:
        logger.exception("Failed to get publishers list: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ingestion/publish-batches", response_model=Dict[str, Any])
async def list_publish_batches(
    limit: int = Query(100, ge=1, le=500),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Recent bulk/retry publish batches (Cosmos audit container)."""
    del current_user  # auth gate only
    try:
        svc = get_publish_batch_service()
        batches = svc.list_recent_batches(limit=limit)
        return {"success": True, "batches": batches, "count": len(batches)}
    except Exception as e:
        logger.exception("list_publish_batches failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                "Publish batch history is unavailable. Ensure the bulk-publish-batches Cosmos container exists "
                "and COSMOS_DB_BULK_PUBLISH_BATCHES_CONTAINER_NAME is set."
            ),
        ) from e


@router.get("/ingestion/publish-batches/search", response_model=Dict[str, Any])
async def search_publish_batches_by_record_id(
    record_id: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Find publish batches that included a given record id."""
    del current_user
    try:
        svc = get_publish_batch_service()
        batches = svc.find_batches_with_record_id(record_id, limit=limit)
        return {"success": True, "record_id": record_id, "batches": batches, "count": len(batches)}
    except Exception as e:
        logger.exception("search_publish_batches_by_record_id failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Publish batch history is unavailable.",
        ) from e


@router.get("/ingestion/publish-batches/{batch_id}", response_model=Dict[str, Any])
async def get_publish_batch_detail(
    batch_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    del current_user
    try:
        svc = get_publish_batch_service()
        batch = svc.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        return {"success": True, "batch": batch}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_publish_batch_detail failed: %s", e)
        raise HTTPException(status_code=503, detail="Publish batch history is unavailable.") from e


@router.post("/ingestion/publish-batches/{batch_id}/unpublish", response_model=Dict[str, Any])
async def unpublish_publish_batch(
    batch_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Unpublish all documents from a stored publish batch (uses successful_record_ids when present).
    """
    try:
        pipeline_service = get_pipeline_service()
        current_status = pipeline_service.get_unpublish_status()
        if current_status.get("has_job") and current_status.get("status") in (
            "Running",
            "Pending",
            "Suspended",
        ):
            raise HTTPException(
                status_code=409,
                detail="An unpublish job is already running. Please wait for it to complete.",
            )

        svc = get_publish_batch_service()
        batch = svc.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        ids = batch.get("successful_record_ids") or []
        if not ids:
            ids = list(batch.get("document_ids") or [])
        if not ids:
            raise HTTPException(status_code=400, detail="This batch has no document ids to unpublish.")

        published_by = f"publish-batch:{batch_id}"
        published_date = "batch"

        result = await _trigger_azure_function(
            "unpublish-documents",
            {
                "document_ids": ids,
                "published_by": published_by,
                "published_date": published_date,
                "started_by": current_user.user_display,
            },
            function_app="data_ingest",
        )

        if not result.get("instance_id"):
            return {
                "success": True,
                "message": result.get("message", "No documents to unpublish."),
                "total_documents": result.get("total_documents", 0),
            }

        return {
            "success": True,
            "message": "Unpublish job started for this batch.",
            "instance_id": result.get("instance_id"),
            "status_url": result.get("status_url"),
            "terminate_url": result.get("terminate_url"),
            "suspend_url": result.get("suspend_url"),
            "resume_url": result.get("resume_url"),
            "total_documents": len(ids),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unpublish_publish_batch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/documents/{document_id}/unpublish", response_model=Dict[str, Any])
async def unpublish_single_document(
    document_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Unpublish a single document by sending a message to the Service Bus queue.
    
    This will:
    1. Set the document's archivist_status to 'Pending'
    2. Clear published_by and published_at fields
    3. Delete the document's data from the search index
    
    Args:
        document_id: The ID of the document to unpublish
        current_user: Authenticated user (from dependency)
    
    Returns:
        Dictionary with success status and message
    """
    try:
        # Verify document exists and is published
        cosmos_service = get_cosmos_service()
        document = cosmos_service.get_document_raw(document_id)
        
        if not document:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        
        current_status = (document.get("archivist_status") or "").strip().lower()
        if current_status != "published":
            raise HTTPException(
                status_code=400,
                detail=f"Document is not published (current status: {current_status})"
            )
        
        # Send message to Service Bus queue
        message = {
            "action": "unpublish",
            "document_id": document_id,
            "requested_by": current_user.user_display
        }
        
        send_to_queue(settings.data_ingestion_queue_name, json.dumps(message))
        
        logger.info(
            "Unpublish request sent for document %s by user %s",
            document_id,
            current_user.user_display
        )
        
        return {
            "success": True,
            "message": f"Unpublish request sent for document {document_id}",
            "document_id": document_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to send unpublish request for document %s: %s", document_id, str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send unpublish request: {str(e)}"
        ) from e


@router.post("/documents/ingest/by-ids", response_model=Dict[str, Any])
async def ingest_documents(
    request: DocumentIdsRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Trigger data ingestion for multiple document IDs.

    This endpoint accepts a list of document IDs and triggers the HTTP function
    for batch processing by the data ingestion orchestrator.

    Note: Only one bulk ingestion job can run at a time. If a job is already running,
    this endpoint will return a 409 Conflict error.

    Args:
        request: Request body containing list of document IDs
        current_user: Authenticated user (from dependency)

    Returns:
        Dictionary with success status, message, job_id, and orchestration URLs
    """
    try:
        # Check if a bulk ingestion job is already running
        pipeline_service = get_pipeline_service()
        current_job = pipeline_service.get_bulk_ingestion_status()
        if current_job.get("has_job") and current_job.get("status") in ("Pending", "Running"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "A bulk ingestion job is already running",
                    "current_job": {
                        "job_type": current_job.get("job_type"),
                        "status": current_job.get("status"),
                        "started_by": current_job.get("started_by"),
                        "started_at": current_job.get("started_at"),
                        "processed_documents": current_job.get("processed_documents"),
                        "total_documents": current_job.get("total_documents")
                    },
                    "message": "Please wait for the current job to complete or cancel it first."
                }
            )

        document_ids = request.document_ids

        if not document_ids:
            raise ValueError("document_ids cannot be empty")

        if not isinstance(document_ids, list):
            raise ValueError("document_ids must be a list")

        # Validate each document_id is a non-empty string
        for doc_id in document_ids:
            if doc_id is None:
                raise ValueError("document_ids cannot contain None values")
            if not isinstance(doc_id, str):
                raise ValueError(f"All document_ids must be strings, got {type(doc_id).__name__}")
            if not doc_id.strip():
                raise ValueError("All document_ids must be non-empty strings")

        cosmos_service = get_cosmos_service()
        unique_ids = list(dict.fromkeys(document_ids))
        not_found: List[str] = []
        lacking_portal: List[str] = []
        for doc_id in unique_ids:
            doc = cosmos_service.get_document_raw(doc_id)
            if not doc:
                not_found.append(doc_id)
                continue
            if not cosmos_service.document_has_portal_publish_date(doc):
                lacking_portal.append(doc_id)

        if not_found:
            preview = ", ".join(not_found[:5])
            suffix = "…" if len(not_found) > 5 else ""
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(not_found)} document ID(s) were not found. "
                    f"Examples: {preview}{suffix}"
                ),
            )
        if lacking_portal:
            preview = ", ".join(lacking_portal[:5])
            suffix = "…" if len(lacking_portal) > 5 else ""
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(lacking_portal)} document(s) are missing a non-empty "
                    "'Date Published to Portal' (TRC) and cannot be queued for ingestion. "
                    f"Examples: {preview}{suffix}"
                ),
            )

        document_ids = unique_ids

        # Build the payload for HTTP trigger
        payload = {
            "job_id": "bulk_ingestion",
            "document_ids": document_ids,
            "operation_type": "bulk",
            "started_by": current_user.user_display
        }

        if request.correlation_id:
            payload["correlation_id"] = request.correlation_id

        # Call HTTP trigger - Azure Function creates the job record with management URLs
        try:
            result = await _trigger_azure_function("data-ingest", payload, function_app="data_ingest")
            
            logger.info(
                "Bulk ingestion started for user %s with %d document IDs (instance_id: %s)",
                current_user.user_display,
                len(document_ids),
                result.get("instance_id")
            )
            
            return {
                "success": True,
                "message": f"{len(document_ids)} document(s) ingestion started",
                "job_id": "bulk_ingestion",
                "instance_id": result.get("instance_id"),
                "status_url": result.get("status_url"),
                "terminate_url": result.get("terminate_url"),
                "suspend_url": result.get("suspend_url"),
                "resume_url": result.get("resume_url"),
                "document_count": len(document_ids),
                "document_ids": document_ids
            }
        except HTTPException:
            raise
        except Exception as trigger_error:
            logger.exception("Failed to start bulk ingestion: %s", str(trigger_error))
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start bulk ingestion: {str(trigger_error)}"
            ) from trigger_error

    except HTTPException:
        raise
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to start documents ingestion: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/documents/{document_id}",
    response_model=DocumentMetadata,
    response_model_exclude_none=True,
)
async def get_document(
    document_id: str,
    partition_key: Optional[str] = Query(
        default=None, description="Partition key (defaults to document_id)"
    ),
):
    """
    Get a specific document by ID.

    Args:
        document_id: Document ID
        partition_key: Partition key value (defaults to document_id)

    Returns:
        Document metadata with SAS tokens appended to asset blob URLs
    """
    try:
        cosmos_service = get_cosmos_service()
        document = cosmos_service.get_document_by_id(
            document_id=document_id, partition_key=partition_key
        )

        if document is None:
            raise HTTPException(
                status_code=404, detail=f"Document '{document_id}' not found"
            )

        document_dict = document.model_dump()
        return await _build_document_response(document_dict)

    except HTTPException:
        raise
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve document: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve document: {str(e)}"
        ) from e


@router.get(
    "/documents/{document_id}/assets/{asset_id}",
    response_model=AssetDetail,
    response_model_exclude_none=True,
)
async def get_asset_by_id(
    document_id: str,
    asset_id: str,
    partition_key: Optional[str] = Query(
        default=None, description="Partition key (defaults to document_id)"
    ),
):
    """
    Get a specific asset by document (record) id and asset id.

    Returns the asset details (with SAS tokens applied to blob URLs) if found.
    """
    try:
        cosmos_service = get_cosmos_service()
        found_asset = cosmos_service.get_document_asset_by_id(
            document_id=document_id, asset_id=asset_id, partition_key=partition_key
        )

        if found_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Asset '{asset_id}' not found for document '{document_id}'",
            )

        found_asset_dict = found_asset.model_dump()
        # Hydrate the asset (populate OCR text field)
        asset = await hydrate_asset_ocr_text(found_asset_dict)

        for field in _ASSET_BLOB_URL_FIELDS:
            value = asset.get(field)
            if value:
                asset[field] = add_sas_token_to_url(value)

        return AssetDetail(**asset)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Failed to retrieve asset %s for document %s: %s",
            asset_id,
            document_id,
            str(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve asset: {str(e)}"
        ) from e

@router.get("/collections/summaries", response_model=CollectionSummariesResponse)
async def get_collection_summaries():
    """
    Get aggregated statistics grouped by collection and repository.

    This endpoint uses server-side aggregation in Cosmos DB for efficiency
    and can handle hundreds of thousands of documents without performance issues.

    Returns:
        Collection summaries with counts, pending items, and completion rates
    """
    try:
        cosmos_service = get_cosmos_service()
        summaries = cosmos_service.get_collection_summaries()

        return CollectionSummariesResponse(summaries=summaries, count=len(summaries))
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve collection summaries: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve collection summaries: {str(e)}"
        ) from e


@router.get("/collections/details")
async def get_collection_details(
    repository: str = Query(..., description="Repository name"),
    collection: str = Query(..., description="Collection name"),
):
    """
    Get statistics for a specific collection.

    Returns:
        Collection details with counts (totalItems, pending, reviewed, published)
        and completion rate
    """
    try:
        stats_service = get_statistics_service()
        # Use direct point read for faster loading (includes avgOcrConfidence from statistics container)
        stats = stats_service.get_collection_statistics(repository, collection)

        if stats:
            return {
                "collection": {
                    **stats,
                    "resourceTypes": [],
                    "resourceTypesCount": 0,
                }
            }

        # Collection not found - return zeros
        return {
            "collection": {
                "collection": collection,
                "repository": repository,
                "totalItems": 0,
                "pending": 0,
                "reviewed": 0,
                "published": 0,
                "completionRate": 0.0,
                "resourceTypes": [],
                "resourceTypesCount": 0,
                "avgOcrConfidence": None,
            }
        }
    except Exception as e:
        logger.exception("Failed to retrieve collection details: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve collection details: {str(e)}"
        ) from e


@router.get("/filters/repositories")
async def get_repository_names():
    """Return distinct repository names available in the database."""
    try:
        cosmos_service = get_cosmos_service()
        repos = cosmos_service.get_distinct_repositories()
        return {"repositories": repos, "count": len(repos)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve repositories: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve repositories: {str(e)}"
        ) from e


@router.get("/filters/collections")
async def get_collections(
    repository: Optional[str] = Query(
        default=None, description="Optional repository to filter collections by"
    )
):
    """
    Return distinct collection names. If `repository` is provided,
    return collections for that repository.
    """
    try:
        cosmos_service = get_cosmos_service()
        collections = cosmos_service.get_distinct_collections(repository=repository)
        return {"collections": collections, "count": len(collections)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve collections: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve collections: {str(e)}"
        ) from e


@router.get("/filters/resource-types")
async def get_resource_types():
    """Return distinct resource type names available in the database."""
    try:
        cosmos_service = get_cosmos_service()
        resource_types = cosmos_service.get_distinct_resource_types()
        return {"resource_types": resource_types, "count": len(resource_types)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve resource types: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve resource types: {str(e)}"
        ) from e


@router.get("/filters/sources")
async def get_sources():
    """Return distinct Digital Item Publisher values available in the database."""
    try:
        cosmos_service = get_cosmos_service()
        sources = cosmos_service.get_distinct_sources()
        return {"sources": sources, "count": len(sources)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve sources: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve sources: {str(e)}"
        ) from e


@router.get("/filters/creators")
async def get_creators(
    repository: Optional[str] = Query(default=None, description="Filter by repository name"),
    collection: Optional[str] = Query(default=None, description="Filter by collection name"),
):
    """Return distinct Creator values grouped by repository and collection."""
    try:
        cosmos_service = get_cosmos_service()
        creators = cosmos_service.get_distinct_creators(
            repository=repository, collection=collection
        )
        return {"creators": creators, "count": len(creators)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve creators: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve creators: {str(e)}"
        ) from e


@router.get("/filters/recipients")
async def get_recipients(
    repository: Optional[str] = Query(default=None, description="Filter by repository name"),
    collection: Optional[str] = Query(default=None, description="Filter by collection name"),
):
    """Return distinct Recipient values for the given repository/collection scope."""
    try:
        cosmos_service = get_cosmos_service()
        recipients = cosmos_service.get_distinct_recipients(
            repository=repository, collection=collection
        )
        return {"recipients": recipients, "count": len(recipients)}
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve recipients: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve recipients: {str(e)}"
        ) from e


@router.get("/repositories", response_model=RepositoryStatisticsResponse)
async def get_repositories(
    title_contains: Optional[str] = Query(
        default=None, description="Filter repositories by name (case-insensitive)"
    ),
    page_number: Optional[int] = Query(default=1, ge=1),
    page_size: Optional[int] = Query(default=20, ge=1, le=100),
):
    """
    Get paginated list of repositories with statistics for table display.

    This endpoint provides paginated repository-level statistics for table binding.

    Returns:
        Paginated repository statistics with counts, pending items, and completion rates
    """
    try:
        cosmos_service = get_cosmos_service()
        stats, total_count = cosmos_service.get_repository_statistics(
            title_contains=title_contains,
            page_number=page_number,
            page_size=page_size
        )

        return RepositoryStatisticsResponse(repositories=stats, count=total_count)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve repositories: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve repositories: {str(e)}"
        ) from e


@router.get("/repositories/statistics", response_model=RepositoryStatisticsSummary)
async def get_repository_statistics():
    """
    Get overall aggregated statistics for repositories page.

    This endpoint provides server-side aggregation for overall repository statistics,
    used for dashboard cards and summary metrics.

    Returns:
        Overall repository statistics summary
    """
    try:
        cosmos_service = get_cosmos_service()
        stats = cosmos_service.get_repository_statistics_summary()

        return RepositoryStatisticsSummary(**stats)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve repository statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve repository statistics: {str(e)}"
        ) from e


@router.get(
    "/repositories/{repository}/statistics",
    response_model=RepositoryCollectionStatisticsSummary,
)
async def get_repository_collection_statistics(repository: str):
    """
    Get overall aggregated statistics for a repository's collections.

    This endpoint provides server-side aggregation for overall repository collection statistics,
    used for dashboard cards and summary metrics.

    Args:
        repository: Repository name (URL-encoded)

    Returns:
        Overall repository collection statistics summary
    """
    try:
        decoded_repository = repository  # Already decoded by FastAPI
        cosmos_service = get_cosmos_service()
        stats = cosmos_service.get_repository_collection_statistics_summary(
            repository=decoded_repository
        )

        return RepositoryCollectionStatisticsSummary(**stats)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve repository collection statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve repository collection statistics: {str(e)}"
        ) from e


@router.get(
    "/repositories/{repository}/collections",
    response_model=CollectionDetailsResponse,
    response_model_exclude_none=True,
)
async def get_collections_by_repository(
    repository: str,
    title_contains: Optional[str] = Query(
        default=None, description="Filter collections by name (case-insensitive)"
    ),
    page_number: Optional[int] = Query(default=1, ge=1),
    page_size: Optional[int] = Query(default=20, ge=1, le=100),
):
    """
    Get paginated list of collections for a specific repository for table display.

    This endpoint provides paginated collection details for table binding.

    Args:
        repository: Repository name (URL-encoded)
        title_contains: Optional search term to filter collection names
        page_number: Page number for pagination (default: 1)
        page_size: Number of items per page (default: 20, max: 100)

    Returns:
        Paginated list of collection details with count
    """
    try:
        decoded_repository = repository  # Already decoded by FastAPI
        cosmos_service = get_cosmos_service()
        collections, total_count = cosmos_service.get_collections_by_repository(
            repository=decoded_repository,
            title_contains=title_contains,
            page_number=page_number,
            page_size=page_size,
        )

        return CollectionDetailsResponse(collections=collections, count=total_count)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve collections: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve collections: {str(e)}"
        ) from e


@router.get(
    "/collections/record-ids",
    response_model=RecordIdsResponse,
    response_model_exclude_none=True,
)
async def get_record_ids(
    repository: Optional[str] = None,
    collection: Optional[str] = None,
    status: Optional[str] = None,
    resource_type: Optional[str] = None,
    min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    max_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    title_contains: Optional[str] = None,
    order_by: str = Query(default="created_at"),
    descending: bool = Query(default=False),
):
    """
    Get only record IDs for navigation purposes (optimized endpoint).

    This endpoint returns only document IDs, not full documents, for efficient
    navigation between records in a collection. This avoids fetching full document
    data when only IDs are needed.

    Args:
        repository: Filter by repository name
        collection: Filter by collection name
        status: Filter by status
        resource_type: Filter by resource type
        min_confidence: Filter by minimum OCR confidence score (0.0 to 1.0)
        max_confidence: Filter by maximum OCR confidence score (0.0 to 1.0)
        title_contains: Filter documents where title contains this text
        order_by: Field to order by (default: created_at)
        descending: Sort in descending order (default: False)

    Returns:
        List of record IDs in order
    """
    try:
        cosmos_service = get_cosmos_service()

        # Validate confidence range
        if min_confidence is not None and max_confidence is not None:
            if min_confidence > max_confidence:
                raise ValueError("min_confidence cannot be greater than max_confidence")

        record_ids = cosmos_service.get_record_ids_by_collection(
            repository=repository,
            collection=collection,
            status_filter=status,
            resource_type_filter=resource_type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            title_contains=title_contains,
            order_by=order_by,
            descending=descending,
        )

        return RecordIdsResponse(record_ids=record_ids, count=len(record_ids))
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve record IDs: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve record IDs: {str(e)}"
        ) from e


@router.get("/dashboard/statistics", response_model=DashboardStatistics)
async def get_dashboard_statistics():
    """
    Get aggregated dashboard statistics.

    This endpoint provides server-side aggregation for dashboard-level statistics,
    avoiding the need for client-side calculation from multiple data sources.

    Returns:
        Dashboard statistics with overall counts and completion rates
    """
    try:
        cosmos_service = get_cosmos_service()
        stats = cosmos_service.get_dashboard_statistics()

        return DashboardStatistics(**stats)
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to retrieve dashboard statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve dashboard statistics: {str(e)}"
        ) from e


@router.post("/statistics/rebuild")
async def rebuild_statistics(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Manually rebuild all pre-computed statistics.

    This endpoint triggers a full rebuild of all statistics (dashboard, repositories,
    collections). Use this for initial setup or manual refresh.

    Note: This operation may take a few minutes depending on the number of documents.

    Returns:
        Success message with rebuild status
    """
    if not current_user.can_run_statistics_rebuild:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        await asyncio.to_thread(stats_service.rebuild_all_statistics)

        return {
            "status": "success",
            "message": "All statistics rebuilt successfully"
        }
    except Exception as e:
        logger.exception("Failed to rebuild statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail="Failed to rebuild statistics"
        ) from e


@router.post("/statistics/rebuild/dashboard")
async def rebuild_dashboard_statistics(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild only dashboard statistics.

    Faster than full rebuild - only updates dashboard aggregate counts.

    Returns:
        Rebuilt dashboard statistics
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        stats = await asyncio.to_thread(stats_service.rebuild_dashboard_statistics)

        return {
            "status": "success",
            "message": "Dashboard statistics rebuilt successfully",
            "data": stats
        }
    except Exception as e:
        logger.exception("Failed to rebuild dashboard statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail="Failed to rebuild dashboard statistics"
        ) from e


@router.post("/statistics/rebuild/repositories")
async def rebuild_repository_statistics(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild repository statistics only.

    Rebuilds all repository-level statistics.

    Returns:
        Success message with rebuild status
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        await asyncio.to_thread(stats_service.rebuild_repository_statistics)

        return {
            "status": "success",
            "message": "Repository statistics rebuilt successfully"
        }
    except Exception as e:
        logger.exception("Failed to rebuild repository statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail="Failed to rebuild repository statistics"
        ) from e


@router.post("/statistics/rebuild/collections")
async def rebuild_collection_statistics(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild collection statistics only.

    Rebuilds all collection-level statistics across all repositories.

    Returns:
        Success message with rebuild status
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        await asyncio.to_thread(stats_service.rebuild_collection_statistics)

        return {
            "status": "success",
            "message": "Collection statistics rebuilt successfully"
        }
    except Exception as e:
        logger.exception("Failed to rebuild collection statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail="Failed to rebuild collection statistics"
        ) from e


@router.post("/statistics/rebuild/collections/{repository}")
async def rebuild_collection_statistics_by_repository(
    repository: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild collection statistics for a specific repository.

    Args:
        repository: Repository name to rebuild stats for

    Returns:
        Success message with rebuild status
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        await asyncio.to_thread(
            stats_service.rebuild_collection_statistics_by_repository, repository
        )

        return {
            "status": "success",
            "message": f"Collection statistics rebuilt for repository '{repository}'"
        }
    except Exception as e:
        logger.error(
            "Failed to rebuild collection statistics for repository '%s': %s",
            repository, str(e), exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to rebuild collection statistics"
        ) from e


@router.get("/repositories/{repository}/collections/{collection}/ocr-report")
async def get_collection_ocr_report(
    repository: str,
    collection: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Get collection-level OCR accuracy report broken down by resource type.

    Returns overall and per-resource-type averages for both OCR accuracy
    (asset_avg_confidence) and metadata extraction confidence.

    Args:
        repository: Repository name
        collection: Collection name

    Returns:
        OCR accuracy report with per-resource-type breakdown
    """
    try:
        stats_service = get_statistics_service()
        report = await asyncio.to_thread(
            stats_service.get_collection_ocr_report, repository, collection
        )
        if report is None:
            raise HTTPException(
                status_code=404,
                detail=f"OCR report not found for '{repository}/{collection}'",
            )
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Failed to get OCR report for '%s/%s': %s",
            repository, collection, str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get OCR report",
        ) from e


@router.post("/statistics/rebuild/ocr-reports")
async def rebuild_all_ocr_reports(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild OCR accuracy reports for all collections.

    Returns:
        Number of reports rebuilt
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        count = await asyncio.to_thread(stats_service.rebuild_all_ocr_reports)
        return {
            "status": "success",
            "message": f"Rebuilt {count} OCR reports",
        }
    except Exception as e:
        logger.exception("Failed to rebuild OCR reports: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Failed to rebuild OCR reports",
        ) from e


@router.post("/statistics/rebuild/ocr-reports/{repository}/{collection}")
async def rebuild_collection_ocr_report(
    repository: str,
    collection: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Rebuild OCR accuracy report for a single collection.

    Args:
        repository: Repository name
        collection: Collection name

    Returns:
        Rebuilt OCR report
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        stats_service = get_statistics_service()
        report = await asyncio.to_thread(
            stats_service.rebuild_collection_ocr_report, repository, collection
        )
        return {
            "status": "success",
            "message": f"OCR report rebuilt for '{repository}/{collection}'",
            "data": report,
        }
    except Exception as e:
        logger.exception(
            "Failed to rebuild OCR report for '%s/%s': %s",
            repository, collection, str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to rebuild OCR report",
        ) from e


@router.delete("/statistics/clear")
async def clear_all_statistics():
    """
    Clear all statistics from the container.

    Use this to remove stale/duplicate data before rebuild.

    Returns:
        Number of statistics documents deleted
    """
    try:
        stats_service = get_statistics_service()
        deleted_count = stats_service.clear_all_statistics()

        return {
            "status": "success",
            "message": f"Cleared {deleted_count} statistics documents"
        }
    except Exception as e:
        logger.exception("Failed to clear statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to clear statistics: {str(e)}"
        ) from e


@router.get("/statistics/status")
async def get_statistics_status():
    """
    Get the status of pre-computed statistics.

    Returns information about when statistics were last updated.

    Returns:
        Statistics status including last update timestamps
    """
    try:
        stats_service = get_statistics_service()
        status = stats_service.get_statistics_status()

        if status["exists"]:
            message = "Statistics are up to date"
        else:
            message = "Statistics need to be rebuilt"
        return {
            "statisticsExist": status["exists"],
            "lastUpdated": status["lastUpdated"],
            "message": message
        }
    except Exception as e:
        logger.exception("Failed to get statistics status: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get statistics status: {str(e)}"
        ) from e


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentMetadata,
    response_model_exclude_none=True,
)
async def update_document(
    document_id: str,
    update_request: DocumentUpdateRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
    partition_key: Optional[str] = Query(
        default=None, description="Partition key (defaults to document_id)"
    ),
    if_match: Optional[str] = Header(
        default=None, description="ETag for optimistic concurrency"
    ),
):
    """
    Update a document's metadata with audit trail.

    Args:
        document_id: Document ID
        update_request: Update request with metadata changes
        current_user: Authenticated user (injected by dependency)
        partition_key: Partition key value (defaults to document_id)
        if_match: ETag from If-Match header for optimistic concurrency
    Returns:
        Updated document metadata
    """
    try:
        cosmos_service = get_cosmos_service()
        pk = partition_key if partition_key is not None else document_id

        # Get current document (Pydantic model for validation/response)
        current_doc_obj = cosmos_service.get_document_by_id(document_id, pk)
        if current_doc_obj is None:
            raise HTTPException(
                status_code=404, detail=f"Document '{document_id}' not found"
            )

        current_doc = current_doc_obj.model_dump()

        # Also get raw document for statistics (preserves Repository/Collection object format)
        current_doc_raw = cosmos_service.get_document_raw(document_id, pk)

        # Track all field changes
        field_changes = []
        updates = {}

        # Handle metadata updates
        if update_request.metadata:
            # Define allowlist for metadata fields
            allowlist = [
                "title",
                "description",
                "creationDate",
                "creator",
                "recipient",
                "citation",
                "resourceType",
                "period",
                "rights",
                "productionMethod",
                "language",
            ]

            # Create field changes for metadata
            metadata_changes = create_field_changes(
                current_doc, update_request.metadata, allowlist
            )
            field_changes.extend(metadata_changes)

            if metadata_changes:
                updates["metadata"] = {}

                # Map UI field names to CosmosDB field names
                field_mapping = {
                    "title": "Title",
                    "description": "Description",
                    "creationDate": "Creation Date",
                    "creator": "Creator",
                    "recipient": "Recipient",
                    "citation": "Citation",
                    "resourceType": "Resource Type",
                    "period": "Period",
                    "rights": "Copyright Status",
                    "productionMethod": "Production Method",
                    "language": "Language",
                }

                for field_key, field_value in update_request.metadata.items():
                    if field_key in allowlist:
                        cosmos_field = field_mapping.get(field_key, field_key)
                        updates["metadata"][cosmos_field] = field_value

        # Handle archivist field updates
        # Check if any archivist field was explicitly provided in the request
        archivist_fields_in_request = any(
            [
                "archivist_notes" in update_request.model_fields_set,
                "archivist_status" in update_request.model_fields_set,
            ]
        )

        if archivist_fields_in_request:
            if "archivist_notes" in update_request.model_fields_set:
                old_notes = current_doc.get("archivist_notes", "")
                new_notes = update_request.archivist_notes or ""
                if old_notes != new_notes:
                    field_changes.append(
                        {"path": "archivist_notes", "from": old_notes, "to": new_notes}
                    )
                    updates["archivist_notes"] = new_notes

            if "archivist_status" in update_request.model_fields_set:
                old_status = current_doc.get("archivist_status") or ""
                new_status = normalize_archivist_status(update_request.archivist_status)
                old_norm = normalize_archivist_status(old_status if old_status else None)
                # Always track status changes, even if both are empty (for audit trail)
                if old_norm != new_status:
                    field_changes.append(
                        {
                            "path": "archivist_status",
                            "from": old_status if old_status else "",
                            "to": new_status if new_status else "",
                        }
                    )
                    updates["archivist_status"] = new_status
                    if new_status != "publishing":
                        updates["archivist_publish_started_at"] = None

                    # Auto-populate validated_by/validated_at when status changes to Reviewed
                    if new_status == "reviewed":
                        updates["validated_by"] = current_user.user_display
                        updates["validated_at"] = datetime.utcnow().isoformat() + "Z"
                        field_changes.append({
                            "path": "validated_by",
                            "from": current_doc.get("validated_by") or "",
                            "to": current_user.user_display
                        })
                    
                    # Auto-populate published_by when status changes to Publishing
                    # Note: published_at is set by the indexing function when document is actually published
                    if new_status == "publishing":
                        updates["published_by"] = current_user.user_display
                        updates["archivist_publish_started_at"] = datetime.now(timezone.utc).isoformat()
                        field_changes.append({
                            "path": "published_by",
                            "from": current_doc.get("published_by") or "",
                            "to": current_user.user_display
                        })
                    
                    logger.info(
                        "Archivist status change detected for document %s: %s -> %s",
                        document_id,
                        old_status or "None/Empty",
                        new_status or "None/Empty"
                    )
                else:
                    logger.debug(
                        "No archivist status change for document %s: %s (unchanged)",
                        document_id,
                        old_status or "None/Empty"
                    )

        # Handle visual description updates
        if "visual_detailed_description_flexible" in update_request.model_fields_set:
            new_visual_desc = update_request.visual_detailed_description_flexible or ""
            
            # Get the existing flexible blob URL
            old_flexible_blob_url = current_doc.get("visual_detailed_description_flexible_blob_url")
            
            # Download old text for comparison
            old_text = ""
            if old_flexible_blob_url:
                try:
                    old_text = await download_blob_text(old_flexible_blob_url)
                except (OSError, asyncio.TimeoutError, BlobDownloadError, BlobNotFoundError) as e:
                    logger.warning(
                        "Could not download existing visual description blob for document %s: %s",
                        document_id,
                        str(e),
                    )
                    old_text = None
            
            # Only update if text actually changed
            if old_text is not None and old_text.strip() == new_visual_desc.strip():
                logger.info(
                    "No visual description changes detected for document %s",
                    document_id,
                )
            else:
                # Upload new version to blob
                if old_flexible_blob_url:
                    # Extract container and blob path from existing URL
                    base_url, cont_name, blob_path = get_container_name(old_flexible_blob_url)
                    version_match = re.search(r'/v(\d+)\.txt$', blob_path)
                    if version_match:
                        current_version = int(version_match.group(1))
                        new_version = current_version + 1
                        new_blob_path = re.sub(r'/v\d+\.txt$', f'/v{new_version}.txt', blob_path)
                    else:
                        # No version in path, add one
                        new_blob_path = blob_path.replace('.txt', '/v1.txt') if blob_path.endswith('.txt') else f"{blob_path}/v1.txt"
                    
                    new_flexible_blob_url = f"{base_url}/{cont_name}/{new_blob_path}"
                    
                    try:
                        upload_text_to_blob_url(new_flexible_blob_url, new_visual_desc)
                        updates["visual_detailed_description_flexible_blob_url"] = new_flexible_blob_url
                        field_changes.append(
                            {
                                "path": "visual_detailed_description_flexible",
                                "from": (old_text if old_text is not None else "") or "",
                                "to": new_visual_desc,
                            }
                        )
                        logger.info(
                            "Uploaded updated visual description to blob: %s",
                            new_flexible_blob_url
                        )
                    except (ValueError, AzureError, BlobDownloadError, BlobAuthenticationError) as e:
                        logger.exception(
                            "Failed to upload visual description blob for document %s: %s",
                            document_id,
                            str(e),
                        )
                        status = 403 if isinstance(e, BlobAuthenticationError) else 500
                        raise HTTPException(
                            status_code=status,
                            detail=str(e) if isinstance(e, BlobAuthenticationError) else f"Failed to upload visual description to blob storage: {str(e)}",
                        ) from e
                else:
                    # No existing blob URL, create new one
                    # Use document_id to construct path similar to sample data structure
                    container_name = "content-assets"  # Default container name from sample
                    new_blob_path = f"{document_id}/visual/flexible/detailed/v1.txt"
                    # Derive the storage host from configuration so the recorded URL
                    # matches the account uploaded to (upload_to_blob uses the same
                    # AZURE_STORAGE_ACCOUNT_NAME) instead of a hardcoded staging account.
                    storage_account = (settings.azure_storage_account_name or "").strip()
                    if not storage_account:
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                "Storage is not configured: set AZURE_STORAGE_ACCOUNT_NAME "
                                "to record visual description blob URLs."
                            ),
                        )
                    base_url = f"https://{storage_account}.blob.core.windows.net"
                    new_flexible_blob_url = f"{base_url}/{container_name}/{new_blob_path}"
                    
                    try:
                        upload_text_to_blob_url(new_flexible_blob_url, new_visual_desc)
                        updates["visual_detailed_description_flexible_blob_url"] = new_flexible_blob_url
                        field_changes.append(
                            {
                                "path": "visual_detailed_description_flexible",
                                "from": "",
                                "to": new_visual_desc,
                            }
                        )
                        logger.info(
                            "Created new visual description blob: %s",
                            new_flexible_blob_url
                        )
                    except (ValueError, AzureError, BlobDownloadError, BlobAuthenticationError) as e:
                        logger.exception(
                            "Failed to create visual description blob for document %s: %s",
                            document_id,
                            str(e),
                        )
                        status = 403 if isinstance(e, BlobAuthenticationError) else 500
                        raise HTTPException(
                            status_code=status,
                            detail=str(e) if isinstance(e, BlobAuthenticationError) else f"Failed to upload visual description to blob storage: {str(e)}",
                        ) from e

        # Only proceed if there are actual changes
        if not field_changes:
            logger.info("No changes detected for document %s", document_id)
            return await _build_document_response(current_doc_obj.model_dump())
        # Update document
        updated_doc = cosmos_service.update_document_with_version(
            document_id=document_id, updates=updates, partition_key=pk, etag=if_match
        )
        
        # Update statistics when document changes
        # Use raw documents (not Pydantic-converted) to preserve metadata structure
        try:
            stats_service = get_statistics_service()
            # Get the raw updated document from Cosmos to preserve metadata format
            updated_doc_raw = cosmos_service.get_document_raw(document_id, pk)
            if updated_doc_raw and current_doc_raw:
                logger.info(
                    "Updating statistics for document %s: old_status=%s, new_status=%s",
                    document_id,
                    current_doc_raw.get("archivist_status", "pending"),
                    updated_doc_raw.get("archivist_status", "pending")
                )
                stats_service.update_on_document_change(
                    old_doc=current_doc_raw,
                    new_doc=updated_doc_raw
                )
                logger.info("Successfully updated statistics for document %s", document_id)
        except (KeyError, TypeError, ValueError) as stats_error:
            # Don't fail the update if statistics update fails
            logger.warning(
                "Failed to update statistics for document %s: %s",
                document_id,
                str(stats_error),
                exc_info=True
            )

        # Handle approval method transitions based on status changes
        updated_doc_dict = updated_doc.model_dump()
        
        old_status = normalize_archivist_status(current_doc.get("archivist_status"))
        new_status = normalize_archivist_status(updated_doc_dict.get("archivist_status"))
        
        # Individual approval: transitioning TO "publishing"
        if new_status == "publishing" and old_status != "publishing":
            # Set approval method and send to processing queue
            approval_updates = {"archivist_approval_method": "individual"}
            try:
                updated_doc = cosmos_service.update_document_with_version(
                    document_id=document_id, 
                    updates=approval_updates, 
                    partition_key=pk, 
                    etag=updated_doc._etag
                )
                logger.info("Set archivist_approval_method to 'individual' for document %s", document_id)
                
                # Send to processing queue
                send_to_queue(
                    settings.data_ingestion_queue_name,
                    json.dumps({"document_id": document_id, "operation_type": "individual"}),
                )
                logger.info("Document %s sent to processing queue", document_id)
            except (AzureError, ServiceBusError) as e:
                logger.exception(
                    "Failed to set approval method and queue document %s: %s", 
                    document_id, str(e)
                )
        
        # Undo approval: transitioning FROM "publishing" 
        elif old_status == "publishing" and new_status != "publishing":
            # Clear approval method
            approval_updates = {
                "archivist_approval_method": None,
                "archivist_publish_started_at": None,
            }
            try:
                updated_doc = cosmos_service.update_document_with_version(
                    document_id=document_id, 
                    updates=approval_updates, 
                    partition_key=pk, 
                    etag=updated_doc._etag
                )
                logger.info("Cleared archivist_approval_method for document %s", document_id)
            except AzureError as e:
                logger.exception(
                    "Failed to clear approval method for document %s: %s", 
                    document_id, str(e)
                )

        # Create audit entry
        try:
            audit_container = cosmos_service.get_audit_container()
            audit_service = AuditService(audit_container)

            new_version = updated_doc.model_dump().get("version", 1)

            logger.info(
                "Creating audit entry for document %s, version %s, %s field changes",
                document_id,
                new_version,
                len(field_changes),
            )

            audit_entry = audit_service.create_audit_entry(
                entity_id=document_id,
                user_id=current_user.user_id,
                user_display=current_user.user_display,
                operation="update",
                version=new_version,
                changed_fields=field_changes,
                correlation_id=update_request.correlation_id,
            )
            logger.info("Successfully created audit entry: %s", audit_entry.get("id"))
        except ValueError as audit_error:
            logger.warning("Audit logging disabled or failed: %s", audit_error)
        except Exception as audit_error:
            logger.exception("Failed to create audit entry: %s", audit_error)

        return await _build_document_response(updated_doc.model_dump())

    except HTTPException:
        raise
    except DocumentVersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        logger.exception("Invalid request: %s", str(e))
        _raise_document_save_value_error(e)
    except Exception as e:
        logger.exception("Failed to update document: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to update document: {str(e)}"
        ) from e


@router.get("/documents/publishing/stuck")
async def list_stuck_publishing_documents(
    threshold_minutes: int = Query(20, ge=1, le=1440),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    List documents stuck in archivist_status=publishing longer than threshold_minutes.
    Admin only. Uses archivist_publish_started_at when present, else updated_at.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    try:
        cosmos_service = get_cosmos_service()
        rows = cosmos_service.list_stuck_publishing_documents(threshold_minutes)
        return {
            "threshold_minutes": threshold_minutes,
            "count": len(rows),
            "documents": rows,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to list stuck publishing documents: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/documents/publishing/reconcile")
async def reconcile_stuck_publishing_documents(
    body: ReconcileStuckPublishingRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Update documents stuck in publishing: set status to failed (with message) or reviewed.
    Admin only.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    try:
        cosmos_service = get_cosmos_service()
        reason = (
            f"manual reconcile by {current_user.user_display}"
            if body.action == "failed"
            else ""
        )
        result = cosmos_service.reconcile_stuck_publishing_documents(
            threshold_minutes=body.threshold_minutes,
            action=body.action,
            reason_suffix=reason,
        )
        logger.info(
            "Stuck publishing reconcile by %s: %s",
            current_user.user_display,
            result,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to reconcile stuck publishing: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch(
    "/documents/{document_id}/assets/{asset_id}/ocr",
    response_model=DocumentMetadata,
    response_model_exclude_none=True,
)
async def update_ocr_text(
    document_id: str,
    asset_id: str,
    ocr_request: OcrUpdateRequest = Body(...),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
    if_match: Optional[str] = Header(
        default=None, description="ETag for optimistic concurrency"
    ),
    partition_key: Optional[str] = Query(
        default=None, description="Partition key (defaults to document_id)"
    ),
):
    """
    Update OCR text for a specific asset with audit trail.

    Args:
        document_id: Document ID
        asset_id: Asset ID
        ocr_request: OCR update request with new text
        current_user: Authenticated user (injected by dependency)
        partition_key: Partition key value (defaults to document_id)
    Returns:
        Updated document metadata
    """
    try:

        text_to_save = (ocr_request.ocr_text or "").strip()
        if not text_to_save:
            raise ValueError("ocr_text is empty or whitespace only")

        cosmos_service = get_cosmos_service()
        pk = partition_key if partition_key is not None else document_id

        # Get current document
        current_doc_obj = cosmos_service.get_document_by_id(document_id, pk)
        if current_doc_obj is None:
            raise HTTPException(
                status_code=404, detail=f"Document '{document_id}' not found"
            )

        current_doc = current_doc_obj.model_dump()

        # Find the asset index in asset_details for updating
        asset_details = current_doc.get("asset_details", [])
        asset_index = None
        for idx, asset in enumerate(asset_details):
            if not isinstance(asset, dict):
                continue
            if asset.get("asset_id") == asset_id or asset.get("id") == asset_id:
                asset_index = idx
                break

        if asset_index is None:
            raise HTTPException(
                status_code=404,
                detail=f"Asset '{asset_id}' not found in document '{document_id}'",
            )
        current_version = current_doc.get("version", 1)
        new_version = current_version + 1

        old_flexible_blob_url = (
            asset_details[asset_index]
            .get("ocr_result", {})
            .get("ocr_text_flexible_blob_url")
        )

        old_text = ""
        if old_flexible_blob_url:
            try:
                old_text = await download_blob_text(old_flexible_blob_url)
            except (OSError, asyncio.TimeoutError, BlobDownloadError, BlobNotFoundError) as e:
                logger.warning(
                    "Could not download existing OCR blob for document %s, asset %s: %s",
                    document_id,
                    asset_id,
                    str(e),
                )
                old_text = None

        if old_text is not None and old_text.strip() == text_to_save:
            logger.info(
                "No OCR text changes detected for document %s, asset %s",
                document_id,
                asset_id,
            )
            return await _build_document_response(current_doc_obj.model_dump())

        # Upload new version of flexible blob before Cosmos (avoid URL pointing at missing/empty blob)
        new_blob_path = f"{document_id}/ocr/flexible/{asset_id}/v{new_version}.txt"
        base_url, cont_name, _ = get_container_name(old_flexible_blob_url or "")
        if not base_url or not cont_name:
            storage_account = (settings.azure_storage_account_name or "").strip()
            if not storage_account:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Storage is not configured: set AZURE_STORAGE_ACCOUNT_NAME "
                        "to record OCR blob URLs."
                    ),
                )
            base_url = f"https://{storage_account}.blob.core.windows.net"
            cont_name = "content-assets"
        new_flexible_blob_url = f"{base_url}/{cont_name}/{new_blob_path}"

        try:
            upload_text_to_blob_url(new_flexible_blob_url, text_to_save)
        except (ValueError, AzureError, BlobDownloadError, BlobAuthenticationError) as e:
            logger.exception(
                "Failed to upload OCR blob for record=%s, asset=%s: %s",
                document_id,
                asset_id,
                e,
            )
            status = 403 if isinstance(e, BlobAuthenticationError) else 500
            raise HTTPException(
                status_code=status,
                detail=str(e) if isinstance(e, BlobAuthenticationError) else f"Failed to upload OCR text to blob storage: {str(e)}",
            ) from e

        # Re-read after blob upload and merge OCR URL into live asset_details (cd3bc88 replace path).
        fresh_obj = cosmos_service.get_document_by_id(document_id, pk)
        if fresh_obj is None:
            raise HTTPException(
                status_code=404, detail=f"Document '{document_id}' not found"
            )
        fresh_doc = fresh_obj.model_dump()
        fresh_asset_details = copy.deepcopy(fresh_doc.get("asset_details", []))
        patched_asset_index = _find_asset_index(fresh_asset_details, asset_id)
        if patched_asset_index is None:
            raise HTTPException(
                status_code=404,
                detail=f"Asset '{asset_id}' not found in document '{document_id}'",
            )
        if not isinstance(fresh_asset_details[patched_asset_index].get("ocr_result"), dict):
            fresh_asset_details[patched_asset_index]["ocr_result"] = {}
        fresh_asset_details[patched_asset_index]["ocr_result"][
            "ocr_text_flexible_blob_url"
        ] = new_flexible_blob_url

        try:
            updated_doc = cosmos_service.update_document_with_version(
                document_id=document_id,
                updates={"asset_details": fresh_asset_details},
                partition_key=pk,
                etag=if_match,
            )
        except DocumentVersionConflictError:
            logger.warning(
                "OCR Cosmos update conflict for document %s asset %s (client If-Match=%s)",
                document_id,
                asset_id,
                if_match,
            )
            logger.error(
                "Cosmos update failed after OCR blob upload (orphan blob): %s",
                new_blob_path,
            )
            raise
        except ValueError:
            logger.error(
                "Cosmos update failed after OCR blob upload (orphan blob): %s",
                new_blob_path,
            )
            raise

        new_version = updated_doc.version or new_version

        # Create audit entry from old flexible URL to new flexible URL

        field_changes = [
            {
                "path": f"/asset_details/{patched_asset_index}/ocr_result/ocr_text",
                "from": (old_text if old_text is not None else "") or "",
                "to": text_to_save,
            }
        ]

        try:
            audit_container = cosmos_service.get_audit_container()
            audit_service = AuditService(audit_container)

            logger.info(
                "Creating audit entry for OCR update",
                extra={
                    "document_id": document_id,
                    "asset_id": asset_id,
                    "version": new_version,
                    "user_id": current_user.user_id,
                },
            )

            audit_entry = audit_service.create_audit_entry(
                entity_id=document_id,
                user_id=current_user.user_id,
                user_display=current_user.user_display,
                operation="update",
                version=new_version,
                changed_fields=field_changes,
                correlation_id=ocr_request.correlation_id,
            )

            logger.info(
                "Successfully created audit entry",
                extra={
                    "audit_entry_id": audit_entry.get("id"),
                    "document_id": document_id,
                },
            )
        except ValueError as audit_error:
            logger.warning(
                "Audit logging disabled or failed",
                extra={"error": str(audit_error), "document_id": document_id},
            )
        except (RuntimeError, ConnectionError) as audit_error:
            logger.exception(
                "Failed to create audit entry",
                extra={"error": str(audit_error), "document_id": document_id},
                exc_info=True,
            )

        return await _build_document_response(updated_doc.model_dump())

    except HTTPException:
        raise
    except DocumentVersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        logger.exception(
            "Invalid request for OCR update",
            extra={"error": str(e), "document_id": document_id},
            exc_info=True,
        )
        _raise_document_save_value_error(e)
    except Exception as e:
        logger.exception(
            "Failed to update OCR text",
            extra={"error": str(e), "document_id": document_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to update OCR text: {str(e)}"
        ) from e


@router.get("/documents/{document_id}/audit", response_model=AuditHistoryResponse)
async def get_audit_history(
    document_id: str,
    max_items: Optional[int] = Query(
        default=None, description="Maximum number of audit entries to return"
    ),
):
    """
    Get audit history for a specific document.

    Args:
        document_id: Document ID
        max_items: Maximum number of entries to return

    Returns:
        Audit history entries
    """
    try:
        logger.info(
            "Fetching audit history for document: %s, max_items: %s",
            document_id,
            max_items,
        )
        cosmos_service = get_cosmos_service()
        audit_container = cosmos_service.get_audit_container()
        audit_service = AuditService(audit_container)

        entries = audit_service.get_audit_history(
            entity_id=document_id, max_items=max_items
        )

        logger.info("Found %s audit entries for document %s", len(entries), document_id)

        hydration_tasks = []
        for entry in entries:
            for field in entry.get("fields", []):
                hydration_tasks.append(
                    _hydrate_audit_field(field, "from"),
                )
                hydration_tasks.append(
                    _hydrate_audit_field(field, "to"),
                )
        if hydration_tasks:
            await asyncio.gather(*hydration_tasks)

        return AuditHistoryResponse(
            entity_id=document_id, entries=entries, count=len(entries)
        )

    except ValueError as e:
        logger.exception("Audit container not configured: %s", str(e))
        raise HTTPException(
            status_code=503, detail="Audit service not available"
        ) from e
    except Exception as e:
        logger.exception(
            "Failed to retrieve audit history for document %s: %s",
            document_id,
            str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve audit history: {str(e)}"
        ) from e


# =============================================================================
# EPUB Processing Endpoints
# =============================================================================


# =============================================================================
# EPUB Workflow Endpoints (Upload -> Parse via Function -> Select -> Ingest)
# =============================================================================


@router.post("/epub/upload", response_model=EpubUploadResponse)
async def upload_epub(
    file: UploadFile = File(...),
    opf_file: Optional[UploadFile] = File(default=None),
    overwrite: bool = Query(default=False, description="Overwrite existing document with same filename"),
):
    """
    Upload an EPUB file with optional OPF metadata file for processing.

    Flow:
    1. Generate UUID for new document
    2. Check if document with same filename exists
    3. If exists and overwrite=false, return 409 Conflict
    4. If exists and overwrite=true, delete existing document first
    5. Upload EPUB file to Azure Storage
    6. Upload OPF file to Azure Storage (if provided)
    7. Create entry in CosmosDB with 'uploaded' status
    8. Send message to queue for processing

    Args:
        file: The EPUB file
        opf_file: Optional OPF metadata file (metadata.opf)
        overwrite: If true, overwrite existing document with same filename

    Returns:
        Upload response with document ID
    """
    # Log received files
    logger.info("Upload request - EPUB file: %s, overwrite: %s", 
               file.filename if file else "None", overwrite)
    opf_filename = opf_file.filename if opf_file and opf_file.filename else "None"
    logger.info("Upload request - OPF file: %s", opf_filename)

    if not file.filename or not file.filename.lower().endswith('.epub'):
        raise HTTPException(status_code=400, detail="File must be an EPUB file")

    if opf_file and opf_file.filename:
        logger.info("OPF file provided: %s", opf_file.filename)
        if not opf_file.filename.lower().endswith('.opf'):
            raise HTTPException(
                status_code=400,
                detail="Metadata file must be an OPF file"
            )
    else:
        logger.info("No OPF file provided")

    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Empty file uploaded")

        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Check if document with same filename already exists
        epub_service = get_epub_document_service()
        existing_doc = epub_service.get_document_by_filename(file.filename)
        
        if existing_doc:
            logger.info("Found existing document with filename: %s (id: %s)", file.filename, existing_doc.get("id"))
        
        if existing_doc and not overwrite:
            # Return 409 Conflict to indicate duplicate
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Document with same filename already exists",
                    "existing_document": {
                        "id": existing_doc.get("id"),
                        "filename": existing_doc.get("filename"),
                        "status": existing_doc.get("status"),
                        "created_at": existing_doc.get("created_at")
                    },
                    "message": "Set overwrite=true to replace the existing document"
                }
            )
        
        if existing_doc and overwrite:
            # Send delete message to queue first - will clean up search index and blobs
            existing_doc_id = existing_doc.get("id")
            logger.info("Overwriting existing document %s, sending delete request...", existing_doc_id)
            delete_message = json.dumps({
                "document_id": existing_doc_id,
                "action": "delete"
            })
            send_to_queue(settings.epub_queue_name, delete_message)
            logger.info("Sent delete request to queue for document: %s", existing_doc_id)
        
        # Generate new UUID for the document
        doc_id = str(uuid.uuid4())

        # Upload EPUB to blob storage (single container, organized by document ID)
        blob_path = f"{doc_id}/{file.filename}"
        blob_url = upload_binary_to_blob(
            container_name=settings.epub_storage_container_name,
            blob_path=blob_path,
            content=content,
            content_type="application/epub+zip"
        )
        logger.info("Uploaded EPUB to blob: %s", blob_url)

        # Upload OPF file if provided
        opf_blob_url = None
        logger.info("Checking OPF file - opf_file=%s, has_filename=%s",
                   opf_file is not None,
                   opf_file.filename if opf_file else "N/A")
        if opf_file and opf_file.filename:
            opf_content = await opf_file.read()
            logger.info("OPF content size: %d bytes", len(opf_content))
            if len(opf_content) > 0:
                opf_blob_path = f"{doc_id}/{opf_file.filename}"
                opf_blob_url = upload_binary_to_blob(
                    container_name=settings.epub_storage_container_name,
                    blob_path=opf_blob_path,
                    content=opf_content,
                    content_type="application/xml"
                )
                logger.info("Uploaded OPF to blob: %s", opf_blob_url)
            else:
                logger.warning("OPF file was empty!")
        else:
            logger.info("No OPF file to upload")

        # Create CosmosDB entry
        epub_doc = {
            "id": doc_id,
            "filename": file.filename,
            "blob_url": blob_url,
            "opf_url": opf_blob_url,  # Store OPF URL
            "status": "uploaded",
            "created_at": timestamp,
            "updated_at": timestamp,
            "file_size": len(content),
        }

        epub_service.upsert_document(epub_doc)
        logger.info("Created EPUB document in CosmosDB: %s", doc_id)
        logger.info("Document saved with opf_url: %s", epub_doc.get("opf_url"))

        # Send to processing queue
        queue_message = json.dumps({
            "document_id": doc_id,
            "action": "parse"
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Sent EPUB to processing queue: %s", doc_id)

        return EpubUploadResponse(
            id=doc_id,
            filename=file.filename,
            status="uploaded",
            message="Files uploaded successfully. Processing will begin shortly."
        )

    except HTTPException:
        # Re-raise HTTP exceptions (like 409 Conflict) without modification
        raise
    except Exception as e:
        logger.exception("Failed to upload EPUB: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to upload EPUB: {str(e)}"
        ) from e


@router.get("/epub/documents", response_model=EpubDocumentList)
async def list_epub_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = Query(default=None),
):
    """
    List all EPUB documents with pagination.
    """
    try:
        epub_service = get_epub_document_service()
        
        if not epub_service.container_exists():
            return EpubDocumentList(documents=[], count=0, page=page, page_size=page_size)
        
        documents, total_count, total_pages = epub_service.list_documents(
            status=status, page=page, page_size=page_size
        )

        return EpubDocumentList(
            documents=documents,
            count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )

    except Exception as e:
        logger.exception("Failed to list EPUB documents: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to list EPUB documents: {str(e)}"
        ) from e


@router.get("/epub/documents/{document_id}", response_model=EpubDocument)
async def get_epub_document(document_id: str):
    """
    Get a specific EPUB document by ID.
    Downloads sections from blob storage if available.
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        # Download sections from blob storage if sections_url exists
        sections_url = doc.get("sections_url")
        if sections_url:
            try:
                sections = await download_json_from_blob(sections_url)
                doc["sections"] = sections

                # Count total including children
                def count_with_children(secs):
                    total = len(secs)
                    for s in secs:
                        if s.get("children"):
                            total += count_with_children(s["children"])
                    return total

                total_count = count_with_children(sections)
                logger.info(
                    "Downloaded %d sections (%d total with children) from blob for %s",
                    len(sections), total_count, document_id
                )
            except Exception as blob_err:
                logger.warning(
                    "Failed to download sections from blob for %s: %s",
                    document_id, str(blob_err)
                )
                # Return doc without sections if blob download fails
                doc["sections"] = []

        return doc

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get EPUB document: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get EPUB document: {str(e)}"
        ) from e


@router.patch("/epub/documents/{document_id}/sections")
async def update_epub_sections(
    document_id: str,
    selection: EpubSectionSelection = Body(...),
):
    """
    Update section selections for an EPUB document.
    Downloads sections from blob, updates selections, and re-uploads.
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        if doc.get("status") != "parsed":
            raise HTTPException(
                status_code=400,
                detail="Document must be in 'parsed' status to update sections"
            )

        # Download sections from blob storage
        sections_url = doc.get("sections_url")
        if not sections_url:
            raise HTTPException(
                status_code=400,
                detail="Document has no sections_url - sections not parsed yet"
            )

        sections = await download_json_from_blob(sections_url)

        # Build selection map from request
        selection_map = {s["order"]: s["selected"] for s in selection.sections}

        # Recursive function to update section selections including children
        def update_section_selection(section_list):
            for section in section_list:
                if section["order"] in selection_map:
                    section["selected"] = selection_map[section["order"]]
                # Also update children
                if section.get("children"):
                    update_section_selection(section["children"])

        update_section_selection(sections)

        # Calculate selected counts (including children)
        def count_selected(section_list):
            count = 0
            words = 0
            for section in section_list:
                if section.get("selected", False):
                    count += 1
                    words += section.get("word_count", 0)
                if section.get("children"):
                    child_count, child_words = count_selected(section["children"])
                    count += child_count
                    words += child_words
            return count, words

        selected_count, selected_words = count_selected(sections)

        # Re-upload sections to blob storage
        upload_json_to_blob(
            settings.epub_storage_container_name,
            f"{document_id}/sections.json",
            sections
        )

        # Update only counts in CosmosDB (not sections)
        doc["selected_sections"] = selected_count
        doc["selected_word_count"] = selected_words

        epub_service.upsert_document(doc)

        return {"status": "success", "message": "Sections updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update EPUB sections: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to update sections: {str(e)}"
        ) from e


@router.get("/epub/filter-defaults")
async def get_epub_filter_defaults():
    """Get default filter configuration from epub_filter_config.json."""
    try:
        import os
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "functions", "helper", "epub_filter_config.json"
        )

        # Try to load from file, fallback to defaults
        defaults = {
            "exclude_tags": [
                "script", "style", "head", "meta", "link", "nav",
                "aside", "footer", "figcaption", "figure", "caption", "img"
            ],
            "exclude_classes": [
                "caption", "image-caption", "img-caption", "photo-caption",
                "figure-caption", "wp-caption", "gallery-caption", "media-caption",
                "sidebar", "advertisement", "ad-container", "footnote", "endnote",
                "page-number", "header", "footer", "navigation", "toc",
                "table-of-contents", "figure"
            ],
            "exclude_ids": [
                "toc", "table-of-contents", "navigation", "sidebar",
                "footer", "header", "advertisement"
            ],
            "class_patterns": [
                "caption", "sidebar", "ad-", "advertisement", "footnote", "endnote"
            ]
        }

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                defaults["exclude_tags"] = file_config.get("exclude_tags", defaults["exclude_tags"])
                defaults["exclude_classes"] = file_config.get("exclude_classes", defaults["exclude_classes"])
                defaults["exclude_ids"] = file_config.get("exclude_ids", defaults["exclude_ids"])
                defaults["class_patterns"] = file_config.get("class_patterns", defaults["class_patterns"])

        return defaults

    except Exception as e:
        logger.exception("Failed to get filter defaults: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get filter defaults: {str(e)}"
        ) from e


@router.get("/epub/documents/{document_id}/filter-config")
async def get_epub_filter_config(document_id: str):
    """Get filter configuration for an EPUB document."""
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        # Return document's custom config or null (frontend will use defaults)
        return {
            "filter_config": doc.get("filter_config"),
            "has_custom_config": doc.get("filter_config") is not None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get EPUB filter config: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get filter config: {str(e)}"
        ) from e


@router.patch("/epub/documents/{document_id}/filter-config")
async def update_epub_filter_config(
    document_id: str,
    config: EpubFilterConfig = Body(...)
):
    """Update filter configuration for an EPUB document."""
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        # Can update config before extraction or at validation stage
        if doc.get("status") not in ("uploaded", "parsed", "validate"):
            raise HTTPException(
                status_code=400,
                detail="Can only update filter config before extraction or at validation"
            )

        # Update filter config
        doc["filter_config"] = config.model_dump()
        epub_service.upsert_document(doc)

        return {"status": "success", "message": "Filter config updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update filter config: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to update filter config: {str(e)}"
        ) from e


@router.post("/epub/documents/{document_id}/re-extract")
async def re_extract_epub_document(document_id: str):
    """
    Re-extract text for an EPUB document at validation stage.
    
    Used when user wants to change filters and re-extract content.
    Resets from 'validate' back to 'extracting' and re-runs extraction.
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        if doc.get("status") != "validate":
            raise HTTPException(
                status_code=400,
                detail=f"Can only re-extract documents in 'validate' status (current: {doc.get('status')})"
            )

        # Check sections exist
        sections_url = doc.get("sections_url")
        if not sections_url:
            raise HTTPException(
                status_code=400,
                detail="Document has no sections - cannot re-extract"
            )

        # Reset status to extracting
        doc["status"] = "extracting"
        # Clear previous extraction results
        doc["original_text_url"] = None
        doc["filtered_text_url"] = None
        doc["extracted_at"] = None
        epub_service.upsert_document(doc)

        # Send to queue for extraction
        queue_message = json.dumps({
            "document_id": document_id,
            "action": "extract"
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Re-extracting EPUB: %s", document_id)

        return {
            "status": "success",
            "message": "Re-extraction started with updated filters"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to re-extract EPUB: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to re-extract: {str(e)}"
        ) from e


@router.post("/epub/documents/{document_id}/extract")
async def extract_epub_document(
    document_id: str,
    enable_ocr: bool = Query(default=False, description="Enable OCR for image-based pages")
):
    """
    Start text extraction for selected sections from an EPUB document.

    This extracts both original and filtered text and sets status to 'validate'.
    User can then review both versions before approving for ingestion.

    Args:
        document_id: The document ID
        enable_ocr: If true, use OCR to extract text from images in the EPUB

    Flow: parsed -> extracting -> validate
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        if doc.get("status") != "parsed":
            raise HTTPException(
                status_code=400,
                detail="Document must be in 'parsed' status to start extraction"
            )

        # Download sections from blob storage to check selections
        sections_url = doc.get("sections_url")
        if not sections_url:
            raise HTTPException(
                status_code=400,
                detail="Document has no sections - parsing may have failed"
            )

        sections = await download_json_from_blob(sections_url)

        # Count selected sections (including nested children)
        def count_selected(section_list):
            count = 0
            for s in section_list:
                if s.get("selected", False):
                    count += 1
                if s.get("children"):
                    count += count_selected(s["children"])
            return count

        selected_count = count_selected(sections)

        if selected_count == 0:
            raise HTTPException(
                status_code=400,
                detail="No sections selected for extraction"
            )

        # Update status to extracting and store OCR preference
        doc["status"] = "extracting"
        doc["enable_ocr"] = enable_ocr
        epub_service.upsert_document(doc)

        # Send to extraction queue with OCR flag
        queue_message = json.dumps({
            "document_id": document_id,
            "action": "extract",
            "enable_ocr": enable_ocr
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Sent EPUB for extraction: %s (OCR: %s)", document_id, enable_ocr)

        return {
            "status": "success",
            "message": f"Extraction started for {selected_count} sections" + (" with OCR" if enable_ocr else ""),
            "selected_sections": selected_count,
            "ocr_enabled": enable_ocr
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to start EPUB extraction: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to start extraction: {str(e)}"
        ) from e


@router.post("/epub/documents/{document_id}/approve")
async def approve_epub_document(document_id: str):
    """
    Approve an EPUB document for ingestion after validation.

    User has reviewed the extracted text and approves it for processing.
    This changes status from 'validate' to 'processing' and starts ingestion.

    Flow: validate -> processing -> completed
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        if doc.get("status") != "validate":
            raise HTTPException(
                status_code=400,
                detail=f"Document must be in 'validate' status to approve "
                       f"(current: {doc.get('status')})"
            )

        # Verify extracted text exists
        filtered_text_url = doc.get("filtered_text_url")
        if not filtered_text_url:
            raise HTTPException(
                status_code=400,
                detail="Document has no extracted text - extraction may have failed"
            )

        # Update status to processing
        doc["status"] = "processing"
        doc["validated_at"] = datetime.now(timezone.utc).isoformat()
        epub_service.upsert_document(doc)

        # Send to ingestion queue
        queue_message = json.dumps({
            "document_id": document_id,
            "action": "ingest"
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Sent approved EPUB for ingestion: %s", document_id)

        return {
            "status": "success",
            "message": "Document approved and ingestion started",
            "original_word_count": doc.get("original_word_count", 0),
            "filtered_word_count": doc.get("filtered_word_count", 0)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to approve EPUB: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to approve document: {str(e)}"
        ) from e


@router.post("/epub/documents/{document_id}/retry")
async def retry_epub_document(document_id: str):
    """
    Retry processing for a failed EPUB document.

    Resets the document status and re-queues it for processing.
    Can retry from any error state.
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        current_status = doc.get("status")
        if current_status != "error":
            raise HTTPException(
                status_code=400,
                detail=f"Can only retry documents in 'error' status (current: {current_status})"
            )

        # Determine which step to retry based on what's been completed
        # If we have sections_url, parsing was done - retry extraction
        # If we have filtered_text_url, extraction was done - retry ingestion
        # Otherwise, retry parsing
        sections_url = doc.get("sections_url")
        filtered_text_url = doc.get("filtered_text_url")

        if filtered_text_url:
            # Extraction completed, retry ingestion
            new_status = "processing"
            action = "ingest"
            message = "Retrying ingestion"
        elif sections_url:
            # Parsing completed, retry extraction
            new_status = "extracting"
            action = "extract"
            message = "Retrying extraction"
        else:
            # Retry from the beginning - parsing
            new_status = "parsing"
            action = "parse"
            message = "Retrying parsing"

        # Clear error and update status
        doc["status"] = new_status
        doc["error_message"] = None
        epub_service.upsert_document(doc)

        # Send to queue
        queue_message = json.dumps({
            "document_id": document_id,
            "action": action
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Retrying EPUB %s with action: %s", document_id, action)

        return {
            "status": "success",
            "message": message,
            "new_status": new_status,
            "action": action
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retry EPUB: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to retry: {str(e)}"
        ) from e


@router.get("/epub/documents/{document_id}/text/{text_type}")
async def get_epub_extracted_text(document_id: str, text_type: str):
    """
    Get extracted text (original or filtered) for an EPUB document.

    text_type must be 'original' or 'filtered'.
    Returns the sections with their extracted content.
    """
    if text_type not in ("original", "filtered"):
        raise HTTPException(
            status_code=400,
            detail="text_type must be 'original' or 'filtered'"
        )

    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        # Get the appropriate URL
        url_field = f"{text_type}_text_url"
        text_url = doc.get(url_field)

        if not text_url:
            raise HTTPException(
                status_code=404,
                detail=f"No {text_type} text available - document may not be extracted yet"
            )

        # Download from blob storage
        sections = await download_json_from_blob(text_url)
        return sections

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get EPUB text: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get text: {str(e)}"
        ) from e


@router.get("/epub/documents/{document_id}/chunks")
async def get_epub_chunks(document_id: str):
    """
    Get indexed chunks for a completed EPUB document.

    Returns the chunks from the epub-chunks CosmosDB container.
    """
    try:
        epub_service = get_epub_document_service()
        chunks = epub_service.get_document_chunks(document_id)
        
        if not chunks:
            # Verify document exists
            doc = epub_service.get_document_by_id(document_id)
            if not doc:
                raise HTTPException(status_code=404, detail="EPUB document not found")
            if doc.get("status") != "completed":
                raise HTTPException(
                    status_code=404,
                    detail="No chunks available - document may not be ingested yet"
                )
        
        return chunks

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get EPUB chunks: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get chunks: {str(e)}"
        ) from e


@router.delete("/epub/documents/{document_id}")
async def delete_epub_document(document_id: str):
    """
    Delete an EPUB document by sending a delete request to the processing queue.
    
    The actual deletion (search index, CosmosDB, blobs) happens in the Azure Function.
    """
    try:
        epub_service = get_epub_document_service()
        doc = epub_service.get_document_by_id(document_id)
        
        if not doc:
            raise HTTPException(status_code=404, detail="EPUB document not found")

        # Update status to 'deleting' to provide UI feedback
        doc["status"] = "deleting"
        epub_service.upsert_document(doc)
        logger.info("Marked document %s for deletion", document_id)

        # Send delete message to processing queue
        queue_message = json.dumps({
            "document_id": document_id,
            "action": "delete"
        })
        send_to_queue(settings.epub_queue_name, queue_message)
        logger.info("Sent delete request to queue for document: %s", document_id)

        return {
            "status": "success", 
            "message": "Document deletion initiated. It will be removed shortly.",
            "document_id": document_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to initiate EPUB document deletion: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to delete document: {str(e)}"
        ) from e


# =============================================================================
# Data Pipeline Monitoring Endpoints
# =============================================================================


@router.get("/pipeline/stages")
async def get_pipeline_stages():
    """
    Get pipeline stages configuration.
    
    Returns the list of pipeline stages with their metadata for UI rendering.
    
    Returns:
        List of pipeline stage configurations
    """
    try:
        service = get_pipeline_service()
        return service.get_pipeline_stages()
    except Exception as e:
        logger.exception("Failed to get pipeline stages: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/pipeline/statistics")
async def get_pipeline_statistics():
    """
    Get aggregated pipeline statistics from the records container.
    
    Counts records by their processing status for each pipeline stage.
    
    Returns:
        Pipeline statistics with counts by stage and status
    """
    try:
        pipeline_service = get_pipeline_service()
        return pipeline_service.get_pipeline_statistics()
    except Exception as e:
        logger.exception("Failed to get pipeline statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get pipeline statistics: {str(e)}"
        ) from e


@router.post("/pipeline/statistics/rebuild")
async def rebuild_pipeline_statistics():
    """
    Rebuild pipeline statistics by querying all records.
    
    This endpoint triggers a full rebuild of the pre-computed pipeline statistics.
    Use this when stats appear stale or after bulk data changes.
    
    Returns:
        Rebuilt pipeline statistics
    """
    try:
        pipeline_service = get_pipeline_service()
        # Run sync Cosmos operations in thread pool to maintain trace context
        # and avoid blocking the event loop
        return await asyncio.to_thread(pipeline_service.rebuild_pipeline_statistics)
    except Exception as e:
        logger.exception("Failed to rebuild pipeline statistics: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to rebuild pipeline statistics: {str(e)}"
        ) from e


@router.get("/pipeline/statistics/status")
async def get_pipeline_statistics_status():
    """
    Get the status of pre-computed pipeline statistics.
    
    Returns:
        Dictionary with exists flag and lastUpdated timestamp
    """
    try:
        pipeline_service = get_pipeline_service()
        return pipeline_service.get_statistics_status()
    except Exception as e:
        logger.exception("Failed to get pipeline statistics status: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get pipeline statistics status: {str(e)}"
        ) from e


@router.get("/pipeline/stages/{stage_id}/errors")
async def get_stage_errors(
    stage_id: str,
    page_number: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100)
):
    """
    Get errors for a specific pipeline stage, grouped by error message.
    
    Args:
        stage_id: The stage ID (e.g., 'related_assets', 'asset_details', 'original_file', 
                  'ocr_batch', 'ocr_processing', 'metadata_extraction')
        page_number: Page number (1-indexed, default 1)
        page_size: Number of errors per page (default 20, max 100)
    
    Returns:
        Dict with stage_id, stage_name, paginated errors list, and totals
    """
    try:
        pipeline_service = get_pipeline_service()
        return pipeline_service.get_stage_errors(stage_id, page_number, page_size)
    except Exception as e:
        logger.exception("Failed to get stage errors for %s: %s", stage_id, str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get stage errors: {str(e)}"
        ) from e


@router.post("/pipeline/stages/{stage_id}/errors/records")
async def get_records_by_error(
    stage_id: str,
    request: dict = Body(...)
):
    """
    Get records that have a specific error message for a pipeline stage.
    
    Args:
        stage_id: The stage ID
        request: { "error_text": "...", "page_number": 1, "page_size": 20 }
    
    Returns:
        Paginated list of records with the specified error
    """
    try:
        error_text = request.get("error_text")
        if not error_text:
            raise HTTPException(status_code=400, detail="error_text is required")
        
        page_number = request.get("page_number", 1)
        page_size = request.get("page_size", 20)
        
        pipeline_service = get_pipeline_service()
        result = pipeline_service.get_records_by_error(
            stage_id, error_text, page_number, page_size
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get records by error for %s: %s", stage_id, str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get records by error: {str(e)}"
        ) from e


# =============================================================================
# Active Pipeline Jobs Management
# =============================================================================

class ActiveJobUpdate(BaseModel):
    """Request body for updating an active job.
    
    The backend saves current_stats based on runtime_status:
    - Completed/Failed/Terminated/Canceled: saves output as current_stats
    - Running/Pending: saves input as current_stats
    """
    runtime_status: str
    custom_status: Optional[Any] = None
    input: Optional[Any] = None
    output: Optional[Any] = None
    last_updated_time: Optional[str] = None


@router.get("/pipeline/jobs")
async def get_active_jobs():
    """
    Get all active pipeline jobs.
    
    Returns:
        List of active job documents
    """
    try:
        pipeline_service = get_pipeline_service()
        jobs = pipeline_service.get_active_jobs()
        normalized_jobs = [
            _normalize_orchestration_urls_in_payload(job) for job in jobs
        ]
        return {"jobs": normalized_jobs}
    except Exception as e:
        logger.exception("Failed to get active jobs: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to get active jobs: {str(e)}"
        ) from e


@router.patch("/pipeline/jobs/{trigger_endpoint:path}")
async def update_active_job(trigger_endpoint: str, job_update: ActiveJobUpdate):
    """
    Update the status of an active job.
    
    The backend saves current_stats based on runtime_status:
    - Completed/Failed/Terminated/Canceled: saves output as current_stats
    - Running/Pending: saves input as current_stats
    
    Args:
        trigger_endpoint: The trigger endpoint (document ID)
        job_update: Fields to update including input (for in-progress) and output (for completed)
        
    Returns:
        Updated job document
    """
    try:
        pipeline_service = get_pipeline_service()
        updated_job = pipeline_service.update_job_status(
            trigger_endpoint,
            job_update.runtime_status,
            job_update.custom_status,
            job_update.input,
            job_update.output,
            job_update.last_updated_time
        )
        if not updated_job:
            raise HTTPException(status_code=404, detail="Job not found")
        if trigger_endpoint == "content-source-periodic-sync":
            oid = updated_job.get("instance_id")
            if oid:
                try:
                    await asyncio.to_thread(
                        record_periodic_sync_finished,
                        str(oid),
                        runtime_status=job_update.runtime_status,
                        output=job_update.output,
                        last_updated_time=job_update.last_updated_time,
                    )
                except Exception:
                    logger.exception(
                        "Failed to merge content-source-periodic-sync completion into periodic_run_history"
                    )
        return updated_job
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update job: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to update job: {str(e)}"
        ) from e


@router.delete("/pipeline/jobs/reset-all")
async def reset_all_jobs():
    """
    Reset all pipeline jobs by deleting all job records from Cosmos DB.
    
    Deletes all documents with ID prefix 'pipeline_step_'.
    
    Returns:
        Dict with deleted_count and error_count
    """
    try:
        pipeline_service = get_pipeline_service()
        result = pipeline_service.reset_all_jobs()
        return {
            "status": "success",
            "deleted_count": result["deleted_count"],
            "error_count": result["error_count"]
        }
    except Exception as e:
        logger.exception("Failed to reset all jobs: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to reset all jobs: {str(e)}"
        ) from e


# =============================================================================
# Orchestration Management Endpoints (Proxy to Azure Durable Functions)
# =============================================================================

class OrchestrationUrlActionRequest(BaseModel):
    """Request body for orchestration management actions (terminate/suspend/resume)."""
    action_url: str
    reason: Optional[str] = None


class OrchestrationStatusRequest(BaseModel):
    """Request body for durable orchestration status polling."""
    status_url: str


_ORCHESTRATION_URL_FIELDS = (
    "status_url",
    "terminate_url",
    "suspend_url",
    "resume_url",
)

_DURABLE_REASON_PLACEHOLDER = "{text}"
_DURABLE_REASON_PLACEHOLDER_ENCODED = "%7Btext%7D"


def _encode_orchestration_query(query: dict[str, list[str]]) -> str:
    """Rebuild query string, preserving Durable {text} reason placeholder unencoded."""
    parts: list[str] = []
    for key, vals in query.items():
        for val in vals:
            if val == _DURABLE_REASON_PLACEHOLDER:
                parts.append(f"{quote(key, safe='')}={_DURABLE_REASON_PLACEHOLDER}")
            else:
                parts.append(f"{quote(key, safe='')}={quote(val, safe='')}")
    return "&".join(parts)


def _resolve_orchestration_function_config(action_url: str) -> tuple[Optional[str], Optional[str]]:
    """Map a durable management URL to the configured Function App base URL and host key."""
    parsed = urlparse(action_url)
    query = parse_qs(parsed.query)
    task_hub = (query.get("taskHub") or [""])[0]
    if (
        task_hub
        and settings.data_ingest_task_hub
        and task_hub == settings.data_ingest_task_hub
        and settings.data_ingest_function_url
    ):
        return (
            settings.data_ingest_function_url.rstrip("/"),
            settings.data_ingest_function_code,
        )
    base = (settings.azure_functions_base_url or "").rstrip("/") or None
    return base, settings.azure_functions_code


def _normalize_orchestration_management_url(action_url: str) -> str:
    """
    Rewrite durable management URLs to the configured Function App host.

    Durable Functions may return management URLs using the request Host header. When
    that host is the shared Application Gateway, terminate/status POSTs hit AGW paths
    that are not routed (403). Always target the private Function App URL from config.
    """
    if not action_url:
        return action_url

    base, code = _resolve_orchestration_function_config(action_url)
    parsed = urlparse(action_url)

    if base:
        parsed_base = urlparse(base)
        # Always target the configured Function App — AGW/public hosts do not route
        # /runtime/webhooks/durableTask/* (403 if left unchanged).
        parsed = parsed._replace(
            scheme=parsed_base.scheme or parsed.scheme,
            netloc=parsed_base.netloc,
        )

    query = parse_qs(parsed.query, keep_blank_values=True)
    if code and not (query.get("code") or [""])[0]:
        query["code"] = [code]

    encoded = _encode_orchestration_query(query)
    return urlunparse(parsed._replace(query=encoded))


def _apply_orchestration_reason(action_url: str, reason: Optional[str]) -> str:
    """
    Durable Functions management URLs include reason={text} as a template placeholder.
    It must be substituted before POST; leaving {text} (or %7Btext%7D after normalization)
    literal yields 403 from the host.
    """
    replacement = quote(reason, safe="") if reason else ""
    for placeholder in (
        _DURABLE_REASON_PLACEHOLDER,
        _DURABLE_REASON_PLACEHOLDER_ENCODED,
        "%7btext%7d",
    ):
        if placeholder in action_url:
            return action_url.replace(placeholder, replacement)
    if not reason:
        return action_url
    parsed = urlparse(action_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["reason"] = [reason]
    encoded = _encode_orchestration_query(query)
    return urlunparse(parsed._replace(query=encoded))


def _prepare_orchestration_action_url(
    action_url: str, reason: Optional[str] = None
) -> str:
    """Substitute reason placeholder, then rewrite host/auth for the Function App."""
    return _normalize_orchestration_management_url(
        _apply_orchestration_reason(action_url, reason)
    )


def _ensure_orchestration_url_auth(action_url: str) -> str:
    """Backward-compatible alias for durable management URL normalization."""
    return _normalize_orchestration_management_url(action_url)


def _function_key_for_orchestration_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    code = (query.get("code") or [None])[0]
    if code:
        return code
    _, config_code = _resolve_orchestration_function_config(url)
    return config_code


async def _call_durable_management_url(method: str, url: str) -> httpx.Response:
    """Call Durable Task management endpoint with host key header + query code."""
    headers: dict[str, str] = {}
    function_key = _function_key_for_orchestration_url(url)
    if function_key:
        headers["x-functions-key"] = function_key
    async with httpx.AsyncClient() as client:
        if method.upper() == "GET":
            return await client.get(url, headers=headers, timeout=30.0)
        return await client.post(url, headers=headers, timeout=30.0)


@router.post("/pipeline/orchestration/status")
async def get_orchestration_status(request: OrchestrationStatusRequest):
    """
    Get orchestration status by proxying to Azure Functions status URL.
    """
    status_url = _ensure_orchestration_url_auth(request.status_url.strip())

    try:
        logger.info("Polling orchestration status: %s", status_url.split("?")[0])
        response = await _call_durable_management_url("GET", status_url)

        if 200 <= response.status_code < 300:
            return response.json()
        logger.warning(
            "Orchestration status returned %s: %s",
            response.status_code,
            response.text[:500],
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to get orchestration status: {response.text}",
        )
    except httpx.RequestError as e:
        logger.exception("Failed to get orchestration status: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to Azure Functions: {str(e)}",
        ) from e


def _normalize_orchestration_urls_in_payload(payload: dict) -> dict:
    """Normalize all durable management URLs in a trigger/job response dict."""
    if not isinstance(payload, dict):
        return payload
    for field in _ORCHESTRATION_URL_FIELDS:
        value = payload.get(field)
        if value:
            payload[field] = _normalize_orchestration_management_url(value)
    return payload


@router.post("/pipeline/orchestration/action")
async def execute_orchestration_action(request: OrchestrationUrlActionRequest):
    """
    Execute an orchestration action (terminate/suspend/resume).
    """
    try:
        url = _prepare_orchestration_action_url(
            request.action_url.strip(),
            request.reason,
        )

        logger.info("Executing orchestration action: %s", url.split("?")[0])

        response = await _call_durable_management_url("POST", url)
        if response.status_code in (200, 202):
            return {"status": "success", "message": "Orchestration action completed"}
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to execute orchestration action: {response.text}"
            )
    except httpx.RequestError as e:
        logger.exception("Failed to execute orchestration action: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Failed to execute orchestration action: {str(e)}"
        ) from e


# =============================================================================
# Pipeline Trigger Routes (Proxy to Azure Functions)
# =============================================================================

class ContentSourceSyncRequest(BaseModel):
    """Request body for Content Source sync trigger."""
    collection_ids: Optional[List[str]] = None
    batch_size: Optional[int] = 100
    parallel_batches: Optional[int] = 20
    skip: Optional[int] = 0
    total: Optional[int] = None


class PeriodicSyncRequest(BaseModel):
    """JSON body for periodic sync (Azure Function ``content-source-periodic-sync``). Mirrors query params where noted in routes."""
    collection_ids: Optional[List[str]] = None
    lookback_hours: Optional[int] = 168
    batch_size: Optional[int] = 100
    parallel_batches: Optional[int] = None
    run_all_stages: Optional[bool] = True
    from_date: Optional[str] = None  # ISO date YYYY-MM-DD (optional window with to_date)
    to_date: Optional[str] = None
    previous_run_id: Optional[str] = None  # Optional lineage when callers chain runs; stored in periodic_run_history.
    periodic_run_origin: Optional[str] = None  # e.g. ``scheduled``, ``adhoc`` (stored in Cosmos ``run_workflow`` / history).
    schedule_id: Optional[str] = None  # Links a triggered run to a saved schedule definition.
    collection_names: Optional[List[str]] = None  # Optional display / future use; stored on history row.


class PeriodicScheduleUpsertRequest(BaseModel):
    """Create or update a periodic ingestion schedule (Cosmos ``entity_type=schedule``)."""
    id: Optional[str] = None
    title: Optional[str] = None
    enabled: Optional[bool] = True
    run_all_stages: Optional[bool] = True
    collection_ids: Optional[List[str]] = None
    collection_names: Optional[List[str]] = None
    batch_size: Optional[int] = 100
    parallel_batches: Optional[int] = 20
    lookback_hours: Optional[int] = 168
    schedule_start_date: Optional[str] = Field(
        "",
        description="From date YYYY-MM-DD (UTC): schedule starts / first eligible next_run_at (or now if past).",
    )
    schedule_start_time_utc: Optional[str] = Field(
        "",
        description="Optional HH:MM 24-hour UTC with schedule_start_date; default 00:00.",
    )
    window_from: Optional[str] = Field("", description="Legacy fixed window start (YYYY-MM-DD)")
    window_to: Optional[str] = Field("", description="Legacy fixed window end (YYYY-MM-DD)")
    recurrence: str = Field(
        "none",
        description="Repeat cadence; also sets ingestion span per run (daily=1d, weekly=7d, monthly=30d, yearly=365d, one-time=1d).",
    )
    first_run_at: Optional[str] = Field(None, description="Legacy: ISO first execution; ignored when schedule_start_date set")
    next_run_at: Optional[str] = None
    ingestion_period_anchor_date: Optional[str] = Field(
        None,
        description="Optional YYYY-MM-DD (UTC): first-ingestion from-date baseline; defaults to save date.",
    )


class StageRequest(BaseModel):
    """Request body for pipeline stage triggers."""
    collection_ids: Optional[List[str]] = None
    batch_size: Optional[int] = 100


class RetryFailedRequest(BaseModel):
    """Request body for retry failed records."""
    step: Optional[str] = "all"  # all, related_assets, asset_details, original_file, ocr_batch, ocr_processing, metadata_extraction
    max_retries: Optional[int] = 3
    batch_size: Optional[int] = 50
    collection_ids: Optional[List[str]] = None


async def _trigger_azure_function(
    endpoint: str, 
    payload: dict,
    function_app: str = "pipeline"
) -> dict:
    """
    Proxy request to Azure Functions.
    
    Args:
        endpoint: The function endpoint (e.g., 'content-source-sync-client', 'bulk-ingest')
        payload: Request body to send
        function_app: Which function app to use:
            - "pipeline" (default): Main pipeline function app
            - "bulk_ingest": Dedicated bulk ingestion function app
            - "search_index_restore": Dedicated search index restore function app
        
    Returns:
        Normalized response with instance_id and status_url
    """
    # Select the appropriate function app URL and code based on function_app parameter
    if function_app == "data_ingest":
        base_url = settings.data_ingest_function_url
        code = settings.data_ingest_function_code
    else:
        # Default to pipeline function app
        base_url = settings.azure_functions_base_url
        code = settings.azure_functions_code

    if not base_url:
        raise HTTPException(
            status_code=500,
            detail=f"Azure Functions base URL not configured for {function_app}"
        )

    # content-source-periodic-sync (periodic sync): mirror browser/curl GET URLs (query params). Data Foundations
    # merges query string with POST JSON (JSON wins on conflict). Single collection_ids
    # query key only — use JSON array string for multiple values (see parse_collection_ids).
    query_pairs: List[tuple[str, str]] = []
    if code:
        query_pairs.append(("code", code))
    if endpoint == "content-source-periodic-sync":
        if payload.get("lookback_hours") is not None:
            query_pairs.append(("lookback_hours", str(int(payload["lookback_hours"]))))
        if "run_all_stages" in payload and payload["run_all_stages"] is not None:
            query_pairs.append(
                ("run_all_stages", "true" if payload["run_all_stages"] else "false")
            )
        if payload.get("batch_size") is not None:
            query_pairs.append(("batch_size", str(int(payload["batch_size"]))))
        if payload.get("parallel_batches") is not None:
            query_pairs.append(
                ("parallel_batches", str(int(payload["parallel_batches"])))
            )
        collection_ids = [
            str(fv).strip()
            for fv in (payload.get("collection_ids") or [])
            if fv is not None and str(fv).strip() != ""
        ]
        if len(collection_ids) == 1:
            query_pairs.append(("collection_ids", collection_ids[0]))
        elif len(collection_ids) > 1:
            query_pairs.append(("collection_ids", json.dumps(collection_ids)))
        fd = payload.get("from_date")
        td = payload.get("to_date")
        if fd is not None and str(fd).strip() != "":
            query_pairs.append(("from_date", str(fd).strip()))
        if td is not None and str(td).strip() != "":
            query_pairs.append(("to_date", str(td).strip()))
        prev = payload.get("previous_run_id")
        if prev is not None and str(prev).strip() != "":
            query_pairs.append(("previous_run_id", str(prev).strip()))
        pro = payload.get("periodic_run_origin")
        if pro is not None and str(pro).strip() != "":
            query_pairs.append(("periodic_run_origin", str(pro).strip()))
        sid = payload.get("schedule_id")
        if sid is not None and str(sid).strip() != "":
            query_pairs.append(("schedule_id", str(sid).strip()))

    url = f"{base_url}/api/{endpoint}"
    if query_pairs:
        url = f"{url}?{urlencode(query_pairs)}"

    logger.info("Triggering Azure Function [%s]: %s", function_app, url)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                timeout=60.0
            )
            
        if response.status_code in (200, 202):
            data = response.json()
            logger.info("Azure Function response: %s", data)
            
            # Normalize Azure Durable Functions response to expected format
            # Azure returns: { id, statusQueryGetUri, ... }
            # Frontend expects: { instance_id, status_url }
            
            # Check if this is an early return (e.g., no documents found)
            if "message" in data and "id" not in data and "statusQueryGetUri" not in data:
                logger.warning(
                    "Azure Function returned non-orchestration response: %s", 
                    data.get("message")
                )
                # Return the message but with null URLs - caller should handle this
                return {
                    "instance_id": None,
                    "status_url": None,
                    "terminate_url": None,
                    "suspend_url": None,
                    "resume_url": None,
                    "message": data.get("message"),
                    "total_documents": data.get("total_documents", 0)
                }

            return _normalize_orchestration_urls_in_payload({
                "instance_id": data.get("id") or data.get("instance_id"),
                "status_url": data.get("statusQueryGetUri") or data.get("status_url"),
                # Include management URLs for orchestration control
                "terminate_url": data.get("terminatePostUri"),
                "suspend_url": data.get("suspendPostUri"),
                "resume_url": data.get("resumePostUri"),
            })
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Azure Function error: {response.text}"
            )
    except httpx.RequestError as e:
        logger.exception("Failed to call Azure Function %s: %s", endpoint, str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to call Azure Function: {str(e)}"
        ) from e


@router.post("/pipeline/trigger/{endpoint}")
async def trigger_pipeline_stage(
    endpoint: str,
    request: dict = Body(default={}),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Generic endpoint to trigger any pipeline stage.
    
    Args:
        endpoint: The Azure Function endpoint name (directly from stage config)
        request: Optional parameters for the stage
        
    Returns:
        instance_id and status_url for the orchestration
    """
    result = await _trigger_azure_function(endpoint, request)
    # Periodic sync (content-source-periodic-sync): persist run metadata to Cosmos for batch/history and auditing.
    if endpoint == "content-source-periodic-sync" and result.get("instance_id"):
        try:
            await asyncio.to_thread(
                record_periodic_sync_started,
                str(result["instance_id"]),
                request,
                result,
                current_user.user_display,
            )
        except Exception:
            logger.exception("Failed to record periodic sync run in periodic_run_history")
    return result


@router.get("/pipeline/periodic-runs", response_model=Dict[str, Any])
async def list_pipeline_periodic_runs(
    limit: int = Query(50, ge=1, le=200),
    exclude_adhoc: bool = Query(
        False,
        description="Omit manual calendar-adhoc rows (calendar From+To only; not lookback-derived or scheduled).",
    ),
    run_workflow: Optional[str] = Query(
        None,
        description="Filter by ``run_workflow`` (adhoc | scheduled | continue | lookback).",
    ),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Recent periodic sync runs stored in Cosmos (``periodic_run_history``)."""
    del current_user
    try:
        runs = list_periodic_sync_runs(
            limit=limit,
            exclude_adhoc=exclude_adhoc,
            run_workflow=run_workflow,
        )
        return {"success": True, "runs": runs, "count": len(runs)}
    except Exception as e:
        logger.exception("list_pipeline_periodic_runs failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                "Periodic run history is unavailable. Ensure the periodic_run_history container exists "
                "and COSMOS_DB_PERIODIC_RUN_HISTORY_CONTAINER_NAME is set if you use a non-default name."
            ),
        ) from e


@router.get("/pipeline/periodic-runs/{run_id}", response_model=Dict[str, Any])
async def get_pipeline_periodic_run(
    run_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    del current_user
    try:
        run = get_periodic_sync_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"success": True, "run": run}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_pipeline_periodic_run failed: %s", e)
        raise HTTPException(status_code=503, detail="Periodic run history is unavailable.") from e


@router.get("/pipeline/periodic-schedules", response_model=Dict[str, Any])
async def list_pipeline_periodic_schedules(
    limit: int = Query(100, ge=1, le=200),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    del current_user
    try:
        schedules = list_schedules(limit=limit)
        return {"success": True, "schedules": schedules, "count": len(schedules)}
    except Exception as e:
        logger.exception("list_pipeline_periodic_schedules failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/pipeline/periodic-schedules", response_model=Dict[str, Any])
async def upsert_pipeline_periodic_schedule(
    body: PeriodicScheduleUpsertRequest,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    rolling = str(body.schedule_start_date or "").strip() != ""
    legacy = str(body.window_from or "").strip() != "" and str(body.window_to or "").strip() != ""
    if rolling:
        try:
            datetime.strptime(str(body.schedule_start_date).strip(), "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail="schedule_start_date must be YYYY-MM-DD."
            ) from e
    elif legacy:
        pass
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide schedule_start_date (YYYY-MM-DD) with repeat (ingestion window follows repeat: "
                "daily=1 day, weekly=7 days, etc.), or window_from and window_to for a legacy fixed window."
            ),
        )
    try:
        doc = upsert_schedule(body.model_dump(exclude_none=True), current_user.user_display)
        return {"success": True, "schedule": doc}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("upsert_pipeline_periodic_schedule failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/pipeline/periodic-schedules/{schedule_id}", response_model=Dict[str, Any])
async def get_pipeline_periodic_schedule(
    schedule_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    del current_user
    doc = get_schedule(schedule_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True, "schedule": doc}


@router.delete("/pipeline/periodic-schedules/{schedule_id}", response_model=Dict[str, Any])
async def delete_pipeline_periodic_schedule(
    schedule_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    del current_user
    if not delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True}


@router.post("/pipeline/periodic-schedules/{schedule_id}/run-now", response_model=Dict[str, Any])
async def run_now_pipeline_periodic_schedule(
    schedule_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Trigger ``content-source-periodic-sync`` once from a saved schedule.

    On successful start, advances ``next_run_at`` by one recurrence step from the **planned** slot
    (document ``next_run_at``), not from click time—so an early **Run now** keeps the schedule cadence
    (e.g. daily next remains the next calendar day at the same clock).
    """
    sch = get_schedule(schedule_id)
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    run_at = datetime.now(timezone.utc)
    occ = _parse_schedule_iso_dt(sch.get("next_run_at")) or run_at
    wf, wt = resolve_schedule_ingestion_calendar_window(sch, run_at=run_at, occurrence_at=occ)
    if not wf or not wt:
        raise HTTPException(
            status_code=400,
            detail="Schedule cannot resolve ingestion dates (schedule_start_date + repeat, or legacy window_from/window_to).",
        )
    fv = sch.get("collection_ids")
    if not isinstance(fv, list):
        fv = []
    cn = sch.get("collection_names")
    if not isinstance(cn, list):
        cn = []
    payload: Dict[str, Any] = {
        "collection_ids": fv,
        "lookback_hours": int(sch.get("lookback_hours") or 168),
        "batch_size": int(sch.get("batch_size") or 100),
        "parallel_batches": int(sch.get("parallel_batches") or 20),
        "run_all_stages": bool(sch.get("run_all_stages", True)),
        "from_date": wf,
        "to_date": wt,
        "periodic_run_origin": "scheduled",
        "schedule_id": str(sch.get("id") or ""),
        "collection_names": cn,
    }
    result = await _trigger_azure_function("content-source-periodic-sync", payload)
    oid = result.get("instance_id")
    if oid:
        await asyncio.to_thread(
            record_periodic_sync_started,
            str(oid),
            payload,
            result,
            current_user.user_display,
        )
        await asyncio.to_thread(
            mark_schedule_after_trigger,
            str(sch.get("id") or schedule_id),
            last_run_at=datetime.now(timezone.utc),
        )
    return {"success": True, "result": result}


@router.post("/pipeline/periodic-schedules/process-due", response_model=Dict[str, Any])
async def process_due_pipeline_periodic_schedules(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """
    Triggers ``content-source-periodic-sync`` for each enabled schedule whose ``next_run_at`` is due, then advances
    ``next_run_at`` / disables one-shots.

    Data Foundations also runs ``ContentSourcePeriodicSyncSchedulesPoller`` (about every minute) with the same semantics
    so schedules fire without calling this endpoint.
    """
    due = list_due_schedules()
    processed: List[Dict[str, Any]] = []
    started_by = current_user.user_display
    run_at = datetime.now(timezone.utc)
    for s in due:
        occ = _parse_schedule_iso_dt(s.get("next_run_at")) or run_at
        wf, wt = resolve_schedule_ingestion_calendar_window(s, run_at=run_at, occurrence_at=occ)
        if not wf or not wt:
            processed.append(
                {
                    "schedule_id": s.get("id"),
                    "error": "cannot resolve ingestion window (repeat-based or legacy window_from/window_to)",
                }
            )
            continue
        fv = s.get("collection_ids")
        if not isinstance(fv, list):
            fv = []
        cn = s.get("collection_names")
        if not isinstance(cn, list):
            cn = []
        payload: Dict[str, Any] = {
            "collection_ids": fv,
            "lookback_hours": int(s.get("lookback_hours") or 168),
            "batch_size": int(s.get("batch_size") or 100),
            "parallel_batches": int(s.get("parallel_batches") or 20),
            "run_all_stages": bool(s.get("run_all_stages", True)),
            "from_date": wf,
            "to_date": wt,
            "periodic_run_origin": "scheduled",
            "schedule_id": str(s.get("id") or ""),
            "collection_names": cn,
        }
        try:
            schedule_id_str = str(s.get("id") or "").strip()
            if not schedule_id_str:
                logger.error("Schedule missing id field, skipping: %s", s)
                processed.append({"error": "schedule missing id"})
                continue
            result = await _trigger_azure_function("content-source-periodic-sync", payload)
            oid = result.get("instance_id")
            if oid:
                await asyncio.to_thread(
                    record_periodic_sync_started,
                    str(oid),
                    payload,
                    result,
                    started_by,
                )
                await asyncio.to_thread(
                    mark_schedule_after_trigger,
                    schedule_id_str,
                    last_run_at=datetime.now(timezone.utc),
                )
            processed.append(
                {
                    "schedule_id": s.get("id"),
                    "instance_id": oid,
                    "message": result.get("message"),
                }
            )
        except Exception as exc:
            logger.exception("process_due schedule %s: %s", s.get("id"), exc)
            processed.append({"schedule_id": s.get("id"), "error": str(exc)})
    return {"success": True, "processed": processed}


# =============================================================================
# Pipeline Configuration
# =============================================================================

class PipelineConfigRequest(BaseModel):
    """Request body for pipeline configuration."""
    collection_ids: Optional[List[str]] = []
    batch_size: Optional[int] = 100
    parallel_batches: Optional[int] = 20


@router.get("/pipeline/config")
async def get_pipeline_config():
    """
    Get the saved pipeline configuration.
    
    Returns:
        Pipeline configuration with collection_ids, batch_size, parallel_batches
    """
    try:
        service = get_pipeline_service()
        return service.get_pipeline_config()
    except Exception as e:
        logger.exception("Failed to get pipeline config: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/pipeline/config")
async def save_pipeline_config(request: PipelineConfigRequest):
    """
    Save pipeline configuration.
    
    Args:
        request: Configuration with collection_ids, batch_size, parallel_batches
        
    Returns:
        The saved configuration
    """
    try:
        service = get_pipeline_service()
        config = request.model_dump()
        return service.save_pipeline_config(config)
    except Exception as e:
        logger.exception("Failed to save pipeline config: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/pipeline/config")
async def reset_pipeline_config():
    """
    Reset pipeline configuration to defaults.
    
    Deletes the saved configuration document from CosmosDB.
    After reset, the system will use DEFAULT_PIPELINE_CONFIG.
    
    Returns:
        Success message and the default configuration
    """
    try:
        service = get_pipeline_service()
        result = service.reset_pipeline_config()
        return result
    except Exception as e:
        logger.exception("Failed to reset pipeline config: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# Field Mapping Configuration
# =============================================================================


class FieldMappingCreateRequest(BaseModel):
    """Request body for creating a field mapping."""
    repository: str
    collection: Optional[str] = None
    identifier_field: str
    display_name: Optional[str] = None


class FieldMappingUpdateRequest(BaseModel):
    """Request body for updating a field mapping."""
    identifier_field: Optional[str] = None
    display_name: Optional[str] = None


@router.get("/field-mappings")
async def get_field_mappings(
    repository: Optional[str] = Query(None, description="Filter by repository"),
    collection: Optional[str] = Query(None, description="Filter by collection")
):
    """
    Get all field mappings, optionally filtered by repository and/or collection.
    
    Args:
        repository: Optional repository name to filter by
        collection: Optional collection name to filter by
        
    Returns:
        List of field mapping configurations
    """
    try:
        service = get_field_mapping_service()
        mappings = service.get_mappings(repository, collection)
        return {
            "mappings": mappings,
            "count": len(mappings)
        }
    except Exception as e:
        logger.exception("Failed to get field mappings: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/field-mappings/resolve")
async def resolve_field_mapping(
    repository: str = Query(..., description="Repository name"),
    collection: Optional[str] = Query(None, description="Collection name")
):
    """
    Get the effective field mapping for a repository/collection.
    Falls back to repository-level mapping if collection-specific one doesn't exist.
    Returns default identifier field if no mapping is configured.
    
    Args:
        repository: Repository name
        collection: Optional collection name
        
    Returns:
        The effective field mapping or default
    """
    try:
        service = get_field_mapping_service()
        mapping = service.get_mapping(repository, collection)
        
        if mapping:
            return {
                "mapping": mapping,
                "is_default": False
            }
        
        # Return default mapping
        return {
            "mapping": {
                "identifier_field": "Source Record ID",
                "display_name": "Source Record ID"
            },
            "is_default": True
        }
    except Exception as e:
        logger.exception("Failed to resolve field mapping: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/field-mappings")
async def create_field_mapping(
    request: FieldMappingCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock)
):
    """
    Create a new field mapping configuration.
    
    Args:
        request: Field mapping configuration
        current_user: Authenticated user (from dependency)
        
    Returns:
        The created field mapping
    """
    try:
        service = get_field_mapping_service()
        mapping = service.create_mapping(
            repository=request.repository,
            identifier_field=request.identifier_field,
            collection=request.collection,
            display_name=request.display_name,
            created_by=current_user.user_display
        )
        return {"mapping": mapping}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to create field mapping: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/field-mappings/{mapping_id}")
async def update_field_mapping(
    mapping_id: str,
    request: FieldMappingUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock)
):
    """
    Update an existing field mapping.
    
    Args:
        mapping_id: The ID of the mapping to update
        request: Updated field mapping data
        current_user: Authenticated user (from dependency)
        
    Returns:
        The updated field mapping
    """
    try:
        service = get_field_mapping_service()
        mapping = service.update_mapping(
            mapping_id=mapping_id,
            identifier_field=request.identifier_field,
            display_name=request.display_name
        )
        return {"mapping": mapping}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to update field mapping: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/field-mappings/{mapping_id}")
async def delete_field_mapping(
    mapping_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock)
):
    """
    Delete a field mapping.
    
    Args:
        mapping_id: The ID of the mapping to delete
        current_user: Authenticated user (from dependency)
        
    Returns:
        Success message
    """
    try:
        service = get_field_mapping_service()
        service.delete_mapping(mapping_id)
        return {"message": f"Field mapping '{mapping_id}' deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to delete field mapping: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/metadata-fields")
async def get_available_metadata_fields():
    """
    Get a list of all available metadata fields that can be used for field mappings.
    
    Returns:
        List of metadata field names
    """
    try:
        service = get_field_mapping_service()
        fields = service.get_available_metadata_fields()
        return {
            "fields": fields,
            "count": len(fields)
        }
    except Exception as e:
        logger.exception("Failed to get metadata fields: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e
