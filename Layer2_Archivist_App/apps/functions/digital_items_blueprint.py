# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Azure Functions Blueprint: digital-items HTTP triggers.

These HTTP-triggered functions handle:
  - Ingesting digital items from external sources (Wikipedia, TRC, TRA) into
    Cosmos DB using Durable Functions for long-running orchestration.
  - Indexing digital items (Moore Chronology, TR Cyclopedia, Genealogy Papers)
    into Azure AI Search using the function app's system-assigned managed identity.

Why here instead of archivist-api?
  - The function app already has 'Search Index Data Contributor' on the search
    service (scoped to entire service, covers all indexes including digital-items).
  - No API key is needed — RBAC via DefaultAzureCredential is used throughout.
  - Blob downloads also use managed identity (function app has Storage Blob Data
    Contributor on the storage account).
  - Long-running ingests and publishes are safe: host.json sets functionTimeout
    to 2 hours, and Durable Functions orchestrators have no timeout at all.

Routes (all require ?code=<function-host-key>):
  POST /api/digital-items/ingest/{source_key}   — Durable orchestrator (returns 202)
  POST /api/digital-items/publish/{source_key}
  POST /api/digital-items/unpublish/{source_key}
  POST /api/digital-items/publish-item/{item_id}?source={source}
  POST /api/digital-items/unpublish-item/{item_id}?source={source}

Called from archivist-api (apps/archivist-api/api/digital_items_routes.py)
using DATA_INGEST_FUNCTION_URL + DATA_INGEST_FUNCTION_CODE.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import azure.durable_functions as df
import azure.functions as func

from helper.constants import KNOWN_SOURCES, NO_OCR_SOURCES, REFERER_MAP, get_referer

blueprint = func.Blueprint()

logger = logging.getLogger(__name__)  # pylint: disable=duplicate-code
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

# Aliases for local references
_KNOWN_SOURCES: Dict[str, str] = KNOWN_SOURCES
_NO_OCR_SOURCES = NO_OCR_SOURCES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_search_index() -> str:
    return os.getenv("AZURE_SEARCH_DIGITAL_ITEMS_INDEX", "digital-items")


def _get_search_client(index_name: str):
    """Return a SearchClient authenticated via DefaultAzureCredential (RBAC)."""
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    if not endpoint:
        raise ValueError("AZURE_SEARCH_ENDPOINT is not configured")
    return SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=DefaultAzureCredential(),
    )


def _download_blob_text(blob_url: str) -> str:
    """Download blob text content using managed identity. Returns '' on any error."""
    if not blob_url:
        return ""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        blob_client = BlobClient.from_blob_url(
            blob_url, credential=DefaultAzureCredential()
        )
        data = blob_client.download_blob().readall()
        return data.decode("utf-8")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to download blob %s: %s", blob_url, exc)
        return ""


def _upload_item_text_to_blob(item_id: str, text: str) -> Optional[str]:
    """Upload plain text to blob storage, return full URL or None on failure.

    Uses the same storage account and container as OCR text uploads.
    Blob path: {item_id}/text/plain_text.txt
    """
    from helper.blob_util import upload_blob_content

    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "digital-items-ocr")
    blob_path = f"{item_id}/text/plain_text.txt"
    return upload_blob_content(blob_path, text.encode("utf-8"), "text/plain; charset=utf-8", container_name)


def _internalize_asset(item_id: str, asset_url: str, asset_suffix: str = "source.pdf") -> Optional[str]:
    """Download an external asset and upload it to Azure Blob Storage.

    Returns the Azure blob URL on success, or None on failure.
    Blob path: {item_id}/assets/{asset_suffix}
    """
    import requests as _requests
    from helper.blob_util import upload_blob_content

    if not asset_url or ".blob.core.windows.net" in asset_url:
        return None  # Already internalized or empty

    try:
        # Download external asset
        logger.info("Downloading external asset for %s: %s", item_id, asset_url[:120])
        referer = get_referer(asset_url)
        download_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": referer,
            "Origin": referer.rstrip("/"),
        }
        resp = _requests.get(asset_url, headers=download_headers, timeout=120, stream=True)
        resp.raise_for_status()
        content = resp.content
        logger.info("Downloaded %d bytes for %s", len(content), item_id)

        # Determine content type
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        if asset_suffix.endswith(".pdf"):
            content_type = "application/pdf"
        elif asset_suffix.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        elif asset_suffix.endswith(".png"):
            content_type = "image/png"
        elif asset_suffix.endswith(".gif"):
            content_type = "image/gif"
        elif asset_suffix.endswith(".webp"):
            content_type = "image/webp"
        elif asset_suffix.endswith(".svg"):
            content_type = "image/svg+xml"

        blob_path = f"{item_id}/assets/{asset_suffix}"
        blob_url = upload_blob_content(blob_path, content, content_type)
        if blob_url:
            logger.info("Internalized asset for %s: %s -> %s", item_id, asset_url[:80], blob_url)
        return blob_url
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to internalize asset for %s from %s: %s (type=%s)", item_id, asset_url[:120], exc, type(exc).__name__)
        return None


