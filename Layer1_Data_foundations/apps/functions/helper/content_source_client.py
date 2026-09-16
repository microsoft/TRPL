# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Content-source ingestion helpers and downstream Cosmos persistence."""

# Environment variables for configuration
import hashlib
import json
import logging
import os
import uuid
import asyncio
import random
import time
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union
from azure.cosmos.exceptions import CosmosHttpResponseError
from .content_source import (
    ContentRecordQuery,
    ContentSourceError,
    ContentSourceErrorCode,
    advance_opaque_cursor,
    get_content_source_adapter,
    map_asset_to_canonical,
    map_record_to_canonical,
    normalize_sync_query_state,
)
from .cosmos_client import get_container, get_database, query_records_by_ids
from .storage_client import build_blob_url, get_blob_service_client

STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
BLOB_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME")

DATABASE_NAME = os.getenv("COSMOS_DATABASE_NAME")
CONTAINER_NAME = os.getenv("COSMOS_CONTAINER_NAME")

blob_service_client = get_blob_service_client()
container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)

# Use shared CosmosClient instance for optimal connection management
database = get_database(DATABASE_NAME)
container = get_container(CONTAINER_NAME, DATABASE_NAME)

COSMOS_LOG_CONTAINER_NAME = os.getenv(
    "COSMOS_LOG_CONTAINER_NAME", "ingestion-errors"
)
missing_original_container = get_container(COSMOS_LOG_CONTAINER_NAME, DATABASE_NAME)

def log_processing_error(
    instance_id: Optional[str] = None,
    repository: Optional[str] = None,
    record_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    status: str = "error",
    reason: str = "unknown_error",
    message: Optional[str] = None
) -> None:
    """Log a processing error entry into the Cosmos DB error container.

    Args:
        instance_id: The orchestration instance ID (optional).
        repository: The repository name (optional).
        record_id: The record identifier associated with the error (optional).
        asset_id: The asset identifier associated with the error (optional).
        status: Status to set (default: "error").
        reason: Short code describing the error type.
        message: Detailed error message or exception text.

    Returns:
        None. The function attempts to write an error log entry to Cosmos DB.
    """
    try:
        item = {
            "id": f"{uuid.uuid4()}",
            "instance_id": instance_id,
            "repository": repository,
            "record_id": record_id,
            "asset_id": asset_id,
            "status": status,
            "error_type": reason,
            "message": message,
        }
        missing_original_container.upsert_item(item)
        logging.info("Logged error: %s for record=%s asset=%s", reason, record_id, asset_id)
    except (TypeError, ValueError, CosmosHttpResponseError) as e:
        logging.exception("Failed to log processing error: %s", e)


def normalize_archivist_status(value: Any) -> str:
    """Return archivist_status as a lowercase string; default 'pending' if missing or empty."""
    if value is None:
        return "pending"
    if isinstance(value, str):
        s = value.strip().lower()
        return s if s else "pending"
    s = str(value).strip().lower()
    return s if s else "pending"


def upsert_with_retry(
    cosmos_container,
    item: Dict[str, Any],
    record_id: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
    instance_id: Optional[str] = None,
    operation_name: str = "upsert"
) -> Dict[str, Any]:
    """
    Upsert a document to Cosmos DB with retry logic for transient failures.

    Handles 429 throttling with exponential backoff and Retry-After header.

    Args:
        cosmos_container: The Cosmos DB container to upsert to
        item: The document to upsert
        record_id: Record ID for logging purposes
        max_retries: Maximum number of retry attempts (default: 5)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)
        instance_id: Optional instance ID for error logging
        operation_name: Name of the operation for logging (default: "upsert")

    Returns:
        Dict with "success" (bool), "error" (str or None), and "record_id"
    """
    if isinstance(item, dict) and "archivist_status" in item:
        item["archivist_status"] = normalize_archivist_status(item.get("archivist_status"))

    retry_count = 0

    while retry_count < max_retries:
        try:
            cosmos_container.upsert_item(body=item)
            return {"record_id": record_id, "success": True, "error": None}
        except CosmosHttpResponseError as e:
            if e.status_code == 429:
                # Get Retry-After header or use exponential backoff
                retry_after_header = e.headers.get("Retry-After") if hasattr(e, 'headers') else None
                if retry_after_header:
                    try:
                        retry_after = int(retry_after_header)
                    except (ValueError, TypeError):
                        retry_after = base_delay * (2 ** retry_count) + random.uniform(0, 1)
                else:
                    retry_after = base_delay * (2 ** retry_count) + random.uniform(0, 1)

                retry_count += 1
                if retry_count < max_retries:
                    logging.warning(
                        "429 Throttled during %s for record %s. Retrying after %.2fs (attempt %d/%d)",
                        operation_name, record_id, retry_after, retry_count, max_retries
                    )
                    time.sleep(retry_after)
                    continue

                error_msg = f"Throttled after {max_retries} retries: {str(e)}"
                logging.error("Max retries reached for record %s after 429 throttling", record_id)
                log_processing_error(
                    instance_id=instance_id,
                    record_id=record_id,
                    reason=f"cosmos_{operation_name}_throttled",
                    message=str(e)
                )
                return {"record_id": record_id, "success": False, "error": error_msg}
            # Other Cosmos DB errors - don't retry
            error_msg = str(e)
            logging.exception("Failed to %s record %s to Cosmos DB: %s", operation_name, record_id, e)
            log_processing_error(
                instance_id=instance_id,
                record_id=record_id,
                reason=f"cosmos_{operation_name}_failed",
                message=error_msg
            )
            return {"record_id": record_id, "success": False, "error": error_msg}
        except Exception as e:
            # Non-retryable errors or unexpected exceptions
            error_msg = str(e)
            logging.exception("Failed to %s record %s: %s", operation_name, record_id, e)
            log_processing_error(
                instance_id=instance_id,
                record_id=record_id,
                reason=f"cosmos_{operation_name}_error",
                message=error_msg
            )
            return {"record_id": record_id, "success": False, "error": error_msg}

    # Should not reach here, but just in case
    return {"record_id": record_id, "success": False, "error": "Max retries exceeded"}


