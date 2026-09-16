# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API routes for Digital Resources (digital-items container)."""
# pylint: disable=duplicate-code  # durable-function status fields shared with routes.py

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, List, Dict
from urllib.parse import urlparse, urlunsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient
from azure.core.exceptions import ResourceNotFoundError, ClientAuthenticationError, HttpResponseError, AzureError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchableField,
    SimpleField,
    SearchField,
    SearchFieldDataType,
    SemanticSearch,
    SemanticConfiguration,
    SemanticPrioritizedFields,
    SemanticField,
)
from pydantic import BaseModel

from services.digital_items_service import (
    list_items_by_source,
    get_item,
    get_item_full,
    get_page_item,
    count_items_by_source,
    get_all_sources_summary,
    get_digital_items_container,
)
from services.blob_service import (
    download_blob_text,
    upload_to_blob,
    add_sas_token_to_url,
    BlobDownloadError,
    BlobNotFoundError,
    BlobAuthenticationError,
)
from services.audit_service import AuditService
from core.config import settings
from api.dependencies import AuthenticatedUser, get_current_user_or_mock

router = APIRouter(prefix="/digital-items", tags=["Digital Resources"])

logger = logging.getLogger(__name__)


def _ensure_can_modify_public_resources(user: AuthenticatedUser) -> None:
    """Allow mutation endpoints only for users with at least one role."""
    if not (user.is_admin or user.is_data_foundations or user.is_archivist):
        raise HTTPException(
            status_code=403,
            detail="Read-only access: you do not have permission to modify public resources.",
        )


# Item-level blob URL fields that point at internalized assets in DF storage and
# need a read SAS token appended so the browser can render them directly.
_DIGITAL_ITEM_BLOB_URL_FIELDS = (
    "image_url",
    "thumbnail_url",
    "primary_pdf_url",
    "pdf_url",
    "audio_url",
    "file_url",
    "blob_url",
    "content_url",
)


def _is_signable_blob_url(value: Any) -> bool:
    """True for private Azure Blob HTTPS URLs (skip external source URLs like Wikipedia)."""
    return (
        isinstance(value, str)
        and value.startswith("https://")
        and ".blob.core.windows.net" in value
    )


def _sign_blob_url_if_internal(value: Optional[str]) -> Optional[str]:
    """Append a read SAS to a single internalized blob URL; pass external/empty URLs through."""
    if _is_signable_blob_url(value):
        return add_sas_token_to_url(value)
    return value


def _get_pdf_url_from_item(item: dict) -> Optional[str]:
    """Get the PDF URL from an item, checking primary_pdf_url (legacy) then files[]."""
    pdf_url = item.get("primary_pdf_url")
    if pdf_url:
        return pdf_url
    files = item.get("files") or []
    for f in files:
        url = f.get("url", "") if isinstance(f, dict) else ""
        if url.lower().endswith(".pdf"):
            return url
    return None


def _get_file_urls_from_item(item: dict) -> list:
    """Get all file URLs from an item (images and PDFs), signed for browser access."""
    files = item.get("files") or []
    urls = [f.get("url") for f in files if isinstance(f, dict) and f.get("url")]
    return [_sign_blob_url_if_internal(u) for u in urls]


def _sign_digital_item_blob_urls(item: Any) -> Any:
    """Append fresh user-delegation SAS tokens to internalized blob URLs in a digital item.

    Applies to the item's own media fields, any ``files`` entries, and every
    ``linked_topics`` thumbnail. External source URLs are left untouched. Source
    agnostic: works for moore-chronology, cyclopedia/tr-cyclopedia, and
    genealogy-papers alike.
    """
    if not isinstance(item, dict):
        return item

    for field in _DIGITAL_ITEM_BLOB_URL_FIELDS:
        value = item.get(field)
        if _is_signable_blob_url(value):
            item[field] = add_sas_token_to_url(value)

    files = item.get("files")
    if isinstance(files, list):
        for file_entry in files:
            if isinstance(file_entry, dict):
                for field in ("url", "blob_url", "thumbnail_url", "file_url"):
                    value = file_entry.get(field)
                    if _is_signable_blob_url(value):
                        file_entry[field] = add_sas_token_to_url(value)

    linked_topics = item.get("linked_topics")
    if isinstance(linked_topics, list):
        for topic in linked_topics:
            if isinstance(topic, dict):
                value = topic.get("thumbnail_url")
                if _is_signable_blob_url(value):
                    topic["thumbnail_url"] = add_sas_token_to_url(value)

    return item

_blob_credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)

# â”€â”€â”€ Singleton: digital-items audit CosmosDB container â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Created once per worker process (same pattern as digital_items_service.py).
_audit_cosmos_container = None
_audit_cosmos_client = None


def _get_digital_items_audit_container():
    """Return the digital-items audit Cosmos container (singleton per process)."""
    global _audit_cosmos_container, _audit_cosmos_client
    if _audit_cosmos_container is not None:
        return _audit_cosmos_container

    from azure.cosmos import CosmosClient, PartitionKey
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    endpoint = settings.cosmos_db_digital_items_endpoint
    database_name = settings.cosmos_db_digital_items_database
    audit_container_name = settings.cosmos_db_digital_items_audit_container

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    _audit_cosmos_client = CosmosClient(
        endpoint,
        credential=credential,
        connection_mode=settings.cosmos_db_connection_mode,
    )

    db = _audit_cosmos_client.get_database_client(database_name)
    try:
        container = db.get_container_client(audit_container_name)
        container.read()  # Verify it exists
        _audit_cosmos_container = container
    except CosmosResourceNotFoundError:
        logger.info("Creating audit container '%s'...", audit_container_name)
        _audit_cosmos_container = db.create_container(
            id=audit_container_name,
            partition_key=PartitionKey(path="/record_id"),
        )
    return _audit_cosmos_container


# â”€â”€â”€ Helper: download OCR text from blob URL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _download_ocr_blob(blob_url: str) -> str:
    """Download OCR text from a blob URL, return empty string on failure."""
    if not blob_url:
        return ""
    try:
        return await download_blob_text(blob_url)
    except (BlobNotFoundError, BlobAuthenticationError, BlobDownloadError) as e:
        logger.warning("Failed to download OCR blob %s: %s", blob_url, e)
        return ""


# â”€â”€â”€ Request/Response models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class OcrUpdateRequest(BaseModel):
    """Request body for updating OCR text."""
    ocr_text: str
    correlation_id: Optional[str] = None


@router.get("/summary")
async def digital_items_summary():
    """Return item counts for all digital resource sources."""
    try:
        return get_all_sources_summary()
    except Exception as exc:
        logger.error("Failed to get sources summary: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve digital items summary. The database may be temporarily unavailable. Please try again in a moment."
        ) from exc


@router.get("/sources/{source}")
async def list_by_source(
    source: str,
    item_type: Optional[str] = Query(None),
    max_items: int = Query(200, le=500),
):
    """List digital items for a given source (moore-chronology, cyclopedia, genealogy-papers)."""
    valid_sources = {"moore-chronology", "cyclopedia", "genealogy-papers", "tr-cyclopedia"}
    if source not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Invalid source. Must be one of: {', '.join(sorted(valid_sources))}")
    try:
        items = list_items_by_source(source, item_type=item_type, max_items=max_items)
    except Exception as exc:
        logger.error("Failed to list items for source '%s': %s", source, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Unable to load items for '{source}'. The database may be temporarily unavailable. Please try again."
        ) from exc
    items = [_sign_digital_item_blob_urls(item) for item in items]
    return {"source": source, "count": len(items), "items": items}


@router.get("/sources/{source}/count")
async def count_by_source(source: str):
    """Return count of items for a source."""
    return {"source": source, "count": count_items_by_source(source)}


@router.get("/items/{item_id}")
async def get_digital_item(
    item_id: str,
    source: str = Query(..., description="Partition key (moore-chronology, cyclopedia, genealogy-papers)"),
):
    """Get a single digital item by ID (full document with all content)."""
    item = get_item_full(item_id, source)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'")

    # Hydrate full plain_text from blob storage when it was offloaded during ingestion
    plain_text_blob_url = item.get("plain_text_blob_url", "")
    if plain_text_blob_url:
        full_text = await download_blob_text(plain_text_blob_url)
        if full_text:
            item["plain_text"] = full_text

    return _sign_digital_item_blob_urls(item)


