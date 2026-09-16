# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Azure Functions v2 Programming Model - All Functions

All functions are defined in this single file using decorators.
No separate folders or function.json files needed.
"""

import os
import json
import logging
import asyncio
import subprocess
import sys
from typing import Optional, Tuple, List, Any, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import azure.functions as func
import azure.durable_functions as df
from helper import content_source_client
from helper.content_source import (
    ContentSourceError,
    ContentSourceErrorCode,
    advance_sync_orchestration,
    normalize_sync_query_state,
    retry_sync_failure_chain,
    sync_query_activity_params,
)
from helper.cosmos_client import query_records_by_ids
from helper.config import AzureStorageConfig
from ocr_digital_items import SOURCE_MAP
from helper.ocr_batch_helper import (
    poll_and_update_batch_status,
    get_batch_status_from_cosmos,
    get_pending_batches,
    process_completed_batch,
    process_completed_metadata_batch,
    process_completed_resource_type_batch,
    update_batch_status_in_cosmos,
    create_metadata_extraction_batch_job_async,
    create_resource_type_batch_job_async,
    store_batch_status,
    extract_resource_type_from_record,
    create_asset_dict,
)
from helper.blob_utils import ensure_blob_accessible, get_user_delegation_sas
from helper.storage_client import build_blob_url
from helper.azure_openai_batch_client import AzureOpenAIBatchClient
from helper.files_util import convert_file_url_to_jpeg_data_urls
from helper.servicebus_client import send_message
from helper.generate_metadata import download_text_from_blob
from helper.pipeline_job_helper import (
    save_pipeline_job,
    check_and_handle_running_job,
    get_orchestration_management_urls,
    persist_pipeline_job_status,
)
from helper.periodic_run_schedule_helper import (
    list_due_schedules,
    mark_schedule_after_trigger,
    merge_periodic_run_history_completion,
    record_periodic_sync_started_from_timer,
    resolve_schedule_ingestion_calendar_window,
)

# Create the function app instance
app = func.FunctionApp()
HTTP_AUTH_LEVEL = (
    func.AuthLevel.ANONYMOUS
    if os.getenv("ENVIRONMENT", "production").strip().lower() == "local"
    else func.AuthLevel.FUNCTION
)


@dataclass
class BatchPollerConfig:
    """Configuration for timer-driven batch poller jobs."""
    batch_type: str
    poller_name: str
    trigger_endpoint: str
    instance_id_prefix: str
    status_field: str
    error_message: str
    error_detail: str
    process_func: Callable[..., dict]


def parse_collection_ids(value) -> list:
    """
    Parse collection identifier input into a list of strings.
    
    Supports:
        - None -> []
        - Single string -> [string]
        - JSON array string -> [string1, string2, ...]
        - List -> list (as-is)
    
    Note: Comma-separated values are not supported because collection labels
    can contain commas (e.g., "Archives, Personal Papers, and Manuscripts").
    Use JSON array format for multiple values: '["value1", "value2"]'
    
    Returns:
        List of collection identifiers (empty list if None or empty)
    """
    if value is None:
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if v]

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []

        # Try to parse as JSON array
        if value.startswith('['):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if v]
            except (json.JSONDecodeError, ValueError):
                pass

        # Single value (do NOT split on comma - values can contain commas)
        return [value]

    return []


def _str_param(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _calendar_range_to_utc_iso(from_day: str, to_day: str) -> Tuple[str, str]:
    """Map a date pair to an inclusive UTC range."""
    a = datetime.strptime(str(from_day).strip()[:10], "%Y-%m-%d")
    b = datetime.strptime(str(to_day).strip()[:10], "%Y-%m-%d")
    if a > b:
        raise ValueError("from_date must be on or before to_date")
    start = datetime.combine(a.date(), time.min, tzinfo=timezone.utc)
    end = datetime.combine(
        b.date(),
        time(23, 59, 59, 999999),
        tzinfo=timezone.utc,
    )
    return start.isoformat(), end.isoformat()


def _resolve_content_source_periodic_sync_window(
    *,
    from_date: Optional[str],
    to_date: Optional[str],
    date_from_iso: Optional[str],
    date_to_iso: Optional[str],
    lookback_hours: int,
) -> Tuple[str, str, str]:
    """
    Return ``(date_from, date_to, log_label)`` for a content-source query.

    Precedence:
        1. ``date_from`` + ``date_to`` (full ISO) — for advanced clients.
        2. ``from_date`` + ``to_date`` (YYYY-MM-DD) — e.g. Archivist ``PeriodicSyncRequest``.
        3. Else rolling window: ``now - lookback_hours`` .. ``now`` (default periodic behavior).
    If only one of from_date / to_date is set, the pair is ignored and (3) is used (with a warning log).
    """
    if date_from_iso and date_to_iso:
        if date_from_iso > date_to_iso:
            raise ValueError("date_from must be on or before date_to (ISO).")
        return date_from_iso, date_to_iso, f"date_from/date_to(ISO) {date_from_iso} -> {date_to_iso}"
    if from_date and to_date:
        try:
            a, b = _calendar_range_to_utc_iso(from_date, to_date)
            return a, b, f"from_date/to_date(calendar) {from_date}..{to_date} -> {a} -> {b}"
        except ValueError as e:
            raise ValueError(
                f"Invalid from_date/to_date: {e}. Use YYYY-MM-DD, from on or before to."
            ) from e
    if (from_date and not to_date) or (to_date and not from_date):
        logging.warning(
            "Only one of from_date or to_date was provided; using lookback_hours window instead."
        )
    try:
        lb = max(1, int(lookback_hours))
    except (TypeError, ValueError):
        logging.warning("Invalid lookback_hours=%r; using 168", lookback_hours)
        lb = 168
    now = datetime.now(timezone.utc)
    rolling_from = (now - timedelta(hours=lb)).isoformat()
    return rolling_from, now.isoformat(), f"lookback_hours={lb} ({rolling_from} -> {now.isoformat()})"


async def _start_content_source_periodic_sync_orchestration(
    starter: df.DurableOrchestrationClient,
    *,
    collection_ids: List[Any],
    lookback_hours: int,
    batch_size: int,
    parallel_batches: int,
    run_all_stages: bool,
    from_date: Optional[str],
    to_date: Optional[str],
    date_from_iso: Optional[str],
    date_to_iso: Optional[str],
    schedule_id: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve content source window, start ``ContentSourcePeriodicSyncOrchestrator``, persist pipeline job.
    Returns orchestration instance id, or None on failure.
    """
    try:
        date_from, date_to, window_log = _resolve_content_source_periodic_sync_window(
            from_date=from_date,
            to_date=to_date,
            date_from_iso=date_from_iso,
            date_to_iso=date_to_iso,
            lookback_hours=lookback_hours,
        )
    except ValueError as ve:
        logging.error("content-source-periodic-sync invalid window: %s", ve)
        return None

    logging.info(
        "content-source-periodic-sync orchestration: collection_ids=%s, lookback_hours=%d, window=%s",
        collection_ids,
        lookback_hours,
        window_log,
    )

    input_data: dict = {
        "collection_ids": collection_ids,
        "batch_size": batch_size,
        "parallel_batches": parallel_batches,
        "date_from": date_from,
        "date_to": date_to,
        "run_all_stages": run_all_stages,
    }
    if schedule_id:
        input_data["schedule_id"] = schedule_id

    try:
        instance_id = await starter.start_new("ContentSourcePeriodicSyncOrchestrator", None, input_data)
    except Exception as e:
        logging.exception("start_new ContentSourcePeriodicSyncOrchestrator failed: %s", e)
        return None

    logging.info(
        "Started Content Source Periodic Sync orchestration id=%s (schedule_id=%s)",
        instance_id,
        schedule_id or "",
    )

    try:
        mgmt_payload = get_orchestration_management_urls(starter, instance_id)
        saved = save_pipeline_job(
            trigger_endpoint="content-source-periodic-sync",
            instance_id=instance_id,
            name="Content Source Periodic Sync",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.error("Failed to save pipeline job for content-source-periodic-sync")
    except Exception as save_error:
        logging.exception("Exception saving pipeline job for content-source-periodic-sync: %s", save_error)

    return instance_id


# Shared status configuration for building queries
STATUS_QUERY_CONFIG = {
    "related_assets_pending": {
        "field": "related_assets_status",
        "where": "(NOT IS_DEFINED(c.related_assets_status) OR c.related_assets_status = 'pending')"
    },
    "asset_details_pending": {
        "field": "asset_details_status",
        "where": "((NOT IS_DEFINED(c.asset_details_status) OR c.asset_details_status = 'pending') AND (c.related_assets_status = 'completed'))"
    },
    "original_file_pending": {
        "field": "original_file_status",
        "where": "((NOT IS_DEFINED(c.original_file_status) OR c.original_file_status = 'pending') AND (c.asset_details_status = 'completed'))"
    },
    "ocr_batch_pending": {
        "field": "ocr_batch_status",
        "where": "((NOT IS_DEFINED(c.ocr_batch_status) OR c.ocr_batch_status = 'pending') AND (c.resource_type_processing_status = 'completed'))"
    },
    "ocr_processing_pending": {
        "field": "ocr_processing_status",
        "where": "((NOT IS_DEFINED(c.ocr_processing_status) OR c.ocr_processing_status = 'pending') AND (c.ocr_batch_status = 'completed'))"
    },
    "metadata_batch_pending": {
        "field": "metadata_batch_status",
        "where": "((NOT IS_DEFINED(c.metadata_batch_status) OR c.metadata_batch_status = 'pending') AND (c.ocr_processing_status = 'completed'))"
    },
    "resource_type_batch_pending": {
        "field": "resource_type_batch_status",
        "where": "((NOT IS_DEFINED(c.resource_type_batch_status) OR c.resource_type_batch_status = 'pending') AND (c.original_file_status = 'completed'))"
    },
}


# Pipeline step order for cascading resets
# When retrying a step, all downstream steps should also be reset to pending
PIPELINE_STEP_ORDER = [
    "related_assets_status",
    "asset_details_status",
    "original_file_status",
    "resource_type_batch_status",
    "resource_type_processing_status",
    "ocr_batch_status",
    "ocr_processing_status",
    "metadata_batch_status",
    "metadata_extraction_status",
]


def _get_downstream_steps(status_field: str) -> list:
    """
    Get all downstream pipeline steps that should be reset when a step is retried.
    
    Args:
        status_field: The status field being reset (e.g., 'related_assets_status')
    
    Returns:
        List of downstream status fields that should also be reset to pending
    """
    if status_field not in PIPELINE_STEP_ORDER:
        return []

    step_index = PIPELINE_STEP_ORDER.index(status_field)
    return PIPELINE_STEP_ORDER[step_index + 1:]


def _build_status_query_parts(status: str, collection_ids: list = None) -> tuple:
    """
    Build the where clause and query parameters for status-based queries.
    
    Args:
        status: Status to query for
        collection_ids: Optional collection identifiers to filter by
    
    Returns:
        Tuple of (where_clause, query_parameters, status_field) or (None, None, None) if invalid status
    """
    config = STATUS_QUERY_CONFIG.get(status)
    if not config:
        return None, None, None

    where_clause = config["where"]
    status_field = config["field"]
    query_parameters = []

    # Add collection_ids filter if provided
    if collection_ids:
        where_clause += " AND IS_DEFINED(c.metadata) AND IS_DEFINED(c.metadata.Repository) AND ARRAY_CONTAINS(@collection_ids, c.metadata.Repository)"
        query_parameters.append({"name": "@collection_ids", "value": collection_ids})

    return where_clause, query_parameters, status_field


def get_pending_records_count(status: str, collection_ids: list = None) -> int:
    """
    Get the count of records with pending status for a specific processing stage.
    
    Args:
        status: Status to count ('related_assets_pending', 'asset_details_pending', 
                'original_file_pending', 'ocr_batch_pending', 'ocr_processing_pending',
                'metadata_extraction_pending', 'resource_type_batch_pending')
        collection_ids: Optional collection identifiers to filter by
    
    Returns:
        Count of pending records
    """
    try:
        where_clause, query_parameters, _ = _build_status_query_parts(status, collection_ids)
        if where_clause is None:
            logging.warning("Unknown status: %s", status)
            return 0

        query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_clause}"

        logging.debug("Counting pending records: %s", query)

        count_result = list(
            content_source_client.container.query_items(
                query=query,
                parameters=query_parameters if query_parameters else None,
                enable_cross_partition_query=True,
            )
        )

        if count_result and len(count_result) > 0:
            count = int(count_result[0]) if count_result[0] is not None else 0
            logging.info("Found %d pending records for status '%s'", count, status)
            return count

        return 0

    except Exception as e:
        logging.exception("Error counting pending records for status '%s': %s", status, e)
        return 0


def orchestrator_log(context, level, message, *args):
    """
    Helper function to log only when orchestrator is not replaying.
    This prevents duplicate log entries during Durable Functions replays.
    """
    if not context.is_replaying:
        if level == "info":
            logging.info(message, *args)
        elif level == "warning":
            logging.warning(message, *args)
        elif level == "error":
            logging.error(message, *args)
        elif level == "exception":
            logging.exception(message, *args)
        elif level == "debug":
            logging.debug(message, *args)


def _mark_batch_records_as_error(batch_status: dict, error_message: str, error_detail: str, status_field: str = "ocr_processing_status"):
    """
    Mark all records associated with a failed batch as error.

    Args:
        batch_status: Batch status document from Cosmos DB
        error_message: Error message for grouping (e.g., "failed", "cancelled")
        error_detail: Full error detail message to store
        status_field: The status field to update (default: "ocr_processing_status", also supports "metadata_extraction_status")
    """
    try:
        # Get record IDs from batch status
        record_ids = batch_status.get("record_ids", [])
        if not record_ids:
            # Fallback: extract record IDs from assets if available
            assets = batch_status.get("assets", [])
            if assets:
                record_ids = list({asset.get("record_id") for asset in assets if asset.get("record_id")})

        if not record_ids:
            logging.warning("No record IDs found in batch status, cannot mark records as error")
            return

        batch_id = batch_status.get("batch_id") or batch_status.get("id", "unknown")
        logging.info(
            "Marking %d records as error for failed batch %s (status_field=%s): %s",
            len(record_ids),
            batch_id,
            status_field,
            error_detail
        )

             # Track progress
        records_marked = 0
        records_skipped = 0

        # Update each record's status to error (only if not already completed/error)
        for record_id in record_ids:
            try:
                # Check current status to avoid overwriting completed/error
                try:
                    record_data = content_source_client.container.read_item(item=record_id, partition_key=record_id)
                    current_status = record_data.get(status_field)
                    if current_status in ("completed", "error"):
                        records_skipped += 1
                        continue
                except Exception:
                    pass  # Continue to mark as error if we can't check

                full_error_detail = f"{error_detail} Batch ID: {batch_id}, Record ID: {record_id}"
                if content_source_client.update_record_status(
                    record_id, status_field, "error", None,
                    error_reason={"message": error_message, "detail": full_error_detail}
                ):
                    records_marked += 1
            except Exception as e:
                logging.exception("Failed to mark record %s as error: %s", record_id, e)

        logging.info(
            "Batch %s: marked %d records as error, skipped %d (already completed/error)",
            batch_id, records_marked, records_skipped
        )

    except Exception as e:
        logging.exception("Error marking batch records as error: %s", e)


def _mark_batch_as_error_and_cleanup(batch_id: str, error: str, batch_type: str = "OCR"):
    """
    Mark a batch as failed in Cosmos DB and clean up Azure OpenAI batch files.

    Args:
        batch_id: The batch ID to mark as failed
        error: The error message to store
        batch_type: Type of batch for logging (e.g., "OCR", "metadata")
    """
    try:
        update_batch_status_in_cosmos(batch_id, "failed", {
            "status": "failed",
            "error": error,
            "processed_at": datetime.utcnow().isoformat()
        })
        logging.info("Marked %s batch %s as failed", batch_type, batch_id)

        # Delete Azure OpenAI batch files
        delete_batch_files_enabled = os.getenv("DELETE_BATCH_FILES_AFTER_PROCESSING", "true").lower() == "true"
        if delete_batch_files_enabled:
            try:
                batch_client = AzureOpenAIBatchClient()
                delete_result = batch_client.delete_batch_files(batch_id)
                logging.info(
                    "Cleaned up %s batch %s files: input=%s, output=%s, error=%s",
                    batch_type,
                    batch_id,
                    delete_result.get("input_file_deleted"),
                    delete_result.get("output_file_deleted"),
                    delete_result.get("error_file_deleted")
                )
            except Exception as cleanup_err:
                logging.warning("Failed to clean up batch files for %s batch %s: %s", batch_type, batch_id, cleanup_err)
    except Exception as status_err:
        logging.exception("Failed to update failed status for %s batch %s: %s", batch_type, batch_id, status_err)


def _cleanup_batch_files(batch_id: str, batch_type: str = "OCR"):
    """
    Clean up Azure OpenAI batch files if enabled.

    Args:
        batch_id: The batch ID to clean up
        batch_type: Type of batch for logging (e.g., "OCR", "metadata")
    """
    delete_batch_files_enabled = os.getenv("DELETE_BATCH_FILES_AFTER_PROCESSING", "true").lower() == "true"
    if delete_batch_files_enabled:
        try:
            batch_client = AzureOpenAIBatchClient()
            delete_result = batch_client.delete_batch_files(batch_id)
            logging.info(
                "Cleaned up %s batch %s files: input=%s, output=%s, error=%s",
                batch_type,
                batch_id,
                delete_result.get("input_file_deleted"),
                delete_result.get("output_file_deleted"),
                delete_result.get("error_file_deleted")
            )
        except Exception as e:
            logging.warning("Failed to clean up batch files for %s batch %s: %s", batch_type, batch_id, e)


def _handle_batch_creation_error(
    batch_result: dict,
    records_to_process: list,
    status_field: str,
    instance_id: str,
    error_message: str = "Batch creation failed",
    records_failed_count_key: str = "records_failed"
) -> int:
    """
    Handle batch creation error by marking only records that weren't already marked during prep.

    Records that fail during preparation are marked with specific errors in the batch creation
    function itself. This function only marks records that passed prep but failed for other
    reasons (e.g., batch upload failed).

    Args:
        batch_result: The batch creation result with error
        records_to_process: List of record dicts or record IDs
        status_field: The status field to update (e.g., "metadata_batch_status")
        instance_id: Orchestration instance ID
        error_message: Base error message for the status update
        records_failed_count_key: Key in batch_result containing count of prep-failed records

    Returns:
        Number of records that failed (includes already-marked prep failures)
    """
    error_msg = batch_result.get("error", "Unknown error creating batch")

    # Get count of records that already had errors marked during preparation
    # These records already have specific error details - don't overwrite them
    prep_failed_count = batch_result.get(records_failed_count_key, 0)
    total_records = len(records_to_process)

    # If all records failed during prep, they're already marked - nothing more to do
    if prep_failed_count >= total_records:
        logging.info(
            "All %d records already marked with specific errors during batch preparation",
            total_records
        )
        return total_records

    # Some records passed prep but batch creation still failed (e.g., upload error)
    # Only mark records that don't already have an error status
    logging.warning(
        "Batch creation failed after prep: %d records passed prep but batch upload failed. Error: %s",
        total_records - prep_failed_count, error_msg
    )

    records_marked = 0
    records_skipped = 0
    for record in records_to_process:
        record_id = record["record_id"] if isinstance(record, dict) else record

        # Check if record already has an error status (from prep failure) - don't overwrite
        try:
            record_data = content_source_client.container.read_item(item=record_id, partition_key=record_id)
            current_status = record_data.get(status_field)
            if current_status == "error":
                logging.debug("Record %s already has error status, skipping", record_id)
                records_skipped += 1
                continue
        except Exception:
            pass  # If we can't check, proceed to mark

        content_source_client.update_record_status(
            record_id, status_field, "error", instance_id,
            error_reason={"message": error_message, "detail": f"Batch upload failed: {error_msg}"}
        )
        records_marked += 1

    logging.info(
        "Batch error handling: %d records marked as error, %d skipped (already had error)",
        records_marked, records_skipped
    )

    return total_records


def _process_batch_creation_success(
    batch_result: dict,
    records_to_process: list,
    status_field: str,
    instance_id: str,
    batch_type: str,
    assets: list = None,
    prep_failed_key: str = "records_failed_prep"
) -> tuple:
    """
    Process successful batch creation: store batch status and mark records.

    Args:
        batch_result: The successful batch creation result
        records_to_process: List of record dicts or record IDs
        status_field: The status field to update (e.g., "metadata_batch_status")
        instance_id: Orchestration instance ID
        batch_type: Type of batch (e.g., "metadata_extraction", "resource_type")
        assets: Optional list of assets (for OCR batches)
        prep_failed_key: Key in batch_result containing prep-failed record IDs

    Returns:
        Tuple of (batch_ids list, successful count, failed count)
    """
    # Handle multiple batches if records were split
    all_batch_ids = batch_result.get("batch_ids", [batch_result.get("batch_id")])
    all_batches = batch_result.get("batches", [batch_result])

    # Get records that failed during preparation (shouldn't be marked as completed or stored in batch)
    prep_failed_ids = set(batch_result.get(prep_failed_key, []))

    # Get record IDs list - EXCLUDE records that failed during prep
    # This ensures that if batch processing fails later, mark_all_records_error won't
    # try to overwrite the specific prep failure errors
    all_record_ids = [
        r["record_id"] if isinstance(r, dict) else r
        for r in records_to_process
    ]
    record_ids_for_batch = [rid for rid in all_record_ids if rid not in prep_failed_ids]

    batch_ids = []
    for batch_info in all_batches:
        batch_id = batch_info.get("batch_id") if isinstance(batch_info, dict) else batch_info
        batch_ids.append(batch_id)

        # Store batch status in Cosmos DB - only include records that passed prep
        try:
            store_batch_status(
                batch_id,
                batch_info if isinstance(batch_info, dict) else batch_result,
                record_ids_for_batch,  # Only records that are actually in the batch
                assets or [],
                instance_id=instance_id,
                batch_type=batch_type
            )
        except Exception as e:
            logging.warning("Error storing batch status for %s: %s", batch_id, e)

    # Mark records as completed, skipping those that failed during prep
    successful = 0
    failed = 0
    for record in records_to_process:
        record_id = record["record_id"] if isinstance(record, dict) else record
        if record_id in prep_failed_ids:
            logging.info("Skipping record %s - already marked during batch prep", record_id)
            failed += 1
        else:
            content_source_client.update_record_status(record_id, status_field, "completed", instance_id)
            successful += 1

    # Log batch creation summary
    batches_created = len(all_batch_ids)
    if batches_created > 1:
        logging.info("Created %d %s batches for %d records: %s",
                    batches_created, batch_type, len(records_to_process), all_batch_ids)
    else:
        logging.info("Created %s batch %s for %d records",
                    batch_type, all_batch_ids[0], len(records_to_process))

    return batch_ids, successful, failed


