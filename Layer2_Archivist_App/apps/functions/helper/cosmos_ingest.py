# pylint: disable=too-many-locals, too-many-statements, too-many-branches

"""
CosmosDB document ingestion module for Azure AI Search.

This module processes documents from CosmosDB, extracts OCR text from assets,
generates embeddings, and indexes them into Azure AI Search.
"""
import copy
import logging
import math
import re
import json
from datetime import datetime, timezone
from typing import Any, Dict
from bs4 import BeautifulSoup

from helper.blob_util import try_download_blob_text
from helper.cosmos_client import CosmosDBClient, execute_with_retry
from helper.config import CosmosDBConfig, IndexingConfig
from helper.search_document_util import sanitize_document_id_for_search as sanitize_document_id
from helper import search_utils, text_utils
from helper.embedding_utils import generate_embeddings_batch
from helper.statistics_helper import get_statistics_helper
from helper.audit_helper import get_audit_helper
from helper.archivist_status import normalize_archivist_status


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Singleton for record-chunks container client
_record_chunks_cosmos_client: CosmosDBClient = None


def get_record_chunks_container():
    """Get record-chunks CosmosDB container for storing indexed document chunks."""
    global _record_chunks_cosmos_client

    if _record_chunks_cosmos_client is None:
        config = CosmosDBConfig.from_env()
        # Override container name for record-chunks
        config.container_name = config.record_chunks_container_name
        _record_chunks_cosmos_client = CosmosDBClient(config)

    return _record_chunks_cosmos_client._container


def get_record_url(record: Dict[str, Any]) -> str:
    """Return an explicit record URL without deriving provider-specific paths."""

    try:
        metadata = record.get('metadata', {})
        value = metadata.get('Record URL', '')
        if isinstance(value, str) and value.startswith("https://"):
            return value
    except (AttributeError, TypeError) as e:
        logging.exception("Could not read Record URL: %s", e)

    return ""


def _metadata_label_to_string(value: Any) -> str:
    """Normalize Cosmos metadata field for bulk-publish audit (label/value shapes)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("label", "value", "name", "text"):
            inner = value.get(key)
            if inner is not None and str(inner).strip():
                return str(inner).strip()
        return ""
    if isinstance(value, list):
        parts = [_metadata_label_to_string(item) for item in value if item]
        return ", ".join(p for p in parts if p).strip()
    return str(value).strip()


def get_publish_batch_document_snapshot(doc_id: str) -> Dict[str, str]:
    """
    Read one Cosmos document and return title, repository, collection for bulk-publish audit storage.
    Kept aligned with metadata usage in process_cosmos (Title / Repository / Collection).
    """
    snapshot: Dict[str, str] = {
        "document_id": doc_id or "",
        "title": "",
        "repository": "",
        "collection": "",
    }
    if not doc_id:
        return snapshot
    try:
        config_cosmos = CosmosDBConfig.from_env()
        cosmos_client = CosmosDBClient(config_cosmos)
        document = cosmos_client.get_document(doc_id)
        if not document:
            return snapshot

        meta = document.get("metadata") or {}
        title = _metadata_label_to_string(meta.get("Title"))
        if title.strip() == "(Untitled)":
            title = ""
        if not title:
            title = _metadata_label_to_string(document.get("title"))

        snapshot["title"] = title
        snapshot["repository"] = _metadata_label_to_string(meta.get("Repository"))
        snapshot["collection"] = _metadata_label_to_string(meta.get("Collection"))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.warning("get_publish_batch_document_snapshot(%s): %s", doc_id, exc)
    return snapshot


def portal_publish_metadata_to_string(value: Any) -> str:
    """
    Normalize Date Published to Portal for the ingestion gate.
    Keep in sync with archivist-api CosmosService.document_has_portal_publish_date
    and archivist-app hasPortalPublishDateMetadata.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("label", "value", "name", "text"):
            inner = value.get(key)
            if inner is not None and str(inner).strip():
                return str(inner).strip()
        return ""
    if isinstance(value, (list, tuple)):
        joined = ", ".join(str(item) for item in value if item)
        return joined.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return ""
        return str(value).strip()
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def is_valid_html(content):
    """
    Check if the content is valid HTML
    """
    if not content or not isinstance(content, str):
        return False

    # Check for basic HTML structure markers
    html_patterns = [
        r'<!DOCTYPE\s+html',
        r'<html[>\s]',
        r'<head[>\s]',
        r'<body[>\s]',
        r'<[a-zA-Z]+[>\s]',
    ]

    # Check if at least one pattern matches
    content_lower = content.lower()
    for pattern in html_patterns:
        if re.search(pattern, content_lower):
            return True

    return False