@router.get("/items/{item_id}/pages/{page_number}")
async def get_digital_item_page(
    item_id: str,
    page_number: int,
    source: str = Query(..., description="Partition key (moore-chronology, cyclopedia, genealogy-papers)"),
):
    """Get OCR text and metadata for a single page of a digital item volume."""
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1")

    volume = get_item(item_id, source)
    if volume is None:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'")

    page = get_page_item(item_id, source, page_number)

    # Some volumes do not have per-page OCR docs yet but have all OCR text
    # stored on the parent document, delimited by "--- Page Break ---".
    # Fall back to splitting the parent text when per-page docs don't exist.
    if page is None:
        ocr_text_original = ""
        ocr_text_flexible = ""

        # Prefer blob-based OCR (new pattern)
        original_blob_url = volume.get("ocr_text_original_blob_url", "")
        flexible_blob_url = volume.get("ocr_text_flexible_blob_url", "")

        if original_blob_url:
            # Download from blob and split by page
            full_original = await _download_ocr_blob(original_blob_url)
            full_flexible = await _download_ocr_blob(flexible_blob_url) if flexible_blob_url else full_original

            if full_original:
                pages_orig = full_original.split("--- Page Break ---")
                if 1 <= page_number <= len(pages_orig):
                    ocr_text_original = pages_orig[page_number - 1].strip()

            if full_flexible:
                pages_flex = full_flexible.split("--- Page Break ---")
                if 1 <= page_number <= len(pages_flex):
                    ocr_text_flexible = pages_flex[page_number - 1].strip()
        else:
            # Legacy: inline ocr_text_original on the volume document
            volume_ocr = volume.get("ocr_text_original", "")
            if volume_ocr:
                pages = volume_ocr.split("--- Page Break ---")
                if 1 <= page_number <= len(pages):
                    ocr_text_original = pages[page_number - 1].strip()
                    ocr_text_flexible = ocr_text_original  # No separate flexible version for legacy

        return {
            "parent_id": item_id,
            "source": source,
            "page_number": page_number,
            "page_count": volume.get("page_count") or volume.get("ocr_page_count"),
            "pdf_url": _sign_blob_url_if_internal(_get_pdf_url_from_item(volume)),
            "files": _get_file_urls_from_item(volume),
            "ocr_text_original": ocr_text_original,
            "ocr_text_flexible": ocr_text_flexible,
            "ocr_version": volume.get("ocr_version", 1),
            "title": volume.get("title"),
            "ocr_available": bool(ocr_text_original),
            "has_edits": ocr_text_original != ocr_text_flexible,
        }

    return {
        "parent_id": item_id,
        "source": source,
        "page_number": page_number,
        "page_count": volume.get("page_count") or page.get("page_count"),
        "pdf_url": _sign_blob_url_if_internal(page.get("pdf_url") or _get_pdf_url_from_item(volume)),
        "files": _get_file_urls_from_item(volume),
        "ocr_text_original": page.get("ocr_text_original", ""),
        "ocr_text_flexible": page.get("ocr_text_flexible", page.get("ocr_text_original", "")),
        "ocr_version": volume.get("ocr_version", 1),
        "title": volume.get("title"),
        "ocr_available": True,
        "has_edits": page.get("ocr_text_flexible") is not None and page.get("ocr_text_flexible") != page.get("ocr_text_original", ""),
    }