def _generate_pdf_thumbnail(item_id: str, pdf_blob_url: str) -> Optional[str]:
    """Render the first page of an internalized PDF as a JPEG thumbnail.

    Downloads the PDF from blob storage, renders page 1 at 150 DPI,
    uploads the JPEG back to blob storage, and returns the blob URL.
    """
    import pymupdf
    from helper.blob_util import get_blob_service, upload_blob_content

    container_name = os.getenv("DIGITAL_ITEMS_ASSETS_CONTAINER", "digital-resources")

    try:
        svc = get_blob_service()
        if not svc:
            return None
        bsc, _account_url = svc

        container_client = bsc.get_container_client(container_name)

        # Download the PDF from blob storage
        # Extract blob path from URL: .../container_name/blob_path
        blob_path_start = pdf_blob_url.find(f"/{container_name}/") + len(container_name) + 2
        pdf_blob_path = pdf_blob_url[blob_path_start:]
        pdf_blob_client = container_client.get_blob_client(pdf_blob_path)
        pdf_bytes = pdf_blob_client.download_blob().readall()

        if len(pdf_bytes) < 100:
            logger.warning("PDF too small for thumbnail: %s (%d bytes)", item_id, len(pdf_bytes))
            return None

        # Render first page as JPEG
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            mat = pymupdf.Matrix(150 / 72, 150 / 72)  # 150 DPI
            pix = page.get_pixmap(matrix=mat)
            thumb_bytes = pix.tobytes("jpeg")
        finally:
            doc.close()

        # Upload thumbnail
        thumb_blob_path = f"{item_id}/assets/thumbnail.jpg"
        thumb_url = upload_blob_content(thumb_blob_path, thumb_bytes, "image/jpeg", container_name)
        if thumb_url:
            logger.info("Generated PDF thumbnail for %s: %s (%d bytes)", item_id, thumb_url, len(thumb_bytes))
        return thumb_url
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to generate PDF thumbnail for %s: %s", item_id, exc)
        return None


def _build_search_doc(item: dict, cosmos_source: str, now_iso: str) -> Optional[dict]:
    """Build a search document from a Cosmos item. Returns None if no text content.

    NOTE: For items with multi-page OCR text, use _build_search_docs_split() instead.
    This returns a single document (used for plain_text items like cyclopedia).
    """
    text_content = _get_item_text_content(item)
    if not text_content.strip():
        return None

    doc = {
        "id": item["id"],
        "source": item.get("source", cosmos_source),
        "item_type": item.get("item_type"),
        "parent_id": item.get("parent_id"),
        "title": item.get("title"),
        "date_range": item.get("date_range"),
        "ocr_text": text_content,
        "plain_text": item.get("plain_text"),
        "page_number": item.get("page_number"),
        "page_count": item.get("page_count"),
        "published_at": now_iso,
    }
    # Azure Search does not accept null values for typed fields
    return {k: v for k, v in doc.items() if v is not None}


_PAGE_BREAK_DELIMITER = "\n\n--- Page Break ---\n\n"


def _get_item_text_content(item: dict) -> str:
    """Download/resolve text content for a Cosmos item."""
    text_content = ""
    flexible_url = item.get("ocr_text_flexible_blob_url", "")
    original_url = item.get("ocr_text_original_blob_url", "")
    plain_text_url = item.get("plain_text_blob_url", "")

    if flexible_url:
        text_content = _download_blob_text(flexible_url)
    if not text_content and original_url:
        text_content = _download_blob_text(original_url)
    if not text_content and plain_text_url:
        text_content = _download_blob_text(plain_text_url)
    if not text_content:
        text_content = item.get("ocr_text_original") or item.get("plain_text") or ""
    return text_content


def _build_search_docs_split(item: dict, cosmos_source: str, now_iso: str) -> List[dict]:
    """Build per-page search documents from a Cosmos item with multi-page OCR text.

    Splits OCR text on '--- Page Break ---' and creates one search doc per page.
    Falls back to a single doc if no page breaks exist.
    Returns empty list if no text content.
    """
    text_content = _get_item_text_content(item)
    if not text_content.strip():
        return []

    item_id = item["id"]
    source = item.get("source", cosmos_source)
    item_type = item.get("item_type")
    parent_id = item.get("parent_id")
    title = item.get("title")
    date_range = item.get("date_range")

    # Split by page break delimiter
    pages = text_content.split(_PAGE_BREAK_DELIMITER)
    page_count = len(pages)

    # Overlap: prepend tail of previous page for search continuity across breaks
    overlap_chars = 200

    docs = []
    for page_num, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue

        # Add overlap from previous page for better search recall
        if page_num > 1 and page_count > 1:
            prev_text = pages[page_num - 2]
            overlap = prev_text[-overlap_chars:] if len(prev_text) > overlap_chars else prev_text
            searchable_text = overlap.strip() + " " + page_text
        else:
            searchable_text = page_text

        doc = {
            "id": f"{item_id}_p{page_num}" if page_count > 1 else item_id,
            "source": source,
            "item_type": item_type,
            "parent_id": parent_id or item_id if page_count > 1 else parent_id,
            "title": f"{title} - Page {page_num}" if page_count > 1 and title else title,
            "date_range": date_range,
            "ocr_text": searchable_text,
            "page_number": page_num,
            "page_count": page_count,
            "published_at": now_iso,
        }
        docs.append({k: v for k, v in doc.items() if v is not None})

    return docs


def _json_response(data: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data),
        status_code=status_code,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Publish source
# ---------------------------------------------------------------------------