async def async_upsert_with_retry(
    cosmos_container,
    item: Dict[str, Any],
    record_id: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
    instance_id: Optional[str] = None,
    operation_name: str = "upsert"
) -> Dict[str, Any]:
    """
    Async version: Upsert a document to Cosmos DB with retry logic for transient failures.

    Handles 429 throttling with exponential backoff and Retry-After header.

    Args:
        cosmos_container: The Cosmos DB container to upsert to
        item: The document to upsert
        record_id: Record ID for logging purposes
        max_retries: Maximum number of retry attempts (default: 5)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)
        instance_id: Optional instance ID for error logging
        operation_name: Name of the operation for logging (default: "upsert")

    Returns:
        Dict with "success" (bool), "error" (str or None), and "record_id"
    """
    if isinstance(item, dict) and "archivist_status" in item:
        item["archivist_status"] = normalize_archivist_status(item.get("archivist_status"))

    retry_count = 0

    while retry_count < max_retries:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cosmos_container.upsert_item, item)
            return {"record_id": record_id, "success": True, "error": None}
        except CosmosHttpResponseError as e:
            if e.status_code == 429:
                # Get Retry-After header or use exponential backoff
                retry_after_header = e.headers.get("Retry-After") if hasattr(e, 'headers') else None
                if retry_after_header:
                    try:
                        retry_after = int(retry_after_header)
                    except (ValueError, TypeError):
                        retry_after = base_delay * (2 ** retry_count) + random.uniform(0, 1)
                else:
                    retry_after = base_delay * (2 ** retry_count) + random.uniform(0, 1)

                retry_count += 1
                if retry_count < max_retries:
                    logging.warning(
                        "429 Throttled during %s for record %s. Retrying after %.2fs (attempt %d/%d)",
                        operation_name, record_id, retry_after, retry_count, max_retries
                    )
                    await asyncio.sleep(retry_after)
                    continue

                error_msg = f"Throttled after {max_retries} retries: {str(e)}"
                logging.error("Max retries reached for record %s after 429 throttling", record_id)
                log_processing_error(
                    instance_id=instance_id,
                    record_id=record_id,
                    reason=f"cosmos_{operation_name}_throttled",
                    message=str(e)
                )
                return {"record_id": record_id, "success": False, "error": error_msg}
            # Other Cosmos DB errors - don't retry
            error_msg = str(e)
            logging.exception("Failed to %s record %s to Cosmos DB: %s", operation_name, record_id, e)
            log_processing_error(
                instance_id=instance_id,
                record_id=record_id,
                reason=f"cosmos_{operation_name}_failed",
                message=error_msg
            )
            return {"record_id": record_id, "success": False, "error": error_msg}
        except Exception as e:
            # Non-retryable errors or unexpected exceptions
            error_msg = str(e)
            logging.exception("Failed to %s record %s: %s", operation_name, record_id, e)
            log_processing_error(
                instance_id=instance_id,
                record_id=record_id,
                reason=f"cosmos_{operation_name}_error",
                message=error_msg
            )
            return {"record_id": record_id, "success": False, "error": error_msg}

    # Should not reach here, but just in case
    return {"record_id": record_id, "success": False, "error": "Max retries exceeded"}