def _mark_records_batch_error(
    records_to_process: list,
    status_field: str,
    instance_id: str,
    error: Exception,
    error_message: str = "Batch creation failed"
) -> int:
    """
    Mark records as error when batch creation throws an exception.

    Skips records that already have an error status (from prep failures) to avoid
    overwriting specific error details with generic messages.

    Args:
        records_to_process: List of record dicts or record IDs
        status_field: The status field to update
        instance_id: Orchestration instance ID
        error: The exception that occurred
        error_message: Base error message

    Returns:
        Number of records counted as failed (includes already-errored records)
    """
    records_marked = 0
    records_skipped = 0

    for record in records_to_process:
        record_id = record["record_id"] if isinstance(record, dict) else record

        # Check if record already has an error status (from prep failure) - don't overwrite
        try:
            record_data = content_source_client.container.read_item(item=record_id, partition_key=record_id)
            current_status = record_data.get(status_field)
            if current_status == "error":
                logging.debug("Record %s already has error status, skipping", record_id)
                records_skipped += 1
                continue
        except Exception:
            pass  # If we can't check, proceed to mark

        content_source_client.update_record_status(
            record_id, status_field, "error", instance_id,
            error_reason={"message": error_message, "detail": f"An error occurred: {str(error)}"}
        )
        records_marked += 1

    if records_skipped > 0:
        logging.info(
            "Exception error handling: %d records marked as error, %d skipped (already had error)",
            records_marked, records_skipped
        )

    return records_marked + records_skipped


def _process_batch_polling(config: BatchPollerConfig) -> dict:
    """Poll pending Azure OpenAI batches for a configured batch type and process completions."""
    total_processed = 0
    total_failed = 0
    total = 0
    started_at = datetime.utcnow().isoformat()
    run_instance_id = f"{config.instance_id_prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    try:
        pending_batches = get_pending_batches(batch_type=config.batch_type)
        total = len(pending_batches)
        logging.info("Found %d pending %s batches to check", total, config.batch_type)

        for batch_status in pending_batches:
            batch_id = batch_status.get("batch_id") or batch_status.get("id")
            if not batch_id:
                continue

            try:
                status_result = poll_and_update_batch_status(batch_id)

                # Transient polling errors should be retried on the next timer run.
                if "error" in status_result and status_result.get("status") == "error":
                    logging.warning(
                        "Error polling %s batch %s: %s. Will retry next cycle.",
                        config.batch_type,
                        batch_id,
                        status_result.get("error"),
                    )
                    continue

                if status_result.get("is_completed"):
                    logging.info("%s Batch %s completed, processing", config.poller_name, batch_id)
                    try:
                        result = config.process_func(batch_id, content_source_client.container, update_status=False)

                        if result.get("status") == "success":
                            total_processed += 1
                            try:
                                update_batch_status_in_cosmos(batch_id, "completed", {
                                    "request_counts": result.get("request_counts"),
                                    "status": "completed",
                                    "processed_at": datetime.utcnow().isoformat()
                                })
                                logging.info("Successfully processed %s batch %s", config.batch_type, batch_id)
                                _cleanup_batch_files(batch_id, config.batch_type)
                            except Exception as e:
                                logging.exception("Error updating batch status for %s: %s", batch_id, e)
                        else:
                            logging.error(
                                "Failed to process %s batch %s: %s",
                                config.batch_type,
                                batch_id,
                                result.get("error"),
                            )
                            total_failed += 1
                            _handle_batch_failure(batch_id, config, f"Batch processing failed: {result.get('error')}")

                    except Exception as e:
                        logging.exception(
                            "Error processing completed %s batch %s: %s",
                            config.batch_type,
                            batch_id,
                            e,
                        )
                        total_failed += 1
                        _handle_batch_failure(batch_id, config, f"Batch processing error: {str(e)}")

                elif status_result.get("is_failed"):
                    failed_status = status_result.get("status", "failed")
                    total_failed += 1
                    logging.warning(
                        "%s Batch %s failed with status: %s",
                        config.batch_type,
                        batch_id,
                        failed_status,
                    )
                    _handle_batch_failure(batch_id, config, config.error_detail)

            except Exception as e:
                logging.exception("Error polling %s batch %s: %s", config.batch_type, batch_id, e)
                continue

        logging.info(
            "%s polling completed. Checked %d batches: %d processed, %d failed",
            config.poller_name,
            total,
            total_processed,
            total_failed,
        )

        save_pipeline_job(
            trigger_endpoint=config.trigger_endpoint,
            instance_id=run_instance_id,
            name=config.poller_name,
            runtime_status="Completed",
            started_at=started_at,
            output_data={
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "total": total,
            },
        )

        return {"status": "success", "processed": total_processed, "failed": total_failed, "total": total}

    except Exception as e:
        logging.exception("Error in %s: %s", config.poller_name, e)
        save_pipeline_job(
            trigger_endpoint=config.trigger_endpoint,
            instance_id=run_instance_id,
            name=config.poller_name,
            runtime_status="Completed",
            started_at=started_at,
            output_data={
                "status": "error",
                "error": str(e),
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "total": total,
            },
            error=str(e),
        )
        return {"status": "error", "error": str(e)}



# ─── Simplified HTTP Endpoints for Cyclopedia and Genealogy ───────────────

@app.function_name(name="DigitalItemsIngestCyclopedia")
@app.route(
    route="digital-items/ingest/cyclopedia",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST"],
)
def digital_items_ingest_cyclopedia(req: func.HttpRequest) -> func.HttpResponse:  # pylint: disable=unused-argument
    """
    Direct ingestion endpoint for Cyclopedia digital items.
    No durable orchestration needed — builds and returns items directly.
    """
    logging.info("DigitalItemsIngestCyclopedia: starting ingestion")
    try:
        from ingest_digital_resources import build_cyclopedia_items

        items = build_cyclopedia_items()
        logging.info("DigitalItemsIngestCyclopedia: built %d items", len(items))

        return func.HttpResponse(
            json.dumps({
                "success": True,
                "source": "cyclopedia",
                "processed": len(items),
                "items": items,
                "message": f"Cyclopedia ingestion completed: {len(items)} items built",
            }),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("DigitalItemsIngestCyclopedia failed")
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "source": "cyclopedia",
                "processed": 0,
                "items": [],
                "error": str(e),
                "message": f"Cyclopedia ingestion failed: {e}",
            }),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="DigitalItemsIngestGenealogy")
@app.route(
    route="digital-items/ingest/genealogy",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST"],
)
def digital_items_ingest_genealogy(req: func.HttpRequest) -> func.HttpResponse:  # pylint: disable=unused-argument
    """
    Direct ingestion endpoint for Genealogy & Papers digital items.
    No durable orchestration needed — builds and returns items directly.
    """
    logging.info("DigitalItemsIngestGenealogy: starting ingestion")
    try:
        from ingest_digital_resources import build_genealogy_items

        items = build_genealogy_items()
        logging.info("DigitalItemsIngestGenealogy: built %d items", len(items))

        return func.HttpResponse(
            json.dumps({
                "success": True,
                "source": "genealogy",
                "processed": len(items),
                "items": items,
                "message": f"Genealogy ingestion completed: {len(items)} items built",
            }),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("DigitalItemsIngestGenealogy failed")
        return func.HttpResponse(
            json.dumps({
                "success": False,
                "source": "genealogy",
                "processed": 0,
                "items": [],
                "error": str(e),
                "message": f"Genealogy ingestion failed: {e}",
            }),
            status_code=500,
            mimetype="application/json",
        )


def _handle_batch_failure(batch_id: str, config: BatchPollerConfig, error_detail: str):
    """Handle batch failure by marking records as error and cleaning up."""
    try:
        batch_status_doc = get_batch_status_from_cosmos(batch_id)
        if batch_status_doc:
            _mark_batch_records_as_error(
                batch_status_doc,
                config.error_message,
                error_detail,
                status_field=config.status_field
            )
        else:
            logging.warning("Could not find batch status document for failed batch %s", batch_id)
        _mark_batch_as_error_and_cleanup(batch_id, error_detail, config.batch_type)
    except Exception as e:
        logging.exception("Error handling batch failure for %s: %s", batch_id, e)


# ============================================================================
# OCR BATCH FUNCTIONS
# ============================================================================


# OCR poller configuration
_OCR_POLLER_CONFIG = BatchPollerConfig(
    batch_type="ocr",
    poller_name="OCR Batch Status Poller",
    trigger_endpoint="ocr-batch-status-poller",
    instance_id_prefix="ocr-poller",
    status_field="ocr_processing_status",
    error_message="Text extraction failed",
    error_detail="The text extraction process did not complete successfully. Please contact support.",
    process_func=process_completed_batch
)


@app.function_name(name="OcrBatchStatusPoller")
@app.timer_trigger(schedule="0 */2 * * * *", arg_name="timer")  # Every 2 minutes
def ocr_batch_status_poller(timer: func.TimerRequest):
    """Timer-triggered function that polls OCR batch status. Runs every 2 minutes."""
    logging.info("Starting OCR batch status polling at %s", timer.schedule_status)
    _process_batch_polling(_OCR_POLLER_CONFIG)


# Metadata poller configuration
_METADATA_POLLER_CONFIG = BatchPollerConfig(
    batch_type="metadata_extraction",
    poller_name="Metadata Batch Status Poller",
    trigger_endpoint="metadata-batch-status-poller",
    instance_id_prefix="metadata-poller",
    status_field="metadata_extraction_status",
    error_message="Metadata extraction failed",
    error_detail="The metadata extraction process did not complete successfully. Please contact support.",
    process_func=process_completed_metadata_batch
)


@app.function_name(name="MetadataBatchStatusPoller")
@app.timer_trigger(schedule="0 */2 * * * *", arg_name="timer")  # Every 2 minutes
def metadata_batch_status_poller(timer: func.TimerRequest):
    """Timer-triggered function that polls metadata batch status. Runs every 2 minutes."""
    logging.info("Starting metadata batch status polling at %s", timer.schedule_status)
    _process_batch_polling(_METADATA_POLLER_CONFIG)


# Resource type poller configuration
_RESOURCE_TYPE_POLLER_CONFIG = BatchPollerConfig(
    batch_type="resource_type",
    poller_name="Resource Type Batch Status Poller",
    trigger_endpoint="resource-type-batch-status-poller",
    instance_id_prefix="resource-type-poller",
    status_field="resource_type_processing_status",
    error_message="Resource type extraction failed",
    error_detail="The resource type extraction process did not complete successfully. Please contact support.",
    process_func=process_completed_resource_type_batch
)


@app.function_name(name="ResourceTypeBatchStatusPoller")
@app.timer_trigger(schedule="0 */2 * * * *", arg_name="timer")  # Every 2 minutes
def resource_type_batch_status_poller(timer: func.TimerRequest):
    """Timer-triggered function that polls resource type batch status. Runs every 2 minutes."""
    logging.info("Starting resource type batch status polling at %s", timer.schedule_status)
    _process_batch_polling(_RESOURCE_TYPE_POLLER_CONFIG)


# ============================================================================
# Content Source SYNC FUNCTIONS
# ============================================================================


@app.function_name(name="ContentSourceSyncClient")
@app.route(
    route="content-source-sync-client",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def content_source_sync_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """Start cursor-driven content-source ingestion.

    ``cursor`` may resume an adapter-owned page chain. ``total`` is retained only
    as advisory progress metadata and never controls page processing.
    """
    # Get parameters from query parameters, request body, or environment variables
    collection_ids = []
    batch_size = None
    parallel_batches = None
    cursor = None
    total = None

    if req.method == "GET":
        collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        batch_size_str = req.params.get("batch_size")
        parallel_batches_str = req.params.get("parallel_batches")
        cursor = req.params.get("cursor") or None
        total_str = req.params.get("total")
        if batch_size_str:
            try:
                batch_size = int(batch_size_str)
            except ValueError:
                pass
        if parallel_batches_str:
            try:
                parallel_batches = int(parallel_batches_str)
            except ValueError:
                pass
        if total_str:
            try:
                total = int(total_str)
            except ValueError:
                pass
    elif req.method == "POST":
        try:
            body = req.get_json()
            if body and isinstance(body, dict):
                collection_ids = parse_collection_ids(body.get("collection_ids"))
                batch_size = body.get("batch_size")
                parallel_batches = body.get("parallel_batches")
                cursor = body.get("cursor")
                total = body.get("total")
        except ValueError:
            pass

    # Fall back to environment variable if not provided
    if not collection_ids:
        collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

    # Check if there's already a running job
    job_check = await check_and_handle_running_job("content-source-sync-client", starter)
    if not job_check["allow_new_job"]:
        return job_check["response"]

    # Build input data for orchestrator
    # Set defaults from environment if not provided (do this in client, not orchestrator)
    if batch_size is None:
        batch_size = int(os.getenv("CONTENT_SOURCE_BATCH_SIZE", "100"))
    if parallel_batches is None:
        parallel_batches = int(os.getenv("CONTENT_SOURCE_PARALLEL_BATCHES", "1"))

    input_data = {}
    if collection_ids:
        input_data["collection_ids"] = collection_ids
    input_data["batch_size"] = batch_size
    input_data["parallel_batches"] = parallel_batches
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.strip():
            return func.HttpResponse(
                json.dumps({"error": "cursor must be a non-empty string"}),
                status_code=400,
                mimetype="application/json",
            )
        input_data["cursor"] = cursor
    if total is not None:
        input_data["total"] = total

    orchestrator_input = input_data

    instance_id = await starter.start_new(
        "ContentSourceSyncOrchestrator", None, orchestrator_input
    )
    logging.info(
        "Started orchestration with ID = '%s' with input = %s.",
        instance_id,
        orchestrator_input,
    )

    # Get management URLs for the orchestration instance
    mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

    # Save initial pipeline job status with management URLs
    saved = save_pipeline_job(
        trigger_endpoint="content-source-sync-client",
        instance_id=instance_id,
        name="Content Source Sync",
        runtime_status="Pending",
        status_url=mgmt_payload.get("statusQueryGetUri"),
        terminate_url=mgmt_payload.get("terminatePostUri"),
        suspend_url=mgmt_payload.get("suspendPostUri"),
        resume_url=mgmt_payload.get("resumePostUri"),
        input_data=orchestrator_input,
    )
    if not saved:
        logging.warning("Failed to save pipeline job for content-source-sync-client, but orchestration started")

    return starter.create_check_status_response(req, instance_id)


@app.function_name(name="ContentSourceSyncOrchestrator")
@app.orchestration_trigger("context")
def content_source_sync_orchestrator(context):
    """Fetch and store one opaque adapter page per Durable generation."""

    input_data = context.get_input()
    if not isinstance(input_data, dict):
        input_data = {}
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 100)
    if batch_size > 500:
        batch_size = 500
        orchestrator_log(context, "warning", "batch_size capped at 500")
    if batch_size < 1:
        batch_size = 100
        orchestrator_log(context, "warning", "batch_size must be >= 1, using 100")

    cursor = input_data.get("cursor")
    date_from = input_data.get("date_from")
    date_to = input_data.get("date_to")
    state = dict(input_data)
    state["batch_size"] = batch_size
    try:
        configured_parallel_batches = int(state.get("parallel_batches", 1) or 1)
    except (TypeError, ValueError):
        configured_parallel_batches = 1
    if configured_parallel_batches != 1:
        orchestrator_log(
            context,
            "warning",
            "parallel_batches is ignored because opaque cursors must advance sequentially",
        )
    state["parallel_batches"] = 1
    if "advisory_total" not in state and "total" in state:
        state["advisory_total"] = state.get("total")
    state.pop("total", None)
    state.pop("skip", None)

    orchestrator_log(
        context,
        "info",
        "Fetching content-source page %d (cursor=%s, batch_size=%d, advisory_total=%s)",
        int(state.get("pages_processed_so_far", 0) or 0) + 1,
        "initial" if cursor is None else "continuation",
        batch_size,
        state.get("advisory_total"),
    )

    activity_params = {
        "collection_ids": collection_ids,
        "cursor": cursor,
        "limit": batch_size,
        "instance_id": context.instance_id,
        "date_from": date_from,
        "date_to": date_to,
        "seen_cursor_fingerprints": state.get(
            "seen_cursor_fingerprints",
            [],
        ),
    }

    page_result = yield context.call_activity(
        "FetchAndStoreContentSourceRecords",
        activity_params,
    )
    transition = advance_sync_orchestration(state, page_result)
    if transition["status"] == "error":
        orchestrator_log(
            context,
            "error",
            "Content-source page failed: %s",
            transition["error"],
        )
        return transition

    if transition["status"] == "continue":
        orchestrator_log(
            context,
            "info",
            "Stored page %d; continuing with adapter-provided cursor",
            transition["pages_processed_so_far"],
        )
        return context.continue_as_new(transition["next_state"])

    transition["message"] = (
        "Content-source ingestion reached the adapter end cursor. "
        f"Stored {transition['total_processed_so_far']} records with "
        f"{transition['total_failed_so_far']} record failures."
    )
    orchestrator_log(
        context,
        "info",
        "Content-source ingestion completed after %d pages: %d stored, %d failed",
        transition["pages_processed_so_far"],
        transition["total_processed_so_far"],
        transition["total_failed_so_far"],
    )
    return transition


@app.function_name(name="FetchAndStoreContentSourceRecords")
@app.activity_trigger("params")
async def fetch_and_store_content_source_records(params: dict) -> dict:
    """
    Activity function that fetches adapter records and stores them directly.
    This avoids accumulating large data in orchestrator state.

    Retryable source failures are retried up to three times.

    Args:
        params: Dictionary with collection_ids (array), cursor, limit, instance_id,
                and optional date_from/date_to for date range filtering

    Returns:
        Dictionary with successful and failed counts (no full details to minimize payload)
    """
    collection_ids = params.get("collection_ids", [])
    cursor = params.get("cursor")
    limit = params.get("limit", 100)
    instance_id = params.get("instance_id")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    try:
        query_state = normalize_sync_query_state(
            {
                "version": 1,
                "cursor": cursor,
                "page_size": limit,
                "collection_ids": collection_ids,
                "date_from": date_from,
                "date_to": date_to,
                "seen_cursor_fingerprints": params.get(
                    "seen_cursor_fingerprints",
                    [],
                ),
            },
            require_complete=True,
        )
    except ContentSourceError as exc:
        return {
            "success": False,
            "successful": 0,
            "failed": 0,
            "error": exc.as_dict(),
        }
    collection_ids = query_state["collection_ids"]
    cursor = query_state["cursor"]
    limit = query_state["page_size"]
    date_from = query_state["date_from"]
    date_to = query_state["date_to"]
    max_retries = 3
    retry_delay_seconds = 5  # Wait between retries

    def log_failure(error: str) -> None:
        if not instance_id or not instance_id.startswith("retry-"):
            content_source_client.log_failed_sync_page(
                query_state=query_state,
                error=error,
                instance_id=instance_id,
            )

    # Retry loop for adapter query
    page = None
    for attempt in range(1, max_retries + 1):
        try:
            page = await content_source_client.query_content_records(
                collection_ids,
                limit=limit,
                cursor=cursor,
                date_from=date_from,
                date_to=date_to,
            )

            if page and "data" in page:
                break  # Success, exit retry loop

            logging.warning(
                "Attempt %d/%d: No page returned for %s cursor, limit=%d",
                attempt,
                max_retries,
                "initial" if cursor is None else "continuation",
                limit,
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds)

        except ContentSourceError as e:
            logging.warning(
                "Attempt %d/%d failed for %s cursor: %s",
                attempt,
                max_retries,
                "initial" if cursor is None else "continuation",
                e,
            )
            if e.retryable and attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds)
            else:
                log_failure(str(e))
                return {
                    "success": False,
                    "successful": 0,
                    "failed": 0,
                    "error": e.as_dict(),
                }
        except Exception as e:
            failure = ContentSourceError(
                ContentSourceErrorCode.SOURCE_FAILURE,
                "The content-source adapter failed unexpectedly",
                details={"exception_type": type(e).__name__},
            )
            logging.exception("Unexpected content-source adapter failure")
            log_failure(str(e))
            return {
                "success": False,
                "successful": 0,
                "failed": 0,
                "error": failure.as_dict(),
            }

    if not page or "data" not in page:
        failure = ContentSourceError(
            ContentSourceErrorCode.SOURCE_FAILURE,
            "The content-source adapter returned no page result",
        )
        log_failure(failure.message)
        return {
            "success": False,
            "successful": 0,
            "failed": 0,
            "error": failure.as_dict(),
        }

    records = page.get("data", [])
    if not records:
        reported_total = None
        if isinstance(page, dict):
            raw = page.get("total")
            if raw is not None:
                try:
                    reported_total = int(raw)
                except (TypeError, ValueError):
                    reported_total = None
        return {
            "success": True,
            "successful": 0,
            "failed": 0,
            "total": 0,
            "source_records_returned": 0,
            "reported_total": reported_total,
            "next_cursor": page.get("next_cursor"),
        }

    # Store records directly - this avoids accumulating in orchestrator
    # Call the store function logic directly (inline to avoid another activity call)
    # Prepare all records first
    prepared_records = []
    failed_prep = []

    for record_data in records:
        record_id = record_data.get("id")
        if not record_id:
            logging.warning("Skipping record without 'id' field")
            failed_prep.append(
                {"record_id": None, "success": False, "error": "Missing 'id' field"}
            )
            continue

        try:
            prep_result = content_source_client.prepare_record_data_from_fetch(record_data)
            if not prep_result.get("success"):
                logging.error("Failed to prepare record data for %s", record_id)
                failed_prep.append(
                    {
                        "record_id": record_id,
                        "success": False,
                        "error": "Failed to prepare record data",
                    }
                )
                continue

            flattened_record = prep_result.get("record_data")
            prepared_records.append(
                {
                    "record_id": record_id,
                    "record_data": flattened_record,
                    "original_data": record_data,
                }
            )
        except Exception as e:
            logging.exception("Failed to prepare record %s: %s", record_id, e)
            failed_prep.append(
                {
                    "record_id": record_id,
                    "success": False,
                    "error": f"Preparation error: {str(e)}",
                }
            )

    if not prepared_records:
        reported_total = None
        if page and isinstance(page, dict):
            raw = page.get("total")
            if raw is not None:
                try:
                    reported_total = int(raw)
                except (TypeError, ValueError):
                    reported_total = None
        failure = ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            "No records in the content-source page could be prepared",
            details={"failed_records": len(records)},
        )
        log_failure(failure.message)
        return {
            "success": False,
            "successful": 0,
            "failed": len(records),
            "total": len(records),
            "source_records_returned": len(records),
            "reported_total": reported_total,
            "next_cursor": page.get("next_cursor"),
            "error": failure.as_dict(),
        }

    # Write all prepared records to Cosmos DB in batch with retry logic
    batch_write_result = None
    for write_attempt in range(1, max_retries + 1):
        try:
            batch_write_result = await content_source_client.save_records_batch(
                prepared_records, instance_id
            )
            # Check if any records failed
            write_failures = len([
                detail
                for detail in batch_write_result.get("details", [])
                if not detail.get("success", False)
            ])
            # If any failed and we have retries left, retry the batch
            if write_failures > 0 and write_attempt < max_retries:
                logging.warning(
                    "Attempt %d/%d: %d/%d records failed in Cosmos write. Retrying...",
                    write_attempt, max_retries, write_failures, len(prepared_records)
                )
                await asyncio.sleep(retry_delay_seconds)
                continue
            break  # Success or final attempt
        except Exception as write_error:
            logging.warning(
                "Attempt %d/%d: Cosmos DB batch write failed for %s cursor: %s",
                write_attempt,
                max_retries,
                "initial" if cursor is None else "continuation",
                str(write_error),
            )
            if write_attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds)
            else:
                logging.exception(
                    "All %d retries exhausted for Cosmos write. Last error: %s",
                    max_retries,
                    write_error,
                )
                log_failure(f"Cosmos write failed: {str(write_error)}")
                return {
                    "success": False,
                    "successful": 0,
                    "failed": len(prepared_records) + len(failed_prep),
                    "error": {
                        "code": "storage_failure",
                        "message": "The content-source page could not be stored",
                        "retryable": True,
                        "details": {"exception_type": type(write_error).__name__},
                    },
                }

    successful = batch_write_result.get("successful", 0) if batch_write_result else 0
    failed_write_count = len([
        detail
        for detail in batch_write_result.get("details", [])
        if not detail.get("success", False)
    ]) if batch_write_result else len(prepared_records)

    total_failed = len(failed_prep) + failed_write_count

    # Log to ingestion-errors if ANY records failed (for later retry)
    page_error = None
    if total_failed > 0:
        page_error = {
            "code": "storage_failure" if failed_write_count else "invalid_data",
            "message": "The content-source page was not stored completely",
            "retryable": bool(failed_write_count),
            "details": {
                "failed_records": total_failed,
                "source_records": len(records),
            },
        }
        log_failure(
            f"Partial failure: {total_failed} of {len(records)} records failed"
        )
        logging.warning(
            "FetchAndStore partial failure: %d successful, %d failed out of %d records. Logged for retry.",
            successful,
            total_failed,
            len(records),
        )
    else:
        logging.info(
            "FetchAndStore complete: %d successful, %d failed out of %d records",
            successful,
            total_failed,
            len(records),
        )

    # Return only summary counts to minimize orchestrator state
    # Adapter totals are retained only as advisory progress metadata.
    reported_total = None
    if page and isinstance(page, dict):
        raw = page.get("total")
        if raw is not None:
            try:
                reported_total = int(raw)
            except (TypeError, ValueError):
                reported_total = None

    return {
        "success": total_failed == 0,
        "successful": successful,
        "failed": total_failed,
        "total": len(records),
        "source_records_returned": len(records),
        "reported_total": reported_total,
        "next_cursor": page.get("next_cursor"),
        **({"error": page_error} if page_error else {}),
    }