@blueprint.function_name(name="PublishDigitalItemsHttp")
@blueprint.route(
    route="digital-items/publish/{source_key}",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def publish_digital_items_http(req: func.HttpRequest) -> func.HttpResponse:
    """Publish all OCR'd items for a source to the digital-items search index.

    Returns JSON:
      { "total_items": int, "items_published": int, "items_failed": int }
    """
    source_key = req.route_params.get("source_key", "")
    if source_key not in _KNOWN_SOURCES:
        return _json_response(
            {"error": f"Unknown source '{source_key}'. Valid: {sorted(_KNOWN_SOURCES)}"},
            status_code=400,
        )

    cosmos_source = _KNOWN_SOURCES[source_key]
    index_name = _get_search_index()

    logger.info("PublishDigitalItemsHttp: starting publish for source=%s", source_key)

    try:
        from helper.digital_items_cosmos import get_digital_items_container

        container = get_digital_items_container()
        search_client = _get_search_client(index_name)

        # Query items that have any form of text content
        query = (
            "SELECT c.id, c.source, c.item_type, c.parent_id, c.title, c.date_range, "
            "c.ocr_text_original, c.plain_text, c.page_number, c.page_count, "
            "c.ocr_text_original_blob_url, c.ocr_text_flexible_blob_url, "
            "c.plain_text_blob_url "
            "FROM c WHERE c.source = @source "
            "AND (IS_DEFINED(c.ocr_text_original) OR IS_DEFINED(c.plain_text) "
            "OR IS_DEFINED(c.ocr_text_original_blob_url) "
            "OR IS_DEFINED(c.plain_text_blob_url))"
        )
        params = [{"name": "@source", "value": cosmos_source}]
        items: List[dict] = list(
            container.query_items(
                query=query, parameters=params, partition_key=cosmos_source
            )
        )

        total_items = len(items)
        logger.info("PublishDigitalItemsHttp: found %d items for source=%s", total_items, source_key)

        if not items:
            return _json_response(
                {
                    "total_items": 0,
                    "items_published": 0,
                    "items_failed": 0,
                    "message": "No items with text content found for this source.",
                }
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        batch_size = 100
        published_count = 0
        failed_count = 0

        # Build all search docs (splitting multi-page OCR into per-page docs)
        all_search_docs: List[dict] = []
        for item in items:
            all_search_docs.extend(_build_search_docs_split(item, cosmos_source, now_iso))

        if not all_search_docs:
            return _json_response(
                {
                    "total_items": total_items,
                    "items_published": 0,
                    "items_failed": 0,
                    "message": "No search documents could be built (empty text content).",
                }
            )

        for i in range(0, len(all_search_docs), batch_size):
            batch = all_search_docs[i : i + batch_size]

            try:
                results = search_client.upload_documents(documents=batch)
                for r in results:
                    if r.succeeded:
                        published_count += 1
                    else:
                        failed_count += 1
                        logger.error(
                            "PublishDigitalItemsHttp: search upload failed for id=%s: %s",
                            r.key,
                            r.error_message,
                        )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("PublishDigitalItemsHttp: batch upload error: %s", exc)
                failed_count += len(batch)

        # Update publish_status in Cosmos for items that had any content
        items_with_content = [
            item["id"]
            for item in items
            if (
                item.get("ocr_text_original")
                or item.get("plain_text")
                or item.get("ocr_text_original_blob_url")
                or item.get("plain_text_blob_url")
            )
        ]
        for doc_id in items_with_content:
            try:
                doc = container.read_item(item=doc_id, partition_key=cosmos_source)
                doc["published_at"] = now_iso
                doc["publish_status"] = "published"
                doc["status"] = "Published"
                container.upsert_item(body=doc)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "PublishDigitalItemsHttp: failed to update Cosmos for id=%s: %s",
                    doc_id,
                    exc,
                )

        logger.info(
            "PublishDigitalItemsHttp: completed source=%s published=%d failed=%d total=%d",
            source_key,
            published_count,
            failed_count,
            total_items,
        )
        return _json_response(
            {
                "total_items": total_items,
                "items_published": published_count,
                "items_failed": failed_count,
            }
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("PublishDigitalItemsHttp: unhandled error for source=%s", source_key)
        return _json_response({"error": f"Publish failed: {exc}"}, status_code=500)


# ---------------------------------------------------------------------------
# Unpublish source
# ---------------------------------------------------------------------------

@blueprint.function_name(name="UnpublishDigitalItemsHttp")
@blueprint.route(
    route="digital-items/unpublish/{source_key}",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def unpublish_digital_items_http(req: func.HttpRequest) -> func.HttpResponse:
    """Remove all published items for a source from the digital-items search index.

    Returns JSON:
      { "total_items": int, "items_removed": int }
    """
    source_key = req.route_params.get("source_key", "")
    if source_key not in _KNOWN_SOURCES:
        return _json_response(
            {"error": f"Unknown source '{source_key}'. Valid: {sorted(_KNOWN_SOURCES)}"},
            status_code=400,
        )

    cosmos_source = _KNOWN_SOURCES[source_key]
    index_name = _get_search_index()

    logger.info("UnpublishDigitalItemsHttp: starting unpublish for source=%s", source_key)

    try:
        from helper.digital_items_cosmos import get_digital_items_container

        container = get_digital_items_container()
        search_client = _get_search_client(index_name)

        # Query only items that are currently published
        query = (
            "SELECT c.id FROM c WHERE c.source = @source "
            "AND IS_DEFINED(c.published_at) AND c.publish_status = 'published'"
        )
        params = [{"name": "@source", "value": cosmos_source}]
        items: List[dict] = list(
            container.query_items(
                query=query, parameters=params, partition_key=cosmos_source
            )
        )

        total_items = len(items)
        logger.info(
            "UnpublishDigitalItemsHttp: found %d published items for source=%s",
            total_items,
            source_key,
        )

        if not items:
            return _json_response(
                {
                    "total_items": 0,
                    "items_removed": 0,
                    "message": "No published items found for this source.",
                }
            )

        doc_ids = [item["id"] for item in items]
        batch_size = 500
        removed_count = 0

        for i in range(0, len(doc_ids), batch_size):
            batch = doc_ids[i : i + batch_size]
            try:
                search_client.delete_documents(documents=[{"id": did} for did in batch])
                removed_count += len(batch)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("UnpublishDigitalItemsHttp: search delete batch error: %s", exc)

        # Clear publish metadata in Cosmos
        for doc_id in doc_ids:
            try:
                doc = container.read_item(item=doc_id, partition_key=cosmos_source)
                doc.pop("published_at", None)
                doc["publish_status"] = "unpublished"
                doc["status"] = "Extracted"
                container.upsert_item(body=doc)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "UnpublishDigitalItemsHttp: failed to update Cosmos for id=%s: %s",
                    doc_id,
                    exc,
                )

        logger.info(
            "UnpublishDigitalItemsHttp: completed source=%s removed=%d total=%d",
            source_key,
            removed_count,
            total_items,
        )
        return _json_response({"total_items": total_items, "items_removed": removed_count})

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("UnpublishDigitalItemsHttp: unhandled error for source=%s", source_key)
        return _json_response({"error": f"Unpublish failed: {exc}"}, status_code=500)


# ---------------------------------------------------------------------------
# Publish single item
# ---------------------------------------------------------------------------

@blueprint.function_name(name="PublishSingleDigitalItemHttp")
@blueprint.route(
    route="digital-items/publish-item/{item_id}",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def publish_single_digital_item_http(req: func.HttpRequest) -> func.HttpResponse:
    """Publish a single digital item to the search index.

    Query params:
      source (required) — Cosmos partition key (e.g. moore-chronology)

    Returns JSON:
      { "success": true, "message": "...", "published_at": "..." }
    """
    item_id = req.route_params.get("item_id", "")
    source = req.params.get("source", "")

    if not source:
        return _json_response(
            {"error": "Query parameter 'source' is required"}, status_code=400
        )

    index_name = _get_search_index()

    try:
        from helper.digital_items_cosmos import get_digital_items_container

        container = get_digital_items_container()

        try:
            doc = container.read_item(item=item_id, partition_key=source)
        except Exception:
            return _json_response(
                {"error": f"Item '{item_id}' not found in source '{source}'"},
                status_code=404,
            )

        # Resolve text content (inline or blob)
        text_content = doc.get("ocr_text_original") or doc.get("plain_text") or ""
        if not text_content.strip():
            flexible_url = doc.get("ocr_text_flexible_blob_url", "")
            original_url = doc.get("ocr_text_original_blob_url", "")
            blob_url = flexible_url or original_url
            if blob_url:
                text_content = _download_blob_text(blob_url)

        # Enrich with linked topics for richer search
        linked_topics = doc.get("linked_topics") or []
        if linked_topics:
            topic_texts = []
            for topic in linked_topics:
                title = topic.get("title", "")
                summary = topic.get("summary", "")
                if title or summary:
                    topic_texts.append(f"{title}. {summary}" if summary else title)
            if topic_texts:
                text_content = text_content + "\n\n" + "\n".join(topic_texts)

        if not text_content.strip():
            return _json_response(
                {"error": "Item has no text content to publish"}, status_code=400
            )

        search_client = _get_search_client(index_name)
        now_iso = datetime.now(timezone.utc).isoformat()

        search_doc = {
            "id": doc["id"],
            "source": doc.get("source", source),
            "item_type": doc.get("item_type"),
            "parent_id": doc.get("parent_id"),
            "title": doc.get("title"),
            "date_range": doc.get("date_range"),
            "ocr_text": text_content,
            "plain_text": doc.get("plain_text"),
            "page_number": doc.get("page_number"),
            "page_count": doc.get("page_count"),
            "published_at": now_iso,
        }
        search_doc = {k: v for k, v in search_doc.items() if v is not None}

        result = search_client.upload_documents(documents=[search_doc])
        if result[0].succeeded:
            doc["published_at"] = now_iso
            doc["publish_status"] = "published"
            doc["status"] = "Published"
            container.upsert_item(body=doc)
            return _json_response(
                {
                    "success": True,
                    "message": f"Item '{item_id}' published to search index",
                    "published_at": now_iso,
                }
            )

        return _json_response(
            {"error": f"Search upload failed: {result[0].error_message}"},
            status_code=500,
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "PublishSingleDigitalItemHttp: unhandled error for item_id=%s", item_id
        )
        return _json_response({"error": f"Failed to publish item: {exc}"}, status_code=500)


# ---------------------------------------------------------------------------
# Unpublish single item
# ---------------------------------------------------------------------------

@blueprint.function_name(name="UnpublishSingleDigitalItemHttp")
@blueprint.route(
    route="digital-items/unpublish-item/{item_id}",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def unpublish_single_digital_item_http(req: func.HttpRequest) -> func.HttpResponse:
    """Remove a single digital item from the search index.

    Query params:
      source (required) — Cosmos partition key (e.g. moore-chronology)

    Returns JSON:
      { "success": true, "message": "..." }
    """
    item_id = req.route_params.get("item_id", "")
    source = req.params.get("source", "")

    if not source:
        return _json_response(
            {"error": "Query parameter 'source' is required"}, status_code=400
        )

    index_name = _get_search_index()

    try:
        from helper.digital_items_cosmos import get_digital_items_container

        container = get_digital_items_container()

        try:
            doc = container.read_item(item=item_id, partition_key=source)
        except Exception:
            return _json_response(
                {"error": f"Item '{item_id}' not found in source '{source}'"},
                status_code=404,
            )

        search_client = _get_search_client(index_name)
        try:
            search_client.delete_documents(documents=[{"id": item_id}])
        except Exception as exc:
            return _json_response(
                {"error": f"Failed to remove from search index: {exc}"},
                status_code=500,
            )

        doc.pop("published_at", None)
        doc["publish_status"] = "unpublished"
        doc["status"] = "Extracted"
        container.upsert_item(body=doc)

        return _json_response(
            {"success": True, "message": f"Item '{item_id}' removed from search index"}
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "UnpublishSingleDigitalItemHttp: unhandled error for item_id=%s", item_id
        )
        return _json_response(
            {"error": f"Failed to unpublish item: {exc}"}, status_code=500
        )


# ===========================================================================
# Ingest from external sources — Durable Functions orchestrator
# ===========================================================================

@blueprint.function_name(name="DigitalItemsIngestHttp")
@blueprint.route(
    route="digital-items/ingest/{source_key}",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
@blueprint.durable_client_input(client_name="client")
async def digital_items_ingest_http(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """Start ingestion orchestrator for a digital items source.

    The orchestrator fetches items from the external source (Wikipedia, TRC,
    TRA), then upserts them into Cosmos DB.  The HTTP trigger returns 202 with
    management URLs for polling, OR waits up to 60s for fast sources and returns
    the result directly (200).

    POST /api/digital-items/ingest/{source_key}?code=...
    POST /api/digital-items/ingest/{source_key}?code=...&step=fetch|ocr|publish
    source_key: tr-cyclopedia | moore-chronology | genealogy-papers
    step (optional): fetch | ocr | publish — run a single pipeline step
    """
    source_key = req.route_params.get("source_key", "")
    if source_key not in _KNOWN_SOURCES:
        return _json_response(
            {"error": f"Unknown source '{source_key}'. Valid: {sorted(_KNOWN_SOURCES)}"},
            status_code=400,
        )

    step = req.params.get("step")
    valid_steps = ("fetch", "ocr", "publish")
    if step and step not in valid_steps:
        return _json_response(
            {"error": f"Invalid step '{step}'. Valid: {list(valid_steps)}"},
            status_code=400,
        )

    client_input: dict[str, Any] = {"source_key": source_key}
    if step:
        client_input["step"] = step

    instance_id = await client.start_new(
        orchestration_function_name="DigitalItemsIngestOrchestrator",
        instance_id=None,
        client_input=client_input,
    )

    logger.info(
        "DigitalItemsIngestHttp: started orchestrator %s for source=%s",
        instance_id,
        source_key,
    )

    # Return 202 immediately with status polling URLs.
    # The orchestrator runs in the background; the API/frontend polls status.
    return client.create_check_status_response(req, instance_id)


@blueprint.function_name(name="DigitalItemsIngestOrchestrator")
@blueprint.orchestration_trigger(context_name="context")
def digital_items_ingest_orchestrator(context: df.DurableOrchestrationContext):
    """Orchestrate: fetch → OCR (if applicable) → publish to search index.

    Each step writes its results to Cosmos DB immediately:
      1. FetchDigitalItemsActivity — fetch items + upsert to Cosmos
      2. OcrDigitalItemsActivity — OCR items with assets + update Cosmos
      3. PublishToSearchActivity — publish Cosmos items to search index

    Optional ``step`` parameter runs a single step:
      - step=fetch   → fetch + upsert to Cosmos
      - step=ocr     → load from Cosmos → OCR → update Cosmos
      - step=publish → publish Cosmos items to search index

    Returns JSON: { "fetched": int, "ocr_processed": int, "published": int }
    """
    input_data = context.get_input()
    source_key = input_data.get("source_key") if isinstance(input_data, dict) else None
    step = input_data.get("step") if isinstance(input_data, dict) else None

    if not source_key:
        return {"error": "source_key is required", "fetched": 0, "ocr_processed": 0, "published": 0}

    run_fetch = step is None or step == "fetch"
    run_ocr = step is None or step == "ocr"
    run_publish = step is None or step == "publish"

    # Sources that don't need OCR (text is extracted during fetch)
    skip_ocr = source_key in _NO_OCR_SOURCES
    if skip_ocr:
        if step == "ocr":
            return {
                "fetched": 0,
                "ocr_processed": 0,
                "published": 0,
                "step": "ocr",
                "message": f"OCR is not applicable for source '{source_key}'. Text is extracted during the fetch step.",
            }
        run_ocr = False

    total_fetched = 0
    ocr_total = 0
    total_published = 0

    # ── Step 1: Fetch items from external source ──────────────────────
    if run_fetch:
        fetch_result = yield context.call_activity(
            "FetchDigitalItemsActivity",
            {"source_key": source_key},
        )

        if isinstance(fetch_result, dict):
            total_fetched = fetch_result.get("fetched", 0)

        if not total_fetched:
            return {"fetched": 0, "ocr_processed": 0, "published": 0, "message": "No items fetched from source"}

        if step == "fetch":
            return {
                "fetched": total_fetched,
                "ocr_processed": 0,
                "published": 0,
                "step": "fetch",
                "message": f"Fetched and saved {total_fetched} items to database",
            }

    # ── Step 2: OCR items that have PDF/image assets ──────────────────
    if run_ocr:
        # Load only items with status="Fetched" (not yet OCR'd)
        items = yield context.call_activity(
            "LoadItemsFromCosmosActivity",
            {"source_key": source_key, "status_filter": "Fetched"},
        )
        if not items:
            if step == "ocr":
                return {
                    "fetched": total_fetched,
                    "ocr_processed": 0,
                    "published": 0,
                    "step": "ocr",
                    "error": "No items found in database. Run the fetch step first.",
                }
        else:
            # Process one item at a time — large PDFs (200+ pages) can take
            # 18+ minutes each; batching 10 exceeds the 2-hour activity timeout.
            for item in items:
                ocr_result = yield context.call_activity(
                    "OcrDigitalItemsActivity",
                    {"items": [item], "source_key": source_key},
                )
                if isinstance(ocr_result, dict):
                    ocr_total += ocr_result.get("ocr_completed", 0)

        if step == "ocr":
            return {
                "fetched": total_fetched,
                "ocr_processed": ocr_total,
                "published": 0,
                "step": "ocr",
                "message": f"OCR completed on {ocr_total} items",
            }

    # ── Step 3: Publish to search index ───────────────────────────────
    if run_publish:
        publish_result = yield context.call_activity(
            "PublishToSearchActivity",
            {"source_key": source_key},
        )
        if isinstance(publish_result, dict):
            total_published = publish_result.get("items_published", 0)

        if step == "publish":
            return {
                "fetched": total_fetched,
                "ocr_processed": ocr_total,
                "published": total_published,
                "step": "publish",
                "message": f"Published {total_published} items to search index",
            }

    return {
        "fetched": total_fetched,
        "ocr_processed": ocr_total,
        "published": total_published,
    }


@blueprint.function_name(name="FetchDigitalItemsActivity")
@blueprint.activity_trigger(input_name="payload")
def fetch_digital_items_activity(payload: dict) -> dict:
    """Activity: fetch items from an external source and upsert to Cosmos.

    Fetches items, uploads plain_text to blob storage (storing reference URL),
    and upserts each item to Cosmos immediately. Returns summary counts.
    """
    from helper.digital_items_cosmos import get_digital_items_container
    from helper.digital_items_ingest import iter_ingest_source

    source_key = payload.get("source_key", "")
    cosmos_source = _KNOWN_SOURCES.get(source_key, source_key)
    logger.info("FetchDigitalItemsActivity: fetching source=%s", source_key)

    # Upsert each item to Cosmos immediately as it's fetched
    container = get_digital_items_container()
    now_iso = datetime.now(timezone.utc).isoformat()
    fetched = 0
    upserted = 0
    failed = 0

    # Determine initial status: no-OCR sources go straight to "Extracted"
    initial_status = "Extracted" if source_key in _NO_OCR_SOURCES else "Fetched"

    try:
        for item in iter_ingest_source(source_key):
            fetched += 1
            item["source"] = cosmos_source
            item["ingested_at"] = now_iso
            item["status"] = initial_status
            item["publish_status"] = item.get("publish_status", "pending")

            item_id = item.get("id", "unknown")

            # Internalize ALL external file assets (PDFs and images in files[])
            files = item.get("files", [])
            for file_idx, f in enumerate(files):
                file_url = f.get("url", "")
                if file_url and ".blob.core.windows.net" not in file_url:
                    file_path = file_url.split("?")[0]
                    file_ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path.split("/")[-1] else "jpg"
                    if file_ext not in ("pdf", "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "svg"):
                        file_ext = "jpg"
                    suffix = f"file_{file_idx}.{file_ext}"
                    internalized_file = _internalize_asset(item_id, file_url, suffix)
                    if internalized_file:
                        f["url"] = internalized_file

            # Set page_count from files if not already set
            if not item.get("page_count") and files:
                pdf_files = [f for f in files if f.get("url", "").lower().endswith(".pdf")]
                if not pdf_files:
                    # Image items: each image is one page
                    item["page_count"] = len(files)

            # Generate thumbnail from first PDF in files, or use first image as thumbnail
            current_thumb = item.get("thumbnail_url", "")
            if not current_thumb:
                pdf_files = [f["url"] for f in files if f.get("url", "").lower().endswith(".pdf") and ".blob.core.windows.net" in f.get("url", "")]
                if pdf_files:
                    thumb_url = _generate_pdf_thumbnail(item_id, pdf_files[0])
                    if thumb_url:
                        item["thumbnail_url"] = thumb_url
                else:
                    # Use first internalized image as thumbnail
                    img_files = [f["url"] for f in files if f.get("url", "") and ".blob.core.windows.net" in f.get("url", "")]
                    if img_files:
                        item["thumbnail_url"] = img_files[0]

            # Internalize external thumbnail
            thumb_url = item.get("thumbnail_url", "")
            if thumb_url and ".blob.core.windows.net" not in thumb_url:
                # Strip query params before extracting extension
                thumb_path = thumb_url.split("?")[0]
                ext = thumb_path.rsplit(".", 1)[-1].lower() if "." in thumb_path.split("/")[-1] else "jpg"
                if ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
                    ext = "jpg"
                internalized_thumb = _internalize_asset(item_id, thumb_url, f"thumbnail.{ext}")
                if internalized_thumb:
                    item["thumbnail_url"] = internalized_thumb

            # Internalize image_url if external
            img_url = item.get("image_url", "")
            if img_url and ".blob.core.windows.net" not in img_url:
                img_path = img_url.split("?")[0]
                img_ext = img_path.rsplit(".", 1)[-1].lower() if "." in img_path.split("/")[-1] else "jpg"
                if img_ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
                    img_ext = "jpg"
                internalized_img = _internalize_asset(item_id, img_url, f"image.{img_ext}")
                if internalized_img:
                    item["image_url"] = internalized_img

            # Internalize linked_topics thumbnail URLs
            linked_topics = item.get("linked_topics")
            if isinstance(linked_topics, list):
                for t_idx, topic in enumerate(linked_topics):
                    if not isinstance(topic, dict):
                        continue
                    t_url = topic.get("thumbnail_url", "")
                    if t_url and ".blob.core.windows.net" not in t_url:
                        t_path = t_url.split("?")[0]
                        t_ext = t_path.rsplit(".", 1)[-1].lower() if "." in t_path.split("/")[-1] else "jpg"
                        if t_ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
                            t_ext = "jpg"
                        internalized_t = _internalize_asset(item_id, t_url, f"topic_{t_idx}.{t_ext}")
                        if internalized_t:
                            topic["thumbnail_url"] = internalized_t

            # Upload plain_text to blob storage and store reference URL
            plain_text = item.get("plain_text", "")
            if plain_text and not item.get("ocr_text_original_blob_url"):
                blob_url = _upload_item_text_to_blob(item_id, plain_text)
                if blob_url:
                    item["plain_text_blob_url"] = blob_url
                    item["plain_text"] = (
                        plain_text[:300].rsplit(" ", 1)[0]
                        if len(plain_text) > 300
                        else plain_text
                    )

            try:
                container.upsert_item(body=item)
                upserted += 1
                logger.info("FetchDigitalItemsActivity: upserted %s (%d done)", item_id, upserted)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "FetchDigitalItemsActivity: failed to upsert %s: %s",
                    item.get("id", "unknown"),
                    exc,
                )
                failed += 1
    except Exception as exc:
        logger.exception(
            "FetchDigitalItemsActivity: failed for source=%s after %d items: %s",
            source_key, fetched, exc,
        )
        raise

    logger.info(
        "FetchDigitalItemsActivity: source=%s fetched=%d upserted=%d failed=%d",
        source_key, fetched, upserted, failed,
    )
    return {"fetched": fetched, "upserted": upserted, "failed": failed}


@blueprint.function_name(name="PublishToSearchActivity")
@blueprint.activity_trigger(input_name="payload")
def publish_to_search_activity(payload: dict) -> dict:
    """Activity: publish items from Cosmos DB to the Azure AI Search index.

    Reads all items with text content for the given source from Cosmos,
    builds search documents, and uploads them to the search index.
    """
    from helper.digital_items_cosmos import get_digital_items_container

    source_key = payload.get("source_key", "")
    cosmos_source = _KNOWN_SOURCES.get(source_key, source_key)
    index_name = _get_search_index()

    logger.info("PublishToSearchActivity: publishing source=%s to index=%s", source_key, index_name)

    container = get_digital_items_container()
    search_client = _get_search_client(index_name)

    query = (
        "SELECT c.id, c.source, c.item_type, c.parent_id, c.title, c.date_range, "
        "c.ocr_text_original, c.plain_text, c.page_number, c.page_count, "
        "c.ocr_text_original_blob_url, c.ocr_text_flexible_blob_url, "
        "c.plain_text_blob_url "
        "FROM c WHERE c.source = @source "
        "AND c.status = 'Extracted' "
        "AND (IS_DEFINED(c.ocr_text_original) OR IS_DEFINED(c.plain_text) "
        "OR IS_DEFINED(c.ocr_text_original_blob_url) "
        "OR IS_DEFINED(c.plain_text_blob_url))"
    )
    params = [{"name": "@source", "value": cosmos_source}]
    items: List[dict] = list(
        container.query_items(query=query, parameters=params, partition_key=cosmos_source)
    )

    if not items:
        logger.info("PublishToSearchActivity: no items with text content for source=%s", source_key)
        return {"items_published": 0, "items_failed": 0, "total_items": 0}

    now_iso = datetime.now(timezone.utc).isoformat()
    batch_size = 100
    published = 0
    failed = 0

    # Build all search docs (splitting multi-page OCR into per-page docs)
    all_search_docs: List[dict] = []
    for item in items:
        all_search_docs.extend(_build_search_docs_split(item, cosmos_source, now_iso))

    if not all_search_docs:
        logger.info("PublishToSearchActivity: no search docs built for source=%s", source_key)
        return {"items_published": 0, "items_failed": 0, "total_items": len(items)}

    for i in range(0, len(all_search_docs), batch_size):
        batch = all_search_docs[i : i + batch_size]
        try:
            results = search_client.upload_documents(documents=batch)
            for r in results:
                if r.succeeded:
                    published += 1
                else:
                    failed += 1
                    logger.warning("PublishToSearchActivity: failed id=%s: %s", r.key, r.error_message)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("PublishToSearchActivity: batch upload error: %s", exc)
            failed += len(batch)

    # Update publish_status and status in Cosmos
    for item in items:
        try:
            doc = container.read_item(item=item["id"], partition_key=cosmos_source)
            doc["published_at"] = now_iso
            doc["publish_status"] = "published"
            doc["status"] = "Published"
            container.upsert_item(body=doc)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    logger.info(
        "PublishToSearchActivity: source=%s published=%d failed=%d total_docs=%d cosmos_items=%d",
        source_key, published, failed, len(all_search_docs), len(items),
    )
    return {"items_published": published, "items_failed": failed, "total_items": len(items)}


@blueprint.function_name(name="OcrDigitalItemsActivity")
@blueprint.activity_trigger(input_name="payload")
def ocr_digital_items_activity(payload: dict) -> dict:
    """Activity: run OCR on a single item and update Cosmos with results.

    Accepts a list of items (typically 1), runs OCR on those with assets,
    uploads extracted text to Blob Storage, and updates Cosmos immediately.
    """
    from helper.digital_items_cosmos import get_digital_items_container
    from helper.ocr_items import ocr_items_batch

    items = payload.get("items", [])
    source_key = payload.get("source_key", "")
    logger.info("OcrDigitalItemsActivity: processing %d item(s) for source=%s", len(items), source_key)

    try:
        results = ocr_items_batch(items)
        completed = sum(1 for r in results if r.get("ocr_status") == "completed")
        logger.info(
            "OcrDigitalItemsActivity: %d completed OCR, %d total for source=%s",
            completed,
            len(results),
            source_key,
        )
    except Exception as exc:
        logger.exception("OcrDigitalItemsActivity: batch failed: %s", exc)
        return {"ocr_completed": 0, "ocr_failed": len(items)}

    # Update Cosmos with OCR metadata immediately after each item
    if source_key and results:
        cosmos_source = _KNOWN_SOURCES.get(source_key, source_key)
        container = get_digital_items_container()
        ocr_map = {r["item_id"]: r for r in results if isinstance(r, dict)}

        for item in items:
            ocr_data = ocr_map.get(item.get("id"))
            if not ocr_data or ocr_data.get("ocr_status") != "completed":
                logger.info("OcrDigitalItemsActivity: skipping %s (status=%s)", item.get("id"), ocr_data.get("ocr_status") if ocr_data else "no-result")
                continue
            try:
                doc = container.read_item(item=item["id"], partition_key=cosmos_source)
                doc["ocr_status"] = "completed"
                doc["ocr_accuracy"] = ocr_data.get("ocr_accuracy", 0.0)
                doc["ocr_page_count"] = ocr_data.get("ocr_page_count", 0)
                doc["ocr_text_original_blob_url"] = ocr_data.get("ocr_text_original_blob_url")
                doc["ocr_text_flexible_blob_url"] = ocr_data.get("ocr_text_flexible_blob_url")
                doc["ocr_version"] = ocr_data.get("ocr_version", 1)
                doc["publish_status"] = "pending"
                doc["status"] = "Extracted"
                container.upsert_item(body=doc)
                logger.info("OcrDigitalItemsActivity: updated Cosmos status=Extracted for %s", item.get("id"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "OcrDigitalItemsActivity: failed to update Cosmos for %s: %s",
                    item.get("id"), exc,
                )

    return {"ocr_completed": completed, "ocr_failed": len(results) - completed}


# ---------------------------------------------------------------------------
# Cosmos loader activity (for individual step execution)
# ---------------------------------------------------------------------------

@blueprint.function_name(name="LoadItemsFromCosmosActivity")
@blueprint.activity_trigger(input_name="payload")
def load_items_from_cosmos_activity(payload: dict) -> list:
    """Activity: load items from Cosmos DB for a given source.

    Used by individual step execution (e.g., OCR step loads previously
    fetched items from Cosmos instead of staging blobs).
    """
    from helper.digital_items_cosmos import get_digital_items_container

    source_key = payload.get("source_key", "")
    cosmos_source = _KNOWN_SOURCES.get(source_key, source_key)

    status_filter = payload.get("status_filter")
    container = get_digital_items_container()

    if status_filter:
        query = "SELECT * FROM c WHERE c.source = @source AND c.status = @status"
        params = [{"name": "@source", "value": cosmos_source}, {"name": "@status", "value": status_filter}]
    else:
        query = "SELECT * FROM c WHERE c.source = @source"
        params = [{"name": "@source", "value": cosmos_source}]
    items = list(
        container.query_items(query=query, parameters=params, partition_key=cosmos_source)
    )
    logger.info("LoadItemsFromCosmosActivity: loaded %d items for source=%s", len(items), source_key)
    return items