def log_failed_sync_page(
    cursor: Optional[str] = None,
    limit: int = 100,
    collection_ids: Optional[List[str]] = None,
    error: Optional[str] = None,
    instance_id: Optional[str] = None,
    date_from: Optional[Union[str, datetime]] = None,
    date_to: Optional[Union[str, datetime]] = None,
    seen_cursor_fingerprints: Optional[List[str]] = None,
    query_state: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Persist the complete, filter-preserving state needed to retry a page."""

    try:
        normalized_query_state = normalize_sync_query_state(
            query_state
            or {
                "version": 1,
                "cursor": cursor,
                "page_size": limit,
                "collection_ids": collection_ids or [],
                "date_from": date_from,
                "date_to": date_to,
                "seen_cursor_fingerprints": seen_cursor_fingerprints or [],
            },
            require_complete=True,
        )
        query_fingerprint = hashlib.sha256(
            json.dumps(
                normalized_query_state,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:12]
        failure_id = (
            f"sync_failure_{query_fingerprint}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        )
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        item = {
            "id": failure_id,
            "type": "sync_failure",
            "status": "pending",
            "query_state": normalized_query_state,
            # Retain these fields for existing operational views; retries use query_state.
            "cursor": normalized_query_state["cursor"],
            "limit": normalized_query_state["page_size"],
            "collection_ids": normalized_query_state["collection_ids"],
            "date_from": normalized_query_state["date_from"],
            "date_to": normalized_query_state["date_to"],
            "error": error,
            "instance_id": instance_id,
            "created_at": created_at,
        }
        missing_original_container.upsert_item(item)
        logging.info(
            "Logged failed content-source page: cursor=%s, limit=%d, error=%s",
            "initial" if normalized_query_state["cursor"] is None else "continuation",
            normalized_query_state["page_size"],
            error,
        )
        return failure_id
    except (TypeError, ValueError, ContentSourceError, CosmosHttpResponseError) as e:
        logging.exception("Failed to log sync failure: %s", e)
        return None


def get_pending_sync_failures(limit: int = 100, collection_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Get pending sync failures from Cosmos DB.

    Args:
        limit: Maximum number of failures to return
        collection_ids: Optional source collection filter

    Returns:
        List of pending sync failure entries
    """
    try:
        query = "SELECT * FROM c WHERE c.type = 'sync_failure' AND c.status = 'pending'"
        parameters = []

        if collection_ids:
            query += " AND c.collection_ids = @collection_ids"
            parameters.append({"name": "@collection_ids", "value": collection_ids})

        query += f" ORDER BY c.created_at ASC OFFSET 0 LIMIT {limit}"

        items = list(
            missing_original_container.query_items(
                query=query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True,
            )
        )
        logging.info("Found %d pending sync failures", len(items))
        return items
    except (TypeError, ValueError, CosmosHttpResponseError) as e:
        logging.exception("Failed to query sync failures: %s", e)
        raise


def get_sync_failure(failure_id: str) -> Dict[str, Any]:
    """Read the latest retry checkpoint for one sync failure."""

    item = missing_original_container.read_item(
        item=failure_id,
        partition_key=failure_id,
    )
    if not isinstance(item, dict) or item.get("type") != "sync_failure":
        raise ValueError("The sync failure record is invalid")
    return item


def update_sync_failure_state(
    failure_id: str,
    query_state: Mapping[str, Any],
    error: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Checkpoint retry progress without clearing the pending failure."""

    try:
        normalized_query_state = normalize_sync_query_state(
            query_state,
            require_complete=True,
        )
        item = missing_original_container.read_item(
            item=failure_id,
            partition_key=failure_id,
        )
        item.update(
            {
                "status": "pending",
                "query_state": normalized_query_state,
                "cursor": normalized_query_state["cursor"],
                "limit": normalized_query_state["page_size"],
                "collection_ids": normalized_query_state["collection_ids"],
                "date_from": normalized_query_state["date_from"],
                "date_to": normalized_query_state["date_to"],
                "last_error": dict(error) if error else None,
                "last_attempt_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        missing_original_container.upsert_item(item)
        return True
    except (TypeError, ValueError, ContentSourceError, CosmosHttpResponseError) as e:
        logging.exception("Failed to checkpoint sync failure %s: %s", failure_id, e)
        return False


def mark_sync_failure_completed(
    failure_id: str,
    query_state: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    Mark a sync failure as completed (successfully retried).

    Args:
        failure_id: The ID of the failure entry

    Returns:
        True if successful, False otherwise
    """
    try:
        item = missing_original_container.read_item(item=failure_id, partition_key=failure_id)
        if query_state is not None:
            normalized_query_state = normalize_sync_query_state(
                query_state,
                require_complete=True,
            )
            item["query_state"] = normalized_query_state
            item["cursor"] = None
        item["status"] = "completed"
        item["last_error"] = None
        item["completed_at"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        missing_original_container.upsert_item(item)
        logging.info("Marked sync failure %s as completed", failure_id)
        return True
    except (TypeError, ValueError, ContentSourceError, CosmosHttpResponseError) as e:
        if isinstance(e, CosmosHttpResponseError) and e.status_code == 404:
            logging.exception("Sync failure %s not found", failure_id)
        else:
            logging.exception("Failed to mark sync failure as completed: %s", e)
        return False


@lru_cache(maxsize=1)
def _adapter():
    return get_content_source_adapter()


async def query_content_records(
    collection_ids: Optional[List[str]] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    date_from: Optional[Union[str, datetime]] = None,
    date_to: Optional[Union[str, datetime]] = None,
) -> Dict[str, Any]:
    """Query the configured adapter and map records to the canonical pipeline shape."""

    selected_collections = list(collection_ids or [])
    updated_from = (
        datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        if isinstance(date_from, str)
        else date_from
    )
    updated_to = (
        datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        if isinstance(date_to, str)
        else date_to
    )
    page = await _adapter().query_records(
        ContentRecordQuery(
            page_size=limit,
            cursor=cursor,
            collection_ids=tuple(selected_collections),
            updated_from=updated_from,
            updated_to=updated_to,
        )
    )
    next_cursor, _ = advance_opaque_cursor(cursor, page.next_cursor)
    return {
        "data": [map_record_to_canonical(record) for record in page.items],
        "total": page.total,
        "next_cursor": next_cursor,
    }


async def fetch_related_assets(
    record_id: str,
    limit: int = 100,
) -> Dict[str, Any]:
    """Drain every adapter asset page exactly once using opaque cursors."""

    if not 1 <= limit <= 500:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_QUERY,
            "limit must be between 1 and 500",
            details={"limit": limit},
        )
    cursor: Optional[str] = None
    seen_cursors: list[str] = []
    advisory_total: Optional[int] = None
    asset_ids: list[str] = []
    seen_asset_ids: set[str] = set()
    while True:
        page = await _adapter().query_assets(
            record_id,
            page_size=limit,
            cursor=cursor,
        )
        if page.total is not None:
            advisory_total = page.total
        for asset in page.items:
            if asset.id in seen_asset_ids:
                raise ContentSourceError(
                    ContentSourceErrorCode.INVALID_DATA,
                    "The content-source adapter returned a duplicate asset identifier",
                    details={"record_id": record_id, "asset_id": asset.id},
                )
            seen_asset_ids.add(asset.id)
            asset_ids.append(asset.id)
        next_cursor, seen_cursors = advance_opaque_cursor(
            cursor,
            page.next_cursor,
            seen_cursors,
        )
        if next_cursor is None:
            break
        cursor = next_cursor
    return {
        "data": [{"id": asset_id} for asset_id in asset_ids],
        "total": advisory_total,
        "next_cursor": None,
    }


def get_source_sequence(asset: Dict[str, Any]) -> Union[int, float]:
    """Return a stable provider-neutral asset sequence for sorting."""

    value = asset.get("sequence")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else float("inf")


def upload_to_blob(record_id: str, file_content: bytes, filename: str, folder: str) -> str:
    """Upload bytes to the configured blob container and return the blob URL.

    Args:
        record_id: Parent record id used as prefix for blob name.
        file_content: Byte content to upload.
        filename: Name of the uploaded file.
        folder: Folder path within the blob container.

    Returns:
        Public blob URL for the uploaded object.
    """
    safe_filename = os.path.basename(str(filename).replace("\\", "/"))
    if not safe_filename:
        raise ValueError("Adapter content filename must not be empty")
    blob_name = f"{record_id}/{folder}/{safe_filename}"
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(file_content, overwrite=True)
    return build_blob_url(BLOB_CONTAINER_NAME, blob_name)


def build_queue_message(flattened: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal queue message payload for downstream processing.

    Args:
        flattened: The flattened metadata dict for a record.

    Returns:
        Dict payload to send to the Service Bus queue.
    """
    message = {
        "record_id": flattened.get("record_id"),
    }
    return message



async def fetch_asset_detail_only(
    asset_id: str,
    record_id: str,
    instance_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Fetch asset detail (metadata) without downloading files.

    Args:
        asset_id: The asset identifier to process
        record_id: The parent record identifier
        instance_id: Optional instance ID for error logging

    Returns:
        Dictionary with asset information (no blob URLs), or None if processing failed
    """
    logging.info("Fetching asset detail for %s (record %s)", asset_id, record_id)

    try:
        asset = await _adapter().get_asset(record_id, asset_id)
    except (ContentSourceError, KeyError, TypeError) as e:
        logging.exception("Failed to fetch asset detail for %s: %s", asset_id, e)
        log_processing_error(instance_id=instance_id, record_id=record_id, asset_id=asset_id, reason="asset_detail_not_found", message=str(e))
        return None

    return map_asset_to_canonical(asset)


async def process_asset_files(
    asset_id: str,
    record_id: str,
    asset_detail_data: Optional[Dict[str, Any]],
    instance_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Read adapter-owned content bytes and upload them to trusted Blob Storage.

    Args:
        asset_id: The asset identifier to process
        record_id: The parent record identifier
        asset_detail_data: Canonical asset metadata from fetch_asset_detail_only
        instance_id: Optional instance ID for error logging

    Returns:
        Dictionary with asset information including blob URL for original file, or None if processing failed
    """
    logging.info("Processing original file for asset %s (record %s)", asset_id, record_id)

    if not asset_detail_data:
        return None

    asset_info = asset_detail_data.copy()

    try:
        try:
            content = await _adapter().get_asset_content(record_id, asset_id)
        except (ContentSourceError, KeyError, TypeError) as e:
            logging.exception("Failed to read source content for asset %s: %s", asset_id, e)
            log_processing_error(instance_id=instance_id, record_id=record_id, asset_id=asset_id, reason="original_file_not_found", message=str(e))
            return None

        try:
            blob_url = upload_to_blob(
                record_id,
                content.data,
                content.filename,
                folder="originals",
            )
            asset_info["blob_url"] = blob_url
            asset_info["content_type"] = content.media_type
        except Exception as e:
            logging.exception("Failed to upload original file for asset %s: %s",
                            asset_id, e)
            log_processing_error(instance_id=instance_id, record_id=record_id, asset_id=asset_id, reason="original_blob_upload_failed", message=str(e))
            return None

        return asset_info

    except (ConnectionError, ValueError, KeyError) as err:
        logging.exception("Error processing files for asset %s: %s", asset_id, err)
        log_processing_error(instance_id=instance_id, record_id=record_id, asset_id=asset_id, reason="asset_file_processing_error", message=str(err))
        return None




def prepare_record_data_from_fetch(record_data: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare a canonical adapter record for downstream persistence.

    Args:
        record_data: Record data dictionary from query_content_records response

    Returns:
        Dictionary with:
        - record_data: Flattened record data
        - related_assets: List of asset IDs (empty, will be fetched later)
        - asset_ids: List of asset IDs to process (empty, will be fetched later)
        - success: Boolean indicating if record data was prepared successfully
    """
    record_id = record_data.get("id")
    logging.info("Preparing record data from fetch response for record ID: %s", record_id)

    if not record_id:
        logging.error("Record data missing 'id' field")
        return {
            "success": False,
            "record_data": None,
            "related_assets": [],
            "asset_ids": []
        }

    flattened = dict(record_data)
    flattened["id"] = record_id
    flattened["record_id"] = record_id
    metadata = flattened.get("metadata")
    if not isinstance(metadata, dict):
        logging.error("Canonical record %s has invalid metadata", record_id)
        return {
            "success": False,
            "record_data": None,
            "related_assets": [],
            "asset_ids": [],
        }
    flattened["metadata_key"] = {
        str(key): str(value)
        for key, value in (flattened.get("metadata_key") or {}).items()
    }

    # Note: related_assets and asset_ids will be fetched later by the Service Bus processor
    # We return empty lists here since we're only storing the record initially
    related_assets = []
    asset_ids = []

    return {
        "success": True,
        "record_data": flattened,
        "related_assets": related_assets,
        "asset_ids": asset_ids
    }


def prepare_record_for_cosmos(
    flattened_record: Dict[str, Any],
    asset_details: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Prepare a record for Cosmos DB storage with all required fields.

    Args:
        flattened_record: Flattened record data dictionary
        asset_details: Optional list of asset detail dictionaries

    Returns:
        Record dictionary ready for Cosmos DB storage
    """
    if asset_details is None:
        asset_details = []

    # Filter out None values (failed asset processing)
    valid_asset_details = [asset for asset in asset_details if asset is not None]

    sorted_assets = sorted(valid_asset_details, key=get_source_sequence)

    record_for_cosmos = flattened_record.copy()
    record_for_cosmos["related_assets"] = [asset.get("asset_id") for asset in sorted_assets if asset.get("asset_id")]
    record_for_cosmos["asset_details"] = sorted_assets
    record_for_cosmos["validated_by"] = record_for_cosmos.get("validated_by", "")
    record_for_cosmos["asset_count"] = len([asset for asset in sorted_assets if asset.get("blob_url")])
    record_for_cosmos["version"] = record_for_cosmos.get("version", 1)
    record_for_cosmos["archivist_status"] = normalize_archivist_status(record_for_cosmos.get("archivist_status"))
    record_for_cosmos["published_by"] = record_for_cosmos.get("published_by", "")
    record_for_cosmos["last_processed"] = datetime.utcnow().isoformat()

    # Set initial status fields for independent stage processing
    # Only set if not already set (preserve existing status if updating)
    if "related_assets_status" not in record_for_cosmos:
        record_for_cosmos["related_assets_status"] = "pending"
    if "asset_details_status" not in record_for_cosmos:
        record_for_cosmos["asset_details_status"] = "pending"
    if "original_file_status" not in record_for_cosmos:
        record_for_cosmos["original_file_status"] = "pending"
    if "ocr_batch_status" not in record_for_cosmos:
        record_for_cosmos["ocr_batch_status"] = "pending"
    if "ocr_processing_status" not in record_for_cosmos:
        record_for_cosmos["ocr_processing_status"] = "pending"
    if "metadata_extraction_status" not in record_for_cosmos:
        record_for_cosmos["metadata_extraction_status"] = "pending"

    from .export_tracking import ensure_export_tracking

    ensure_export_tracking(record_for_cosmos)

    return record_for_cosmos


_SOURCE_OWNED_RECORD_FIELDS = {
    "id",
    "record_id",
    "source_record_id",
    "title",
    "type",
    "type_label",
    "summary",
    "published",
    "source_created_at",
    "source_updated_at",
    "metadata",
    "metadata_key",
    "rights",
}


def _merge_source_record(
    flattened_record: Dict[str, Any],
    existing_record: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Refresh source-owned fields without regressing downstream workflow state."""
    if not existing_record:
        return flattened_record.copy()

    merged = existing_record.copy()
    for field in _SOURCE_OWNED_RECORD_FIELDS:
        if field in flattened_record:
            merged[field] = flattened_record[field]
    return merged



async def save_records_batch(
    prepared_records: List[Dict[str, Any]],
    instance_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Save multiple prepared records to Cosmos DB in batch.

    Args:
        prepared_records: List of dictionaries with "record_id" and "record_data" (flattened_record)
        instance_id: Optional instance ID for error logging

    Returns:
        Dictionary with "successful" count, "failed" count, and "details" list
    """
    if not prepared_records:
        return {"successful": 0, "failed": 0, "details": []}

    record_ids = [prep_record["record_id"] for prep_record in prepared_records]
    existing_records, _ = query_records_by_ids(
        record_ids,
        cosmos_container=container,
    )

    # Prepare all records for Cosmos DB
    records = []
    record_id_list = []  # Keep track of record IDs in order

    for prep_record in prepared_records:
        record_id = prep_record["record_id"]
        flattened_record = prep_record["record_data"]

        try:
            existing_record = existing_records.get(record_id)
            merged_record = _merge_source_record(flattened_record, existing_record)
            existing_assets = (
                existing_record.get("asset_details", [])
                if existing_record
                else []
            )
            record_for_cosmos = prepare_record_for_cosmos(
                merged_record,
                asset_details=existing_assets,
            )
            records.append(record_for_cosmos)
            record_id_list.append(record_id)
        except (TypeError, ValueError, KeyError) as err:
            logging.exception("Failed to prepare record %s for Cosmos DB: %s", record_id, err)
            log_processing_error(
                instance_id=instance_id,
                record_id=record_id,
                reason="cosmos_preparation_failed",
                message=str(err)
            )
            raise

    if not records:
        logging.warning("No valid records prepared for batch write")
        return {
            "successful": 0,
            "failed": len(prepared_records),
            "details": [{"record_id": prep_record["record_id"], "success": False, "error": "Preparation failed"}
                       for prep_record in prepared_records]
        }

    # Use asyncio for parallel upserts with retry
    async def upsert_record_with_retry(record_for_cosmos, record_id):
        """Upsert a single record with retry logic and 429 throttling handling."""
        # Use the reusable async_upsert_with_retry helper
        return await async_upsert_with_retry(
            cosmos_container=container,
            item=record_for_cosmos,
            record_id=record_id,
            max_retries=5,
            base_delay=1.0,
            instance_id=instance_id,
            operation_name="upsert"
        )

    async def upsert_all_records():
        """Upsert all records in parallel with concurrency limit and 429 handling."""
        semaphore = asyncio.Semaphore(20)  # limit concurrent tasks

        async def worker(record, record_id):
            async with semaphore:
                return await upsert_record_with_retry(record, record_id)

        # Create tasks for all records
        tasks = [
            worker(record_for_cosmos, record_id)
            for record_for_cosmos, record_id in zip(records, record_id_list)
        ]

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and track 429 errors
        successful = 0
        failed = []
        details = []
        throttled_count = 0
        duplicate_count = 0

        for result in results:
            if isinstance(result, Exception):
                logging.exception("Unexpected error in insert: %s", result)
                continue

            if result.get("success"):
                successful += 1
                details.append({
                    "record_id": result["record_id"],
                    "success": True,
                    "asset_count": 0,
                })
            else:
                failed.append(result["record_id"])
                error_msg = result.get("error", "Unknown error")

                # Track 429 throttling errors
                if "Throttled" in error_msg or "429" in error_msg:
                    throttled_count += 1
                # Track duplicate records
                elif "already exists" in error_msg or "409" in error_msg:
                    duplicate_count += 1

                details.append({
                    "record_id": result["record_id"],
                    "success": False,
                    "error": error_msg,
                })

        # Log summary of 429 errors if any
        if throttled_count > 0:
            logging.warning(
                "Encountered %d throttling (429) errors out of %d total records",
                throttled_count,
                len(records)
            )

        # Log summary of duplicate records if any
        if duplicate_count > 0:
            logging.warning(
                "Encountered %d duplicate records out of %d total records",
                duplicate_count,
                len(records)
            )

        return successful, failed, details

    # Run async upserts
    total = len(records)
    try:
        successful, failed, details = await upsert_all_records()
        logging.info("Bulk upsert complete: %d/%d processed", successful, total)
    except Exception as e:
        logging.exception("Error in parallel insert: %s", e)
        # Mark all as failed if async execution fails
        successful = 0
        failed = record_id_list
        details = [
            {
                "record_id": record_id,
                "success": False,
                "error": f"Async execution error: {str(e)}",
            }
            for record_id in record_id_list
        ]

    return {
        "successful": successful,
        "failed": len(failed),
        "details": details,
    }


def update_record_status(
    record_id: str,
    status_field: str,
    status_value: str,
    instance_id: Optional[str] = None,
    error_reason: Optional[Union[str, Dict[str, str]]] = None
) -> bool:
    """Update a specific status field for a record in Cosmos DB with retry logic.

    Args:
        record_id: The record ID to update
        status_field: The status field name (e.g., 'related_assets_status', 'asset_details_status')
        status_value: The status value to set (e.g., 'pending', 'completed', 'failed', 'error')
        instance_id: Optional instance ID for error logging
        error_reason: Optional error reason - can be:
            - A string (stored as-is for backward compatibility)
            - A dict with 'message' (exception message for grouping) and 'detail' (operation description)
              Example: {"message": "Connection timeout after 30s", "detail": "Failed to read record from Cosmos DB"}

    Returns:
        Boolean indicating if update was successful
    """
    try:
        if status_field == "archivist_status":
            status_value = normalize_archivist_status(status_value)

        record_data = container.read_item(item=record_id, partition_key=record_id)
        current_status = record_data.get(status_field)

        # Preserve "error" status unless explicitly overwriting with "error" or "failed"
        # This prevents overwriting error status with "completed" or "pending"
        if current_status == "error" and status_value not in ("error", "failed"):
            logging.warning(
                "Skipping status update for record %s: %s is already 'error', not overwriting with '%s'",
                record_id, status_field, status_value
            )
            return False

        record_data[status_field] = status_value
        record_data["last_processed"] = datetime.utcnow().isoformat()

        if status_value == "completed":
            if status_field == "ocr_processing_status":
                from .export_tracking import mark_pipeline_ocr

                mark_pipeline_ocr(record_data)
            elif status_field == "metadata_extraction_status":
                from .export_tracking import mark_pipeline_metadata

                mark_pipeline_metadata(record_data)

        # Store error reason if provided and status is error/failed
        if error_reason and status_value in ("error", "failed"):
            error_field = f"{status_field}_error"
            # Support both string (legacy) and dict (structured) error formats
            if isinstance(error_reason, dict):
                # Structured error: {"message": "generic", "detail": "specific"}
                record_data[error_field] = error_reason
            else:
                # Legacy string format - wrap in structured format for consistency
                record_data[error_field] = {"message": str(error_reason), "detail": "Unknown error"}
            log_msg = error_reason.get("message") if isinstance(error_reason, dict) else error_reason
            logging.warning(
                "Record %s %s set to %s: %s",
                record_id,
                status_field,
                status_value,
                log_msg,
            )

        # Use upsert_with_retry for automatic retry on transient failures (429 throttling)
        result = upsert_with_retry(
            cosmos_container=container,
            item=record_data,
            record_id=record_id,
            max_retries=5,
            instance_id=instance_id,
            operation_name="status_update"
        )

        if result["success"]:
            log_msg = error_reason.get("message") if isinstance(error_reason, dict) else error_reason
            logging.info("Updated record %s: %s = %s%s", record_id, status_field, status_value, f" (error: {log_msg})" if error_reason else "")
            return True
        else:
            logging.error("Failed to update status for record %s: %s", record_id, result.get("error"))
            return False

    except Exception as e:
        logging.exception("Failed to update status for record %s: %s", record_id, e)
        log_processing_error(instance_id=instance_id, record_id=record_id, reason="status_update_failed", message=str(e))
        return False


def save_record_with_related_assets(
    record_data: Dict[str, Any],
    asset_ids: List[str],
    instance_id: Optional[str] = None
) -> bool:
    """Save record with related asset IDs and update status.

    Args:
        record_data: Flattened record data dictionary
        asset_ids: List of asset IDs
        instance_id: Optional instance ID for error logging

    Returns:
        Boolean indicating if save was successful
    """
    record_id = record_data.get("record_id")

    # Check if status is already "error" - don't overwrite it
    current_status = record_data.get("related_assets_status")
    if current_status == "error":
        logging.warning("Record %s already has related_assets_status='error', not overwriting", record_id)
        return False

    record_data["related_assets"] = asset_ids
    record_data["related_assets_status"] = "completed"
    record_data["asset_details_status"] = "pending"  # Next stage
    record_data["version"] = record_data.get("version", 1)
    record_data["archivist_status"] = normalize_archivist_status(record_data.get("archivist_status"))
    record_data["last_processed"] = datetime.utcnow().isoformat()

    try:
        container.upsert_item(record_data)
        logging.info("Stored record %s with %d related assets. Status: asset_details_pending", record_id, len(asset_ids))
        return True
    except (TypeError, ValueError, KeyError, CosmosHttpResponseError) as err:
        logging.exception("Failed to upload record %s to Cosmos DB: %s", record_id, err)
        log_processing_error(instance_id=instance_id, record_id=record_id, reason="cosmos_upsert_failed", message=str(err))
        # Update error status
        try:
            update_record_status(record_id, "related_assets_status", "error", instance_id, error_reason={"message": "Save failed", "detail": f"We retrieved the related files but were unable to save them: {str(err)}. Please contact support with Record ID: {record_id}"})
        except Exception as status_err:
            logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
        return False


def save_record_with_asset_details(
    record_data: Dict[str, Any],
    asset_details: List[Optional[Dict[str, Any]]],
    instance_id: Optional[str] = None
) -> bool:
    """Save record with asset details (metadata) and update status.

    Args:
        record_data: Flattened record data dictionary
        asset_details: List of asset detail dictionaries (with metadata, but no files yet)
        instance_id: Optional instance ID for error logging

    Returns:
        Boolean indicating if save was successful
    """
    record_id = record_data.get("record_id")

    # Check if status is already "error" - don't overwrite it
    current_status = record_data.get("asset_details_status")
    if current_status == "error":
        logging.warning("Record %s already has asset_details_status='error', not overwriting", record_id)
        return False

    # Filter out None values (failed asset processing)
    valid_asset_details = [asset for asset in asset_details if asset is not None]

    # NO PARTIAL SUCCESS: If any assets failed, mark as error
    expected_assets = record_data.get("related_assets", [])
    if expected_assets and len(valid_asset_details) < len(expected_assets):
        # Find which asset IDs are missing
        fetched_asset_ids = {asset.get("asset_id") for asset in valid_asset_details if asset.get("asset_id")}
        missing_asset_ids = [aid for aid in expected_assets if aid not in fetched_asset_ids]
        failed_count = len(missing_asset_ids)

        if failed_count == len(expected_assets):
            # All assets failed
            error_message = "File details unavailable"
            error_detail = f"We were unable to retrieve details for any of the {len(expected_assets)} files. Please contact support with Record ID: {record_id}, Failed Asset IDs: {missing_asset_ids}"
            logging.error("Record %s: All %d asset detail fetches failed. Missing asset IDs: %s", record_id, len(expected_assets), missing_asset_ids)
        else:
            # Partial failure
            error_message = "Some file details unavailable"
            error_detail = f"We were unable to retrieve details for {failed_count} of {len(expected_assets)} files. Please contact support with Record ID: {record_id}, Failed Asset IDs: {missing_asset_ids}"
            logging.error("Record %s: %d of %d asset detail fetches failed. Missing asset IDs: %s", record_id, failed_count, len(expected_assets), missing_asset_ids)
        try:
            update_record_status(
                record_id, "asset_details_status", "error", instance_id,
                error_reason={"message": error_message, "detail": error_detail}
            )
        except Exception as status_err:
            logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
        return False

    sorted_assets = sorted(valid_asset_details, key=get_source_sequence)

    record_data["asset_details"] = sorted_assets
    record_data["asset_details_status"] = "completed"
    record_data["original_file_status"] = "pending"  # Next stage
    record_data["version"] = record_data.get("version", 1)
    record_data["archivist_status"] = normalize_archivist_status(record_data.get("archivist_status"))
    record_data["last_processed"] = datetime.utcnow().isoformat()

    try:
        container.upsert_item(record_data)
        logging.info("Stored record %s with %d asset details. Status: original_file_pending", record_id, len(sorted_assets))
        return True
    except (TypeError, ValueError, KeyError, CosmosHttpResponseError) as err:
        logging.exception("Failed to upload record %s to Cosmos DB: %s", record_id, err)
        log_processing_error(instance_id=instance_id, record_id=record_id, reason="cosmos_upsert_failed", message=str(err))
        # Update error status
        try:
            update_record_status(record_id, "asset_details_status", "error", instance_id, error_reason={"message": "Save failed", "detail": f"We retrieved the file details but were unable to save them: {str(err)}. Please contact support with Record ID: {record_id}"})
        except Exception as status_err:
            logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
        return False


def save_record_with_original_files(
    record_data: Dict[str, Any],
    asset_details: List[Optional[Dict[str, Any]]],
    instance_id: Optional[str] = None
) -> int:
    """Save record with processed asset files (blob URLs) and update status.

    Args:
        record_data: Flattened record data dictionary
        asset_details: List of asset detail dictionaries (with blob URLs)
        instance_id: Optional instance ID for error logging

    Returns:
        Number of assets with blob URLs (for OCR processing)
    """
    record_id = record_data.get("record_id")

    # Check if status is already "error" - don't overwrite it
    current_status = record_data.get("original_file_status")
    if current_status == "error":
        logging.warning("Record %s already has original_file_status='error', not overwriting", record_id)
        return 0

    # Filter out None values (failed asset processing)
    valid_asset_details = [asset for asset in asset_details if asset is not None]

    sorted_assets = sorted(valid_asset_details, key=get_source_sequence)

    # Determine if there are assets with blob URLs for OCR
    assets_with_blob_urls = [
        asset for asset in sorted_assets
        if asset.get("blob_url")
    ]

    # NO PARTIAL SUCCESS: If any assets failed, mark as error
    expected_assets = record_data.get("asset_details", [])
    if expected_assets and len(assets_with_blob_urls) < len(expected_assets):
        # Find which asset IDs are missing blob URLs
        assets_with_blob_ids = {asset.get("asset_id") for asset in assets_with_blob_urls if asset.get("asset_id")}
        missing_asset_ids = [asset.get("asset_id") for asset in expected_assets if asset.get("asset_id") not in assets_with_blob_ids]
        failed_count = len(missing_asset_ids)

        if failed_count == len(expected_assets):
            # All assets failed
            error_message = "Files unavailable"
            error_detail = f"We were unable to download any of the {len(expected_assets)} files. The source files may be unavailable. Please contact support with Record ID: {record_id}, Failed Asset IDs: {missing_asset_ids}"
            logging.error("Record %s: All %d asset file processing failed. Missing asset IDs: %s", record_id, len(expected_assets), missing_asset_ids)
        else:
            # Partial failure
            error_message = "Some files unavailable"
            error_detail = f"We were unable to download {failed_count} of {len(expected_assets)} files. The source files may be unavailable. Please contact support with Record ID: {record_id}, Failed Asset IDs: {missing_asset_ids}"
            logging.error("Record %s: %d of %d asset file processing failed. Missing asset IDs: %s", record_id, failed_count, len(expected_assets), missing_asset_ids)
        try:
            update_record_status(
                record_id, "original_file_status", "error", instance_id,
                error_reason={"message": error_message, "detail": error_detail}
            )
        except Exception as status_err:
            logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
        return 0

    # Update asset_details with file information
    record_data["asset_details"] = sorted_assets
    record_data["asset_count"] = len(assets_with_blob_urls)
    record_data["original_file_status"] = "completed"
    record_data["ocr_batch_status"] = "pending"  # Next stage: create OCR batch
    record_data["ocr_processing_status"] = "pending"  # Will be completed when AI batch completes
    record_data["version"] = record_data.get("version", 1)
    record_data["archivist_status"] = normalize_archivist_status(record_data.get("archivist_status"))
    record_data["published_by"] = record_data.get("published_by", "")
    record_data["last_processed"] = datetime.utcnow().isoformat()

    try:
        container.upsert_item(record_data)
        logging.info(
            "Stored record %s with %d assets (blob URLs). Status: ocr_pending",
            record_id, len(assets_with_blob_urls)
        )

        # Return asset count (only count assets with blob_url, not blob_thumbnail_url)
        assets_with_blob_urls_count = len(assets_with_blob_urls)
        return assets_with_blob_urls_count
    except (TypeError, ValueError, KeyError, CosmosHttpResponseError) as err:
        logging.exception("Failed to upload record %s to Cosmos DB: %s", record_id, err)
        log_processing_error(instance_id=instance_id, record_id=record_id, reason="cosmos_upsert_failed", message=str(err))
        # Update error status
        try:
            update_record_status(record_id, "original_file_status", "error", instance_id, error_reason={"message": "Save failed", "detail": f"We processed the files but were unable to save them: {str(err)}. Please contact support with Record ID: {record_id}"})
        except Exception as status_err:
            logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
        return 0