# ============================================================================
# RETRY FAILED SYNC PAGES
# ============================================================================


@app.function_name(name="GetPendingSyncFailures")
@app.activity_trigger("params")
def get_pending_sync_failures_activity(params: dict):
    """Activity that gets pending sync failures from Cosmos DB."""
    limit = params.get("limit", 10)
    collection_ids = params.get("collection_ids")

    try:
        failures = content_source_client.get_pending_sync_failures(
            limit=limit,
            collection_ids=collection_ids if collection_ids else None
        )
        return {"success": True, "failures": failures, "count": len(failures)}
    except Exception as e:
        logging.exception("Error getting pending sync failures: %s", e)
        return {"success": False, "failures": [], "count": 0, "error": str(e)}


async def _retry_failed_sync_page_chain(failure: dict) -> dict:
    """Resume one persisted failed page and drain its remaining cursor chain."""

    failure_id = failure.get("id") if isinstance(failure, dict) else None
    if not isinstance(failure_id, str) or not failure_id:
        return {
            "success": False,
            "status": "error",
            "error": {
                "code": ContentSourceErrorCode.INVALID_QUERY.value,
                "message": "The sync failure identifier is missing",
                "retryable": False,
                "details": {},
            },
        }
    try:
        failure = content_source_client.get_sync_failure(failure_id)
    except Exception as exc:
        logging.exception("Failed to load sync failure %s: %s", failure_id, exc)
        return {
            "success": False,
            "status": "error",
            "failure_id": failure_id,
            "error": {
                "code": "storage_failure",
                "message": "The latest sync retry checkpoint could not be loaded",
                "retryable": True,
                "details": {"exception_type": type(exc).__name__},
            },
        }
    if failure.get("status") == "completed":
        return {
            "success": True,
            "status": "completed",
            "failure_id": failure_id,
            "pages_processed": 0,
            "successful": 0,
            "failed": 0,
        }

    async def fetch_page(query_state):
        return await fetch_and_store_content_source_records(
            sync_query_activity_params(
                query_state,
                instance_id=f"retry-{failure_id}",
            )
        )

    def checkpoint(query_state, error):
        return content_source_client.update_sync_failure_state(
            failure_id,
            query_state,
            error,
        )

    def complete(query_state):
        return content_source_client.mark_sync_failure_completed(
            failure_id,
            query_state,
        )

    result = await retry_sync_failure_chain(
        failure,
        fetch_page,
        checkpoint,
        complete,
    )
    result["failure_id"] = failure_id
    return result


@app.function_name(name="RetryFailedSyncPageChain")
@app.activity_trigger("params")
async def retry_failed_sync_page_chain_activity(params: dict):
    """Activity that drains and atomically completes one persisted retry chain."""

    try:
        return await _retry_failed_sync_page_chain(params.get("failure"))
    except Exception as e:
        logging.exception("Unexpected sync retry-chain failure: %s", e)
        return {
            "success": False,
            "status": "error",
            "error": {
                "code": ContentSourceErrorCode.SOURCE_FAILURE.value,
                "message": "The sync retry chain failed unexpectedly",
                "retryable": True,
                "details": {"exception_type": type(e).__name__},
            },
        }


@app.function_name(name="MarkSyncFailureCompleted")
@app.activity_trigger("params")
def mark_sync_failure_completed_activity(params: dict):
    """Activity that marks a sync failure as completed."""
    failure_id = params.get("failure_id")

    try:
        success = content_source_client.mark_sync_failure_completed(
            failure_id,
            params.get("query_state"),
        )
        return {"success": success, "failure_id": failure_id}
    except Exception as e:
        logging.exception("Error marking sync failure as completed: %s", e)
        return {"success": False, "failure_id": failure_id, "error": str(e)}


# ============================================================================
# INDEPENDENT STAGE-BASED PROCESSING ORCHESTRATORS
# ============================================================================