def extract_body(content):
    """
    Extract the body content from HTML with validation
    """
    # Validate if content is HTML
    if not is_valid_html(content):
        return content

    # Parse the HTML
    soup = BeautifulSoup(content, 'html.parser')

    # Check if body tag exists
    body = soup.body

    if body is None:
        return ""

    return body.get_text()


def clean_escape_characters(text):
    """
    Clean escape characters like \\n, \\t, \\r, etc. from text.
    
    Args:
        text: The text string to clean
        
    Returns:
        Cleaned text with escape characters removed or replaced with spaces
    """
    if not text or not isinstance(text, str):
        return text

    # Replace common escape characters with spaces
    # \n = newline, \t = tab, \r = carriage return, \v = vertical tab, \f = form feed
    cleaned = text.replace('\\n', ' ').replace('\\t', ' ').replace('\\r', ' ')
    cleaned = cleaned.replace('\\v', ' ').replace('\\f', ' ')

    # Replace actual escape sequences (if they exist as literal characters)
    cleaned = cleaned.replace('\n', ' ').replace('\t', ' ').replace('\r', ' ')
    cleaned = cleaned.replace('\v', ' ').replace('\f', ' ')

    # Normalize multiple spaces to single space
    cleaned = re.sub(r' +', ' ', cleaned)

    # Strip leading and trailing whitespace
    return cleaned.strip()


def extract_text_for_indexing(document: Dict[str, Any], asset_details_sorted: list, doc_id: str):
    """
    Extract text for indexing from either visual description or OCR text from assets.
    
    If visual_description_possible == "Y" and visual_detailed_description_flexible_blob_url exists,
    use that. Otherwise, iterate through assets and concatenate OCR text.
    
    Args:
        document: The document dictionary from CosmosDB
        asset_details_sorted: Sorted list of asset details
        doc_id: Document ID for logging
        
    Returns:
        Tuple of (complete_text: str, processed_count: int)
    """
    complete_text = ""
    processed_count = 0
    
    # Check if we should use visual description
    visual_description_possible = document.get("visual_description_possible")
    if visual_description_possible == "Y":
        visual_desc_url = document.get("visual_detailed_description_flexible_blob_url")
        if visual_desc_url:
            text = try_download_blob_text(visual_desc_url)
            if text:
                processed_count = 1
                logging.info("Using visual description for document %s", doc_id)
                return text, processed_count
            logging.info(
                "Visual description blob missing or empty for document %s, falling back to OCR",
                doc_id,
            )

    # Fall back to OCR text from assets
    for i, asset in enumerate(asset_details_sorted, start=1):
        ocr_result = asset.get("ocr_result")
        if not ocr_result:
            logging.info("Asset %d in %s has no OCR result, skipping.", i, doc_id)
            continue

        text = None
        ocr_link = (ocr_result.get("ocr_text_flexible_blob_url") or "").strip()
        if ocr_link:
            text = try_download_blob_text(ocr_link)
            if text is None:
                logging.warning(
                    "Asset %d in %s OCR flexible blob not found: %s",
                    i,
                    doc_id,
                    ocr_link,
                )

        if not text:
            inline = (ocr_result.get("ocr_text") or "").strip()
            if inline:
                text = inline
                logging.info(
                    "Asset %d in %s using inline ocr_text (blob unavailable).",
                    i,
                    doc_id,
                )

        if not text:
            original_link = (ocr_result.get("ocr_text_original_blob_url") or "").strip()
            if original_link and original_link != ocr_link:
                text = try_download_blob_text(original_link)
                if text:
                    logging.info(
                        "Asset %d in %s using original OCR blob fallback.",
                        i,
                        doc_id,
                    )

        if not text:
            logging.info("Asset %d in %s has no readable OCR text, skipping.", i, doc_id)
            continue

        if text == "NO_READABLE_TEXT":
            logging.info(
                "Asset %d in %s OCR text indicates no readable text, skipping.",
                i,
                doc_id
            )
            continue

        if processed_count > 0:
            complete_text += ". " + text
        else:
            complete_text = text
        processed_count += 1
    
    return complete_text, processed_count