@router.get("/pdf-proxy")
async def pdf_proxy(url: str = Query(..., description="Source PDF URL to proxy")):
    """Proxy PDF bytes for restricted TRC S3 URLs to avoid browser access-denied errors."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    allowed_hosts = {
        "theodorerooseveltcenter.s3.amazonaws.com",
        "www.theodorerooseveltcenter.org",
    }
    if parsed.netloc.lower() not in allowed_hosts:
        raise HTTPException(status_code=400, detail="URL host not allowed")

    try:
        upstream = requests.get(
            url,
            headers={
                "Referer": "https://www.theodorerooseveltcenter.org/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch PDF: {exc}") from exc

    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="Upstream PDF request failed")

    content_type = upstream.headers.get("Content-Type", "application/pdf")
    return Response(content=upstream.content, media_type=content_type)


@router.get("/asset-proxy")
async def asset_proxy(
    request: Request,
    url: str | None = Query(None, description="Source image/pdf URL to proxy"),
    u: str | None = Query(None, description="Base64url-encoded source URL"),
):
    """Proxy external digital-item assets (images, PDFs, audio) so the frontend does
    not fetch third-party URLs directly.  Supports HTTP Range requests so that
    <audio> and <video> elements can seek/stream without buffering the entire file.
    """
    if not url and u:
        try:
            padded = u + "=" * (-len(u) % 4)
            url = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise HTTPException(status_code=400, detail="Invalid encoded URL") from exc

    if not url:
        raise HTTPException(status_code=400, detail="Missing asset URL")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    host = parsed.netloc.lower()

    # Internalized assets are private blobs; stream them via backend using AAD.
    if host.endswith(".blob.core.windows.net"):
        try:
            # Strip any existing SAS token so the SDK uses the managed identity credential.
            clean_blob_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
            blob_client = BlobClient.from_blob_url(clean_blob_url, credential=_blob_credential)
            downloader = blob_client.download_blob()
            content = downloader.readall()
            props = blob_client.get_blob_properties()
            content_type = (
                props.content_settings.content_type
                if props and props.content_settings and props.content_settings.content_type
                else "application/octet-stream"
            )
            return Response(content=content, media_type=content_type)
        except ResourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Blob asset not found") from exc
        except ClientAuthenticationError as exc:
            raise HTTPException(status_code=403, detail="Blob access denied") from exc
        except HttpResponseError as exc:
            raise HTTPException(status_code=502, detail=f"Blob request failed: {exc}") from exc

    allowed_hosts = {
        "theodorerooseveltcenter.s3.amazonaws.com",
        "s3-us-west-2.amazonaws.com",
        "www.theodorerooseveltcenter.org",
        "www.theodoreroosevelt.org",
        "theodoreroosevelt.org",
        "images.clubexpress.com",
        "cdn.clubexpress.com",
        "files.clubexpress.com",
        "storage.clubexpress.com",
    }
    if host not in allowed_hosts:
        raise HTTPException(status_code=400, detail="URL host not allowed")

    # Forward the browser's Range header so audio/video elements can stream and seek.
    upstream_headers: dict[str, str] = {
        "Referer": "https://www.theodoreroosevelt.org/",
        "User-Agent": "Mozilla/5.0",
    }
    range_header = request.headers.get("Range")
    if range_header:
        upstream_headers["Range"] = range_header

    try:
        upstream = requests.get(
            url,
            headers=upstream_headers,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch asset: {exc}") from exc

    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="Upstream asset request failed")

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")

    # Forward headers required for audio/video streaming
    response_headers: dict[str, str] = {}
    for h in ("Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
        val = upstream.headers.get(h)
        if val:
            response_headers[h] = val
    # Signal that this proxy accepts range requests even if upstream didn't say so
    if "Accept-Ranges" not in response_headers:
        response_headers["Accept-Ranges"] = "bytes"

    return Response(
        content=upstream.content,
        media_type=content_type,
        status_code=upstream.status_code,  # preserve 206 Partial Content
        headers=response_headers,
    )


# =============================================================================
# Digital Items Ingestion Process
# =============================================================================

# ---------------------------------------------------------------------------
# Cosmos-backed ingestion job state
# ---------------------------------------------------------------------------
# All job state is stored in and read from Cosmos DB (the digital-items
# container).  There is no in-memory cache or local disk file â€” safe for
# multiple gunicorn workers and multiple App Service instances.
# ---------------------------------------------------------------------------

# Cosmos partition key and doc-kind tag for all ingestion job documents
_INGESTION_STATS_SOURCE = "system-ingestion-jobs"
_INGESTION_STATS_KIND = "digital_ingestion_job_state"
_INGESTION_JOB_PARTITION_KEY = _INGESTION_STATS_SOURCE

# Sources that support OCR via the Data Foundations DigitalItemsOcr function.
# TR Cyclopedia and Genealogy/Papers intentionally skip OCR.
# df_source_key is the route parameter for POST /api/digital-items/ocr/{source}.
OCR_SOURCES = {
    "moore-chronology": {
        "label": "The Moore Chronology",
        "script_arg": "moore",
        "df_source_key": "moore",
    },
}


def _cosmos_job_doc_id(source_key: str) -> str:
    return f"ingestion-job:{source_key}"


def _cosmos_upsert_job(
    source_key: str,
    job: dict[str, Any],
    *,
    stop_requested: bool = False,
) -> None:
    """Write (or overwrite) a job document in Cosmos -- the single source of truth."""
    from azure.cosmos import exceptions as _cosmos_exc
    body = {
        "id": _cosmos_job_doc_id(source_key),
        "source": _INGESTION_STATS_SOURCE,
        "doc_kind": _INGESTION_STATS_KIND,
        "job_source": source_key,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stop_requested": stop_requested,
        "job": job,
    }
    for _attempt in range(4):
        try:
            container = get_digital_items_container()
            container.upsert_item(body=body)
            return
        except _cosmos_exc.CosmosHttpResponseError as exc:
            if exc.status_code in (429, 503) and _attempt < 3:
                _wait = min(2 ** _attempt, 8)
                time.sleep(_wait)
            else:
                logger.error("Failed to persist job state for %s: %s", source_key, exc)
                return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to persist job state for %s: %s", source_key, exc)
            return

def _cosmos_get_job(source_key: str) -> Optional[dict[str, Any]]:
    """Return just the job payload for a source, or None if not found."""
    try:
        container = get_digital_items_container()
        doc = container.read_item(
            item=_cosmos_job_doc_id(source_key),
            partition_key=_INGESTION_STATS_SOURCE,
        )
        return doc.get("job")
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _cosmos_get_all_jobs() -> dict[str, dict[str, Any]]:
    """Return all ingestion job payloads keyed by source_key."""
    try:
        container = get_digital_items_container()
        docs = list(
            container.query_items(
                query=(
                    "SELECT c.job_source, c.job FROM c "
                    "WHERE c.doc_kind = @kind AND c.source = @source"
                ),
                parameters=[
                    {"name": "@kind", "value": _INGESTION_STATS_KIND},
                    {"name": "@source", "value": _INGESTION_STATS_SOURCE},
                ],
                partition_key=_INGESTION_STATS_SOURCE,
            )
        )
        return {
            doc["job_source"]: doc["job"]
            for doc in docs
            if "job_source" in doc and "job" in doc
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to read ingestion jobs from Cosmos: %s", exc)
        return {}


def _cosmos_delete_job(source_key: str) -> None:
    """Delete a job document from Cosmos."""
    try:
        container = get_digital_items_container()
        container.delete_item(
            item=_cosmos_job_doc_id(source_key),
            partition_key=_INGESTION_STATS_SOURCE,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Silently ignore if already deleted or not found


# Seconds a Running job can remain without updates before being marked stale
_STALE_THRESHOLD_SECONDS_DEFAULT = 3600  # 1 hour


def _recover_stale_jobs() -> None:
    """Mark stale Running jobs as failed after app restarts."""
    try:
        container = get_digital_items_container()
        docs = list(
            container.query_items(
                query=(
                    "SELECT c.id, c.job_source, c.job, c.updated_at FROM c "
                    "WHERE c.doc_kind = @kind AND c.source = @source"
                ),
                parameters=[
                    {"name": "@kind", "value": _INGESTION_STATS_KIND},
                    {"name": "@source", "value": _INGESTION_STATS_SOURCE},
                ],
                partition_key=_INGESTION_STATS_SOURCE,
            )
        )
        now = datetime.now(timezone.utc)
        for doc in docs:
            job = doc.get("job", {})
            if job.get("status") != "Running":
                continue

            stale_threshold = _STALE_THRESHOLD_SECONDS_DEFAULT
            updated_str = doc.get("updated_at", "")
            try:
                updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                age_secs = (now - updated_at).total_seconds()
            except (ValueError, TypeError):
                age_secs = stale_threshold + 1

            if age_secs > stale_threshold:
                job["status"] = "Failed"
                job["error_message"] = (
                    "Ingestion was interrupted (server restarted or instance recycled). "
                    "Please re-run the pipeline."
                )
                job["completed_at"] = now.isoformat()
                container.upsert_item({**doc, "job": job, "stop_requested": False})
                logger.info(
                    "Recovered stale Running job for %s (age %ss)",
                    doc.get("job_source"),
                    f"{age_secs:.0f}",
                )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Stale job recovery failed (non-critical): %s", exc)


# Mark any orphaned Running jobs left by a previous server instance as Failed
_recover_stale_jobs()

# Map source keys to their ingestion script + arguments
_API_SCRIPTS_DIR = Path(__file__).resolve().parent


def _iter_ingestion_script_dirs() -> list[Path]:
    """Return candidate directories that may contain ingestion/OCR scripts."""
    candidates: list[Path] = []

    # Optional override when scripts live outside archivist-api/api.
    override = (
        os.getenv("PUBLIC_INGESTION_SCRIPTS_DIR", "").strip()
        or os.getenv("INGESTION_SCRIPTS_DIR", "").strip()
    )
    if override:
        candidates.append(Path(override))

    # Self-contained scripts in ArchivistApp/apps/archivist-api/api.
    candidates.append(_API_SCRIPTS_DIR)

    unique_existing: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_dir():
            unique_existing.append(path)
    return unique_existing



INGESTION_SOURCES = {
    "tr-cyclopedia": {
        "label": "Theodore Roosevelt Cyclopedia",
        "delegate_to_df": True,
        "df_source_key": "tr-cyclopedia",
        "cosmos_source": "tr-cyclopedia",
    },
    "moore-chronology": {
        "label": "The Moore Chronology",
        "delegate_to_df": True,
        "df_source_key": "moore-chronology",
        "cosmos_source": "moore-chronology",
    },
    "genealogy-papers": {
        "label": "Digitized Genealogy & Papers by the TRA",
        "delegate_to_df": True,
        "df_source_key": "genealogy-papers",
        "cosmos_source": "genealogy-papers",
    },
}




def _call_df_ingestion(df_source_key: str, step: Optional[str] = None) -> dict[str, Any]:
    """POST to Data Foundations digital-items ingestion and return management URLs.

    Fires the Durable Functions HTTP trigger and returns immediately with the
    orchestration management URLs (status, terminate, suspend, resume).
    The frontend polls status via /pipeline/orchestration/status -- matching
    the same pattern used by data pipelines.

    Args:
        df_source_key: Source key for the DF function (cyclopedia, moore, genealogy).
        step: Optional step to run individually (fetch, ocr, upsert).

    Returns:
        {
            "instance_id": str | None,
            "status_url": str | None,
            "terminate_url": str | None,
            "suspend_url": str | None,
            "resume_url": str | None,
            "completed_synchronously": bool,
            "output": dict | None,  # Only if completed synchronously (200)
        }
    """
    base_url = (settings.data_ingest_function_url or "").rstrip("/")
    if not base_url:
        raise ValueError(
            "DATA_INGEST_FUNCTION_URL is not configured. "
            "Cannot delegate ingestion to Data Foundations."
        )

    code = (settings.data_ingest_function_code or "").strip()
    url = f"{base_url}/api/digital-items/ingest/{df_source_key}"
    params: dict[str, str] = {}
    if code:
        params["code"] = code
    if step:
        params["step"] = step

    # Request with retries for cold-start 503s
    max_retries = 5
    resp = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, params=params, timeout=120)
            if resp.status_code == 400:
                error = resp.json().get("error") if resp.content else resp.text[:500]
                raise ValueError(f"Data Foundations rejected ingestion: {error}")
            if resp.status_code >= 500 and attempt < max_retries - 1:
                wait = 2 ** attempt + 5 if resp.status_code == 503 else 2 ** attempt
                logger.warning(
                    "DF ingestion error (HTTP %d, attempt %d/%d), retrying in %ds...",
                    resp.status_code, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                raise RuntimeError(
                    f"Data Foundations ingestion error (HTTP {resp.status_code}) "
                    f"after {max_retries} attempts: {resp.text[:500]}"
                )
            break  # 200 or 202
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 2
                logger.warning(
                    "DF ingestion connection error (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"Data Foundations ingestion unreachable after {max_retries} attempts: {exc}"
                ) from exc

    if resp is None:
        raise RuntimeError("Data Foundations ingestion failed after retries")

    data = resp.json() if resp.content else {}

    # 200 -- orchestrator completed within the wait window (fast source)
    if resp.status_code == 200:
        output = data.get("output", data) if "output" in data else data
        logger.info(
            "DF ingestion completed synchronously for source=%s: %d items",
            df_source_key, output.get("processed", 0) if isinstance(output, dict) else 0,
        )
        return {
            "instance_id": data.get("id"),
            "status_url": data.get("statusQueryGetUri"),
            "terminate_url": data.get("terminatePostUri"),
            "suspend_url": data.get("suspendPostUri"),
            "resume_url": data.get("resumePostUri"),
            "completed_synchronously": True,
            "output": output,
        }

    # 202 -- orchestrator started, return management URLs for frontend polling
    if resp.status_code == 202:
        logger.info(
            "DF ingestion orchestrator started for source=%s (instance=%s)",
            df_source_key, data.get("id", "unknown"),
        )
        return {
            "instance_id": data.get("id"),
            "status_url": data.get("statusQueryGetUri"),
            "terminate_url": data.get("terminatePostUri"),
            "suspend_url": data.get("suspendPostUri"),
            "resume_url": data.get("resumePostUri"),
            "completed_synchronously": False,
            "output": None,
        }

    # Unexpected status
    raise RuntimeError(
        f"Data Foundations returned unexpected HTTP {resp.status_code}: {resp.text[:500]}"
    )


def _call_df_ocr(df_source_key: str) -> str:
    """POST to Data Foundations OCR endpoint and return a batch id."""
    base_url = (settings.data_ingest_function_url or "").rstrip("/")
    if not base_url:
        raise ValueError(
            "DATA_INGEST_FUNCTION_URL is not configured. "
            "Cannot delegate OCR to Data Foundations."
        )

    code = (settings.data_ingest_function_code or "").strip()
    url = f"{base_url}/api/digital-items/ocr/{df_source_key}"
    params = {"code": code} if code else {}

    max_retries = 4
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, params=params, timeout=120)
            if resp.status_code == 400:
                raise ValueError(f"Data Foundations rejected OCR request: {resp.text[:500]}")
            if resp.status_code == 503 and attempt < max_retries - 1:
                wait = 2 ** attempt + 1
                logger.warning(
                    "DF OCR returned 503 (attempt %d/%d), retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                raise RuntimeError(
                    f"Data Foundations OCR function error (HTTP {resp.status_code}): {resp.text[:500]}"
                )

            payload = resp.json() if resp.content else {}
            batch_ids = payload.get("batch_ids") or {}
            return batch_ids.get(df_source_key) or "submitted"
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = 2 ** attempt + 1
                logger.warning(
                    "DF OCR connection error (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"Data Foundations OCR unreachable after {max_retries} attempts: {exc}"
                ) from exc

    raise RuntimeError(
        "Data Foundations OCR failed after retries"
        + (f": {last_exc}" if last_exc else "")
    )


def _start_ingestion_for_source(source_key: str, step: Optional[str] = None) -> dict[str, Any]:
    """Trigger ingestion for a single source via Data Foundations.

    Calls the DF function app, stores orchestration management URLs in Cosmos,
    and returns immediately.  The frontend polls orchestrator status via the
    existing /pipeline/orchestration/status proxy endpoint.

    Args:
        source_key: Source key (tr-cyclopedia, moore-chronology, genealogy-papers).
        step: Optional step to run individually (fetch, ocr, upsert).

    Returns the Cosmos job state dict (with management URLs embedded).
    """
    config = INGESTION_SOURCES[source_key]
    job_state: dict[str, Any] = {
        "source": source_key,
        "label": config["label"],
        "status": "Running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "items_ingested": 0,
        "error_message": None,
        "instance_id": None,
        "status_url": None,
        "terminate_url": None,
        "suspend_url": None,
        "resume_url": None,
        "current_step": step,  # which step is running (fetch/ocr/upsert or None=all)
    }

    try:
        df_result = _call_df_ingestion(config["df_source_key"], step=step)

        # Store management URLs for frontend polling
        job_state["instance_id"] = df_result.get("instance_id")
        job_state["status_url"] = df_result.get("status_url")
        job_state["terminate_url"] = df_result.get("terminate_url")
        job_state["suspend_url"] = df_result.get("suspend_url")
        job_state["resume_url"] = df_result.get("resume_url")

        if df_result.get("completed_synchronously"):
            # Fast source completed within the DF wait window
            output = df_result.get("output") or {}
            processed = output.get("processed", 0) if isinstance(output, dict) else 0
            job_state["status"] = "Completed"
            job_state["items_ingested"] = processed
            job_state["completed_at"] = datetime.now(timezone.utc).isoformat()
            if processed == 0:
                job_state["error_message"] = "No items found for this source."
            logger.info(
                "Ingestion completed synchronously for %s: %d items",
                source_key, processed,
            )
        else:
            # Orchestrator is running -- frontend will poll status_url
            logger.info(
                "Ingestion orchestrator started for %s (instance=%s)",
                source_key, df_result.get("instance_id"),
            )

    except Exception as exc:
        logger.exception("Ingestion failed for %s: %s", source_key, exc)
        job_state["status"] = "Failed"
        raw = str(exc)
        if "HTTP 503" in raw or "503" in raw:
            job_state["error_message"] = (
                "The Data Foundations function app is temporarily unavailable (HTTP 503). "
                "This usually means the function app is cold-starting or its host storage "
                "is misconfigured. Check that AzureWebJobsStorage connection string has been "
                "replaced with AzureWebJobsStorage__accountName + __credential=managedidentity "
                "in the Function App settings, then retry."
            )
        else:
            job_state["error_message"] = f"Unexpected error: {raw}"
        job_state["completed_at"] = datetime.now(timezone.utc).isoformat()

    _cosmos_upsert_job(source_key, job_state)
    return job_state


@router.post("/ingestion/start")
async def start_digital_ingestion(
    source: str = Query(..., description="Source to ingest: tr-cyclopedia, moore-chronology, genealogy-papers, or all"),
    step: Optional[str] = Query(None, description="Run a single step: fetch, ocr, or publish"),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Trigger ingestion for a digital resource source.

    Fires the Durable Functions orchestrator and returns immediately with
    orchestration management URLs.  The frontend polls status via the
    existing /pipeline/orchestration/status proxy endpoint.

    Optional ``step`` parameter runs only a single pipeline step:
      - fetch: Scrape/fetch items from source and write to Cosmos DB
      - ocr: Load items from Cosmos, run OCR, update Cosmos
      - publish: Publish Cosmos items to Azure AI Search index
    """
    _ensure_can_modify_public_resources(current_user)

    valid_steps = ("fetch", "ocr", "publish")
    if step and step not in valid_steps:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid step '{step}'. Must be one of: {', '.join(valid_steps)}",
        )

    if source == "all":
        requested_sources = list(INGESTION_SOURCES.keys())
    elif source in INGESTION_SOURCES:
        requested_sources = [source]
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source. Must be one of: {', '.join(sorted(INGESTION_SOURCES.keys()))}, all",
        )

    # Query Cosmos for current state
    all_jobs = _cosmos_get_all_jobs()
    running_sources: list[str] = []
    sources_to_run: list[str] = []
    for s in requested_sources:
        existing = all_jobs.get(s)
        if existing and existing.get("status") == "Running":
            running_sources.append(s)
        else:
            sources_to_run.append(s)

    if not sources_to_run:
        raise HTTPException(
            status_code=409,
            detail=f"Ingestion already running for: {', '.join(running_sources)}.",
        )

    # Fire each source -- fast since _call_df_ingestion returns after the HTTP
    # trigger responds (no polling).  Stagger slightly to avoid 429.
    results: list[dict[str, Any]] = []
    for s in sources_to_run:
        result = _start_ingestion_for_source(s, step=step)
        results.append(result)

    step_msg = f" (step={step})" if step else ""
    skipped_msg = f" (already running: {', '.join(running_sources)})" if running_sources else ""
    return {
        "success": True,
        "message": f"Ingestion started for: {', '.join(sources_to_run)}{step_msg}{skipped_msg}",
        "sources": sources_to_run,
        "skipped": running_sources,
        "jobs": {
            r["source"]: {
                "status": r["status"],
                "instance_id": r.get("instance_id"),
                "status_url": r.get("status_url"),
                "terminate_url": r.get("terminate_url"),
            }
            for r in results
        },
    }