@app.function_name(name="FetchRelatedAssetsClient")
@app.route(
    route="fetch-related-assets",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def fetch_related_assets_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger that starts the FetchRelatedAssets orchestrator
    to fetch related asset IDs for records.

    Query Parameters:
        - batch_size (optional): Number of records to process in each batch (default: 50)
        - collection_ids (optional): Filter records by source collection identifiers
          Supports: single value, comma-separated values, or JSON array
    """
    try:
        batch_size = 100
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 100))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("fetch-related-assets", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("related_assets_pending", collection_ids)

        logging.info(
            "Starting FetchRelatedAssets orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "FetchRelatedAssetsOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="fetch-related-assets",
            instance_id=instance_id,
            name="Fetch Related Assets",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for fetch-related-assets, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting FetchRelatedAssets orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="UpdatePipelineJobStatus")
@app.activity_trigger("payload")
def update_pipeline_job_status(payload: dict) -> dict:
    """Persist a Durable pipeline job status transition."""
    return persist_pipeline_job_status(payload)


@app.function_name(name="FetchRelatedAssetsOrchestrator")
@app.orchestration_trigger("context")
def fetch_related_assets_orchestrator(context):
    """
    Orchestrator that fetches related asset IDs for records.
    Queries records with related_assets_status = 'pending' or not defined.
    """
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 100)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 100
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 100")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "FetchRelatedAssetsOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    # Update pipeline job status to Running
    yield context.call_activity(
        "UpdatePipelineJobStatus",
        {
            "trigger_endpoint": "fetch-related-assets",
            "instance_id": context.instance_id,
            "name": "Fetch Related Assets",
            "runtime_status": "Running",
            "input_data": input_data,
        },
    )

    try:
        query_params = {"limit": batch_size, "status": "related_assets_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            error_result = {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }
            # Update pipeline job status to Completed (with error in output)
            yield context.call_activity(
                "UpdatePipelineJobStatus",
                {
                    "trigger_endpoint": "fetch-related-assets",
                    "instance_id": context.instance_id,
                    "name": "Fetch Related Assets",
                    "runtime_status": "Completed",
                    "output_data": error_result,
                },
            )
            return error_result

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            success_result = {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }
            # Update pipeline job status to Completed
            yield context.call_activity(
                "UpdatePipelineJobStatus",
                {
                    "trigger_endpoint": "fetch-related-assets",
                    "instance_id": context.instance_id,
                    "name": "Fetch Related Assets",
                    "runtime_status": "Completed",
                    "output_data": success_result,
                },
            )
            return success_result

        next_skip = skip + page_count  # Increment by actual records found, not batch_size
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call to minimize replay overhead
        batch_result = yield context.call_activity(
            "FetchRelatedAssetsBatch",
            {
                "record_ids": record_ids,
                "instance_id": f"fetch-assets-batch-{skip}",
            },
        )

        page_successful = batch_result.get("successful", 0)
        page_failed = batch_result.get("failed", 0)
        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size (no more pending records)
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            success_result = {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }
            # Update pipeline job status to Completed
            yield context.call_activity(
                "UpdatePipelineJobStatus",
                {
                    "trigger_endpoint": "fetch-related-assets",
                    "instance_id": context.instance_id,
                    "name": "Fetch Related Assets",
                    "runtime_status": "Completed",
                    "output_data": success_result,
                },
            )
            return success_result

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in FetchRelatedAssetsOrchestrator: %s", e)
        error_result = {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }
        # Update pipeline job status to Completed (with error in output)
        yield context.call_activity(
            "UpdatePipelineJobStatus",
            {
                "trigger_endpoint": "fetch-related-assets",
                "instance_id": context.instance_id,
                "name": "Fetch Related Assets",
                "runtime_status": "Completed",
                "output_data": error_result,
            },
        )
        return error_result


@app.function_name(name="FetchRelatedAssetsBatch")
@app.activity_trigger("payload")
async def fetch_related_assets_batch(payload: dict):
    """Activity that fetches related asset IDs for multiple records in parallel."""
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Fetching related assets for %d records", len(record_ids))

    async def process_record(record_id: str):
        """Process a single record."""
        try:
            record_data = content_source_client.container.read_item(
                item=record_id, partition_key=record_id
            )
        except Exception as e:
            logging.exception("Failed to get record %s from Cosmos DB: %s", record_id, e)
            content_source_client.update_record_status(record_id, "related_assets_status", "error", instance_id, error_reason={"message": "Database error", "detail": f"We were unable to retrieve this record from the database: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

        # Check if record already has related_assets (already processed)
        existing_assets = record_data.get("related_assets", [])
        if existing_assets and len(existing_assets) > 0:
            # Record already has assets, just update status to completed
            logging.info("Record %s already has %d related assets, updating status only", record_id, len(existing_assets))
            content_source_client.update_record_status(record_id, "related_assets_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "asset_details_status", "pending", instance_id)
            return {"success": True, "record_id": record_id, "asset_count": len(existing_assets), "already_processed": True}

        try:
            related_data = await content_source_client.fetch_related_assets(record_id)
            asset_ids = [a.get("id") for a in related_data.get("data", []) if a.get("id")]

            # Treat zero related assets as an error - records should have associated assets
            if len(asset_ids) == 0:
                error_msg = "No related assets found"
                logging.warning("Record %s has no related assets, marking as error", record_id)
                content_source_client.update_record_status(
                    record_id, "related_assets_status", "error", instance_id,
                    error_reason={"message": error_msg, "detail": f"No digital files were found for this record. This record may not have any associated images or documents. Record ID: {record_id}"}
                )
                return {"success": False, "record_id": record_id, "error": error_msg}

            if content_source_client.save_record_with_related_assets(record_data, asset_ids, instance_id):
                logging.info("Record %s updated with %d related assets", record_id, len(asset_ids))
                return {"success": True, "record_id": record_id, "asset_count": len(asset_ids), "already_processed": False}
            else:
                error_msg = "Failed to save record with related assets (error status already set)"
                return {"success": False, "record_id": record_id, "error": error_msg}
        except Exception as e:
            logging.exception("Failed to fetch related assets for %s: %s", record_id, e)
            content_source_client.update_record_status(record_id, "related_assets_status", "error", instance_id, error_reason={"message": "Service unavailable", "detail": f"We were unable to retrieve the related files for this record: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

    tasks = [process_record(record_id) for record_id in record_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = 0
    failed = 0
    already_processed = 0
    new_records = 0
    for idx, result in enumerate(results):
        record_id = record_ids[idx] if idx < len(record_ids) else "unknown"
        if isinstance(result, Exception):
            failed += 1
            logging.exception("Exception processing record %s: %s", record_id, result)
            # Update error status for the record
            content_source_client.update_record_status(
                record_id, "related_assets_status", "error", instance_id,
                error_reason={"message": "Processing error", "detail": f"An unexpected error occurred while retrieving related files: {str(result)}. Please contact support with Record ID: {record_id}"}
            )
        elif result and result.get("success", False):
            successful += 1
            if result.get("already_processed", False):
                already_processed += 1
            else:
                new_records += 1
        else:
            failed += 1

    logging.info(
        "FetchRelatedAssetsBatch complete: %d successful (%d new, %d already processed), %d failed out of %d records",
        successful, new_records, already_processed, failed, len(record_ids)
    )
    return {"successful": successful, "failed": failed, "total": len(record_ids), "new_records": new_records, "already_processed": already_processed}


@app.function_name(name="ProcessAssetDetailsClient")
@app.route(
    route="process-asset-details",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def process_asset_details_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """HTTP trigger that starts the ProcessAssetDetails orchestrator."""
    try:
        batch_size = 50
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("process-asset-details", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("asset_details_pending", collection_ids)

        logging.info(
            "Starting ProcessAssetDetails orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "ProcessAssetDetailsOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="process-asset-details",
            instance_id=instance_id,
            name="Process Asset Details",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for process-asset-details, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting ProcessAssetDetails orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="ProcessAssetDetailsOrchestrator")
@app.orchestration_trigger("context")
def process_asset_details_orchestrator(context):
    """Orchestrator that fetches asset details (metadata) for records."""
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 50
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 50")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "ProcessAssetDetailsOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    try:
        query_params = {"limit": batch_size, "status": "asset_details_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            return {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count  # Increment by actual records found, not batch_size
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call to minimize replay overhead
        batch_result = yield context.call_activity(
            "ProcessAssetDetailsBatch",
            {
                "record_ids": record_ids,
                "instance_id": f"asset-details-batch-{skip}",
            },
        )

        page_successful = batch_result.get("successful", 0)
        page_failed = batch_result.get("failed", 0)
        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size (no more pending records)
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in ProcessAssetDetailsOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }


@app.function_name(name="ProcessAssetDetailsBatch")
@app.activity_trigger("payload")
async def process_asset_details_batch(payload: dict):
    """Activity that fetches asset details (metadata) for multiple records in parallel."""
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Processing asset details for %d records", len(record_ids))

    async def process_record(record_id: str):
        """Process a single record."""
        try:
            record_data = content_source_client.container.read_item(
                item=record_id, partition_key=record_id
            )
        except Exception as e:
            logging.exception("Failed to get record %s from Cosmos DB: %s", record_id, e)
            content_source_client.update_record_status(record_id, "asset_details_status", "error", instance_id, error_reason={"message": "Database error", "detail": f"We were unable to retrieve this record from the database: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

        if record_data.get("related_assets_status") != "completed":
            logging.warning("Record %s skipped: related_assets_status is not completed (current: %s)",
                          record_id, record_data.get("related_assets_status"))
            return {"success": False, "record_id": record_id, "error": f"Dependency not met: related_assets_status is not completed"}

        asset_ids = record_data.get("related_assets", [])
        if not asset_ids:
            logging.info("Record %s has no related assets", record_id)
            content_source_client.update_record_status(record_id, "asset_details_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "original_file_status", "completed", instance_id)
            # No assets means no OCR processing needed
            content_source_client.update_record_status(record_id, "ocr_batch_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "ocr_processing_status", "completed", instance_id)
            return {"success": True, "record_id": record_id, "asset_count": 0}

        semaphore = asyncio.Semaphore(20)

        async def fetch_detail(asset_id: str):
            """Fetch typed asset metadata without dereferencing external URLs."""
            try:
                async with semaphore:
                    asset_detail = await content_source_client.fetch_asset_detail_only(asset_id, record_id, instance_id)
                    if not asset_detail:
                        return None
                    return asset_detail
            except Exception as e:
                logging.exception("Unexpected exception in fetch_detail for asset %s (record %s): %s", asset_id, record_id, e)
                content_source_client.log_processing_error(
                    instance_id=instance_id,
                    record_id=record_id,
                    asset_id=asset_id,
                    reason="asset_detail_fetch_exception",
                    message=str(e)
                )
                # Re-raise to be caught by asyncio.gather
                raise

        asset_tasks = [
            fetch_detail(asset_id)
            for asset_id in asset_ids
        ]
        asset_results = await asyncio.gather(*asset_tasks, return_exceptions=True)

        asset_details = []
        failed_asset_ids = []
        error_messages = []  # Track unique error messages
        for idx, asset_result in enumerate(asset_results):
            asset_id = asset_ids[idx] if idx < len(asset_ids) else "unknown"
            if isinstance(asset_result, Exception):
                logging.exception("Asset detail fetch exception for asset %s (record %s): %s", asset_id, record_id, asset_result)
                failed_asset_ids.append(asset_id)
                error_messages.append(str(asset_result))
            elif asset_result:
                asset_details.append(asset_result)
            else:
                # asset_result is None (fetch_asset_detail_only returned None)
                logging.warning("Asset detail fetch returned None for asset %s (record %s)", asset_id, record_id)
                failed_asset_ids.append(asset_id)
                error_messages.append("Asset detail not found")

        # NO PARTIAL SUCCESS: If ANY asset failed, mark record as error
        if failed_asset_ids:
            # Use first error message for grouping
            error_message = "File details unavailable"
            error_detail = f"We were unable to retrieve details for {len(failed_asset_ids)} of {len(asset_ids)} files. Please contact support with Record ID: {record_id}, Failed Asset IDs: {failed_asset_ids}"
            logging.error("Partial/complete failure for record %s: %d of %d assets failed. Failed asset IDs: %s", record_id, len(failed_asset_ids), len(asset_ids), failed_asset_ids)
            try:
                content_source_client.update_record_status(
                    record_id, "asset_details_status", "error", instance_id,
                    error_reason={"message": error_message, "detail": error_detail}
                )
            except Exception as status_err:
                logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
            return {"success": False, "record_id": record_id, "error": error_detail}

        try:
            if content_source_client.save_record_with_asset_details(record_data, asset_details, instance_id):
                logging.info("Record %s updated with %d asset details", record_id, len(asset_details))
                return {"success": True, "record_id": record_id, "asset_count": len(asset_details)}
            else:
                error_msg = "Failed to save record with asset details (error status already set)"
                return {"success": False, "record_id": record_id, "error": error_msg}
        except Exception as e:
            logging.exception("Exception processing asset details for record %s: %s", record_id, e)
            content_source_client.update_record_status(record_id, "asset_details_status", "error", instance_id, error_reason={"message": "Save failed", "detail": f"We retrieved the file details but were unable to save them: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

    tasks = [process_record(record_id) for record_id in record_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = 0
    failed = 0
    for idx, result in enumerate(results):
        record_id = record_ids[idx] if idx < len(record_ids) else "unknown"
        if isinstance(result, Exception):
            failed += 1
            logging.exception("Exception processing record %s: %s", record_id, result)
            # Update error status for the record
            content_source_client.update_record_status(
                record_id, "asset_details_status", "error", instance_id,
                error_reason={"message": "Processing error", "detail": f"An unexpected error occurred while retrieving file details: {str(result)}. Please contact support with Record ID: {record_id}"}
            )
        elif result and result.get("success", False):
            successful += 1
        else:
            failed += 1

    logging.info("Batch complete: %d successful, %d failed out of %d records", successful, failed, len(record_ids))
    return {"successful": successful, "failed": failed, "total": len(record_ids)}


@app.function_name(name="ProcessOriginalFilesClient")
@app.route(
    route="process-original-files",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def process_original_files_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """HTTP trigger that starts the ProcessOriginalFiles orchestrator."""
    try:
        batch_size = 50
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("process-original-files", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("original_file_pending", collection_ids)

        logging.info(
            "Starting ProcessOriginalFiles orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "ProcessOriginalFilesOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="process-original-files",
            instance_id=instance_id,
            name="Process Original Files",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for process-original-files, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting ProcessOriginalFiles orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="ProcessOriginalFilesOrchestrator")
@app.orchestration_trigger("context")
def process_original_files_orchestrator(context):
    """Orchestrator that downloads and uploads original files for records."""
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 50
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 50")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "ProcessOriginalFilesOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    try:
        query_params = {"limit": batch_size, "status": "original_file_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            return {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count  # Increment by actual records found, not batch_size
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call to minimize replay overhead
        batch_result = yield context.call_activity(
            "ProcessOriginalFilesBatch",
            {
                "record_ids": record_ids,
                "instance_id": f"original-files-batch-{skip}",
            },
        )

        page_successful = batch_result.get("successful", 0)
        page_failed = batch_result.get("failed", 0)
        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size (no more pending records)
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in ProcessOriginalFilesOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }


@app.function_name(name="ProcessOriginalFilesBatch")
@app.activity_trigger("payload")
async def process_original_files_batch(payload: dict):
    """Activity that downloads and uploads original files for multiple records in parallel."""
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Processing original files for %d records", len(record_ids))

    async def process_record(record_id: str):
        """Process a single record."""
        try:
            record_data = content_source_client.container.read_item(
                item=record_id, partition_key=record_id
            )
        except Exception as e:
            logging.exception("Failed to get record %s from Cosmos DB: %s", record_id, e)
            content_source_client.update_record_status(record_id, "original_file_status", "error", instance_id, error_reason={"message": "Database error", "detail": f"We were unable to retrieve this record from the database: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

        if record_data.get("asset_details_status") != "completed":
            logging.warning("Record %s skipped: asset_details_status is not completed (current: %s)",
                          record_id, record_data.get("asset_details_status"))
            return {"success": False, "record_id": record_id, "error": f"Dependency not met: asset_details_status is not completed"}

        asset_details = record_data.get("asset_details", [])
        if not asset_details:
            logging.info("Record %s has no asset details", record_id)
            content_source_client.update_record_status(record_id, "original_file_status", "completed", instance_id)
            # No asset details means no OCR processing needed
            content_source_client.update_record_status(record_id, "ocr_batch_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "ocr_processing_status", "completed", instance_id)
            return {"success": True, "record_id": record_id, "asset_count": 0}

        semaphore = asyncio.Semaphore(10)  # Lower concurrency for file downloads

        async def process_file_with_semaphore(asset_detail):
            try:
                async with semaphore:
                    asset_id = asset_detail.get("asset_id")
                    return await content_source_client.process_asset_files(asset_id, record_id, asset_detail, instance_id)
            except Exception as e:
                asset_id = asset_detail.get("asset_id", "unknown")
                logging.exception("Unexpected exception in process_file_with_semaphore for asset %s (record %s): %s", asset_id, record_id, e)
                content_source_client.log_processing_error(
                    instance_id=instance_id,
                    record_id=record_id,
                    asset_id=asset_id,
                    reason="original_file_processing_exception",
                    message=str(e)
                )
                # Re-raise to be caught by asyncio.gather
                raise

        asset_tasks = [
            process_file_with_semaphore(asset_detail)
            for asset_detail in asset_details
        ]
        asset_results = await asyncio.gather(*asset_tasks, return_exceptions=True)

        processed_assets = []
        failed_asset_ids = []
        error_messages = []  # Track unique error messages
        for idx, asset_result in enumerate(asset_results):
            asset_detail = asset_details[idx] if idx < len(asset_details) else {}
            asset_id = asset_detail.get("asset_id", "unknown")
            if isinstance(asset_result, Exception):
                logging.exception("Asset file processing exception for asset %s (record %s): %s", asset_id, record_id, asset_result)
                failed_asset_ids.append(asset_id)
                error_messages.append(str(asset_result))
            elif asset_result:
                # Check if asset has blob_url (file was successfully processed)
                if asset_result.get("blob_url"):
                    processed_assets.append(asset_result)
                else:
                    # Asset processed but no blob_url (file not found or download failed)
                    logging.warning("Asset %s (record %s) processed but no blob_url obtained", asset_id, record_id)
                    failed_asset_ids.append(asset_id)
                    error_messages.append("No blob URL")
            else:
                # asset_result is None (process_asset_files returned None)
                logging.warning("Asset file processing returned None for asset %s (record %s)", asset_id, record_id)
                failed_asset_ids.append(asset_id)
                error_messages.append("File processing failed")

        # NO PARTIAL SUCCESS: If ANY asset failed, mark record as error
        if failed_asset_ids:
            # Use first error message for grouping
            error_message = "File download failed"
            error_detail = f"We were unable to download {len(failed_asset_ids)} of {len(asset_details)} files. The source files may be unavailable. Please contact support with Record ID: {record_id}, Failed Asset IDs: {failed_asset_ids}"
            logging.error("Partial/complete failure for record %s: %d of %d assets failed. Failed asset IDs: %s", record_id, len(failed_asset_ids), len(asset_details), failed_asset_ids)
            try:
                content_source_client.update_record_status(
                    record_id, "original_file_status", "error", instance_id,
                    error_reason={"message": error_message, "detail": error_detail}
                )
            except Exception as status_err:
                logging.exception("Failed to update error status for record %s: %s", record_id, status_err)
            return {"success": False, "record_id": record_id, "error": error_detail}

        try:
            asset_count = content_source_client.save_record_with_original_files(record_data, processed_assets, instance_id)
            logging.info("Record %s updated with %d processed assets", record_id, asset_count)
            return {"success": True, "record_id": record_id, "asset_count": asset_count}
        except Exception as e:
            logging.exception("Exception processing original files for record %s: %s", record_id, e)
            content_source_client.update_record_status(record_id, "original_file_status", "error", instance_id, error_reason={"message": "Save failed", "detail": f"We processed the files but were unable to save them: {str(e)}. Please contact support with Record ID: {record_id}"})
            return {"success": False, "record_id": record_id, "error": str(e)}

    tasks = [process_record(record_id) for record_id in record_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = 0
    failed = 0
    for idx, result in enumerate(results):
        record_id = record_ids[idx] if idx < len(record_ids) else "unknown"
        if isinstance(result, Exception):
            failed += 1
            logging.exception("Exception processing record %s: %s", record_id, result)
            # Update error status for the record
            content_source_client.update_record_status(
                record_id, "original_file_status", "error", instance_id,
                error_reason={"message": "Processing error", "detail": f"An unexpected error occurred while processing files: {str(result)}. Please contact support with Record ID: {record_id}"}
            )
        elif result and result.get("success", False):
            successful += 1
        else:
            failed += 1

    logging.info("Batch complete: %d successful, %d failed out of %d records", successful, failed, len(record_ids))
    return {"successful": successful, "failed": failed, "total": len(record_ids)}


@app.function_name(name="CreateOCRBatchClient")
@app.route(
    route="create-ocr-batch",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def create_ocr_batch_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """HTTP trigger that starts the CreateOCRBatch orchestrator."""
    try:
        batch_size = 50
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("create-ocr-batch", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("ocr_batch_pending", collection_ids)

        logging.info(
            "Starting CreateOCRBatch orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "CreateOCRBatchOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="create-ocr-batch",
            instance_id=instance_id,
            name="Create OCR Batch",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for create-ocr-batch, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting CreateOCRBatch orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="CreateOCRBatchOrchestrator")
@app.orchestration_trigger("context")
def create_ocr_batch_orchestrator(context):
    """Orchestrator that creates OCR batches for records."""
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 50
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 50")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "CreateOCRBatchOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    try:
        query_params = {"limit": batch_size, "status": "ocr_batch_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            return {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count  # Increment by actual records found, not batch_size
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call to minimize replay overhead
        # Wrap in try-except to handle activity failures (OOM, crashes) gracefully
        try:
            batch_result = yield context.call_activity(
                "CreateOCRBatchBatch",
                {
                    "record_ids": record_ids,
                    "instance_id": f"ocr-batch-batch-{skip}",
                },
            )
            page_successful = batch_result.get("successful", 0)
            page_failed = batch_result.get("failed", 0)
        except Exception as activity_error:
            # Activity crashed (OOM, timeout, etc.) - mark records as error and continue
            orchestrator_log(
                context,
                "exception",
                "Activity failed for batch at skip=%d: %s. Marking %d records as error and continuing.",
                skip,
                str(activity_error),
                len(record_ids),
            )
            # Mark all records in this batch as error in Cosmos DB
            error_msg = f"Activity crashed: {str(activity_error)}"
            for record_id in record_ids:
                try:
                    content_source_client.update_record_status(
                        record_id, "ocr_batch_status", "error", None,
                        error_reason={"message": "Activity crashed", "detail": error_msg}
                    )
                except Exception as mark_err:
                    logging.warning("Failed to mark record %s as error: %s", record_id, mark_err)
            page_successful = 0
            page_failed = len(record_ids)

        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size (no more pending records)
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in CreateOCRBatchOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }


@app.function_name(name="CreateOCRBatchBatch")
@app.activity_trigger("payload")
async def create_ocr_batch_batch(payload: dict):
    """
    Activity that creates OCR batches for multiple records.

    Performance optimization: Batches multiple records together into a single batch job
    instead of creating one batch job per record. This significantly improves
    throughput by reducing the number of batch jobs and file conversions.
    """
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Creating OCR batches for %d records", len(record_ids))

    if not record_ids:
        return {"successful": 0, "failed": 0, "total": 0}

    # Step 1: Query all records in single batch
    try:
        records_map, missing_record_ids = query_records_by_ids(record_ids)

        # Mark missing records as errors
        for record_id in missing_record_ids:
            logging.warning("Record %s not found in Cosmos DB", record_id)
            content_source_client.update_record_status(
                record_id, "ocr_batch_status", "error", instance_id,
                error_reason={"message": "Record not found", "detail": f"This record could not be found in the system. It may have been removed. Please contact support with Record ID: {record_id}"}
            )

        logging.info("Found %d records out of %d requested", len(records_map), len(record_ids))

    except Exception as e:
        logging.exception("Failed to query records from Cosmos DB: %s", e)
        for record_id in record_ids:
            content_source_client.update_record_status(
                record_id, "ocr_batch_status", "error", instance_id,
                error_reason={"message": "Database error", "detail": f"We were unable to access the database to process this record for text extraction: {str(e)}. Please contact support with Record ID: {record_id}"}
            )
        return {"successful": 0, "failed": len(record_ids), "total": len(record_ids)}

    # Step 2: Filter records by dependency and asset count
    records_to_process = []
    records_with_no_assets = []
    records_skipped = []

    for record_id in record_ids:
        if record_id not in records_map:
            records_skipped.append({
                "record_id": record_id,
                "error": "Record not found in Cosmos DB"
            })
            continue

        record_data = records_map[record_id]

        # Check dependency
        if record_data.get("original_file_status") != "completed":
            logging.warning("Record %s skipped: original_file_status is not completed (current: %s)",
                          record_id, record_data.get("original_file_status"))
            records_skipped.append({
                "record_id": record_id,
                "error": f"Dependency not met: original_file_status is not completed"
            })
            continue

        asset_count = record_data.get("asset_count", 0)
        if asset_count == 0:
            logging.info("Record %s has no assets with blob URLs", record_id)
            records_with_no_assets.append(record_id)
        else:
            records_to_process.append(record_id)

    # Step 3: Mark records with no assets as completed
    for record_id in records_with_no_assets:
        content_source_client.update_record_status(record_id, "ocr_batch_status", "completed", instance_id)
        content_source_client.update_record_status(record_id, "ocr_processing_status", "completed", instance_id)

    if not records_to_process:
        logging.info("No records with assets to process. %d records had no assets, %d skipped",
                     len(records_with_no_assets), len(records_skipped))
        return {
            "successful": len(records_with_no_assets),
            "failed": len(records_skipped),
            "total": len(record_ids)
        }

    # Step 4: Collect all assets from all records and create ONE batch job
    # This dramatically reduces batch creation overhead from 50 separate jobs to 1 job
    # Record-level tracking is maintained via custom_id in batch requests
    successful = len(records_with_no_assets)
    failed = len(records_skipped)
    batch_ids = []

    # Collect all assets from all records
    all_record_assets = []

    for record_id in records_to_process:
        record_data = records_map[record_id]
        asset_details = record_data.get("asset_details", [])

        # Get resource_type from record metadata (used for prompt selection)
        resource_type = extract_resource_type_from_record(record_data)

        # Filter assets that have blob URLs (images ready for OCR)
        record_assets = []
        for asset in asset_details:
            asset_dict = create_asset_dict(record_id, asset, resource_type, use_thumbnail=False)
            if asset_dict.get("blob_url"):
                record_assets.append(asset_dict)
                all_record_assets.append(asset_dict)

        if not record_assets:
            logging.info("Record %s has no assets with blob URLs, marking as completed", record_id)
            content_source_client.update_record_status(record_id, "ocr_batch_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "ocr_processing_status", "completed", instance_id)
            successful += 1

    if not all_record_assets:
        logging.info("No assets to process across all records")
        return {
            "successful": successful,
            "failed": failed,
            "total": len(record_ids),
            "batch_ids": [],
            "batches_created": 0
        }

    # Create ONE batch job for all records - much faster!
    logging.info("Creating single batch job for %d records with %d total assets",
                 len(records_to_process), len(all_record_assets))

    try:
        from helper.ocr_batch_helper import create_ocr_batch_job_async

        batch_result = await create_ocr_batch_job_async(all_record_assets)

        if not batch_result.get("batch_id"):
            # Batch creation failed - mark all records as error
            if batch_result.get("status") == "error":
                logging.error("Failed to create OCR batch: %s", batch_result.get("error"))
                failed += _handle_batch_creation_error(
                    batch_result, records_to_process,
                    "ocr_batch_status", instance_id,
                    error_message="Text extraction failed"
                )
                return {
                    "successful": successful,
                    "failed": failed,
                    "total": len(record_ids),
                    "batch_ids": [],
                    "batches_created": 0
                }

        # Process successful batch creation (OCR doesn't track prep failures)
        batch_ids, batch_successful, batch_failed = _process_batch_creation_success(
            batch_result, records_to_process,
            "ocr_batch_status", instance_id,
            batch_type="ocr",
            assets=all_record_assets
        )
        successful += batch_successful
        failed += batch_failed

    except Exception as e:
        logging.exception("Exception creating OCR batch: %s", e)
        failed += _mark_records_batch_error(
            records_to_process, "ocr_batch_status", instance_id, e,
            error_message="Text extraction failed"
        )

    logging.info("Batch processing complete: %d successful, %d failed, %d batch jobs created",
                successful, failed, len(batch_ids))

    return {
        "successful": successful,
        "failed": failed,
        "total": len(record_ids),
        "batch_ids": batch_ids,
        "batches_created": len(batch_ids)
    }


# ============================================================================
# METADATA EXTRACTION BATCH FUNCTIONS (using Azure OpenAI Batch API)
# ============================================================================


@app.function_name(name="CreateMetadataBatchClient")
@app.route(
    route="create-metadata-batch",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def create_metadata_batch_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """HTTP trigger that starts the CreateMetadataBatch orchestrator."""
    try:
        batch_size = 50
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("create-metadata-batch", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("metadata_batch_pending", collection_ids)

        logging.info(
            "Starting CreateMetadataBatch orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "CreateMetadataBatchOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="create-metadata-batch",
            instance_id=instance_id,
            name="Create Metadata Batch",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for create-metadata-batch, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting CreateMetadataBatch orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="CreateMetadataBatchOrchestrator")
@app.orchestration_trigger("context")
def create_metadata_batch_orchestrator(context):
    """Orchestrator that creates metadata extraction batches for records."""
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 50
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 50")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "CreateMetadataBatchOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    try:
        query_params = {"limit": batch_size, "status": "metadata_batch_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            return {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call
        # Wrap in try-except to handle activity failures (OOM, crashes) gracefully
        try:
            batch_result = yield context.call_activity(
                "CreateMetadataBatchBatch",
                {
                    "record_ids": record_ids,
                    "instance_id": f"metadata-batch-batch-{skip}",
                },
            )
            page_successful = batch_result.get("successful", 0)
            page_failed = batch_result.get("failed", 0)
        except Exception as activity_error:
            # Activity crashed (OOM, timeout, etc.) - mark records as error and continue
            orchestrator_log(
                context,
                "exception",
                "Activity failed for batch at skip=%d: %s. Marking %d records as error and continuing.",
                skip,
                str(activity_error),
                len(record_ids),
            )
            # Mark all records in this batch as error in Cosmos DB
            error_msg = f"Activity crashed: {str(activity_error)}"
            for record_id in record_ids:
                try:
                    content_source_client.update_record_status(
                        record_id, "metadata_batch_status", "error", None,
                        error_reason={"message": "Activity crashed", "detail": error_msg}
                    )
                except Exception as mark_err:
                    logging.warning("Failed to mark record %s as error: %s", record_id, mark_err)
            page_successful = 0
            page_failed = len(record_ids)

        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in CreateMetadataBatchOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }


@app.function_name(name="CreateMetadataBatchBatch")
@app.activity_trigger("payload")
async def create_metadata_batch_batch(payload: dict):
    """
    Activity that creates metadata extraction batches for multiple records.
    Collects OCR text from records and submits to Azure OpenAI Batch API.
    """
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Creating metadata extraction batches for %d records", len(record_ids))

    if not record_ids:
        return {"successful": 0, "failed": 0, "total": 0}

    sas_token = get_user_delegation_sas(expiry_hours=24)

    # Step 1: Query all records in single batch
    try:
        records_map, missing_record_ids = query_records_by_ids(record_ids)

        for record_id in missing_record_ids:
            logging.warning("Record %s not found in Cosmos DB", record_id)
            content_source_client.update_record_status(
                record_id, "metadata_batch_status", "error", instance_id,
                error_reason={"message": "Record not found", "detail": f"This record could not be found in the system. Record ID: {record_id}"}
            )

        logging.info("Found %d records out of %d requested", len(records_map), len(record_ids))

    except Exception as e:
        logging.exception("Failed to query records from Cosmos DB: %s", e)
        for record_id in record_ids:
            content_source_client.update_record_status(
                record_id, "metadata_batch_status", "error", instance_id,
                error_reason={"message": "Database error", "detail": f"We were unable to access the database: {str(e)}. Record ID: {record_id}"}
            )
        return {"successful": 0, "failed": len(record_ids), "total": len(record_ids)}

    # Step 2: Filter and collect OCR text for eligible records
    records_to_process = []
    records_with_no_text = []
    records_skipped = []

    for record_id in record_ids:
        if record_id not in records_map:
            records_skipped.append({"record_id": record_id, "error": "Record not found"})
            continue

        record_data = records_map[record_id]

        # Check dependency
        if record_data.get("ocr_processing_status") != "completed":
            logging.warning("Record %s skipped: ocr_processing_status is not completed", record_id)
            records_skipped.append({"record_id": record_id, "error": "Dependency not met"})
            continue

        asset_details = record_data.get("asset_details", [])
        if not asset_details:
            logging.info("Record %s has no asset details, marking as completed", record_id)
            content_source_client.update_record_status(record_id, "metadata_batch_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "metadata_extraction_status", "completed", instance_id)
            records_with_no_text.append(record_id)
            continue

        # Collect OCR text and image URLs from all assets
        ocr_texts = []
        image_urls = []
        for asset in asset_details:
            # Collect OCR text
            ocr_url = asset.get('ocr_result', {}).get("ocr_text_flexible_blob_url")
            if ocr_url:
                try:
                    ocr_text = await download_text_from_blob(ensure_blob_accessible(ocr_url, sas_token))
                    if ocr_text:
                        ocr_texts.append(ocr_text)
                except Exception as e:
                    logging.warning("Failed to download OCR text for asset %s (record %s): %s",
                                  asset.get("asset_id"), record_id, e)

            # Collect image URLs for vision-based metadata extraction
            # Convert to base64 data URLs (required for Azure OpenAI Batch API)
            blob_url = asset.get("blob_url")
            if blob_url:
                try:
                    # Ensure the blob URL is accessible with SAS token
                    accessible_url = ensure_blob_accessible(blob_url, sas_token)
                    # Convert to base64 data URLs (handles JPEG, PNG, PDF, TIFF)
                    data_urls = await asyncio.to_thread(
                        convert_file_url_to_jpeg_data_urls,
                        accessible_url
                    )
                    if data_urls:
                        image_urls.extend(data_urls)
                except Exception as e:
                    logging.warning("Failed to convert image for asset %s (record %s): %s",
                                  asset.get("asset_id"), record_id, e)

        if not ocr_texts and not image_urls:
            logging.warning("Record %s has no OCR text or images available", record_id)
            content_source_client.update_record_status(
                record_id, "metadata_batch_status", "error", instance_id,
                error_reason={"message": "No content found", "detail": f"No readable text or images were found for this record. Record ID: {record_id}"}
            )
            records_skipped.append({"record_id": record_id, "error": "No OCR text or images"})
            continue

        # Concatenate all OCR texts
        concatenated_text = "\n\n".join(ocr_texts) if ocr_texts else ""
        records_to_process.append({
            "record_id": record_id,
            "ocr_text": concatenated_text,
            "image_urls": image_urls  # Include images for vision-based extraction
        })

    if not records_to_process:
        logging.info("No records with OCR text to process")
        return {
            "successful": len(records_with_no_text),
            "failed": len(records_skipped) + len(missing_record_ids),
            "total": len(record_ids)
        }

    # Step 3: Create batch job for metadata extraction
    successful = len(records_with_no_text)
    failed = len(records_skipped) + len(missing_record_ids)
    batch_ids = []

    logging.info("Creating metadata extraction batch for %d records", len(records_to_process))

    try:
        batch_result = await create_metadata_extraction_batch_job_async(records_to_process)

        if not batch_result.get("batch_id"):
            if batch_result.get("status") == "error":
                logging.error("Failed to create metadata batch: %s", batch_result.get("error"))
                failed += _handle_batch_creation_error(
                    batch_result, records_to_process,
                    "metadata_batch_status", instance_id,
                    records_failed_count_key="records_skipped"
                )
                return {
                    "successful": successful,
                    "failed": failed,
                    "total": len(record_ids),
                    "batch_ids": [],
                    "batches_created": 0
                }

        # Process successful batch creation
        batch_ids, batch_successful, batch_failed = _process_batch_creation_success(
            batch_result, records_to_process,
            "metadata_batch_status", instance_id,
            batch_type="metadata_extraction",
            prep_failed_key="records_skipped_prep"
        )
        successful += batch_successful
        failed += batch_failed

    except Exception as e:
        logging.exception("Exception creating metadata batch: %s", e)
        failed += _mark_records_batch_error(
            records_to_process, "metadata_batch_status", instance_id, e
        )

    return {
        "successful": successful,
        "failed": failed,
        "total": len(record_ids),
        "batch_ids": batch_ids,
        "batches_created": len(batch_ids)
    }


# ============================================================================
# VISUAL EXTRACTION BATCH FUNCTIONS (using Azure OpenAI Batch API)
# ============================================================================


@app.function_name(name="CreateResourceTypeBatchClient")
@app.route(
    route="create-resource-type-batch",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def create_resource_type_batch_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """HTTP trigger that starts the CreateResourceTypeBatch orchestrator."""
    try:
        batch_size = 50
        collection_ids = []

        if req.method == "GET":
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("create-resource-type-batch", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        # Get total count of pending records
        total = get_pending_records_count("resource_type_batch_pending", collection_ids)

        logging.info(
            "Starting CreateResourceTypeBatch orchestration (batch_size=%d, collection_ids=%s, total=%d)",
            batch_size,
            collection_ids,
            total,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "skip": 0,
            "total": total,
            "total_processed_so_far": 0,
            "total_failed_so_far": 0,
        }

        instance_id = await starter.start_new(
            "CreateResourceTypeBatchOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s' (total=%d)", instance_id, total)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        saved = save_pipeline_job(
            trigger_endpoint="create-resource-type-batch",
            instance_id=instance_id,
            name="Create Resource Type Batch",
            runtime_status="Pending",
            status_url=mgmt_payload.get("statusQueryGetUri"),
            terminate_url=mgmt_payload.get("terminatePostUri"),
            suspend_url=mgmt_payload.get("suspendPostUri"),
            resume_url=mgmt_payload.get("resumePostUri"),
            input_data=input_data,
        )
        if not saved:
            logging.warning("Failed to save pipeline job for create-resource-type-batch, but orchestration started")

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting CreateResourceTypeBatch orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="CreateResourceTypeBatchOrchestrator")
@app.orchestration_trigger("context")
def create_resource_type_batch_orchestrator(context):
    """Orchestrator that creates resource type batches for records."""
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    if batch_size > 200:
        batch_size = 200
        orchestrator_log(context, "warning", "batch_size capped at 200")
    if batch_size < 1:
        batch_size = 50
        orchestrator_log(context, "warning", "batch_size must be >= 1, using default 50")

    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "CreateResourceTypeBatchOrchestrator started (batch_size=%d, collection_ids=%s, skip=%d, total=%d, cumulative: %d processed, %d failed)",
        batch_size,
        collection_ids,
        skip,
        total,
        total_processed_so_far,
        total_failed_so_far,
    )

    try:
        query_params = {"limit": batch_size, "status": "resource_type_batch_pending"}
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsByStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records found")
            return {
                "status": "success",
                "message": "No more records to process",
                "total_processed_so_far": total_processed_so_far,
                "total_failed_so_far": total_failed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records",
            skip,
            page_count,
        )

        # Batch all records into a single activity call
        # Wrap in try-except to handle activity failures (OOM, crashes) gracefully
        try:
            batch_result = yield context.call_activity(
                "CreateResourceTypeBatchBatch",
                {
                    "record_ids": record_ids,
                    "instance_id": f"resource-type-batch-batch-{skip}",
                },
            )
            page_successful = batch_result.get("successful", 0)
            page_failed = batch_result.get("failed", 0)
        except Exception as activity_error:
            # Activity crashed (OOM, timeout, etc.) - mark records as error and continue
            orchestrator_log(
                context,
                "exception",
                "Activity failed for batch at skip=%d: %s. Marking %d records as error and continuing.",
                skip,
                str(activity_error),
                len(record_ids),
            )
            # Mark all records in this batch as error in Cosmos DB
            error_msg = f"Activity crashed: {str(activity_error)}"
            for record_id in record_ids:
                try:
                    content_source_client.update_record_status(
                        record_id, "resource_type_batch_status", "error", None,
                        error_reason={"message": "Activity crashed", "detail": error_msg}
                    )
                except Exception as mark_err:
                    logging.warning("Failed to mark record %s as error: %s", record_id, mark_err)
            page_successful = 0
            page_failed = len(record_ids)

        total_processed = total_processed_so_far + page_successful
        total_failed = total_failed_so_far + page_failed

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d successful, %d failed (Cumulative: %d processed, %d failed)",
            skip,
            page_successful,
            page_failed,
            total_processed,
            total_failed,
        )

        # Stop if we got fewer records than batch_size
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All records processed (last batch had %d records). Total: %d successful, %d failed",
                page_count,
                total_processed,
                total_failed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
                "total_failed_so_far": total_failed,
            }
        )

    except Exception as e:
        # Always log exceptions (regardless of replay status)
        logging.exception("[ResourceTypeBatch] Exception in orchestrator: %s", e)
        orchestrator_log(context, "exception", "Error in CreateResourceTypeBatchOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
            "total_failed_so_far": total_failed_so_far,
        }


@app.function_name(name="CreateResourceTypeBatchBatch")
@app.activity_trigger("payload")
async def create_resource_type_batch_batch(payload: dict):
    """
    Activity that creates resource type batches for multiple records.
    Collects images from records and submits to Azure OpenAI Batch API.
    """
    record_ids = payload.get("record_ids", [])
    instance_id = payload.get("instance_id")

    logging.info("Creating resource type batches for %d records", len(record_ids))

    if not record_ids:
        return {"successful": 0, "failed": 0, "total": 0}

    # Step 1: Query all records in single batch
    try:
        records_map, missing_record_ids = query_records_by_ids(record_ids)

        for record_id in missing_record_ids:
            logging.warning("Record %s not found in Cosmos DB", record_id)
            content_source_client.update_record_status(
                record_id, "resource_type_batch_status", "error", instance_id,
                error_reason={"message": "Record not found", "detail": f"This record could not be found in the system. Record ID: {record_id}"}
            )

        logging.info("Found %d records out of %d requested", len(records_map), len(record_ids))

    except Exception as e:
        logging.exception("Failed to query records from Cosmos DB: %s", e)
        for record_id in record_ids:
            content_source_client.update_record_status(
                record_id, "resource_type_batch_status", "error", instance_id,
                error_reason={"message": "Database error", "detail": f"We were unable to access the database: {str(e)}. Record ID: {record_id}"}
            )
        return {"successful": 0, "failed": len(record_ids), "total": len(record_ids)}

    # Step 2: Filter and collect image URLs for eligible records
    records_to_process = []
    records_with_no_images = []
    records_skipped = []

    for record_id in record_ids:
        if record_id not in records_map:
            records_skipped.append({"record_id": record_id, "error": "Record not found"})
            continue

        record_data = records_map[record_id]

        # Check dependency - original file processing must be completed
        if record_data.get("original_file_status") != "completed":
            logging.warning("Record %s skipped: original_file_status is not completed", record_id)
            records_skipped.append({"record_id": record_id, "error": "Dependency not met"})
            continue

        asset_details = record_data.get("asset_details", [])
        if not asset_details:
            logging.info("Record %s has no asset details, marking as completed", record_id)
            content_source_client.update_record_status(record_id, "resource_type_batch_status", "completed", instance_id)
            content_source_client.update_record_status(record_id, "resource_type_processing_status", "completed", instance_id)
            records_with_no_images.append(record_id)
            continue

        # Check if record has assets with blob_url
        # The batch function will access asset_details[].blob_url directly
        has_valid_assets = False
        for asset in asset_details:
            blob_url = asset.get("blob_url")
            if blob_url:
                has_valid_assets = True
                break

        if not has_valid_assets:
            logging.warning("Record %s has no assets with blob_url", record_id)
            content_source_client.update_record_status(
                record_id, "resource_type_batch_status", "error", instance_id,
                error_reason={"message": "No images found", "detail": f"No assets with blob_url were found for this record. Record ID: {record_id}"}
            )
            records_skipped.append({"record_id": record_id, "error": "No valid assets"})
            continue

        # Store full record data for batch processing
        # The batch function will access asset_details[].blob_url directly from the record
        records_to_process.append(record_data)

    if not records_to_process:
        logging.info("No records with images to process")
        return {
            "successful": len(records_with_no_images),
            "failed": len(records_skipped) + len(missing_record_ids),
            "total": len(record_ids)
        }

    # Step 3: Create batch job for resource type extraction
    successful = len(records_with_no_images)
    failed = len(records_skipped) + len(missing_record_ids)
    batch_ids = []

    logging.info("Creating resource type batch for %d records", len(records_to_process))

    try:
        batch_result = await create_resource_type_batch_job_async(records_to_process)

        if not batch_result.get("batch_id"):
            if batch_result.get("status") == "error":
                logging.error("Failed to create resource type batch: %s", batch_result.get("error"))
                failed += _handle_batch_creation_error(
                    batch_result, records_to_process,
                    "resource_type_batch_status", instance_id,
                    records_failed_count_key="records_failed"
                )
                return {
                    "successful": successful,
                    "failed": failed,
                    "total": len(record_ids),
                    "batch_ids": [],
                    "batches_created": 0
                }

        # Process successful batch creation
        batch_ids, batch_successful, batch_failed = _process_batch_creation_success(
            batch_result, records_to_process,
            "resource_type_batch_status", instance_id,
            batch_type="resource_type",
            prep_failed_key="records_failed_prep"
        )
        successful += batch_successful
        failed += batch_failed

    except Exception as e:
        logging.exception("Exception creating resource type batch: %s", e)
        failed += _mark_records_batch_error(
            records_to_process, "resource_type_batch_status", instance_id, e
        )

    return {
        "successful": successful,
        "failed": failed,
        "total": len(record_ids),
        "batch_ids": batch_ids,
        "batches_created": len(batch_ids)
    }


@app.function_name(name="QueryRecordsByStatus")
@app.activity_trigger("params")
def query_records_by_status(params: dict):
    """
    Activity function that queries Cosmos DB for records by status field.

    Args:
        params: Dictionary with:
            - status: Status to query for ('related_assets_pending', 'asset_details_pending', 'original_file_pending', 'ocr_processing_pending')
            - limit (optional): Maximum number of records to return (default: 100)
            - collection_ids (optional): List of metadata.Repository values to filter by

    Returns:
        Dictionary with:
            - success: Boolean indicating if query was successful
            - record_ids: List of record IDs
            - count: Number of records returned
            - error: Error message if query failed
    """
    status = params.get("status")
    limit = int(params.get("limit", 100))
    collection_ids = params.get("collection_ids", [])

    if not status:
        return {
            "success": False,
            "record_ids": [],
            "count": 0,
            "error": "Status parameter is required",
        }

    try:
        where_clause, query_parameters, status_field = _build_status_query_parts(status, collection_ids)
        if where_clause is None:
            return {
                "success": False,
                "record_ids": [],
                "count": 0,
                "error": f"Unknown status: {status}",
            }

        if collection_ids:
            logging.info(
                "Querying Cosmos DB for top %d records with %s = 'pending' and metadata.Repository IN %s",
                limit,
                status_field,
                collection_ids,
            )
        else:
            logging.info(
                "Querying Cosmos DB for top %d records with %s = 'pending'",
                limit,
                status_field,
            )

        query = f"SELECT TOP {limit} c.record_id FROM c WHERE {where_clause}"

        logging.debug("Executing Cosmos DB query: %s with parameters: %s", query, query_parameters)

        items = list(
            content_source_client.container.query_items(
                query=query,
                parameters=query_parameters if query_parameters else None,
                enable_cross_partition_query=True,
            )
        )

        record_ids = [item.get("record_id") for item in items if item.get("record_id")]

        if collection_ids:
            logging.info(
                "Found %d records with %s = 'pending' and metadata.Repository IN %s (limit=%d)",
                len(record_ids),
                status_field,
                collection_ids,
                limit,
            )
        else:
            logging.info(
                "Found %d records with %s = 'pending' (limit=%d)",
                len(record_ids),
                status_field,
                limit,
            )

        return {
            "success": True,
            "record_ids": record_ids,
            "count": len(record_ids),
        }

    except Exception as e:
        logging.exception(
            "Error querying records by status '%s': %s",
            status,
            e,
        )
        logging.error("Parameters: status=%s, limit=%s, collection_ids=%s", status, limit, collection_ids)
        return {
            "success": False,
            "record_ids": [],
            "count": 0,
            "error": str(e),
        }


# ============================================================================
# RETRY FAILED RECORDS FUNCTIONS
# ============================================================================

@app.function_name(name="RetryFailedRecordsClient")
@app.route(
    route="retry-failed-records",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def retry_failed_records_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger that starts the RetryFailedRecords orchestrator
    to retry records that failed in any of the processing steps.

    Query Parameters:
        - step (optional): Specific step to retry:
          'all' - retry all failed records
          'content_source_sync' - retry failed content-source pages (from log container)
          'related_assets', 'asset_details', 'original_file', 'ocr_batch',
          'ocr_processing', 'metadata_batch', 'metadata_extraction',
          'resource_type_batch', 'resource_type_processing'
        - max_retries (optional): Maximum retry count per record (default: 3)
        - batch_size (optional): Number of records to process per batch (default: 50)
        - collection_ids (optional): Filter records by source collection identifiers
          Supports: single value, comma-separated values, or JSON array
        - force_retry (optional): When true, retries ALL records where status != 'pending'
          (including error, completed, processing, etc.), ignores max_retries limit, and resets retry counts to 0
    """
    try:
        step = req.params.get("step", "all")
        max_retries = int(os.getenv("RETRY_MAX_RETRIES", "10"))
        batch_size = 50
        collection_ids = []
        force_retry = False

        if req.method == "GET":
            max_retries_str = req.params.get("max_retries")
            if max_retries_str:
                try:
                    max_retries = int(max_retries_str)
                except ValueError:
                    pass
            batch_size_str = req.params.get("batch_size")
            if batch_size_str:
                try:
                    batch_size = int(batch_size_str)
                except ValueError:
                    pass
            collection_ids = parse_collection_ids(req.params.get("collection_ids"))
            force_retry_str = req.params.get("force_retry", "").lower()
            force_retry = force_retry_str in ("true", "1", "yes")
        elif req.method == "POST":
            try:
                body = req.get_json()
                if body and isinstance(body, dict):
                    step = body.get("step", "all")
                    if "max_retries" in body:
                        max_retries = int(body.get("max_retries", os.getenv("RETRY_MAX_RETRIES", "10")))
                    if "batch_size" in body:
                        batch_size = int(body.get("batch_size", 50))
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
                    force_retry = bool(body.get("force_retry", False))
            except ValueError:
                pass

        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("retry-failed-records", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        logging.info(
            "Starting RetryFailedRecords orchestration (step=%s, max_retries=%d, batch_size=%d, collection_ids=%s, force_retry=%s)",
            step,
            max_retries,
            batch_size,
            collection_ids,
            force_retry,
        )

        input_data = {
            "step": step,
            "max_retries": max_retries,
            "batch_size": batch_size,
            "force_retry": force_retry,
        }
        if collection_ids:
            input_data["collection_ids"] = collection_ids

        instance_id = await starter.start_new(
            "RetryFailedRecordsOrchestrator", None, input_data
        )
        logging.info("Started retry orchestration with ID = '%s'", instance_id)

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        try:
            saved = save_pipeline_job(
                trigger_endpoint="retry-failed-records",
                instance_id=instance_id,
                name="Retry Failed Records",
                runtime_status="Pending",
                status_url=mgmt_payload.get("statusQueryGetUri"),
                terminate_url=mgmt_payload.get("terminatePostUri"),
                suspend_url=mgmt_payload.get("suspendPostUri"),
                resume_url=mgmt_payload.get("resumePostUri"),
                input_data=input_data,
            )
            if not saved:
                logging.error("Failed to save pipeline job for retry-failed-records")
        except Exception as save_error:
            logging.exception("Exception saving pipeline job for retry-failed-records: %s", save_error)

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting RetryFailedRecords orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="RetryFailedRecordsOrchestrator")
@app.orchestration_trigger("context")
def retry_failed_records_orchestrator(context):
    """
    Orchestrator that retries records with error status in any of the processing steps.
    Resets error status to pending and tracks retry count.
    Also supports retrying failed content-source pages (step='content_source_sync').
    """
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 50)
    step = input_data.get("step", "all")
    max_retries = input_data.get("max_retries", int(os.getenv("RETRY_MAX_RETRIES", "10")))
    force_retry = input_data.get("force_retry", False)
    skip = input_data.get("skip", 0)
    total = input_data.get("total", 0)
    total_processed_so_far = input_data.get("total_processed_so_far", 0)
    # Additional tracking for content_source_sync step
    total_succeeded_so_far = input_data.get("total_succeeded_so_far", 0)
    total_failed_so_far = input_data.get("total_failed_so_far", 0)

    orchestrator_log(
        context,
        "info",
        "RetryFailedRecordsOrchestrator started (step=%s, max_retries=%d, batch_size=%d, skip=%d, total=%d, cumulative: %d processed, force_retry=%s)",
        step,
        max_retries,
        batch_size,
        skip,
        total,
        total_processed_so_far,
        force_retry,
    )

    try:
        # Handle content_source_sync step separately (uses different data source)
        if step == "content_source_sync":
            query_result = yield context.call_activity(
                "GetPendingSyncFailures",
                {
                    "limit": batch_size,
                    "collection_ids": collection_ids,
                },
            )
            if not query_result.get("success"):
                return {
                    "status": "error",
                    "error": {
                        "code": "storage_failure",
                        "message": "Pending content-source failures could not be loaded",
                        "retryable": True,
                        "details": {"error": query_result.get("error")},
                    },
                    "total_succeeded": total_succeeded_so_far,
                    "total_failed": total_failed_so_far,
                }

            failures = query_result.get("failures", [])
            if not failures:
                orchestrator_log(context, "info", "No pending sync failures to retry")
                return {
                    "status": "success",
                    "message": "No failed sync pages to retry",
                    "total_succeeded": total_succeeded_so_far,
                    "total_failed": total_failed_so_far,
                }

            orchestrator_log(context, "info", "Found %d pending sync failures to retry", len(failures))

            retry_errors = []
            for failure in failures:
                failure_id = failure.get("id")
                orchestrator_log(
                    context,
                    "info",
                    "Retrying complete sync cursor chain for failure %s",
                    failure_id,
                )
                retry_result = yield context.call_activity(
                    "RetryFailedSyncPageChain",
                    {"failure": failure},
                )

                if retry_result.get("success"):
                    orchestrator_log(
                        context,
                        "info",
                        "Sync failure %s chain retry succeeded after %d pages",
                        failure_id,
                        retry_result.get("pages_processed", 0),
                    )
                    total_succeeded_so_far += 1
                else:
                    orchestrator_log(
                        context, "warning",
                        "Sync failure %s chain retry failed: %s",
                        failure_id,
                        retry_result.get("error", "Unknown error"),
                    )
                    total_failed_so_far += 1
                    retry_errors.append(
                        {
                            "failure_id": failure_id,
                            "error": retry_result.get("error"),
                        }
                    )

            orchestrator_log(
                context, "info",
                "Batch complete: %d succeeded, %d failed (will retry)",
                total_succeeded_so_far, total_failed_so_far
            )

            if retry_errors:
                return {
                    "status": "error",
                    "error": {
                        "code": "source_failure",
                        "message": "One or more content-source retry chains remain pending",
                        "retryable": True,
                        "details": {"failures": retry_errors},
                    },
                    "total_succeeded": total_succeeded_so_far,
                    "total_failed": total_failed_so_far,
                }

            if len(failures) < batch_size:
                return {
                    "status": "success",
                    "message": "All sync failure chains completed",
                    "total_succeeded": total_succeeded_so_far,
                    "total_failed": total_failed_so_far,
                }

            return context.continue_as_new({
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "step": step,
                "force_retry": force_retry,
                "total_succeeded_so_far": total_succeeded_so_far,
                "total_failed_so_far": total_failed_so_far,
            })

        # Determine which status fields to query based on step
        if step == "all":
            status_fields = [
                "related_assets_status",
                "asset_details_status",
                "original_file_status",
                "ocr_batch_status",
                "ocr_processing_status",
                "metadata_batch_status",
                "metadata_extraction_status",
                "resource_type_batch_status",
                "resource_type_processing_status",
            ]
        elif step == "related_assets":
            status_fields = ["related_assets_status"]
        elif step == "asset_details":
            status_fields = ["asset_details_status"]
        elif step == "original_file":
            status_fields = ["original_file_status"]
        elif step == "ocr_batch":
            status_fields = ["ocr_batch_status"]
        elif step == "ocr_processing":
            status_fields = ["ocr_processing_status"]
        elif step == "metadata_batch":
            status_fields = ["metadata_batch_status"]
        elif step == "metadata_extraction":
            status_fields = ["metadata_extraction_status"]
        elif step == "resource_type_batch":
            status_fields = ["resource_type_batch_status"]
        elif step == "resource_type_processing":
            status_fields = ["resource_type_processing_status"]
        else:
            orchestrator_log(
                context,
                "error",
                "Invalid step parameter: %s. Valid values: all, content_source_sync, related_assets, asset_details, original_file, ocr_batch, ocr_processing, metadata_batch, metadata_extraction, resource_type_batch, resource_type_processing",
                step,
            )
            return {
                "status": "error",
                "error": f"Invalid step: {step}",
                "total_processed_so_far": total_processed_so_far,
            }

        # Query for records with error status
        query_params = {
            "limit": batch_size,
            "status_fields": status_fields,
            "max_retries": max_retries,
            "force_retry": force_retry,
        }
        if collection_ids:
            query_params["collection_ids"] = collection_ids

        query_result = yield context.call_activity(
            "QueryRecordsWithErrorStatus",
            query_params,
        )

        if not query_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to query records with error status: %s",
                query_result.get("error"),
            )
            return {
                "status": "error",
                "error": query_result.get("error"),
                "total_processed_so_far": total_processed_so_far,
            }

        record_ids = query_result.get("record_ids", [])
        page_count = len(record_ids)

        if not record_ids:
            orchestrator_log(context, "info", "No more records with error status found")
            return {
                "status": "success",
                "message": "No more records to retry",
                "total_processed_so_far": total_processed_so_far,
                "skip": skip,
                "total": total,
            }

        next_skip = skip + page_count  # Increment by actual records found, not batch_size
        orchestrator_log(
            context,
            "info",
            "Processing batch at skip=%d: Found %d records with error status",
            skip,
            page_count,
        )

        # Reset error status to pending for retry
        reset_result = yield context.call_activity(
            "ResetErrorStatusForRetry",
            {
                "record_ids": record_ids,
                "status_fields": status_fields,
                "max_retries": max_retries,
                "force_retry": force_retry,
                "instance_id": f"retry-batch-{skip}",
            },
        )

        page_reset = reset_result.get("reset", 0)
        page_skipped = reset_result.get("skipped", 0)
        total_processed = total_processed_so_far + page_reset

        orchestrator_log(
            context,
            "info",
            "Batch at skip=%d complete: %d reset, %d skipped (Cumulative: %d processed)",
            skip,
            page_reset,
            page_skipped,
            total_processed,
        )

        # Stop if we got fewer records than batch_size (no more error records)
        if page_count < batch_size:
            orchestrator_log(
                context,
                "info",
                "All error records processed (last batch had %d records). Total reset: %d",
                page_count,
                total_processed,
            )
            return {
                "status": "success",
                "total_processed_so_far": total_processed,
                "skip": next_skip,
                "total": total,
            }

        return context.continue_as_new(
            {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "step": step,
                "max_retries": max_retries,
                "force_retry": force_retry,
                "skip": next_skip,
                "total": total,
                "total_processed_so_far": total_processed,
            }
        )

    except Exception as e:
        orchestrator_log(context, "exception", "Error in RetryFailedRecordsOrchestrator: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "total_processed_so_far": total_processed_so_far,
        }