def process_cosmos(doc_id: str, operation_type: str = "bulk", started_by: str = None):
    """
    Ingest each asset (with OCR text) from a CosmosDB document into Azure AI Search.

    Args:
        doc_id: The document ID to process from CosmosDB
        operation_type: Type of operation (bulk, individual, reindex, retry_failed)
        started_by: User who initiated the operation (for setting published_by)
    """
    try:
        if not doc_id:
            logging.warning("Message missing 'doc_id' field.")
            return

        logging.info("Processing CosmosDB document: %s", doc_id)

        # Initialize clients and configs
        config_cosmos = CosmosDBConfig.from_env()
        cosmos_client = CosmosDBClient(config_cosmos)
        config_index = IndexingConfig.from_env()

        # Fetch document from CosmosDB
        document = cosmos_client.get_document(doc_id)
        if not document:
            logging.warning("No document found in CosmosDB for id: %s", doc_id)
            return

        # Capture original state and set status to Publishing
        original_document = copy.deepcopy(document)
        original_status = normalize_archivist_status(document.get("archivist_status"))

        # Set approval method for new processing operations (don't override for reindexing)
        if operation_type != "reindex":
            document["archivist_approval_method"] = operation_type

        if original_status != "publishing":
            document["archivist_status"] = "publishing"
            ts = _utc_now_iso()
            document["archivist_publish_started_at"] = ts
            document["updated_at"] = ts
            # Set published_by if not already set and we have user info
            if started_by and not document.get("published_by"):
                document["published_by"] = started_by
            # pylint: disable=protected-access
            execute_with_retry(
                cosmos_client._container.upsert_item,
                operation_name=f"upsert_item({doc_id})",
                body=document
            )
            # pylint: enable=protected-access
            logging.info("Document %s status set to Publishing (by: %s)", doc_id, started_by or "system")

            # Update statistics for Publishing status
            try:
                stats_helper = get_statistics_helper()
                stats_helper.update_on_document_change(
                    old_doc=original_document,
                    new_doc=document
                )
            except Exception as stats_error:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "Failed to update statistics for Publishing status: %s",
                    str(stats_error)
                )

            # Create audit entry for Publishing status change
            if original_status != "publishing":
                try:
                    audit_helper = get_audit_helper()
                    audit_helper.create_status_change_audit(
                        document_id=doc_id,
                        old_status=original_status,
                        new_status="publishing",
                        version=document.get("version", 1)+1
                    )
                except Exception as audit_error:  # pylint: disable=broad-exception-caught
                    logging.warning(
                        "Failed to create audit entry for Publishing status: %s",
                        str(audit_error)
                    )

        asset_details = document.get("asset_details", [])
        if not asset_details:
            logging.warning("Document %s has no asset_details. Setting to failed.", doc_id)
            # Capture current state before updating to failed
            current_document = copy.deepcopy(document)
            document["archivist_status"] = "failed"
            document["archivist_publish_started_at"] = None
            document["archivist_error_message"] = "No asset_details found in document"
            execute_with_retry(
                cosmos_client._container.upsert_item,
                operation_name=f"upsert_item({doc_id})",
                body=document
            )
            # Update statistics for Failed status
            try:
                stats_helper = get_statistics_helper()
                stats_helper.update_on_document_change(
                    old_doc=current_document,
                    new_doc=document
                )
            except Exception as stats_error:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "Failed to update statistics for Failed status: %s",
                    str(stats_error)
                )
            return

        # Sort assets by the canonical adapter sequence for consistent ordering.
        try:
            asset_details_sorted = sorted(
                asset_details,
                key=lambda a: int(a.get("sequence", 0))
                if str(a.get("sequence", "")).isdigit()
                else float("inf")
            )
        except (ValueError, TypeError) as sort_error:
            logging.warning(
                "Could not sort assets by sequence for %s: %s. Using original order.",
                doc_id, sort_error
            )
            asset_details_sorted = asset_details

        # Extract metadata and text for indexing
        existing_metadata = document.get("metadata", "")
        raw_extracted_metadata = document.get("extracted_metadata", "")
        # Handle extracted_metadata being a list (one dict per asset) or a single dict
        if isinstance(raw_extracted_metadata, list) and len(raw_extracted_metadata) > 0:
            # Use the first item's metadata as the primary source
            extracted_metadata = raw_extracted_metadata[0]
        elif isinstance(raw_extracted_metadata, dict):
            extracted_metadata = raw_extracted_metadata
        else:
            extracted_metadata = {}
        ai_generated_fields = []

        # Determine AI fallback behavior based on approval method
        approval_method = document.get("archivist_approval_method")  # ✅ No default
        if not approval_method:  # For missing/null/empty values
            approval_method = "bulk"
            logging.info("Document %s missing archivist_approval_method, defaulting to 'bulk'", doc_id)        
        allow_ai_fallback = approval_method == "bulk"

        # Helper function to normalize value to string
        def normalize_to_string(value):
            """Convert value to string - handles objects with 'label' property and arrays."""
            if value is None:
                return ""
            if isinstance(value, dict):
                # Handle {"label": "value"} format
                return str(value.get("label", ""))
            if isinstance(value, list):
                # Join array items with comma
                return ", ".join(str(item) for item in value if item)
            return str(value)

        # Helper function to get value with fallback
        def get_metadata_value(key, default = ""):
            value = existing_metadata.get(key, "") if existing_metadata else ""

            # Title-specific fallback: treat "(Untitled)" as missing
            if key == "Title" and normalize_to_string(value).strip() == "(Untitled)":
                value = ""

            if not value and extracted_metadata and allow_ai_fallback:
                if key in extracted_metadata and key not in ai_generated_fields:
                    ai_generated_fields.append(key)
                value = extracted_metadata.get(key, default)
            
            # Always return a string
            return normalize_to_string(value)

        # Check portal_publish_date early - required for ingestion (broader shapes than normalize_to_string)
        raw_portal = existing_metadata.get("Date Published to Portal", "") if existing_metadata else ""
        if not raw_portal and extracted_metadata and allow_ai_fallback:
            raw_portal = extracted_metadata.get("Date Published to Portal", "")
        portal_publish_date = portal_publish_metadata_to_string(raw_portal)
        if not portal_publish_date or not portal_publish_date.strip():
            logging.warning("Document %s missing 'Date Published to Portal'. Skipping ingestion.", doc_id)
            # Revert status back to original since we can't proceed with ingestion
            if normalize_archivist_status(document.get("archivist_status")) == "publishing":
                # Capture current state before reverting
                publishing_document = copy.deepcopy(document)
                document["archivist_status"] = original_status
                document["archivist_publish_started_at"] = None
                document["updated_at"] = _utc_now_iso()
                # pylint: disable=protected-access
                execute_with_retry(
                    cosmos_client._container.upsert_item,
                    operation_name=f"upsert_item({doc_id})",
                    body=document
                )
                # pylint: enable=protected-access
                logging.info("Document %s status reverted to %s (missing Date Published to Portal)", 
                            doc_id, original_status)
                
                # Update statistics for status reversion
                try:
                    stats_helper = get_statistics_helper()
                    stats_helper.update_on_document_change(
                        old_doc=publishing_document,
                        new_doc=document
                    )
                except Exception as stats_error:  # pylint: disable=broad-exception-caught
                    logging.warning(
                        "Failed to update statistics for status reversion: %s",
                        str(stats_error)
                    )
                
                # Create audit entry for status reversion
                try:
                    audit_helper = get_audit_helper()
                    audit_helper.create_status_change_audit(
                        document_id=doc_id,
                        old_status="publishing",
                        new_status=original_status,
                        version=document.get("version", 1)+1
                    )
                except Exception as audit_error:  # pylint: disable=broad-exception-caught
                    logging.warning(
                        "Failed to create audit entry for status reversion: %s",
                        str(audit_error)
                    )
            return

        trc_url = get_record_url(document)
        # Use sorted assets for URL list to maintain consistent ordering
        trpl_asset_urls = [asset["blob_url"] for asset in asset_details_sorted]

        # Extract all metadata fields with fallback pattern
        source_record_id = get_metadata_value("Source Record ID", "")
        title = get_metadata_value("Title", "")
        description = get_metadata_value("Description", "")
        collection = get_metadata_value("Collection", "")
        repository = get_metadata_value("Repository", "")
        creator = get_metadata_value("Creator", "")
        recipient = get_metadata_value("Recipient", "")
        resource_type = get_metadata_value("Resource Type", "")
        production_method = get_metadata_value("Production Method", "")
        creation_date = get_metadata_value("Creation Date", "")
        citation = get_metadata_value("citation", "")
        copyright_notes = get_metadata_value("Copyright Notes", "")
        period = get_metadata_value("Period", "")

        if description:
            description = extract_body(description)
            description = clean_escape_characters(description)

        selected_metadata_json = {
            "record_id": doc_id,
            "trc_url": trc_url,
            "trpl_file_urls": trpl_asset_urls,
            "title": title,
            "creation_date": creation_date,
            "repository": repository,
            "collection": collection,
        }

        metadata = {

            # searchable fields
            "creator": creator,
            "recipient": recipient,
            "creation_date": creation_date,
            "title": title,
            "description": description,
            "production_method": production_method,
            "resource_type": resource_type,
            "collection": collection,
            "repository": repository,
            "period": period,

            # non-searchable fields
            "record_id": doc_id,
            "source_record_id": source_record_id,
            "citation": citation,
            "copyright_notes": copyright_notes,
            "trc_url": trc_url,
            "trpl_file_url": trpl_asset_urls if isinstance(trpl_asset_urls, list) else [],
            "selected_metadata_json": json.dumps(selected_metadata_json),
            "ai_generated_fields": ai_generated_fields if isinstance(ai_generated_fields, list) else [],
            "portal_publish_date": portal_publish_date
        }

        # Extract text for indexing (visual description or OCR from assets)
        complete_text, processed_count = extract_text_for_indexing(
            document, asset_details_sorted, doc_id
        )

        if not complete_text.strip():
            logging.warning("Document %s has no valid OCR text. Setting to failed.", doc_id)
            # Capture current state before updating to failed
            current_document = copy.deepcopy(document)
            document["archivist_status"] = "failed"
            document["archivist_publish_started_at"] = None
            document["archivist_error_message"] = "No valid OCR text found in document"
            execute_with_retry(
                cosmos_client._container.upsert_item,
                operation_name=f"upsert_item({doc_id})",
                body=document
            )
            # Update statistics for Failed status
            try:
                stats_helper = get_statistics_helper()
                stats_helper.update_on_document_change(
                    old_doc=current_document,
                    new_doc=document
                )
            except Exception as stats_error:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "Failed to update statistics for Failed status: %s",
                    str(stats_error)
                )
            return

        token_count = text_utils.count_tokens(complete_text)
        logging.info("Document %s has approximately %d tokens", doc_id, token_count)

        chunks = text_utils.split_text(
            text = complete_text,
            chunk_size_tokens = config_index.chunk_size_tokens,
            overlap_tokens = config_index.chunk_overlap_tokens
        )

        if not chunks:
            logging.warning("Document %s has no chunks after text splitting. Setting to failed.", doc_id)
            # Capture current state before updating to failed
            current_document = copy.deepcopy(document)
            document["archivist_status"] = "failed"
            document["archivist_publish_started_at"] = None
            document["archivist_error_message"] = "Text chunking resulted in no chunks"
            execute_with_retry(
                cosmos_client._container.upsert_item,
                operation_name=f"upsert_item({doc_id})",
                body=document
            )
            # Update statistics for Failed status
            try:
                stats_helper = get_statistics_helper()
                stats_helper.update_on_document_change(
                    old_doc=current_document,
                    new_doc=document
                )
            except Exception as stats_error:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "Failed to update statistics for Failed status: %s",
                    str(stats_error)
                )
            return

        logging.info("Split document %s into %d chunks", doc_id, len(chunks))

        # Generate ALL embeddings in a SINGLE batch API call (much faster + has retry logic)
        embeddings = generate_embeddings_batch(
            texts=chunks,
            deployment_name=config_index.azure_openai_embedding_deployment_name,
            azure_endpoint=config_index.azure_openai_endpoint
        )

        logging.info("Generated %d embeddings for document %s", len(embeddings), doc_id)

        # Fields that MUST be arrays (Collection types in search index)
        ARRAY_FIELDS = {"record_ocr_text_vector", "trpl_file_url", "ai_generated_fields"}
        
        def ensure_array(value):
            """Ensure a value is always a list."""
            if value is None:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                # Try to parse as JSON array
                if value.startswith('['):
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, list):
                            return parsed
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Single string - wrap in array
                return [value] if value else []
            # Other types - wrap in array
            return [value]
        
        def ensure_string(value, field_name: str = ""):
            """Ensure a value is always a string."""
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                # Object - try to get label
                if "label" in value:
                    return str(value["label"])
                logging.warning("Field '%s' is object without label, converting to JSON", field_name)
                return json.dumps(value)
            if isinstance(value, list):
                logging.warning("Field '%s' is array, converting to comma-separated string", field_name)
                return ", ".join(str(item) for item in value if item is not None)
            return str(value)
        
        def sanitize_for_search_index(doc: dict) -> dict:
            """Ensure all fields have correct types for the search index."""
            sanitized = {}
            for key, value in doc.items():
                if key in ARRAY_FIELDS:
                    # These fields MUST be arrays
                    sanitized[key] = ensure_array(value)
                else:
                    # All other fields must be primitives (string/int)
                    if key in ("token_count", "chunk_order"):
                        # Integer fields
                        sanitized[key] = int(value) if value is not None else 0
                    else:
                        # String fields
                        sanitized[key] = ensure_string(value, key)
            return sanitized

        indexed_docs = []
        for chunk_idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{sanitize_document_id(doc_id)}_chunk_{chunk_idx}"

            # Create a copy of metadata for each chunk to avoid mutation issues
            chunk_metadata = metadata.copy()
            chunk_metadata["id"] = chunk_id
            chunk_metadata["record_ocr_text_vector"] = embedding
            chunk_metadata["record_ocr_text"] = chunk_text
            chunk_metadata["chunk_name"] = chunk_id
            chunk_metadata["token_count"] = text_utils.count_tokens(chunk_text)
            chunk_metadata["chunk_order"] = chunk_idx
            
            # Sanitize before adding to list
            indexed_docs.append(sanitize_for_search_index(chunk_metadata))

        # Populate the record-chunks container (parallel writes using asyncio)
        record_chunks_container = get_record_chunks_container()
        
        logging.info("Writing %d chunks to record-chunks container for document %s", len(indexed_docs), doc_id)
        
        # Use asyncio for parallel chunk writes
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from functools import partial
        
        async def write_chunks_parallel(chunks, container, max_parallel=20):
            """Write chunks in parallel using asyncio with semaphore control."""
            semaphore = asyncio.Semaphore(max_parallel)
            executor = ThreadPoolExecutor(max_workers=max_parallel)
            
            def write_chunk_sync(chunk_doc):
                execute_with_retry(
                    container.upsert_item,
                    operation_name=f"upsert_chunk({chunk_doc.get('id', 'unknown')})",
                    body=chunk_doc
                )
                return True
            
            async def write_single_chunk(chunk_doc):
                async with semaphore:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(executor, partial(write_chunk_sync, chunk_doc))
                    return True
            
            try:
                tasks = [write_single_chunk(chunk) for chunk in chunks]
                results = await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                executor.shutdown(wait=False)
            
            success = sum(1 for r in results if r is True)
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logging.error("Failed to write chunk %s: %s", 
                                chunks[idx].get("id", "unknown"), str(result))
            return success
        
        chunks_written = asyncio.run(write_chunks_parallel(indexed_docs, record_chunks_container))
        
        logging.info("Successfully wrote %d/%d chunks to record-chunks container for document %s", 
                    chunks_written, len(indexed_docs), doc_id)

        # Populate the search index
        search_utils.index_documents(
            document_id=doc_id,
            endpoint=config_index.search_endpoint,
            index_name=config_index.search_index,
            documents=indexed_docs,
        )
        logging.info("PUBLISH_STATUS: Document %s - search index updated, proceeding to set published status", doc_id)

        # Capture current state (with Publishing status) before final update
        publishing_document = copy.deepcopy(document)

        # Set final status to Published with timestamp
        logging.info("PUBLISH_STATUS: Document %s - setting status to 'published' (current: %s)", 
                     doc_id, document.get("archivist_status"))
        document["archivist_status"] = "published"
        document["archivist_publish_started_at"] = None
        document["archivist_error_message"] = None
        document["published_at"] = datetime.now(timezone.utc).isoformat()
        document["updated_at"] = _utc_now_iso()
        # pylint: disable=protected-access
        execute_with_retry(
            cosmos_client._container.upsert_item,
            operation_name=f"upsert_item({doc_id})",
            body=document
        )
        # pylint: enable=protected-access
        logging.info("PUBLISH_STATUS: Document %s - successfully saved 'published' status to CosmosDB", doc_id)

        # Update statistics: track transition from Publishing to Published
        try:
            stats_helper = get_statistics_helper()
            stats_helper.update_on_document_change(
                old_doc=publishing_document,
                new_doc=document
            )
        except Exception as stats_error:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Failed to update statistics for document %s: %s",
                doc_id, str(stats_error)
            )

        # Create audit entry for published status change
        try:
            audit_helper = get_audit_helper()
            audit_helper.create_status_change_audit(
                document_id=doc_id,
                old_status="publishing",
                new_status="published",
                version=document.get("version", 1)+1
            )
        except Exception as audit_error:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Failed to create audit entry for published status: %s",
                str(audit_error)
            )

        logging.info(
            "Successfully indexed %d chunks from %d assets for document %s",
            len(chunks),
            processed_count,
            doc_id
        )

    except Exception as e:
        logging.exception("Error during CosmosDB ingestion for document %s: %s", doc_id, str(e))
        
        # Set archivist_status to Failed so the document doesn't stay stuck in Publishing
        try:
            config_cosmos = CosmosDBConfig.from_env()
            cosmos_client = CosmosDBClient(config_cosmos)
            document = cosmos_client.get_document(doc_id)
            
            if document and normalize_archivist_status(document.get("archivist_status")) == "publishing":
                # Store current state for stats update
                publishing_document = copy.deepcopy(document)
                
                # Update status to Failed
                document["archivist_status"] = "failed"
                document["archivist_publish_started_at"] = None
                document["archivist_error_message"] = str(e)[:500]  # Truncate long error messages
                document["updated_at"] = _utc_now_iso()
                # pylint: disable=protected-access
                execute_with_retry(
                    cosmos_client._container.upsert_item,
                    operation_name=f"upsert_item({doc_id})",
                    body=document
                )
                # pylint: enable=protected-access
                logging.info("Document %s status set to Failed due to error", doc_id)
                
                # Update statistics
                try:
                    stats_helper = get_statistics_helper()
                    stats_helper.update_on_document_change(
                        old_doc=publishing_document,
                        new_doc=document
                    )
                except Exception as stats_error:  # pylint: disable=broad-exception-caught
                    logging.warning(
                        "Failed to update statistics for Failed status: %s",
                        str(stats_error)
                    )
                
                # Create audit entry for Failed status
                try:
                    audit_helper = get_audit_helper()
                    audit_helper.create_status_change_audit(
                        document_id=doc_id,
                        old_status="publishing",
                        new_status="failed",
                        version=document.get("version", 1)+1
                    )
                except Exception as audit_error:  # pylint: disable=broad-exception-caught
                    logging.warning(
                        "Failed to create audit entry for failed status: %s",
                        str(audit_error)
                    )
                    
        except Exception as status_update_error:
            logging.exception(
                "Failed to update document %s status to Failed: %s",
                doc_id, str(status_update_error)
            )
        
        # Re-raise the exception so the activity is marked as failed
        raise