def _compute_ocr_progress(source_key: str) -> Optional[dict[str, int]]:
    """Live OCR progress for a source by inspecting digital items in Cosmos.

    Data Foundations OCR runs asynchronously and writes ocr_status='completed'
    (or 'failed') onto each digital item once its Azure OpenAI Batch job
    finishes.  Counting those gives the *real* progress so the ingestion status
    reflects it instead of staying frozen at 'delegated' with 0/0 items.
    Returns None if the digital-items container can't be queried.
    """
    cosmos_source = INGESTION_SOURCES.get(source_key, {}).get("cosmos_source", source_key)
    # OCR-able items mirror the selection used by the DF OCR job: non-page items
    # that have an image, PDF, or files[] asset to run OCR against.
    base = (
        "FROM c WHERE c.source = @source "
        "AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page') "
        "AND (IS_DEFINED(c.primary_pdf_url) OR IS_DEFINED(c.image_url) OR IS_DEFINED(c.files))"
    )
    params = [{"name": "@source", "value": cosmos_source}]
    try:
        container = get_digital_items_container()

        def _count(extra: str = "") -> int:
            rows = list(container.query_items(
                query=f"SELECT VALUE COUNT(1) {base}{extra}",
                parameters=params,
                partition_key=cosmos_source,
            ))
            return int(rows[0]) if rows else 0

        return {
            "total": _count(),
            "completed": _count(" AND c.ocr_status = 'completed'"),
            "failed": _count(" AND c.ocr_status = 'failed'"),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not compute OCR progress for %s: %s", source_key, exc)
        return None


def _enrich_job_with_ocr_progress(
    source_key: str, job: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Overlay live OCR progress onto a job whose OCR was delegated to DF.

    For delegated / in-flight OCR this fills ocr_items_total and
    ocr_items_processed from Cosmos and flips ocr_status to 'completed' once
    every OCR-able item has been processed (or 'processing' once some have).
    Other states are returned unchanged.
    """
    if not job or job.get("ocr_status") not in ("delegated", "submitted", "processing", "completed"):
        return job
    progress = _compute_ocr_progress(source_key)
    if not progress:
        return job
    job = dict(job)  # don't mutate the cached/stored dict
    job["ocr_items_total"] = progress["total"]
    job["ocr_items_processed"] = progress["completed"]
    job["ocr_items_failed"] = progress["failed"]
    done = progress["completed"] + progress["failed"]
    if progress["total"] > 0 and done >= progress["total"]:
        job["ocr_status"] = "completed"
    elif job.get("ocr_status") in ("delegated", "submitted") and progress["completed"] > 0:
        job["ocr_status"] = "processing"
    return job


def _sync_orchestrator_status(source_key: str, job: dict[str, Any]) -> dict[str, Any]:
    """Check live orchestrator status and update Cosmos if terminal.

    When a job is Running and has a status_url, poll the orchestrator to see if
    it has completed/failed/terminated since we last checked.  If it has, update
    Cosmos so subsequent calls return the correct state.
    """
    if job.get("status") != "Running":
        return job
    status_url = job.get("status_url")
    if not status_url:
        return job
    try:
        resp = requests.get(status_url, timeout=10)
        if resp.status_code != 200:
            return job
        data = resp.json()
        runtime_status = data.get("runtimeStatus", "")
        if runtime_status in ("Completed", "Failed", "Terminated", "Canceled"):
            job = dict(job)  # don't mutate original
            job["status"] = runtime_status
            job["completed_at"] = datetime.now(timezone.utc).isoformat()
            if runtime_status == "Completed":
                output = data.get("output")
                if isinstance(output, dict):
                    job["items_ingested"] = output.get("processed", 0)
            elif runtime_status == "Failed":
                output = data.get("output")
                job["error_message"] = (
                    output if isinstance(output, str)
                    else str(output) if output
                    else "Orchestrator failed"
                )
            _cosmos_upsert_job(source_key, job)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Could not sync orchestrator status for %s: %s", source_key, exc)
    return job


@router.get("/ingestion/status")
async def get_digital_ingestion_status():
    """Get status of all digital resource ingestion jobs from Cosmos."""
    jobs = _cosmos_get_all_jobs()
    enriched = {}
    for source_key, job in jobs.items():
        job = _sync_orchestrator_status(source_key, job)
        job = _enrich_job_with_ocr_progress(source_key, job)
        enriched[source_key] = job
    return {"jobs": enriched}


@router.get("/ingestion/status/{source}")
async def get_digital_ingestion_status_for_source(source: str):
    """Get ingestion status for a specific source from Cosmos."""
    job = _cosmos_get_job(source)
    if job is None:
        return {"source": source, "status": "never_run", "job": None}
    job = _sync_orchestrator_status(source, job)
    job = _enrich_job_with_ocr_progress(source, job)
    return {"source": source, "status": job.get("status", "never_run"), "job": job}


@router.delete("/ingestion/clear/{source}")
async def clear_digital_ingestion_job(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Clear a completed/failed/stopped job from Cosmos."""
    _ensure_can_modify_public_resources(current_user)
    job = _cosmos_get_job(source)
    if job and job.get("status") == "Running":
        raise HTTPException(status_code=409, detail="Cannot clear a running job")
    _cosmos_delete_job(source)
    return {"success": True}


@router.delete("/ingestion/clear-all")
async def clear_all_digital_ingestion_jobs(
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Clear all ingestion job documents from Cosmos."""
    _ensure_can_modify_public_resources(current_user)
    all_jobs = _cosmos_get_all_jobs()
    cleared = 0
    for source_key in all_jobs:
        _cosmos_delete_job(source_key)
        cleared += 1
    return {"success": True, "cleared": cleared}


@router.post("/ingestion/stop/{source}")
async def stop_digital_ingestion(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Stop a running ingestion job.

    Marks the job as Terminated in Cosmos and terminates the Durable Functions
    orchestrator via its management URL if available.
    """
    _ensure_can_modify_public_resources(current_user)
    if source not in INGESTION_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source '{source}'. Must be one of: {', '.join(sorted(INGESTION_SOURCES.keys()))}",
        )

    job = _cosmos_get_job(source)
    if not job or job.get("status") != "Running":
        raise HTTPException(
            status_code=409,
            detail=f"No running ingestion job for source '{source}'.",
        )

    # Terminate the orchestrator if we have a terminate URL
    terminate_url = job.get("terminate_url")
    if terminate_url:
        try:
            resp = requests.post(terminate_url, timeout=10)
            logger.info(
                "Sent terminate to orchestrator for %s: HTTP %d", source, resp.status_code
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to terminate orchestrator for %s: %s", source, exc)

    job["status"] = "Terminated"
    job["completed_at"] = datetime.now(timezone.utc).isoformat()
    job["error_message"] = "Ingestion stopped by user."
    _cosmos_upsert_job(source, job, stop_requested=True)

    return {"success": True, "source": source, "message": f"Ingestion for '{source}' stopped."}


# =============================================================================
# OCR Processing â€” delegated to Data Foundations DigitalItemsOcr function
# =============================================================================

# In-memory OCR job tracker
_ocr_jobs: dict[str, dict[str, Any]] = {}
_ocr_lock = threading.Lock()


def _run_ocr(source_key: str):
    """Delegate OCR to Data Foundations via HTTP POST to DigitalItemsOcr function.

    Returns immediately after the batch job is submitted (HTTP 202 from DF).
    The DigitalItemsOcrPoller timer function (runs every 2 min) monitors the
    Azure OpenAI Batch job and writes results back to Cosmos DB within 5â€“30 min.
    """
    config = OCR_SOURCES[source_key]
    job_id = source_key

    with _ocr_lock:
        _ocr_jobs[job_id] = {
            "source": source_key,
            "label": config["label"],
            "status": "Running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "items_processed": 0,
            "items_failed": 0,
            "items_skipped": 0,
            "total_items": 0,
            "error_message": None,
            "output_lines": [],
        }

    try:
        batch_id = _call_df_ocr(config["df_source_key"])
        completed_at = datetime.now(timezone.utc).isoformat()
        with _ocr_lock:
            _ocr_jobs[job_id]["status"] = "Delegated"
            _ocr_jobs[job_id]["batch_id"] = batch_id
            _ocr_jobs[job_id]["output_lines"] = [
                f"OCR batch submitted to Data Foundations (batch_id={batch_id}).",
                "Processing is async â€” results appear in Cosmos DB within 5\u201330 minutes.",
            ]
            _ocr_jobs[job_id]["completed_at"] = completed_at
        logger.info("OCR for '%s' delegated to Data Foundations (batch_id=%s)", source_key, batch_id)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        with _ocr_lock:
            _ocr_jobs[job_id]["status"] = "Failed"
            _ocr_jobs[job_id]["error_message"] = f"Failed to submit OCR batch to Data Foundations: {exc}"
            _ocr_jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/ocr/start")
async def start_ocr_processing(
    source: str = Query(..., description="Source to OCR: tr-cyclopedia, moore-chronology, genealogy-papers, or all"),
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Trigger GPT-4 Vision OCR processing for digital resource items."""
    _ensure_can_modify_public_resources(current_user)
    if source == "all":
        sources_to_run = list(OCR_SOURCES.keys())
    elif source in OCR_SOURCES:
        sources_to_run = [source]
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source. Must be one of: {', '.join(sorted(OCR_SOURCES.keys()))}, all",
        )

    # Check if any of the requested sources are already running
    with _ocr_lock:
        for s in sources_to_run:
            existing = _ocr_jobs.get(s)
            if existing and existing["status"] == "Running":
                raise HTTPException(
                    status_code=409,
                    detail=f"OCR for '{s}' is already running.",
                )

    # Start each source in a background thread
    for s in sources_to_run:
        t = threading.Thread(target=_run_ocr, args=(s,), daemon=True)
        t.start()

    return {
        "success": True,
        "message": f"OCR started for: {', '.join(sources_to_run)}",
        "sources": sources_to_run,
    }


@router.get("/ocr/status")
async def get_ocr_status():
    """Get status of all OCR processing jobs."""
    with _ocr_lock:
        jobs = dict(_ocr_jobs)
    return {"jobs": jobs}


@router.get("/ocr/status/{source}")
async def get_ocr_status_for_source(source: str):
    """Get OCR status for a specific source."""
    with _ocr_lock:
        job = _ocr_jobs.get(source)
    if job is None:
        return {"source": source, "status": "never_run", "job": None}
    return {"source": source, "status": job["status"], "job": job}


@router.delete("/ocr/clear/{source}")
async def clear_ocr_job(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Clear a completed/failed OCR job from the tracker."""
    _ensure_can_modify_public_resources(current_user)
    with _ocr_lock:
        job = _ocr_jobs.get(source)
        if job and job["status"] == "Running":
            raise HTTPException(status_code=409, detail="Cannot clear a running job")
        _ocr_jobs.pop(source, None)
    return {"success": True}


# =============================================================================
# Publish / Unpublish to Azure AI Search (public-sources index)
# Indexed directly from archivist-api using managed identity.
# =============================================================================

# In-memory publish job tracker
_publish_jobs: dict[str, dict[str, Any]] = {}
_publish_lock = threading.Lock()

_KNOWN_PUBLIC_SOURCES: Dict[str, str] = {
    "tr-cyclopedia": "tr-cyclopedia",
    "moore-chronology": "moore-chronology",
    "genealogy-papers": "genealogy-papers",
}

_search_index_lock = threading.Lock()
_ensured_search_indexes: set[str] = set()


def _get_public_sources_index_name() -> str:
    return (
        os.getenv("AZURE_SEARCH_PUBLIC_SOURCES_INDEX")
        or os.getenv("AZURE_SEARCH_DIGITAL_ITEMS_INDEX")
        or "public-sources"
    )


def _get_search_endpoint() -> str:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
    if not endpoint:
        raise ValueError("AZURE_SEARCH_ENDPOINT is not configured")
    return endpoint


def _get_public_sources_search_client(index_name: str) -> SearchClient:
    return SearchClient(
        endpoint=_get_search_endpoint(),
        index_name=index_name,
        credential=_blob_credential,
    )


def _ensure_public_sources_index(index_name: str) -> None:
    with _search_index_lock:
        if index_name in _ensured_search_indexes:
            return

        index_client = SearchIndexClient(
            endpoint=_get_search_endpoint(),
            credential=_blob_credential,
        )

        try:
            index_client.get_index(index_name)
            _ensured_search_indexes.add(index_name)
            return
        except ResourceNotFoundError:
            pass

        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True, sortable=True),
            SimpleField(name="item_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="source_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="module_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="date_range", type=SearchFieldDataType.String, filterable=True, facetable=True, sortable=True),
            SimpleField(name="page_number", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SimpleField(name="page_count", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SimpleField(name="has_audio", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
            SimpleField(name="source_url", type=SearchFieldDataType.String),
            SimpleField(name="audio_url", type=SearchFieldDataType.String),
            SimpleField(name="image_url", type=SearchFieldDataType.String),
            SimpleField(name="published_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            SearchableField(name="title", type=SearchFieldDataType.String, filterable=True, sortable=True),
            SearchableField(name="description", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchField(
                name="section_titles",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
            SearchField(
                name="linked_topic_titles",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                searchable=True,
                filterable=True,
                facetable=True,
            ),
        ]

        semantic = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="public-sources-semantic-config",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        prioritized_content_fields=[SemanticField(field_name="content")],
                        prioritized_keywords_fields=[
                            SemanticField(field_name="source"),
                            SemanticField(field_name="item_type"),
                        ],
                    ),
                )
            ]
        )

        index = SearchIndex(name=index_name, fields=fields, semantic_search=semantic)
        index_client.create_or_update_index(index)
        _ensured_search_indexes.add(index_name)
        logger.info("Created/verified Azure Search index '%s' for public resources", index_name)


def _download_blob_text_with_mi(blob_url: str) -> str:
    if not blob_url:
        return ""
    try:
        blob_client = BlobClient.from_blob_url(blob_url, credential=_blob_credential)
        data = blob_client.download_blob().readall()
        return data.decode("utf-8")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to download blob text from %s: %s", blob_url, exc)
        return ""


def _flatten_content(item: dict) -> tuple[str, List[str], List[str]]:
    section_titles: List[str] = []
    linked_topic_titles: List[str] = []
    parts: List[str] = []

    for key in ("title", "description", "plain_text", "ocr_text_original"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())

    flexible_url = item.get("ocr_text_flexible_blob_url", "")
    original_url = item.get("ocr_text_original_blob_url", "")
    blob_text = _download_blob_text_with_mi(flexible_url) or _download_blob_text_with_mi(original_url)
    if blob_text.strip():
        parts.append(blob_text.strip())

    for key in ("document_sections", "speech_sections", "toc_entries", "cyclopedia_entries"):
        val = item.get(key)
        if isinstance(val, list):
            for entry in val:
                if not isinstance(entry, dict):
                    continue
                title = (
                    entry.get("heading")
                    or entry.get("title")
                    or entry.get("name")
                    or ""
                )
                body = entry.get("body") or entry.get("content") or ""
                if isinstance(title, str) and title.strip():
                    section_titles.append(title.strip())
                    parts.append(title.strip())
                if isinstance(body, str) and body.strip():
                    parts.append(body.strip())

    linked_topics = item.get("linked_topics")
    if isinstance(linked_topics, list):
        for topic in linked_topics:
            if not isinstance(topic, dict):
                continue
            title = topic.get("title") or ""
            summary = topic.get("summary") or ""
            if isinstance(title, str) and title.strip():
                linked_topic_titles.append(title.strip())
                parts.append(title.strip())
            if isinstance(summary, str) and summary.strip():
                parts.append(summary.strip())

    return "\n\n".join(parts).strip(), section_titles, linked_topic_titles


def _build_public_source_search_doc(item: dict, now_iso: str) -> Optional[dict]:
    content, section_titles, linked_topic_titles = _flatten_content(item)
    if not content:
        return None

    doc = {
        "id": item.get("id"),
        "source": item.get("source"),
        "item_type": item.get("item_type"),
        "source_id": item.get("source_id"),
        "parent_id": item.get("parent_id"),
        "module_id": item.get("module_id"),
        "title": item.get("title"),
        "description": item.get("description"),
        "content": content,
        "section_titles": sorted(set(section_titles)),
        "linked_topic_titles": sorted(set(linked_topic_titles)),
        "date_range": item.get("date_range"),
        "page_number": item.get("page_number"),
        "page_count": item.get("page_count"),
        "has_audio": item.get("has_audio"),
        "source_url": item.get("source_url"),
        "audio_url": item.get("audio_url"),
        "image_url": item.get("image_url"),
        "published_at": now_iso,
    }
    return {k: v for k, v in doc.items() if v is not None}


def _load_items_for_source(source_key: str) -> List[dict]:
    cosmos_source = _KNOWN_PUBLIC_SOURCES[source_key]
    container = get_digital_items_container()
    query = (
        "SELECT * FROM c WHERE c.source = @source "
        "AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page')"
    )
    params = [{"name": "@source", "value": cosmos_source}]
    return list(
        container.query_items(
            query=query,
            parameters=params,
            partition_key=cosmos_source,
        )
    )


def _run_publish(source_key: str):
    """Publish all items for a source into Azure Search public-sources index."""
    job_id = f"publish-{source_key}"

    with _publish_lock:
        _publish_jobs[job_id] = {
            "source": source_key,
            "action": "publish",
            "status": "Running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "items_published": 0,
            "items_failed": 0,
            "total_items": 0,
            "error_message": None,
        }

    try:
        cosmos_source = _KNOWN_PUBLIC_SOURCES[source_key]
        index_name = _get_public_sources_index_name()
        _ensure_public_sources_index(index_name)
        search_client = _get_public_sources_search_client(index_name)
        container = get_digital_items_container()
        items = _load_items_for_source(source_key)

        now_iso = datetime.now(timezone.utc).isoformat()
        docs_to_upload: List[dict] = []

        for item in items:
            doc = _build_public_source_search_doc(item, now_iso)
            if doc is None:
                continue
            docs_to_upload.append(doc)

        items_published = 0
        items_failed = 0
        succeeded_doc_ids: List[str] = []
        batch_size = 100
        for i in range(0, len(docs_to_upload), batch_size):
            batch = docs_to_upload[i:i + batch_size]
            results = search_client.upload_documents(documents=batch)
            for result in results:
                if getattr(result, "succeeded", False):
                    items_published += 1
                    key = getattr(result, "key", None)
                    if isinstance(key, str) and key:
                        succeeded_doc_ids.append(key)
                else:
                    items_failed += 1

        for doc_id in succeeded_doc_ids:
            try:
                item = container.read_item(item=doc_id, partition_key=cosmos_source)
                item["published_at"] = now_iso
                item["publish_status"] = "published"
                container.upsert_item(body=item)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to update publish status for item %s: %s", doc_id, exc)

        result = {
            "items_published": items_published,
            "items_failed": items_failed,
            "total_items": len(items),
        }
        completed_at = datetime.now(timezone.utc).isoformat()
        with _publish_lock:
            _publish_jobs[job_id]["status"] = "Completed"
            _publish_jobs[job_id]["items_published"] = result.get("items_published", 0)
            _publish_jobs[job_id]["items_failed"] = result.get("items_failed", 0)
            _publish_jobs[job_id]["total_items"] = result.get("total_items", 0)
            _publish_jobs[job_id]["completed_at"] = completed_at
    except Exception as exc:  # pylint: disable=broad-exception-caught
        err_str = str(exc)
        if "Forbidden" in err_str or "403" in err_str:
            msg = (
                "Search index publish failed: Forbidden (403). "
                "The archivist-api managed identity needs 'Search Index Data Contributor' "
                "and 'Search Service Contributor' roles on the Azure AI Search service. "
                "Run 'azd provision' to apply the updated infra RBAC, then retry."
            )
        else:
            msg = f"Publish failed: {exc}"
        with _publish_lock:
            _publish_jobs[job_id]["status"] = "Failed"
            _publish_jobs[job_id]["error_message"] = msg
            _publish_jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


def _run_unpublish(source_key: str):
    """Unpublish all items for a source from Azure Search public-sources index."""
    job_id = f"unpublish-{source_key}"

    with _publish_lock:
        _publish_jobs[job_id] = {
            "source": source_key,
            "action": "unpublish",
            "status": "Running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "items_removed": 0,
            "total_items": 0,
            "error_message": None,
        }

    try:
        cosmos_source = _KNOWN_PUBLIC_SOURCES[source_key]
        index_name = _get_public_sources_index_name()
        _ensure_public_sources_index(index_name)
        search_client = _get_public_sources_search_client(index_name)
        container = get_digital_items_container()

        items = _load_items_for_source(source_key)
        doc_ids = [item.get("id") for item in items if isinstance(item.get("id"), str)]

        batch_size = 500
        items_removed = 0
        for i in range(0, len(doc_ids), batch_size):
            batch = doc_ids[i:i + batch_size]
            if not batch:
                continue
            search_client.delete_documents(documents=[{"id": did} for did in batch])
            items_removed += len(batch)

        for doc_id in doc_ids:
            try:
                item = container.read_item(item=doc_id, partition_key=cosmos_source)
                item.pop("published_at", None)
                item["publish_status"] = "unpublished"
                container.upsert_item(body=item)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to update unpublish status for item %s: %s", doc_id, exc)

        result = {
            "items_removed": items_removed,
            "total_items": len(items),
        }
        completed_at = datetime.now(timezone.utc).isoformat()
        with _publish_lock:
            _publish_jobs[job_id]["status"] = "Completed"
            _publish_jobs[job_id]["items_removed"] = result.get("items_removed", 0)
            _publish_jobs[job_id]["total_items"] = result.get("total_items", 0)
            _publish_jobs[job_id]["completed_at"] = completed_at
    except Exception as exc:  # pylint: disable=broad-exception-caught
        err_str = str(exc)
        if "Forbidden" in err_str or "403" in err_str:
            msg = (
                "Search index unpublish failed: Forbidden (403). "
                "The archivist-api managed identity needs 'Search Index Data Contributor' "
                "on the Azure AI Search service. Run 'azd provision' to apply RBAC, then retry."
            )
        else:
            msg = f"Unpublish failed: {exc}"
        with _publish_lock:
            _publish_jobs[job_id]["status"] = "Failed"
            _publish_jobs[job_id]["error_message"] = msg
            _publish_jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/publish/{source}")
async def publish_digital_items(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Publish all OCR'd items for a source to the digital-items search index."""
    _ensure_can_modify_public_resources(current_user)
    if source not in _KNOWN_PUBLIC_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source. Must be one of: {', '.join(sorted(_KNOWN_PUBLIC_SOURCES.keys()))}",
        )

    job_id = f"publish-{source}"
    with _publish_lock:
        existing = _publish_jobs.get(job_id)
        if existing and existing["status"] == "Running":
            raise HTTPException(status_code=409, detail=f"Publish for '{source}' is already running.")

    t = threading.Thread(target=_run_publish, args=(source,), daemon=True)
    t.start()

    return {"success": True, "message": f"Publishing started for '{source}'", "source": source}


@router.post("/unpublish/{source}")
async def unpublish_digital_items(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user_or_mock),
):
    """Remove all items for a source from the digital-items search index."""
    _ensure_can_modify_public_resources(current_user)
    if source not in _KNOWN_PUBLIC_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source. Must be one of: {', '.join(sorted(_KNOWN_PUBLIC_SOURCES.keys()))}",
        )

    job_id = f"unpublish-{source}"
    with _publish_lock:
        existing = _publish_jobs.get(job_id)
        if existing and existing["status"] == "Running":
            raise HTTPException(status_code=409, detail=f"Unpublish for '{source}' is already running.")

    t = threading.Thread(target=_run_unpublish, args=(source,), daemon=True)
    t.start()

    return {"success": True, "message": f"Unpublishing started for '{source}'", "source": source}


@router.get("/publish/status")
async def get_publish_status():
    """Get status of all publish/unpublish jobs."""
    with _publish_lock:
        jobs = dict(_publish_jobs)
    return {"jobs": jobs}


@router.get("/publish/status/{source}")
async def get_publish_status_for_source(source: str):
    """Get publish status for a specific source."""
    with _publish_lock:
        publish_job = _publish_jobs.get(f"publish-{source}")
        unpublish_job = _publish_jobs.get(f"unpublish-{source}")
    return {"source": source, "publish": publish_job, "unpublish": unpublish_job}


# =============================================================================
# Per-item Review / Publish / Unpublish
# =============================================================================

@router.post("/items/{item_id}/review")
async def mark_item_reviewed(
    item_id: str,
    source: str = Query(..., description="Partition key"),
):
    """Mark a digital item as reviewed (human-verified). Required before publishing for Moore's items."""
    container = get_digital_items_container()
    try:
        doc = container.read_item(item=item_id, partition_key=source)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found") from exc

    doc["publish_status"] = "reviewed"
    doc["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    container.upsert_item(body=doc)

    return {"success": True, "message": f"Item '{item_id}' marked as reviewed", "publish_status": "reviewed"}


@router.post("/items/{item_id}/publish")
async def publish_single_item(
    item_id: str,
    source: str = Query(..., description="Partition key"),
):
    """Publish a single item to the public-sources search index."""
    if source not in _KNOWN_PUBLIC_SOURCES.values():
        raise HTTPException(status_code=400, detail="Invalid source partition key")

    container = get_digital_items_container()
    try:
        item = container.read_item(item=item_id, partition_key=source)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'") from exc

    try:
        index_name = _get_public_sources_index_name()
        _ensure_public_sources_index(index_name)
        search_client = _get_public_sources_search_client(index_name)

        now_iso = datetime.now(timezone.utc).isoformat()
        search_doc = _build_public_source_search_doc(item, now_iso)
        if search_doc is None:
            raise HTTPException(status_code=400, detail="Item has no text content to publish")

        result = search_client.upload_documents(documents=[search_doc])
        if not result or not getattr(result[0], "succeeded", False):
            error_message = getattr(result[0], "error_message", "Unknown indexing error") if result else "No indexing result returned"
            raise HTTPException(status_code=500, detail=f"Search upload failed: {error_message}")

        item["published_at"] = now_iso
        item["publish_status"] = "published"
        container.upsert_item(body=item)
        return {
            "success": True,
            "message": f"Item '{item_id}' published to search index",
            "published_at": now_iso,
        }
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Publish failed: {exc}") from exc


@router.post("/items/{item_id}/unpublish")
async def unpublish_single_item(
    item_id: str,
    source: str = Query(..., description="Partition key"),
):
    """Remove a single item from the public-sources search index."""
    if source not in _KNOWN_PUBLIC_SOURCES.values():
        raise HTTPException(status_code=400, detail="Invalid source partition key")

    container = get_digital_items_container()
    try:
        item = container.read_item(item=item_id, partition_key=source)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'") from exc

    try:
        index_name = _get_public_sources_index_name()
        _ensure_public_sources_index(index_name)
        search_client = _get_public_sources_search_client(index_name)
        search_client.delete_documents(documents=[{"id": item_id}])

        item.pop("published_at", None)
        item["publish_status"] = "unpublished"
        container.upsert_item(body=item)
        return {
            "success": True,
            "message": f"Item '{item_id}' removed from search index",
        }
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Unpublish failed: {exc}") from exc


# =============================================================================
# OCR Text Editing (Modified/Flexible OCR)
# =============================================================================

@router.patch("/items/{item_id}/ocr")
async def update_digital_item_ocr(
    item_id: str,
    source: str = Query(..., description="Partition key (moore-chronology, cyclopedia, genealogy-papers)"),
    ocr_request: OcrUpdateRequest = Body(...),
):
    """
    Update the flexible (modified) OCR text for a digital item.

    Creates a new version of the flexible blob, preserving the original.
    Records the change in the audit trail.
    """
    container = get_digital_items_container()
    try:
        doc = container.read_item(item=item_id, partition_key=source)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'") from exc

    if not ocr_request.ocr_text or not ocr_request.ocr_text.strip():
        raise HTTPException(status_code=400, detail="ocr_text cannot be empty")

    # Get current blob URLs
    original_blob_url = doc.get("ocr_text_original_blob_url", "")
    old_flexible_blob_url = doc.get("ocr_text_flexible_blob_url", "")

    if not original_blob_url:
        raise HTTPException(
            status_code=400,
            detail="Item has no blob-based OCR. Run OCR processing first."
        )

    # Check if text actually changed
    old_text = ""
    if old_flexible_blob_url:
        old_text = await _download_ocr_blob(old_flexible_blob_url)
        if old_text.strip() == ocr_request.ocr_text.strip():
            return {"success": True, "message": "No changes detected", "version": doc.get("ocr_version", 1)}
    else:
        # First edit: the "old" text is the original OCR
        old_text = await _download_ocr_blob(original_blob_url)

    # Determine new version
    current_version = doc.get("ocr_version", 1)
    new_version = current_version + 1

    # Derive container name and base URL from existing blob URL
    parsed = urlparse(original_blob_url)
    path_parts = parsed.path.lstrip("/").split("/", 1)
    blob_container_name = path_parts[0] if path_parts else "digital-items"
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Upload new flexible blob version
    new_blob_path = f"{item_id}/ocr/flexible/{item_id}/v{new_version}.txt"
    new_flexible_blob_url = f"{base_url}/{blob_container_name}/{new_blob_path}"

    try:
        upload_to_blob(
            container_name=blob_container_name,
            blob=new_blob_path,
            content=ocr_request.ocr_text,
        )
    except (AzureError, Exception) as e:
        logger.exception("Failed to upload OCR blob for %s: %s", item_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload OCR text to blob storage: {str(e)}"
        ) from e

    # Update Cosmos document with new flexible URL and version
    doc["ocr_text_flexible_blob_url"] = new_flexible_blob_url
    doc["ocr_version"] = new_version
    doc["ocr_last_modified"] = datetime.now(timezone.utc).isoformat()
    container.upsert_item(body=doc)

    # Create audit entry with actual text content
    try:
        audit_container = _get_digital_items_audit_container()
        audit_service = AuditService(audit_container)

        # Store actual text in audit (truncate to 2000 chars for storage)
        max_audit_len = 2000
        old_snippet = old_text[:max_audit_len] if old_text else ""
        new_snippet = ocr_request.ocr_text[:max_audit_len]

        field_changes = [
            {
                "path": "/ocr_text",
                "from": old_snippet,
                "to": new_snippet,
            },
            {
                "path": "/ocr_text_flexible_blob_url",
                "from": old_flexible_blob_url or "",
                "to": new_flexible_blob_url,
            },
        ]

        audit_service.create_audit_entry(
            entity_id=item_id,
            user_id="system",  # auth not yet wired on this endpoint
            user_display="System",
            operation="update",
            version=new_version,
            changed_fields=field_changes,
            correlation_id=ocr_request.correlation_id,
        )
        logger.info("Created OCR audit entry for item %s, version %d", item_id, new_version)
    except Exception as audit_err:
        logger.warning("Failed to create audit entry for %s: %s", item_id, audit_err)
        # Non-fatal: OCR was saved, audit just failed

    return {
        "success": True,
        "message": f"OCR text updated (version {new_version})",
        "version": new_version,
        "ocr_text_flexible_blob_url": new_flexible_blob_url,
    }


@router.get("/items/{item_id}/ocr/compare")
async def compare_digital_item_ocr(
    item_id: str,
    source: str = Query(..., description="Partition key"),
    page_number: Optional[int] = Query(None, description="Page number (1-indexed) to compare, or None for full text"),
):
    """
    Compare original vs modified OCR text for a digital item.

    Returns both versions side-by-side so the frontend can render a diff view.
    """
    container = get_digital_items_container()
    try:
        doc = container.read_item(item=item_id, partition_key=source)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found in source '{source}'") from exc

    original_blob_url = doc.get("ocr_text_original_blob_url", "")
    flexible_blob_url = doc.get("ocr_text_flexible_blob_url", "")

    if not original_blob_url:
        # Legacy inline text
        original_text = doc.get("ocr_text_original", "")
        flexible_text = original_text
    else:
        original_text = await _download_ocr_blob(original_blob_url)
        flexible_text = await _download_ocr_blob(flexible_blob_url) if flexible_blob_url else original_text

    # If page_number is specified, split and return that page only
    if page_number is not None:
        orig_pages = original_text.split("--- Page Break ---")
        flex_pages = flexible_text.split("--- Page Break ---")

        orig_page = orig_pages[page_number - 1].strip() if 1 <= page_number <= len(orig_pages) else ""
        flex_page = flex_pages[page_number - 1].strip() if 1 <= page_number <= len(flex_pages) else ""

        return {
            "item_id": item_id,
            "source": source,
            "page_number": page_number,
            "ocr_text_original": orig_page,
            "ocr_text_flexible": flex_page,
            "has_edits": orig_page != flex_page,
            "ocr_version": doc.get("ocr_version", 1),
        }

    return {
        "item_id": item_id,
        "source": source,
        "ocr_text_original": original_text,
        "ocr_text_flexible": flexible_text,
        "has_edits": original_text != flexible_text,
        "ocr_version": doc.get("ocr_version", 1),
    }


# =============================================================================
# OCR Audit History
# =============================================================================

@router.get("/items/{item_id}/ocr/history")
async def get_digital_item_ocr_history(
    item_id: str,
    source: str = Query(..., description="Partition key"),
    max_items: int = Query(50, le=200),
):
    """
    Get OCR edit history for a digital item.

    Returns audit trail entries showing who changed OCR text, when, and version transitions.
    """
    try:
        audit_container = _get_digital_items_audit_container()
        audit_service = AuditService(audit_container)
        entries = audit_service.get_audit_history(item_id, max_items=max_items)

        return {
            "item_id": item_id,
            "source": source,
            "total_entries": len(entries),
            "entries": entries,
        }
    except Exception as e:
        logger.error("Failed to get OCR history for %s: %s", item_id, e)
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve OCR edit history. Please try again."
        ) from e