@app.function_name(name="QueryRecordsWithErrorStatus")
@app.activity_trigger("params")
def query_records_with_error_status(params: dict):
    """Query Cosmos DB for records with error status and retry count below max."""
    status_fields = params.get("status_fields", [])
    limit = int(params.get("limit", 50))
    max_retries = int(params.get("max_retries", os.getenv("RETRY_MAX_RETRIES", "10")))
    force_retry = params.get("force_retry", False)
    # collection_ids is accepted for API consistency.

    if not status_fields:
        return {
            "success": False,
            "record_ids": [],
            "count": 0,
            "error": "status_fields parameter is required",
        }

    try:
        logging.info(
            "Querying Cosmos DB for records to retry in fields: %s (limit=%d, max_retries=%d, force_retry=%s)",
            status_fields,
            limit,
            max_retries,
            force_retry,
        )

        # Build WHERE clause to check status fields
        # When force_retry is True, get records where status != 'pending' (retry everything)
        # Otherwise, only get records with 'error' status and retry count below max
        conditions = []
        for field in status_fields:
            if force_retry:
                # Force retry: get all records where status is not pending
                conditions.append(f"(IS_DEFINED(c.{field}) AND c.{field} != 'pending')")
            else:
                # Normal retry: only get records with error status and retry count below max
                conditions.append(
                    f"(c.{field} = 'error' AND (NOT IS_DEFINED(c.{field}_retry_count) OR c.{field}_retry_count < {max_retries}))"
                )

        where_clause = "(" + " OR ".join(conditions) + ")"

        query = f"SELECT TOP {limit} c.record_id FROM c WHERE {where_clause}"

        logging.debug("Executing Cosmos DB query: %s", query)

        items = list(
            content_source_client.container.query_items(
                query=query,
                enable_cross_partition_query=True,
            )
        )

        record_ids = [item.get("record_id") for item in items if item.get("record_id")]

        logging.info("Found %d records to retry (limit=%d, force_retry=%s)", len(record_ids), limit, force_retry)

        return {"success": True, "record_ids": record_ids, "count": len(record_ids)}

    except Exception as e:
        logging.exception("Error querying records with error status: %s", e)
        return {"success": False, "record_ids": [], "count": 0, "error": str(e)}


@app.function_name(name="ResetErrorStatusForRetry")
@app.activity_trigger("payload")
def reset_error_status_for_retry(payload: dict):
    """Reset error status to pending for retry and increment retry count."""
    record_ids = payload.get("record_ids", [])
    status_fields = payload.get("status_fields", [])
    max_retries = int(payload.get("max_retries", os.getenv("RETRY_MAX_RETRIES", "10")))
    force_retry = payload.get("force_retry", False)
    # instance_id is not used in this function but kept for API consistency

    logging.info(
        "Resetting error status for %d records (fields: %s, max_retries=%d, force_retry=%s)",
        len(record_ids),
        status_fields,
        max_retries,
        force_retry,
    )

    reset_count = 0
    skipped_count = 0

    for record_id in record_ids:
        try:
            record_data = content_source_client.container.read_item(
                item=record_id, partition_key=record_id
            )

            reset_any = False
            for field in status_fields:
                current_status = record_data.get(field)
                retry_count = record_data.get(f"{field}_retry_count", 0)

                # When force_retry is True, reset any non-pending records and reset count to 0
                # Otherwise, only reset 'error' records with retry count below max
                if force_retry:
                    should_reset = current_status is not None and current_status != "pending"
                else:
                    should_reset = current_status == "error" and retry_count < max_retries

                if should_reset:
                    record_data[field] = "pending"
                    if force_retry:
                        # Force retry: reset retry count to 0
                        record_data[f"{field}_retry_count"] = 0
                        new_retry_count = 0
                    else:
                        # Normal retry: increment retry count
                        record_data[f"{field}_retry_count"] = retry_count + 1
                        new_retry_count = retry_count + 1
                    error_field = f"{field}_error"
                    if error_field in record_data:
                        record_data[f"{field}_last_error"] = record_data.get(error_field)
                        del record_data[error_field]
                    reset_any = True
                    logging.info(
                        "Reset %s for record %s (was: %s, retry count: %d -> %d%s)",
                        field,
                        record_id,
                        current_status,
                        retry_count,
                        new_retry_count,
                        " [FORCED]" if force_retry else "",
                    )

                    # Reset corresponding batch status when processing status is reset
                    # This ensures the batch will be recreated
                    batch_status_mapping = {
                        "ocr_processing_status": "ocr_batch_status",
                        "metadata_extraction_status": "metadata_batch_status",
                        "resource_type_processing_status": "resource_type_batch_status",
                    }
                    if field in batch_status_mapping:
                        batch_field = batch_status_mapping[field]
                        if record_data.get(batch_field) != "pending":
                            record_data[batch_field] = "pending"
                            logging.info(
                                "Also reset batch status %s to pending for record %s",
                                batch_field,
                                record_id,
                            )

                    # Reset all downstream pipeline steps to pending
                    # Pipeline order: related_assets -> asset_details -> original_file -> ocr_batch -> ocr_processing -> metadata_batch -> metadata_extraction
                    downstream_steps = _get_downstream_steps(field)
                    for downstream_field in downstream_steps:
                        if record_data.get(downstream_field) != "pending":
                            record_data[downstream_field] = "pending"
                            logging.info(
                                "Also reset downstream step %s to pending for record %s",
                                downstream_field,
                                record_id,
                            )

            if reset_any:
                record_data["last_processed"] = datetime.utcnow().isoformat()
                record_data["archivist_status"] = content_source_client.normalize_archivist_status(
                    record_data.get("archivist_status")
                )
                content_source_client.container.upsert_item(record_data)
                reset_count += 1
            else:
                skipped_count += 1

        except Exception as e:
            logging.exception("Failed to reset error status for record %s: %s", record_id, e)
            skipped_count += 1

    logging.info(
        "Reset complete: %d reset, %d skipped out of %d records",
        reset_count,
        skipped_count,
        len(record_ids),
    )
    return {"reset": reset_count, "skipped": skipped_count, "total": len(record_ids)}


@app.function_name(name="CancelAllBatchJobsClient")
@app.route(
    route="cancel-all-batches",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def cancel_all_batch_jobs_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger that starts the durable orchestration to cancel all active OpenAI batch jobs.

    Query Parameters:
        - dry_run (optional): If "true", only shows what would be cancelled without actually cancelling
        - status_filter (optional): Filter by status (e.g., "in_progress", "validating", "finalizing")

    Returns:
        JSON response with orchestration instance ID and status URL
    """
    try:
        # Get query parameters
        dry_run = req.params.get("dry_run", "").lower() == "true"
        status_filter = req.params.get("status_filter")  # Optional filter by status

        logging.info(
            "Starting cancel all batch jobs orchestration (dry_run=%s, status_filter=%s)",
            dry_run,
            status_filter,
        )

        # Start the orchestrator
        input_data = {"dry_run": dry_run, "status_filter": status_filter}

        instance_id = await starter.start_new(
            "CancelAllBatchJobsOrchestrator", None, input_data
        )
        logging.info("Started orchestration with ID = '%s'", instance_id)

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting cancel all batch jobs orchestration: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="CancelAllBatchJobsOrchestrator")
@app.orchestration_trigger("context")
def cancel_all_batch_jobs_orchestrator(context):
    """
    Orchestrator that cancels all active OpenAI batch jobs in parallel.
    Only fetches batches that are not completed (filters out completed, cancelled, failed, expired).
    """
    input_data = context.get_input()
    dry_run = input_data.get("dry_run", False)
    status_filter = input_data.get("status_filter")

    orchestrator_log(
        context,
        "info",
        "CancelAllBatchJobsOrchestrator started (dry_run=%s, status_filter=%s)",
        dry_run,
        status_filter,
    )

    try:
        # Step 1: Fetch non-completed batches
        fetch_result = yield context.call_activity(
            "FetchNonCompletedBatches", {"status_filter": status_filter}
        )

        if not fetch_result.get("success"):
            orchestrator_log(
                context,
                "error",
                "Failed to fetch batches: %s",
                fetch_result.get("error"),
            )
            return {
                "status": "error",
                "error": fetch_result.get("error"),
                "cancelled": 0,
                "failed": 0,
                "skipped": 0,
                "batches": [],
            }

        batches_to_cancel = fetch_result.get("batches", [])
        total_batches = fetch_result.get("total_batches", 0)
        by_status = fetch_result.get("by_status", {})

        orchestrator_log(
            context,
            "info",
            "Found %d batches to cancel out of %d total batches",
            len(batches_to_cancel),
            total_batches,
        )

        if not batches_to_cancel:
            orchestrator_log(context, "info", "No batches to cancel")
            return {
                "status": "success",
                "dry_run": dry_run,
                "total_batches": total_batches,
                "batches_to_cancel": 0,
                "cancelled": 0,
                "failed": 0,
                "skipped": 0,
                "by_status": by_status,
                "batches": [],
            }

        if dry_run:
            # Dry run - just return what would be cancelled
            orchestrator_log(
                context,
                "info",
                "DRY RUN: Would cancel %d batches",
                len(batches_to_cancel),
            )
            batches_info = []
            for batch_info in batches_to_cancel:
                batches_info.append(
                    {
                        "batch_id": batch_info.get("batch_id"),
                        "status": batch_info.get("status"),
                        "action": "would_cancel",
                    }
                )

            return {
                "status": "success",
                "dry_run": True,
                "total_batches": total_batches,
                "batches_to_cancel": len(batches_to_cancel),
                "cancelled": 0,
                "failed": 0,
                "skipped": len(batches_to_cancel),
                "by_status": by_status,
                "batches": batches_info,
            }

        # Step 2: Cancel all batches in parallel
        cancel_tasks = []
        for batch_info in batches_to_cancel:
            batch_id = batch_info.get("batch_id")
            if batch_id:
                cancel_tasks.append(
                    context.call_activity(
                        "CancelSingleBatch",
                        {"batch_id": batch_id, "status": batch_info.get("status")},
                    )
                )

        # Wait for all cancellations to complete (in parallel)
        cancel_results = yield context.task_all(cancel_tasks)

        # Aggregate results
        cancelled = 0
        failed = 0
        batches_info = []

        for result in cancel_results:
            if result and result.get("success"):
                cancelled += 1
                batches_info.append(
                    {
                        "batch_id": result.get("batch_id"),
                        "status": result.get("status"),
                        "new_status": result.get("new_status"),
                        "action": "cancelled",
                    }
                )
            else:
                failed += 1
                batches_info.append(
                    {
                        "batch_id": result.get("batch_id") if result else "unknown",
                        "status": result.get("status") if result else "unknown",
                        "action": "failed",
                        "error": result.get("error") if result else "Unknown error",
                    }
                )

        orchestrator_log(
            context,
            "info",
            "Cancel operation completed: %d cancelled, %d failed",
            cancelled,
            failed,
        )

        return {
            "status": "success",
            "dry_run": False,
            "total_batches": total_batches,
            "batches_to_cancel": len(batches_to_cancel),
            "cancelled": cancelled,
            "failed": failed,
            "skipped": 0,
            "by_status": by_status,
            "batches": batches_info,
        }

    except Exception as e:
        orchestrator_log(
            context, "error", "Error in cancel_all_batch_jobs_orchestrator: %s", e
        )
        return {
            "status": "error",
            "error": str(e),
            "cancelled": 0,
            "failed": 0,
            "skipped": 0,
            "batches": [],
        }


@app.function_name(name="FetchNonCompletedBatches")
@app.activity_trigger("params")
def fetch_non_completed_batches(params: dict):
    """
    Activity function that fetches all non-completed batches from Azure OpenAI.
    Filters out completed, cancelled, failed, and expired batches.
    """
    status_filter = params.get("status_filter")

    try:
        logging.info("Fetching non-completed batches (status_filter=%s)", status_filter)

        # Initialize batch client
        batch_client = AzureOpenAIBatchClient()
        openai_client = batch_client.client

        # List all batches
        all_batches = []
        try:
            batches = openai_client.batches.list(limit=100)
            for batch in batches:
                all_batches.append(batch)
                if len(all_batches) % 50 == 0:
                    logging.info("Fetched %d batches so far...", len(all_batches))
        except Exception as e:
            logging.exception("Error listing batches: %s", e)
            return {"success": False, "error": f"Failed to list batches: {str(e)}"}

        if not all_batches:
            logging.info("No batches found")
            return {"success": True, "total_batches": 0, "batches": [], "by_status": {}}

        # Define statuses that are considered "completed" (should be excluded)
        completed_statuses = {"completed", "cancelled", "failed", "expired"}

        # Active statuses that can be cancelled
        active_statuses = ["validating", "in_progress", "finalizing", "cancelling"]

        # Categorize batches by status
        categorized = {
            "validating": [],
            "in_progress": [],
            "finalizing": [],
            "cancelling": [],
            "completed": [],
            "failed": [],
            "cancelled": [],
            "expired": [],
            "other": [],
        }

        for batch in all_batches:
            status = (
                str(batch.status) if hasattr(batch.status, "value") else batch.status
            )
            status_lower = status.lower() if status else "other"

            if status_lower in categorized:
                categorized[status_lower].append(batch)
            else:
                categorized["other"].append(batch)

        # Filter batches to cancel - only non-completed ones
        batches_to_cancel = []
        if status_filter:
            # Filter by specific status if provided
            filter_lower = status_filter.lower()
            if filter_lower in categorized and filter_lower not in completed_statuses:
                batches_to_cancel = [
                    {
                        "batch_id": batch.id,
                        "status": (
                            str(batch.status)
                            if hasattr(batch.status, "value")
                            else batch.status
                        ),
                    }
                    for batch in categorized[filter_lower]
                ]
        else:
            # Default: only active batches (exclude completed ones)
            for status in active_statuses:
                for batch in categorized[status]:
                    batches_to_cancel.append(
                        {
                            "batch_id": batch.id,
                            "status": (
                                str(batch.status)
                                if hasattr(batch.status, "value")
                                else batch.status
                            ),
                        }
                    )

        # Build status summary (only include non-empty categories)
        by_status = {
            status: len(batches) for status, batches in categorized.items() if batches
        }

        logging.info(
            "Found %d batches to cancel out of %d total batches",
            len(batches_to_cancel),
            len(all_batches),
        )

        return {
            "success": True,
            "total_batches": len(all_batches),
            "batches": batches_to_cancel,
            "by_status": by_status,
        }

    except Exception as e:
        logging.exception("Error in fetch_non_completed_batches: %s", e)
        return {"success": False, "error": str(e)}


@app.function_name(name="CancelSingleBatch")
@app.activity_trigger("params")
def cancel_single_batch(params: dict):
    """
    Activity function that cancels a single batch job.
    """
    batch_id = params.get("batch_id")
    status = params.get("status")

    if not batch_id:
        return {"success": False, "error": "No batch_id provided"}

    try:
        logging.info("Cancelling batch: %s (status: %s)", batch_id, status)

        batch_client = AzureOpenAIBatchClient()
        cancelled_batch = batch_client.cancel_batch_job(batch_id)

        cancelled_status = (
            str(cancelled_batch.status)
            if hasattr(cancelled_batch.status, "value")
            else cancelled_batch.status
        )

        logging.info("Batch %s cancelled. New status: %s", batch_id, cancelled_status)

        return {
            "success": True,
            "batch_id": batch_id,
            "status": status,
            "new_status": cancelled_status,
        }

    except Exception as e:
        logging.exception("Error cancelling batch %s: %s", batch_id, e)
        return {
            "success": False,
            "batch_id": batch_id,
            "status": status,
            "error": str(e),
        }


# ============================================================================
# PERIODIC Content Source SYNC (TIMER-TRIGGERED)
# ============================================================================


@app.function_name(name="MergePeriodicRunHistoryCompletion")
@app.activity_trigger("payload")
def merge_periodic_run_history_completion_activity(payload: dict):
    """Patch Archivist Cosmos ``periodic_run_history`` when ``content-source-periodic-sync`` orchestration completes."""
    return merge_periodic_run_history_completion(payload or {})


async def _process_due_archivist_schedules_once(starter: df.DurableOrchestrationClient) -> None:
    """
    Poll Cosmos for due Archivist schedules, start ``content-source-periodic-sync``, record run history,
    and advance ``next_run_at``.
    """
    env_collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))
    try:
        default_lookback_hours = max(1, int(os.getenv("CONTENT_SOURCE_PERIODIC_LOOKBACK_HOURS", "168")))
    except (TypeError, ValueError):
        default_lookback_hours = 168
    try:
        default_batch_size = int(os.getenv("CONTENT_SOURCE_BATCH_SIZE", "100"))
        if default_batch_size < 1:
            default_batch_size = 100
    except (TypeError, ValueError):
        default_batch_size = 100
    try:
        default_parallel_batches = int(os.getenv("CONTENT_SOURCE_PARALLEL_BATCHES", "20"))
        if default_parallel_batches < 1:
            default_parallel_batches = 20
    except (TypeError, ValueError):
        default_parallel_batches = 20

    process_schedules = os.getenv("CONTENT_SOURCE_PERIODIC_TIMER_PROCESS_SCHEDULES", "true").lower() not in (
        "0",
        "false",
        "no",
    )
    if not process_schedules:
        return

    due = list_due_schedules(limit=20)
    logging.info("Content Source Periodic schedule poller: %d due schedule(s) from Cosmos", len(due))
    run_at = datetime.now(timezone.utc)
    for s in due:
        wf, wt = resolve_schedule_ingestion_calendar_window(s, run_at=run_at)
        if not wf or not wt:
            logging.warning(
                "Schedule %s: cannot resolve ingestion window (rolling days or legacy window); skip",
                s.get("id"),
            )
            continue

        job_check = await check_and_handle_running_job("content-source-periodic-sync", starter)
        if not job_check["allow_new_job"]:
            logging.info(
                "Schedule poller: content-source-periodic-sync already running (instance=%s); defer remaining schedules.",
                (job_check.get("existing_job") or {}).get("instance_id"),
            )
            break

        fv = s.get("collection_ids") or []
        if not isinstance(fv, list) or len(fv) == 0:
            fv = list(env_collection_ids) if env_collection_ids else []

        try:
            lb = max(1, int(s.get("lookback_hours") or default_lookback_hours))
        except (TypeError, ValueError):
            lb = default_lookback_hours
        try:
            bs = int(s.get("batch_size") or default_batch_size)
            if bs < 1:
                bs = default_batch_size
        except (TypeError, ValueError):
            bs = default_batch_size
        try:
            pb = int(s.get("parallel_batches") or default_parallel_batches)
            if pb < 1:
                pb = default_parallel_batches
        except (TypeError, ValueError):
            pb = default_parallel_batches

        run_all = bool(s.get("run_all_stages", True))
        sid = str(s.get("id") or "")

        oid = await _start_content_source_periodic_sync_orchestration(
            starter,
            collection_ids=fv,
            lookback_hours=lb,
            batch_size=bs,
            parallel_batches=pb,
            run_all_stages=run_all,
            from_date=wf,
            to_date=wt,
            date_from_iso=None,
            date_to_iso=None,
            schedule_id=sid or None,
        )
        if oid:
            mgmt = get_orchestration_management_urls(starter, oid)
            trigger_result = {
                "status_url": mgmt.get("statusQueryGetUri"),
                "terminate_url": mgmt.get("terminatePostUri"),
            }
            cn = s.get("collection_names")
            if not isinstance(cn, list):
                cn = []
            payload_hist: dict = {
                "collection_ids": fv,
                "lookback_hours": lb,
                "batch_size": bs,
                "parallel_batches": pb,
                "run_all_stages": run_all,
                "from_date": wf,
                "to_date": wt,
                "periodic_run_origin": "scheduled",
                "schedule_id": sid,
                "collection_names": cn,
            }
            await asyncio.to_thread(
                record_periodic_sync_started_from_timer,
                oid,
                payload_hist,
                trigger_result,
                "Content Source Periodic schedule poller",
            )
            lr = datetime.now(timezone.utc)
            await asyncio.to_thread(
                lambda sid_=sid, lr_=lr: mark_schedule_after_trigger(sid_, last_run_at=lr_),
            )
        else:
            logging.warning("Schedule poller: failed to start orchestration for schedule %s", sid)
            continue


@app.function_name(name="ContentSourcePeriodicSyncSchedulesPoller")
@app.timer_trigger(
    schedule="0 * * * * *",
    arg_name="timer",
    run_on_startup=False,
)  # Every minute UTC (checks ``next_run_at``; starts at most one orchestration per tick if due)
@app.durable_client_input(client_name="starter")
async def content_source_periodic_sync_schedules_poller(
    timer: func.TimerRequest, starter: df.DurableOrchestrationClient
):
    """
    Frequently checks schedule docs (``entity_type=schedule``) and starts any that are due.
    """
    logging.info(
        "Content Source Periodic schedule poller fired at %s (timer=%s)",
        datetime.utcnow().isoformat(),
        json.dumps(getattr(timer, "__dict__", {}), default=str),
    )
    try:
        await _process_due_archivist_schedules_once(starter)
    except Exception as e:
        logging.exception("Error in Content Source Periodic schedule poller: %s", e)


@app.function_name(name="ContentSourcePeriodicSyncTrigger")
@app.timer_trigger(schedule="0 0 2 * * 0", arg_name="timer")  # Weekly on Sunday at 2 AM UTC
@app.durable_client_input(client_name="starter")
async def content_source_periodic_sync_trigger(
    timer: func.TimerRequest, starter: df.DurableOrchestrationClient
):
    """
    Timer-triggered function that starts the complete Content Source sync pipeline.
    Runs weekly on Sunday at 2 AM UTC to pull data from the last week.

    The function:
    1. Queries records through the configured content-source adapter
    2. Runs all processing orchestrators in sequence:
       - ContentSourceSyncOrchestrator (fetch and store records)
       - FetchRelatedAssetsOrchestrator
       - ProcessAssetDetailsOrchestrator
       - ProcessOriginalFilesOrchestrator
       - CreateOCRBatchOrchestrator
       - ExtractMetadataOrchestrator

    Environment Variables:
        - CONTENT_SOURCE_COLLECTION_IDS: Collection identifiers (JSON array or single value)
        - CONTENT_SOURCE_PERIODIC_LOOKBACK_HOURS: Hours to look back for updates (default: 168 = 1 week)
        - CONTENT_SOURCE_BATCH_SIZE: Batch size for sync (default: 100)
        - CONTENT_SOURCE_PARALLEL_BATCHES: Parallel batches for sync (default: 20)
        - CONTENT_SOURCE_ADAPTER: Fixed registered adapter name (only synthetic is shipped)
    """
    logging.info("Content Source Periodic Sync triggered at %s (timer: %s)", datetime.utcnow().isoformat(), json.dumps(timer.__dict__))

    try:
        # Get configuration from environment
        collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))
        try:
            lookback_hours = max(1, int(os.getenv("CONTENT_SOURCE_PERIODIC_LOOKBACK_HOURS", "168")))
        except (TypeError, ValueError):
            lookback_hours = 168
        try:
            batch_size = int(os.getenv("CONTENT_SOURCE_BATCH_SIZE", "100"))
            if batch_size < 1:
                batch_size = 100
        except (TypeError, ValueError):
            batch_size = 100
        try:
            parallel_batches = int(os.getenv("CONTENT_SOURCE_PARALLEL_BATCHES", "20"))
            if parallel_batches < 1:
                parallel_batches = 20
        except (TypeError, ValueError):
            parallel_batches = 20

        skip_legacy = os.getenv("CONTENT_SOURCE_PERIODIC_TIMER_SKIP_LEGACY_LOOKBACK", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        if skip_legacy:
            logging.info("Timer: legacy lookback skipped by CONTENT_SOURCE_PERIODIC_TIMER_SKIP_LEGACY_LOOKBACK")
            return

        _ = await _start_content_source_periodic_sync_orchestration(
            starter,
            collection_ids=collection_ids,
            lookback_hours=lookback_hours,
            batch_size=batch_size,
            parallel_batches=parallel_batches,
            run_all_stages=True,
            from_date=None,
            to_date=None,
            date_from_iso=None,
            date_to_iso=None,
            schedule_id=None,
        )

    except Exception as e:
        logging.exception("Error in Content Source Periodic Sync trigger: %s", e)


@app.function_name(name="ContentSourcePeriodicSyncClient")
@app.route(
    route="content-source-periodic-sync",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST", "GET"],
)
@app.durable_client_input(client_name="starter")
async def content_source_periodic_sync_client(
    req: func.HttpRequest, starter: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger to manually start the Content Source periodic sync pipeline.
    Useful for testing or triggering sync outside the scheduled time.

    Query Parameters:
        - collection_ids: Filter by collection identifiers (JSON array or single value)
        - lookback_hours: Rolling window in hours (default: 168). Used when no custom
          window is selected (see from_date / to_date / date_from / date_to).
        - from_date, to_date: Optional YYYY-MM-DD pair (Archivist). Both required to
          use a calendar window; start 00:00:00 and end 23:59:59.999999 UTC on those days.
        - date_from, date_to: Optional full ISO datetimes; if both set, they override
          from_date / to_date and lookback.
        - batch_size: Batch size for sync (default: 100)
        - parallel_batches: Parallel batches for sync (default: 20)
        - run_all_stages: Whether to run all processing stages (default: true)

    Total and record paging use the same typed adapter query.
    """
    try:
        # Parse parameters
        collection_ids = []
        lookback_hours = 168  # Default: 1 week (7 * 24 = 168 hours)
        batch_size = 100
        parallel_batches = 20
        run_all_stages = True
        from_date: Optional[str] = _str_param(req.params.get("from_date"))
        to_date: Optional[str] = _str_param(req.params.get("to_date"))
        date_from_iso: Optional[str] = _str_param(req.params.get("date_from"))
        date_to_iso: Optional[str] = _str_param(req.params.get("date_to"))

        # Query string: used for GET (browser/curl) and for POST when callers append
        # parameters to the URL (e.g. API gateway / BFF). POST JSON overrides below.
        collection_ids = parse_collection_ids(req.params.get("collection_ids"))
        lookback_hours_str = req.params.get("lookback_hours")
        if lookback_hours_str:
            try:
                lookback_hours = max(1, int(lookback_hours_str))
            except (TypeError, ValueError):
                pass
        batch_size_str = req.params.get("batch_size")
        if batch_size_str:
            try:
                v = int(batch_size_str)
                if v >= 1:
                    batch_size = v
            except (TypeError, ValueError):
                pass
        parallel_batches_str = req.params.get("parallel_batches")
        if parallel_batches_str:
            try:
                v = int(parallel_batches_str)
                if v >= 1:
                    parallel_batches = v
            except (TypeError, ValueError):
                pass
        if req.params.get("run_all_stages") is not None:
            run_all_stages = req.params.get("run_all_stages", "true").lower() != "false"

        if req.method == "POST":
            try:
                body = req.get_json()
            except ValueError:
                body = None
            if body and isinstance(body, dict):
                if "collection_ids" in body:
                    collection_ids = parse_collection_ids(body.get("collection_ids"))
                if body.get("lookback_hours") is not None:
                    try:
                        lookback_hours = max(1, int(body["lookback_hours"]))
                    except (TypeError, ValueError):
                        pass
                if body.get("batch_size") is not None:
                    try:
                        v = int(body["batch_size"])
                        if v >= 1:
                            batch_size = v
                    except (TypeError, ValueError):
                        pass
                if body.get("parallel_batches") is not None:
                    try:
                        v = int(body["parallel_batches"])
                        if v >= 1:
                            parallel_batches = v
                    except (TypeError, ValueError):
                        pass
                if "run_all_stages" in body:
                    rs = body["run_all_stages"]
                    if isinstance(rs, bool):
                        run_all_stages = rs
                    else:
                        run_all_stages = str(rs).lower() not in (
                            "false",
                            "0",
                            "no",
                        )
                if "from_date" in body and body.get("from_date") is not None:
                    from_date = _str_param(body.get("from_date"))
                if "to_date" in body and body.get("to_date") is not None:
                    to_date = _str_param(body.get("to_date"))
                if "date_from" in body and body.get("date_from") is not None:
                    date_from_iso = _str_param(body.get("date_from"))
                if "date_to" in body and body.get("date_to") is not None:
                    date_to_iso = _str_param(body.get("date_to"))

        # Fall back to environment variable
        if not collection_ids:
            collection_ids = parse_collection_ids(os.getenv("CONTENT_SOURCE_COLLECTION_IDS"))

        # Check if there's already a running job
        job_check = await check_and_handle_running_job("content-source-periodic-sync", starter)
        if not job_check["allow_new_job"]:
            return job_check["response"]

        try:
            date_from, date_to, window_log = _resolve_content_source_periodic_sync_window(
                from_date=from_date,
                to_date=to_date,
                date_from_iso=date_from_iso,
                date_to_iso=date_to_iso,
                lookback_hours=lookback_hours,
            )
        except ValueError as ve:
            return func.HttpResponse(
                json.dumps({"error": str(ve)}),
                status_code=400,
                mimetype="application/json",
            )

        logging.info(
            "Starting Content Source Periodic Sync via HTTP: collection_ids=%s, lookback_hours=%d, window=%s",
            collection_ids,
            lookback_hours,
            window_log,
        )

        input_data = {
            "collection_ids": collection_ids,
            "batch_size": batch_size,
            "parallel_batches": parallel_batches,
            "date_from": date_from,
            "date_to": date_to,
            "run_all_stages": run_all_stages,
        }

        instance_id = await starter.start_new(
            "ContentSourcePeriodicSyncOrchestrator", None, input_data
        )
        logging.info(
            "Started Content Source Periodic Sync orchestration with ID = '%s'",
            instance_id,
        )

        # Get management URLs for the orchestration instance
        mgmt_payload = get_orchestration_management_urls(starter, instance_id, req)

        # Save initial pipeline job status with management URLs
        try:
            saved = save_pipeline_job(
                trigger_endpoint="content-source-periodic-sync",
                instance_id=instance_id,
                name="Content Source Periodic Sync",
                runtime_status="Pending",
                status_url=mgmt_payload.get("statusQueryGetUri"),
                terminate_url=mgmt_payload.get("terminatePostUri"),
                suspend_url=mgmt_payload.get("suspendPostUri"),
                resume_url=mgmt_payload.get("resumePostUri"),
                input_data=input_data,
            )
            if not saved:
                logging.error("Failed to save pipeline job for content-source-periodic-sync")
        except Exception as save_error:
            logging.exception("Exception saving pipeline job for content-source-periodic-sync: %s", save_error)

        return starter.create_check_status_response(req, instance_id)

    except Exception as e:
        logging.exception("Error starting Content Source Periodic Sync: %s", e)
        return func.HttpResponse(
            json.dumps({"error": f"Failed to start orchestration: {str(e)}"}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# DIGITAL ITEMS OCR — HTTP Trigger (Batch Mode)
# Called by the Archivist API to submit a batch OCR job for public digital resources.
# Returns immediately with a batch job ID; results are processed async by DigitalItemsOcrPoller.
# OCR batch submission logic lives in ocr_digital_items.py (co-deployed with this function app).
# Result processing happens via DigitalItemsOcrPoller (timer-triggered, runs every 2 minutes).
# =============================================================================


def _execute_digital_items_ingest(source: str) -> dict:
    """Execute Moore digital-items ingest via subprocess and return a normalized result payload."""
    valid_sources = {"moore"}
    if source not in valid_sources:
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "returncode": 2,
            "message": (
                f"Invalid source '{source}' for subprocess ingestion. "
                f"Must be one of: {', '.join(sorted(valid_sources))}"
            ),
            "stdout_tail": [],
            "stderr_tail": [],
        }

    script_path = os.path.join(os.path.dirname(__file__), "ingest_digital_resources.py")
    cmd = [sys.executable, script_path, "--source", source, "--internalize-assets"]

    logging.info(
        "_execute_digital_items_ingest: checking prerequisites for source=%s (COSMOS_ENDPOINT=%s, COSMOS_DATABASE_NAME=%s)",
        source, os.getenv("COSMOS_ENDPOINT", "<not-set>"), os.getenv("COSMOS_DATABASE_NAME", "<not-set>")
    )

    try:
        logging.info("_execute_digital_items_ingest: running command: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=os.path.dirname(script_path),
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        logging.error("_execute_digital_items_ingest: timeout (60min) for source=%s", source)
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "returncode": 124,
            "message": "Digital items ingestion timed out after 60 minutes",
            "stdout_tail": [],
            "stderr_tail": ["TIMEOUT: Subprocess did not complete within 3600 seconds"],
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.exception("_execute_digital_items_ingest: failed to execute subprocess for source='%s'", source)
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "returncode": 1,
            "message": f"Failed to execute ingestion: {exc}",
            "stdout_tail": [],
            "stderr_tail": [str(exc)],
        }

    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()

    processed = 0
    failed = 0
    partial_success = False

    for line in reversed(output.splitlines()):
        lower = line.lower()
        if "upserted" in lower and "/" in line:
            # Parse lines like "Upserted 42 / 50 items"
            tokens = line.replace("/", " ").replace(",", " ").split()
            for i, token in enumerate(tokens):
                if token.isdigit() and i > 0:
                    try:
                        processed = int(token)
                        if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                            total = int(tokens[i + 1])
                            if processed < total:
                                partial_success = True
                                failed = total - processed
                        break
                    except (ValueError, IndexError):
                        pass
            if processed > 0:
                break

    if result.returncode != 0:
        logging.warning(
            "_execute_digital_items_ingest: subprocess exited with code %d for source=%s. "
            "Last 10 lines of output:\n%s\n\nLast 10 lines of stderr:\n%s",
            result.returncode, source,
            "\n".join(output.splitlines()[-10:] if output else ["(no stdout)"]),
            "\n".join(err.splitlines()[-10:] if err else ["(no stderr)"])
        )
    else:
        logging.info(
            "_execute_digital_items_ingest: completed successfully for source=%s (processed=%d%s)",
            source, processed, f", partial={partial_success}, failed={failed}" if partial_success else ""
        )

    return {
        "success": result.returncode == 0,
        "source": source,
        "processed": processed,
        "failed": failed if partial_success else 0,
        "returncode": result.returncode,
        "message": (
            "Ingestion completed"
            if result.returncode == 0
            else f"Ingestion failed (exit code {result.returncode})"
        ),
        "stdout_tail": output.splitlines()[-20:] if output else [],
        "stderr_tail": err.splitlines()[-20:] if err else [],
    }


def _execute_digital_items_ingest_inprocess(source: str) -> dict:
    """Execute cyclopedia/genealogy ingest in-process (no subprocess) and return normalized payload."""
    valid_sources = {"cyclopedia", "genealogy"}
    if source not in valid_sources:
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "failed": 0,
            "returncode": 2,
            "message": (
                f"Invalid source '{source}' for in-process ingestion. "
                f"Must be one of: {', '.join(sorted(valid_sources))}"
            ),
            "stdout_tail": [],
            "stderr_tail": [],
        }

    try:
        from ingest_digital_resources import (
            _load_local_settings,
            build_cyclopedia_items,
            build_genealogy_items,
            AssetInternalizer,
            internalize_item_assets,
        )
        from helper.digital_items_client import upsert_digital_items_batch

        _load_local_settings()

        builder = build_cyclopedia_items if source == "cyclopedia" else build_genealogy_items
        items = builder()
        total = len(items)

        internalized = 0
        storage_account = (os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
        storage_container = (
            os.getenv("DIGITAL_ITEMS_STORAGE_CONTAINER")
            or os.getenv("AZURE_STORAGE_CONTAINER_NAME")
            or "digital-resources"
        )
        blob_prefix = os.getenv("DIGITAL_ITEMS_ASSET_BLOB_PREFIX", "digital-items")

        if items and storage_account:
            internalizer = AssetInternalizer(
                storage_account=storage_account,
                storage_container=storage_container,
                blob_prefix=blob_prefix,
            )
            for item in items:
                if internalize_item_assets(item, internalizer):
                    internalized += 1
        elif items:
            logging.warning(
                "In-process ingest(%s): AZURE_STORAGE_ACCOUNT_NAME not set; skipping asset internalization",
                source,
            )

        processed = upsert_digital_items_batch(items) if items else 0
        failed = max(0, total - processed)
        success = failed == 0

        return {
            "success": success,
            "source": source,
            "processed": processed,
            "failed": failed,
            "returncode": 0 if success else 1,
            "message": (
                f"Ingestion completed for {source} ({processed}/{total}, internalized={internalized})"
                if success
                else f"Ingestion completed with failures for {source} ({processed}/{total})"
            ),
            "stdout_tail": [
                f"built_items={total}",
                f"internalized_items={internalized}",
                f"processed_items={processed}",
                f"failed_items={failed}",
            ],
            "stderr_tail": [],
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.exception("_execute_digital_items_ingest_inprocess failed for source=%s", source)
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "failed": 0,
            "returncode": 1,
            "message": f"In-process ingestion failed: {exc}",
            "stdout_tail": [],
            "stderr_tail": [str(exc)],
        }



@app.function_name(name="RunDigitalItemsIngestActivity")
@app.activity_trigger("payload")
def run_digital_items_ingest_activity(payload: dict) -> dict:
    """Durable activity wrapper for long-running digital-items ingestion."""
    source = str((payload or {}).get("source", "")).lower().strip()
    logging.info("RunDigitalItemsIngestActivity: starting ingestion for source=%s", source)
    try:
        result = _execute_digital_items_ingest(source)
        if result.get("success"):
            logging.info(
                "RunDigitalItemsIngestActivity: completed successfully for source=%s (processed=%d)",
                source, result.get("processed", 0)
            )
        else:
            logging.error(
                "RunDigitalItemsIngestActivity: failed for source=%s. Message: %s",
                source, result.get("message", "unknown error")
            )
        return result
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("RunDigitalItemsIngestActivity: exception for source=%s", source)
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "returncode": 1,
            "message": f"Activity execution failed: {e}",
            "stdout_tail": [],
            "stderr_tail": [str(e)],
        }


@app.function_name(name="RunCyclopediaDigitalItemsIngestActivity")
@app.activity_trigger("payload")
def run_cyclopedia_digital_items_ingest_activity(payload: dict) -> dict:  # pylint: disable=unused-argument
    """Durable activity for Cyclopedia ingest (no subprocess)."""
    source = "cyclopedia"
    logging.info("RunCyclopediaDigitalItemsIngestActivity: starting")
    result = _execute_digital_items_ingest_inprocess(source)
    logging.info(
        "RunCyclopediaDigitalItemsIngestActivity: completed success=%s processed=%s",
        result.get("success"),
        result.get("processed", 0),
    )
    return result


@app.function_name(name="RunGenealogyDigitalItemsIngestActivity")
@app.activity_trigger("payload")
def run_genealogy_digital_items_ingest_activity(payload: dict) -> dict:  # pylint: disable=unused-argument
    """Durable activity for Genealogy ingest (no subprocess)."""
    source = "genealogy"
    logging.info("RunGenealogyDigitalItemsIngestActivity: starting")
    result = _execute_digital_items_ingest_inprocess(source)
    logging.info(
        "RunGenealogyDigitalItemsIngestActivity: completed success=%s processed=%s",
        result.get("success"),
        result.get("processed", 0),
    )
    return result


@app.function_name(name="DigitalItemsIngestOrchestrator")
@app.orchestration_trigger("context")
def digital_items_ingest_orchestrator(context):
    """Durable orchestrator for digital-items ingestion."""
    payload = context.get_input() or {}
    source = str(payload.get("source", "")).lower().strip()
    logging.info("DigitalItemsIngestOrchestrator: orchestrating ingestion for source=%s", source)
    try:
        result = yield context.call_activity("RunDigitalItemsIngestActivity", {"source": source})
        logging.info("DigitalItemsIngestOrchestrator: activity completed for source=%s, success=%s", source, result.get("success"))
        return result
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("DigitalItemsIngestOrchestrator: activity failed for source=%s: %s", source, str(e))
        return {
            "success": False,
            "source": source,
            "processed": 0,
            "returncode": 1,
            "message": f"Orchestration failed: {e}",
            "stdout_tail": [],
            "stderr_tail": [str(e)],
        }


@app.function_name(name="CyclopediaDigitalItemsIngestOrchestrator")
@app.orchestration_trigger("context")
def cyclopedia_digital_items_ingest_orchestrator(context):
    """Durable orchestrator for Cyclopedia ingestion (no subprocess)."""
    try:
        return (yield context.call_activity("RunCyclopediaDigitalItemsIngestActivity", {}))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("CyclopediaDigitalItemsIngestOrchestrator failed: %s", str(e))
        return {
            "success": False,
            "source": "cyclopedia",
            "processed": 0,
            "returncode": 1,
            "message": f"Orchestration failed: {e}",
            "stdout_tail": [],
            "stderr_tail": [str(e)],
        }


@app.function_name(name="GenealogyDigitalItemsIngestOrchestrator")
@app.orchestration_trigger("context")
def genealogy_digital_items_ingest_orchestrator(context):
    """Durable orchestrator for Genealogy ingestion (no subprocess)."""
    try:
        return (yield context.call_activity("RunGenealogyDigitalItemsIngestActivity", {}))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("GenealogyDigitalItemsIngestOrchestrator failed: %s", str(e))
        return {
            "success": False,
            "source": "genealogy",
            "processed": 0,
            "returncode": 1,
            "message": f"Orchestration failed: {e}",
            "stdout_tail": [],
            "stderr_tail": [str(e)],
        }


@app.function_name(name="DigitalItemsIngestAllOrchestrator")
@app.orchestration_trigger("context")
def digital_items_ingest_all_orchestrator(context):
    """Durable orchestrator that ingests all sources using source-specific activities."""
    results = []
    results.append((yield context.call_activity("RunDigitalItemsIngestActivity", {"source": "moore"})))
    results.append((yield context.call_activity("RunCyclopediaDigitalItemsIngestActivity", {})))
    results.append((yield context.call_activity("RunGenealogyDigitalItemsIngestActivity", {})))

    processed = sum(int(r.get("processed", 0)) for r in results if isinstance(r, dict))
    failed = sum(int(r.get("failed", 0)) for r in results if isinstance(r, dict))
    success = all(bool(r.get("success")) for r in results if isinstance(r, dict))
    return {
        "success": success,
        "source": "all",
        "processed": processed,
        "failed": failed,
        "returncode": 0 if success else 1,
        "message": "Ingestion completed for all sources" if success else "Ingestion completed with failures for all sources",
        "results": results,
        "stdout_tail": [],
        "stderr_tail": [],
    }


@app.function_name(name="DigitalItemsIngestClient")
@app.route(
    route="digital-items/ingest/{source}",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST"],
)
@app.durable_client_input(client_name="starter")
async def digital_items_ingest(
    req: func.HttpRequest,
    starter: df.DurableOrchestrationClient,
) -> func.HttpResponse:
    """Start digital-items ingestion as a durable workflow and return management URLs."""
    source = req.route_params.get("source", "").lower().strip()
    valid_sources = {"moore", "cyclopedia", "genealogy", "all"}
    if source not in valid_sources:
        logging.warning("DigitalItemsIngestClient: invalid source requested: %s", source)
        return func.HttpResponse(
            json.dumps({
                "error": (
                    f"Invalid source '{source}'. "
                    f"Must be one of: {', '.join(sorted(valid_sources))}"
                )
            }),
            status_code=400,
            mimetype="application/json",
        )

    try:
        orchestrator_name = {
            "moore": "DigitalItemsIngestOrchestrator",
            "cyclopedia": "CyclopediaDigitalItemsIngestOrchestrator",
            "genealogy": "GenealogyDigitalItemsIngestOrchestrator",
            "all": "DigitalItemsIngestAllOrchestrator",
        }[source]

        instance_id = await starter.start_new(
            orchestrator_name,
            None,
            {"source": source},
        )
        logging.info(
            "DigitalItemsIngestClient: started orchestration=%s instanceId=%s (source=%s)",
            orchestrator_name,
            instance_id,
            source,
        )
        return starter.create_check_status_response(req, instance_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("DigitalItemsIngestClient: failed to start orchestration for source=%s", source)
        return func.HttpResponse(
            json.dumps({
                "error": f"Failed to start ingestion orchestration: {e}",
                "source": source,
            }),
            status_code=500,
            mimetype="application/json",
        )

@app.function_name(name="DigitalItemsOcr")
@app.route(
    route="digital-items/ocr/{source}",
    auth_level=HTTP_AUTH_LEVEL,
    methods=["POST"],
)
def digital_items_ocr(req: func.HttpRequest) -> func.HttpResponse:
    """
    Submit a batch OCR job on digital items for a given public-resource source.

    Uses the model configured via AZURE_OPENAI_BATCH_DEPLOYMENT_NAME (batch API).
    Called by the Archivist API to submit OCR processing.
    Returns immediately with batch job ID (HTTP 202 Accepted).

    Path Parameters:
        source: One of ``moore`` or ``all``.

    Query Parameters:
        dry_run: ``true`` to query items and report counts without submitting batch
                 (default: ``false``).

    Returns:
        202 JSON  { batch_id, sources, dry_run } — job submitted successfully
        400 JSON  { error }  for unknown source
        500 JSON  { error }  on unexpected failure
    """
    source = req.route_params.get("source", "").lower().strip()
    dry_run = req.params.get("dry_run", "").lower() in ("true", "1")

    valid_sources = {"moore", "all"}
    if source not in valid_sources:
        return func.HttpResponse(
            json.dumps({
                "error": (
                    f"Invalid source '{source}'. "
                    f"Must be one of: {', '.join(sorted(valid_sources))}"
                )
            }),
            status_code=400,
            mimetype="application/json",
        )

    try:
        from ocr_digital_items import run_ocr

        # TR Cyclopedia and Genealogy are ingested without OCR by design.
        sources_to_run = ["moore"] if source == "all" else [source]
        batch_ids = {}

        for src in sources_to_run:
            logging.info("DigitalItemsOcr: submitting batch job for source '%s' (dry_run=%s)", src, dry_run)
            try:
                batch_id = run_ocr(src, dry_run=dry_run)
                if batch_id:
                    batch_ids[src] = batch_id
                    logging.info("DigitalItemsOcr: batch submitted for source '%s', batch_id=%s", src, batch_id)
                else:
                    batch_ids[src] = "no_items" if not dry_run else "dry_run"
                    logging.info("DigitalItemsOcr: no items to process for source '%s'", src)
            except Exception as src_exc:
                logging.exception("DigitalItemsOcr: batch submission failed for source '%s': %s", src, src_exc)
                batch_ids[src] = f"error: {str(src_exc)}"

        return func.HttpResponse(
            json.dumps({
                "batch_ids": batch_ids,
                "sources": sources_to_run,
                "dry_run": dry_run,
            }),
            status_code=202,
            mimetype="application/json",
        )

    except ImportError as imp_exc:
        logging.exception("DigitalItemsOcr: failed to import ocr_digital_items: %s", imp_exc)
        return func.HttpResponse(
            json.dumps({"error": f"OCR module unavailable: {str(imp_exc)}"}),
            status_code=500,
            mimetype="application/json",
        )
    except Exception as exc:
        logging.exception("DigitalItemsOcr: unexpected failure for source '%s': %s", source, exc)
        return func.HttpResponse(
            json.dumps({"error": f"Batch submission failed: {str(exc)}"}),
            status_code=500,
            mimetype="application/json",
        )


@app.function_name(name="DigitalItemsOcrPoller")
@app.timer_trigger(arg_name="timer", schedule="0 */2 * * * *")  # Every 2 minutes
def digital_items_ocr_poller(timer: func.TimerRequest) -> None:
    """
    Poll Azure OpenAI batch jobs for Digital Items OCR and process results.

    Runs on a 2-minute schedule. Monitors batch status, processes completed jobs,
    uploads OCR results to blob storage, and updates Cosmos DB with results.

    This timer-triggered function handles all async result processing for batch jobs
    submitted by DigitalItemsOcr HTTP endpoint.
    """
    import json as json_module
    from helper.azure_openai_batch_client import AzureOpenAIBatchConfig
    from helper.cosmos_client import get_container as get_cosmos_container

    if timer.past_due:
        logging.info("DigitalItemsOcrPoller: timer is past due, skipping")
        return

    try:
        batch_config = AzureOpenAIBatchConfig.from_env()
        batch_client = AzureOpenAIBatchClient(batch_config)

        # Get batch status container
        batch_container = get_cosmos_container("batchstatus")

        # Query for pending digital items OCR batches
        query = (
            "SELECT * FROM c WHERE c.batch_type = 'digital-items-ocr' "
            "AND c.status IN ('submitted', 'in_progress')"
        )
        pending_batches = list(batch_container.query_items(query=query))

        if not pending_batches:
            logging.info("DigitalItemsOcrPoller: no pending batches")
            return

        logging.info("DigitalItemsOcrPoller: found %d pending batches", len(pending_batches))

        for batch_doc in pending_batches:
            batch_id = batch_doc["batch_id"]
            source_key = batch_doc["source"]
            metadata = batch_doc.get("metadata", {})
            cosmos_source = SOURCE_MAP.get(source_key)

            try:
                # Check batch status
                batch_status = batch_client.get_batch_status(batch_id)
                logging.info("DigitalItemsOcrPoller: batch %s status = %s", batch_id, batch_status)

                if batch_status == "completed":
                    logging.info("DigitalItemsOcrPoller: processing completed batch %s", batch_id)
                    _process_digital_items_ocr_batch(batch_client, batch_id, metadata, cosmos_source)

                    # Mark batch as processed
                    batch_doc["status"] = "completed"
                    batch_doc["completed_at"] = datetime.now(timezone.utc).isoformat()
                    batch_container.upsert_item(batch_doc)
                    logging.info("DigitalItemsOcrPoller: batch %s marked as completed", batch_id)

                elif batch_status == "failed":
                    logging.error("DigitalItemsOcrPoller: batch %s failed", batch_id)
                    batch_doc["status"] = "failed"
                    batch_doc["failed_at"] = datetime.now(timezone.utc).isoformat()
                    batch_container.upsert_item(batch_doc)

                elif batch_status in ("in_progress", "validating"):
                    logging.info("DigitalItemsOcrPoller: batch %s still %s, will check again later", batch_id, batch_status)

            except Exception as batch_exc:
                logging.exception("DigitalItemsOcrPoller: error processing batch %s: %s", batch_id, batch_exc)

    except Exception as exc:
        logging.exception("DigitalItemsOcrPoller: unexpected error: %s", exc)


def _process_digital_items_ocr_batch(batch_client, batch_id: str, _metadata: dict, cosmos_source: str) -> None:
    """Process completed batch job results and update Cosmos/blob storage."""
    import json as json_module
    from helper.cosmos_client import get_container as get_cosmos_container
    from helper.blob_utils import upload_text_to_blob

    try:
        # Get batch results
        results_file = batch_client.get_batch_results(batch_id)
        logging.info("_process_digital_items_ocr_batch: retrieved results for batch %s", batch_id)

        # Parse JSONL results
        item_results = {}  # item_id -> list of page results
        failed_items = {}

        with open(results_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                result = json_module.loads(line)
                custom_id = result.get("custom_id", "")

                # custom_id format: "{item_id}::page-{page_num}"
                if "::" not in custom_id:
                    logging.warning("_process_digital_items_ocr_batch: unexpected custom_id format: %s", custom_id)
                    continue

                item_id, page_spec = custom_id.split("::", 1)
                page_num = int(page_spec.split("-")[-1]) if "page-" in page_spec else 0

                if item_id not in item_results:
                    item_results[item_id] = []

                # Parse the response from batch result
                if "response" in result and "body" in result["response"]:
                    body = result["response"]["body"]
                    if "choices" in body and body["choices"]:
                        choice = body["choices"][0]
                        content = choice.get("message", {}).get("content", "")

                        # Try to parse JSON from content
                        try:
                            parsed = json_module.loads(content)
                            ocr_text = parsed.get("ocr_text", content)
                            confidence = parsed.get("confidence_scores", {}).get("overall_ocr_confidence", 0.0)
                        except json_module.JSONDecodeError:
                            ocr_text = content
                            confidence = 0.0

                        item_results[item_id].append({
                            "page_number": page_num,
                            "ocr_text": ocr_text,
                            "confidence": confidence,
                        })
                elif "error" in result:
                    logging.error("_process_digital_items_ocr_batch: batch result error for %s: %s",
                                  custom_id, result["error"])
                    if item_id not in failed_items:
                        failed_items[item_id] = []
                    failed_items[item_id].append(page_num)

        # Update Cosmos DB and upload blobs for each item
        digital_items_container = get_cosmos_container("digital-items")
        blob_container = os.getenv("DIGITAL_ITEMS_STORAGE_CONTAINER") or os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")

        if not blob_container or not storage_account_name:
            logging.error("_process_digital_items_ocr_batch: missing storage config")
            return

        for item_id, page_results in item_results.items():
            # An item with no successful page text (all pages errored or came
            # back empty) must NOT be written as a "completed" item with empty
            # OCR — mark it failed below instead.
            if not any(r.get("ocr_text", "").strip() for r in page_results):
                failed_items.setdefault(item_id, [])
                continue
            try:
                # Read current item
                item_doc = digital_items_container.read_item(item=item_id, partition_key=cosmos_source)

                # Combine OCR text
                combined_text = "\n\n--- Page Break ---\n\n".join(
                    r["ocr_text"] for r in sorted(page_results, key=lambda x: x["page_number"])
                    if r.get("ocr_text")
                )

                # Calculate accuracy
                confidences = [r["confidence"] for r in page_results if r["confidence"] > 0]
                ocr_accuracy = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

                # Upload to blob
                blob_original = f"{item_id}/ocr/original/{item_id}.txt"
                blob_flexible = f"{item_id}/ocr/flexible/{item_id}/v1.txt"

                upload_text_to_blob(
                    container_name=blob_container,
                    blob=blob_original,
                    content=combined_text,
                )
                upload_text_to_blob(
                    container_name=blob_container,
                    blob=blob_flexible,
                    content=combined_text,
                )

                # Generate URLs
                ocr_original_url = build_blob_url(blob_container, blob_original)
                ocr_flexible_url = build_blob_url(blob_container, blob_flexible)

                # Update Cosmos
                item_doc["ocr_text_original_blob_url"] = ocr_original_url
                item_doc["ocr_text_flexible_blob_url"] = ocr_flexible_url
                item_doc["ocr_version"] = 1
                item_doc["ocr_accuracy"] = ocr_accuracy
                item_doc["ocr_page_count"] = len(page_results)
                item_doc["ocr_status"] = "completed"
                item_doc["publish_status"] = "pending"
                digital_items_container.upsert_item(item_doc)

                logging.info("_process_digital_items_ocr_batch: ✓ completed %s (accuracy=%.3f, pages=%d)",
                             item_id, ocr_accuracy, len(page_results))

            except Exception as item_exc:
                logging.exception("_process_digital_items_ocr_batch: failed to update %s: %s", item_id, item_exc)

        # Mark items that produced no usable OCR text as failed. Without this,
        # downstream progress tracking (the Archivist ingestion job, which counts
        # ocr_status in ('completed','failed') against the eligible total) would
        # never reach a terminal state and the job would hang in "processing".
        for item_id in failed_items:
            if item_id in item_results and any(
                r.get("ocr_text", "").strip() for r in item_results[item_id]
            ):
                continue  # actually succeeded above; do not override
            try:
                item_doc = digital_items_container.read_item(item=item_id, partition_key=cosmos_source)
                item_doc["ocr_status"] = "failed"
                item_doc["ocr_error"] = "OCR batch returned no usable text for this item."
                digital_items_container.upsert_item(item_doc)
                logging.warning("_process_digital_items_ocr_batch: marked %s as failed", item_id)
            except Exception as item_exc:
                logging.exception(
                    "_process_digital_items_ocr_batch: could not mark %s as failed: %s",
                    item_id, item_exc,
                )

    except Exception as exc:
        logging.exception("_process_digital_items_ocr_batch: failed to process batch %s: %s", batch_id, exc)


@app.function_name(name="ContentSourcePeriodicSyncOrchestrator")
@app.orchestration_trigger("context")
def content_source_periodic_sync_orchestrator(context):
    """
    Orchestrator that runs the complete Content Source sync pipeline sequentially.

    Pipeline stages:
    1. ContentSourceSyncOrchestrator - Query and store typed adapter records
    2. FetchRelatedAssetsOrchestrator - Fetch related asset IDs for records
    3. ProcessAssetDetailsOrchestrator - Fetch asset details (metadata, thumbnails)
    4. ProcessOriginalFilesOrchestrator - Download and upload original files
    5. CreateOCRBatchOrchestrator - Create OCR batch jobs
    6. ExtractMetadataOrchestrator - Extract metadata from OCR text

    Each stage processes all pending records before moving to the next stage.
    Uses sub-orchestration to track progress and handle failures gracefully.
    """
    input_data = context.get_input()
    collection_ids = input_data.get("collection_ids", [])
    batch_size = input_data.get("batch_size", 100)
    parallel_batches = input_data.get("parallel_batches", 20)
    current_stage = input_data.get("current_stage", 0)
    run_all_stages = input_data.get("run_all_stages", True)
    stage_results = input_data.get("stage_results", {})

    # Define pipeline stages
    stages = [
        {"name": "ContentSourceSync", "orchestrator": "ContentSourceSyncOrchestrator"},
        {"name": "FetchRelatedAssets", "orchestrator": "FetchRelatedAssetsOrchestrator"},
        {"name": "ProcessAssetDetails", "orchestrator": "ProcessAssetDetailsOrchestrator"},
        {"name": "ProcessOriginalFiles", "orchestrator": "ProcessOriginalFilesOrchestrator"},
        {"name": "CreateOCRBatch", "orchestrator": "CreateOCRBatchOrchestrator"},
        {"name": "CreateMetadataBatch", "orchestrator": "CreateMetadataBatchOrchestrator"},
    ]

    orchestrator_log(
        context,
        "info",
        "ContentSourcePeriodicSyncOrchestrator stage %d/%d (collection_ids=%s, run_all_stages=%s)",
        current_stage + 1,
        len(stages),
        collection_ids,
        run_all_stages,
    )

    # Check if we've completed all stages
    if current_stage >= len(stages):
        completed_result = {
            "status": "completed",
            "stages_completed": len(stages),
            "stage_results": stage_results,
            "message": "Content Source Periodic Sync pipeline completed successfully",
        }
        orchestrator_log(
            context,
            "info",
            "Content Source Periodic Sync completed all stages. Results: %s",
            stage_results,
        )
        yield context.call_activity(
            "MergePeriodicRunHistoryCompletion",
            {
                "instance_id": context.instance_id,
                "runtime_status": "Completed",
                "output": completed_result,
                "last_updated_time": context.current_utc_datetime.isoformat(),
            },
        )
        return completed_result

    # Get current stage info
    stage = stages[current_stage]
    stage_name = stage["name"]
    orchestrator_name = stage["orchestrator"]

    orchestrator_log(
        context,
        "info",
        "Starting stage %d: %s (orchestrator: %s)",
        current_stage + 1,
        stage_name,
        orchestrator_name,
    )

    # Preserve the source update window across stages.
    date_from = input_data.get("date_from")
    date_to = input_data.get("date_to")

    try:
        # Build input for the sub-orchestrator
        if stage_name == "ContentSourceSync":
            if date_from and date_to:
                orchestrator_log(
                    context,
                    "info",
                    "ContentSourceSync stage: UTC window %s -> %s",
                    date_from,
                    date_to,
                )
            sub_input = {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "parallel_batches": parallel_batches,
            }
            if date_from and date_to:
                sub_input["date_from"] = date_from
                sub_input["date_to"] = date_to
        else:
            # Other stages use standard input
            sub_input = {
                "collection_ids": collection_ids,
                "batch_size": batch_size,
                "skip": 0,
                "total": 0,
                "total_processed_so_far": 0,
                "total_failed_so_far": 0,
            }

        # Call sub-orchestrator and wait for completion
        sub_instance_id = f"{context.instance_id}-{stage_name.lower()}"
        result = yield context.call_sub_orchestrator(
            orchestrator_name,
            sub_input,
            sub_instance_id,
        )

        stage_results[stage_name] = {
            "status": result.get("status", "unknown"),
            "total_processed_so_far": result.get("total_processed_so_far", 0),
            "total_failed_so_far": result.get("total_failed_so_far", 0),
        }
        if result.get("error") is not None:
            stage_results[stage_name]["error"] = result["error"]

        if result.get("status") == "error":
            pipeline_error = {
                "status": "error",
                "failed_stage": stage_name,
                "error": result.get("error"),
                "stage_results": stage_results,
            }
            yield context.call_activity(
                "MergePeriodicRunHistoryCompletion",
                {
                    "instance_id": context.instance_id,
                    "runtime_status": "Failed",
                    "output": pipeline_error,
                    "last_updated_time": context.current_utc_datetime.isoformat(),
                },
            )
            return pipeline_error

        orchestrator_log(
            context,
            "info",
            "Stage %d (%s) completed: total_processed_so_far=%d, total_failed_so_far=%d",
            current_stage + 1,
            stage_name,
            result.get("total_processed_so_far", 0),
            result.get("total_failed_so_far", 0),
        )

    except Exception as e:
        orchestrator_log(
            context,
            "error",
            "Stage %d (%s) failed with error: %s",
            current_stage + 1,
            stage_name,
            str(e),
        )
        error = {
            "code": "stage_failure",
            "message": str(e),
            "retryable": False,
            "details": {"stage": stage_name},
        }
        stage_results[stage_name] = {"status": "error", "error": error}
        pipeline_error = {
            "status": "error",
            "failed_stage": stage_name,
            "error": error,
            "stage_results": stage_results,
        }
        yield context.call_activity(
            "MergePeriodicRunHistoryCompletion",
            {
                "instance_id": context.instance_id,
                "runtime_status": "Failed",
                "output": pipeline_error,
                "last_updated_time": context.current_utc_datetime.isoformat(),
            },
        )
        return pipeline_error

    # Check if we should continue to next stages
    if not run_all_stages:
        partial_result = {
            "status": "completed",
            "stages_completed": current_stage + 1,
            "stage_results": stage_results,
            "message": f"Completed {current_stage + 1} stage(s)",
        }
        orchestrator_log(
            context,
            "info",
            "Stopping after stage %d (run_all_stages=False)",
            current_stage + 1,
        )
        yield context.call_activity(
            "MergePeriodicRunHistoryCompletion",
            {
                "instance_id": context.instance_id,
                "runtime_status": "Completed",
                "output": partial_result,
                "last_updated_time": context.current_utc_datetime.isoformat(),
            },
        )
        return partial_result

    # Continue to the next stage while preserving the source update window.
    next_input = {
        "collection_ids": collection_ids,
        "batch_size": batch_size,
        "parallel_batches": parallel_batches,
        "current_stage": current_stage + 1,
        "run_all_stages": run_all_stages,
        "stage_results": stage_results,
    }
    if date_from and date_to:
        next_input["date_from"] = date_from
        next_input["date_to"] = date_to
    return context.continue_as_new(next_input)
