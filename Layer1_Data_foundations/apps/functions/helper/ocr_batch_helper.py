# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Helper functions for creating OCR batch jobs with Azure OpenAI.

This module provides functionality to create batch jobs for OCR processing
of content source records using Azure OpenAI batch API.
"""

import os
import json
import logging
import re
import tempfile
import time
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from azure.cosmos.exceptions import CosmosHttpResponseError

from .files_util import convert_file_url_to_jpeg_data_urls, compress_image_to_target, FileConversionError
from .azure_openai_batch_client import AzureOpenAIBatchClient
from .prompt_loader import PromptLoader, VISUAL_RESOURCE_TYPES
from .config import (
    AzureConfig,
    CosmosDBConfig,
    AzureStorageConfig,
    get_chat_completion_parameters,
    get_cosmosdb_config,
    get_storage_config,
)
from .cosmos_client import CosmosDBClient
from .blob_utils import ensure_blob_accessible, get_user_delegation_sas, upload_text_to_blob
from .storage_client import build_blob_url
from .content_source_client import normalize_archivist_status, upsert_with_retry
from .batch_utils import parse_record_id_from_custom_id

logger = logging.getLogger(__name__)


# =============================================================================
# BATCH TYPE CONFIGURATION
# =============================================================================

class BatchType:
    """Constants for batch types with their configuration."""
    OCR = "ocr"
    METADATA = "metadata"
    RESOURCE_TYPE = "resource_type"


# Configuration for each batch type
BATCH_TYPE_CONFIG = {
    BatchType.OCR: {
        "status_field": "ocr_processing_status",
        "error_field": "ocr_processing_status_error",
        "error_message": "Text extraction failed",
        "custom_id_prefix": "ocr_",
    },
    BatchType.METADATA: {
        "status_field": "metadata_extraction_status",
        "error_field": "metadata_extraction_status_error",
        "error_message": "Metadata extraction failed",
        "custom_id_prefix": "metadata_",
    },
    BatchType.RESOURCE_TYPE: {
        "status_field": "resource_type_processing_status",
        "error_field": "resource_type_processing_status_error",
        "error_message": "Resource type extraction failed",
        "custom_id_prefix": "resource_type_",
    },
}


# =============================================================================
# SHARED HELPER FUNCTIONS
# =============================================================================

# Azure OpenAI image size limits (configurable via environment variables)
# MAX_IMAGE_SIZE_MB: Per-image limit (Azure's limit is 20MB, default 15MB for safety)
# MAX_TOTAL_IMAGE_SIZE_MB: Total images per record (Azure's limit is 50MB, default 45MB for safety)
MAX_IMAGE_SIZE_MB = float(os.getenv("MAX_IMAGE_SIZE_MB", "15"))
MAX_IMAGE_SIZE_BYTES = int(MAX_IMAGE_SIZE_MB * 1024 * 1024)
MAX_TOTAL_IMAGE_SIZE_MB = float(os.getenv("MAX_TOTAL_IMAGE_SIZE_MB", "45"))
MAX_TOTAL_SIZE_BYTES = int(MAX_TOTAL_IMAGE_SIZE_MB * 1024 * 1024)


def get_data_url_size(data_url: str) -> int:
    """
    Calculate the size of a data URL as it will be sent to Azure OpenAI.

    Azure OpenAI measures the actual base64-encoded string size in the JSON request,
    not the decoded binary size. This function returns the full data URL string length.

    Args:
        data_url: A data URL (e.g., "data:image/jpeg;base64,...")

    Returns:
        Size in bytes (length of the full data URL string)
    """
    if data_url.startswith("data:"):
        # Return the full data URL string length (what Azure actually measures)
        return len(data_url)
    return len(data_url)


def get_blob_url_size(url: str, timeout: int = 30) -> int:
    """
    Get the size of a blob/image from its URL using HEAD request.

    Args:
        url: The blob/image URL (with SAS token if needed)
        timeout: Request timeout in seconds

    Returns:
        Size in bytes, or 0 if unable to determine
    """
    import requests
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 200:
            content_length = response.headers.get("Content-Length")
            if content_length:
                return int(content_length)
    except Exception as e:
        logger.debug("Failed to get size for URL %s: %s", url[:80], e)
    return 0


def get_image_size(img_url: str) -> int:
    """
    Get the estimated size of an image as it will be sent to Azure OpenAI.

    For data URLs: returns the full string length (what Azure measures)
    For blob URLs: returns the binary size × 4/3 (estimated base64 overhead if converted)

    Args:
        img_url: Either a data URL or a blob/http URL

    Returns:
        Estimated size in bytes as Azure will measure it
    """
    if img_url.startswith("data:"):
        return get_data_url_size(img_url)
    else:
        # It's a blob/http URL - get size via HEAD request
        size = get_blob_url_size(img_url)
        if size == 0:
            # Fallback: estimate conservatively if HEAD fails
            # Use 15MB to ensure compression is triggered for unknown sizes
            logger.warning("Could not determine size for URL, estimating 15MB to trigger compression")
            return 15 * 1024 * 1024
        # Add base64 overhead estimate (blob URLs get converted to data URLs)
        # Base64 encoding adds ~33% overhead, plus data URL prefix (~30 bytes)
        return int(size * 4 / 3) + 50


def convert_url_to_compressed_data_url(
    url: str,
    target_size_mb: float = 15.0,
    min_quality: int = 40,
    timeout: int = 60
) -> str:
    """
    Download an image URL and convert it to a compressed JPEG data URL.

    Uses compress_image_to_target from files_util for the core compression logic.

    Args:
        url: The image URL to download and convert
        target_size_mb: Target maximum size in MB
        min_quality: Minimum JPEG quality to try (default: 40)
        timeout: Download timeout in seconds

    Returns:
        Compressed JPEG data URL

    Raises:
        ValueError: If compression fails
    """
    import requests
    import io
    import base64
    from PIL import Image

    try:
        # Download the image
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        # Open with PIL
        img = Image.open(io.BytesIO(response.content))

        # Convert to RGB if necessary (handles RGBA, palette modes, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        target_size_bytes = int(target_size_mb * 1024 * 1024)

        # Use shared compression helper from files_util (raises FileConversionError if fails)
        jpeg_bytes = compress_image_to_target(img, target_size_bytes, min_quality)

        base64_data = base64.b64encode(jpeg_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{base64_data}"

    except (ValueError, FileConversionError) as e:
        # Re-raise as ValueError so caller can handle it consistently
        raise ValueError(str(e)) from e
    except Exception as e:
        raise ValueError(f"Failed to convert URL to compressed data URL: {e}") from e


def compress_data_url(
    data_url: str,
    target_size_mb: float,
    min_quality: int = 40
) -> str:
    """
    Compress an existing data URL to fit within target size.

    Uses compress_image_to_target from files_util for the core compression logic.

    Args:
        data_url: Base64 data URL (e.g., "data:image/jpeg;base64,...")
        target_size_mb: Target maximum size in MB
        min_quality: Minimum JPEG quality to try

    Returns:
        Compressed JPEG data URL

    Raises:
        ValueError: If compression fails or data URL is invalid
    """
    import io
    import base64
    from PIL import Image

    try:
        # Extract base64 data from data URL
        if not data_url.startswith("data:"):
            raise ValueError("Invalid data URL format: must start with 'data:'")

        # Find the base64 data after the comma
        comma_idx = data_url.find(",")
        if comma_idx < 0:
            raise ValueError("Invalid data URL format: missing comma separator")

        base64_data = data_url[comma_idx + 1:]

        # Decode base64 to bytes
        image_bytes = base64.b64decode(base64_data)

        # Open with PIL
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB if necessary
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        target_size_bytes = int(target_size_mb * 1024 * 1024)

        # Use shared compression helper from files_util (raises FileConversionError if fails)
        jpeg_bytes = compress_image_to_target(img, target_size_bytes, min_quality)

        result_base64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{result_base64}"

    except (ValueError, FileConversionError) as e:
        # Re-raise as ValueError so caller can handle it consistently
        raise ValueError(str(e)) from e
    except Exception as e:
        raise ValueError(f"Failed to compress data URL: {e}") from e


def filter_images_by_size(
    image_urls: List[str],
    max_images: int = None,
    context_id: str = "unknown",
    context_type: str = "record"
) -> tuple:
    """
    Filter and compress images to comply with Azure OpenAI size limits.

    Used by all batch types (OCR, Metadata, Resource Type) to ensure images
    fit within Azure OpenAI's limits before submission.

    Handles both data URLs and blob URLs:
    - For data URLs: calculates size from string length
    - For blob URLs: gets size via HEAD request + base64 overhead estimate
    - If total size exceeds limit, compresses all images evenly to fit

    Enforces (configurable via environment variables):
    - MAX_IMAGE_SIZE_MB: Per-image limit (default: 15MB, Azure limit: 20MB)
    - MAX_TOTAL_IMAGE_SIZE_MB: Total for all images (default: 45MB, Azure limit: 50MB)

    Args:
        image_urls: List of image URLs (data URLs or blob URLs)
        max_images: Optional maximum number of images to include
        context_id: ID for logging (record_id, asset_id, etc.)
        context_type: Type of context for logging (e.g., "record", "asset")

    Returns:
        Tuple of (filtered_urls, total_size_bytes, error_message)
        - filtered_urls: List of images that fit within limits (may be converted to data URLs)
        - total_size_bytes: Total size of filtered images
        - error_message: Error message if images could not be processed, None if successful
    """
    if not image_urls:
        return [], 0, None

    # Step 1: Limit to max_images if specified
    urls_to_process = image_urls[:max_images] if max_images else image_urls

    # Step 2: Calculate size of each image
    image_sizes = []
    for img_url in urls_to_process:
        size = get_image_size(img_url)
        image_sizes.append((img_url, size))

    total_size = sum(size for _, size in image_sizes)
    num_images = len(image_sizes)

    logger.debug(
        "%s %s: %d images, total size %.2fMB",
        context_type.capitalize(), context_id,
        num_images, total_size / (1024 * 1024)
    )

    # Step 3: Calculate target size per image if total exceeds limit
    target_size_per_image_mb = None
    if total_size > MAX_TOTAL_SIZE_BYTES:
        # Use 80% of the limit divided by number of images for safety buffer
        target_size_per_image_mb = (MAX_TOTAL_IMAGE_SIZE_MB * 0.8) / num_images
        target_size_per_image_mb = min(target_size_per_image_mb, MAX_IMAGE_SIZE_MB * 0.8)
        logger.info(
            "%s %s: Total %.2fMB exceeds %dMB, compressing to %.2fMB per image",
            context_type.capitalize(), context_id,
            total_size / (1024 * 1024), MAX_TOTAL_IMAGE_SIZE_MB, target_size_per_image_mb
        )

    # Step 4: Process each image - compress if needed
    filtered_urls = []
    final_total_size = 0

    for img_url, img_size in image_sizes:
        url_to_use = img_url
        final_size = img_size

        # Determine if this image needs compression
        needs_compression = False
        target_mb = None

        if img_size > MAX_IMAGE_SIZE_BYTES:
            # Image exceeds per-image limit - target the limit itself
            # (binary_target_mb calculation below will account for base64 overhead)
            needs_compression = True
            target_mb = MAX_IMAGE_SIZE_MB
        elif target_size_per_image_mb and img_size > target_size_per_image_mb * 1024 * 1024:
            # Image exceeds calculated target for even distribution
            needs_compression = True
            target_mb = target_size_per_image_mb

        if needs_compression:
            try:
                # Binary target = 75% of desired data URL size (accounts for base64 overhead)
                binary_target_mb = target_mb * 0.75
                if not img_url.startswith("data:"):
                    compressed = convert_url_to_compressed_data_url(img_url, target_size_mb=binary_target_mb)
                else:
                    compressed = compress_data_url(img_url, target_size_mb=binary_target_mb)
                url_to_use = compressed
                final_size = get_data_url_size(compressed)
            except ValueError as e:
                error_msg = f"Image compression failed: {e}"
                logger.error("%s %s: %s", context_type.capitalize(), context_id, error_msg)
                return [], 0, error_msg

        # Safety check: stop if adding this image would exceed the total limit
        if final_total_size + final_size > MAX_TOTAL_SIZE_BYTES:
            error_msg = f"Total image size would exceed {MAX_TOTAL_IMAGE_SIZE_MB}MB limit after compression (current: {final_total_size / (1024 * 1024):.2f}MB, next image: {final_size / (1024 * 1024):.2f}MB)"
            logger.error("%s %s: %s", context_type.capitalize(), context_id, error_msg)
            return [], 0, error_msg

        filtered_urls.append(url_to_use)
        final_total_size += final_size

    # Final validation
    if final_total_size > MAX_TOTAL_SIZE_BYTES:
        error_msg = f"Total size {final_total_size / (1024 * 1024):.2f}MB exceeds {MAX_TOTAL_IMAGE_SIZE_MB}MB limit"
        logger.error("%s %s: %s", context_type.capitalize(), context_id, error_msg)
        return [], 0, error_msg

    logger.debug(
        "%s %s: Filtered to %d images, %.2fMB total",
        context_type.capitalize(), context_id,
        len(filtered_urls), final_total_size / (1024 * 1024)
    )

    return filtered_urls, final_total_size, None


def _mark_records_error_safe(
    record_ids: List[str],
    cosmos_container,
    status_field: str,
    error_message: str,
    error_detail: str,
    batch_id: str = "unknown",
    check_completed_field: str = None,
) -> tuple:
    """
    Mark records as error, safely skipping records that already have error or completed status.
    
    This prevents overwriting specific error messages with generic ones.
    
    Args:
        record_ids: List of record IDs to mark as error
        cosmos_container: Cosmos DB container client for reading current status
        status_field: The status field to update (e.g., "ocr_processing_status")
        error_message: Short error message (e.g., "Batch retrieval failed")
        error_detail: Detailed error message template (batch_id and record_id will be appended)
        batch_id: Batch ID for error detail
        check_completed_field: Optional field to check for completion (e.g., "resource_type")
        
    Returns:
        Tuple of (records_marked, records_skipped)
    """
    from . import content_source_client  # Import here to avoid circular imports

    records_marked = 0
    records_skipped = 0

    for rid in record_ids:
        try:
            # Check if record already has error/completed status - don't overwrite
            try:
                record_data = cosmos_container.read_item(item=rid, partition_key=rid)
                current_status = record_data.get(status_field)

                # Skip if already has error status
                if current_status == "error":
                    logger.debug("Record %s already has error status, skipping", rid)
                    records_skipped += 1
                    continue

                # Skip if completed (with optional completed field check)
                if current_status == "completed":
                    if check_completed_field:
                        if record_data.get(check_completed_field):
                            logger.debug("Record %s already completed with %s, skipping", rid, check_completed_field)
                            records_skipped += 1
                            continue
                    else:
                        logger.debug("Record %s already completed, skipping", rid)
                        records_skipped += 1
                        continue

            except Exception:
                pass  # If we can't check, proceed to mark

            full_error_detail = f"{error_detail} Batch ID: {batch_id}, Record ID: {rid}"
            content_source_client.update_record_status(
                rid, status_field, "error", None,
                error_reason={"message": error_message, "detail": full_error_detail}
            )
            records_marked += 1

        except Exception as e:
            logger.exception("Failed to mark record %s as error: %s", rid, e)

    if records_marked > 0 or records_skipped > 0:
        logger.info(
            "Error marking for batch %s: %d records marked, %d skipped (already error/completed)",
            batch_id, records_marked, records_skipped
        )

    return records_marked, records_skipped


def _build_grouped_error_message(
    failed_records: List[tuple],
    base_message: str,
    max_groups: int = 5
) -> str:
    """
    Build a grouped error message from failed/skipped records.

    Groups records by error reason for concise messaging.

    Args:
        failed_records: List of (record_id, error_message) tuples
        base_message: Base message prefix (e.g., "No valid records with images")
        max_groups: Maximum number of error groups to show (default 5)

    Returns:
        Formatted error message string
    """
    if not failed_records:
        return f"{base_message} (no records provided)"

    # Group records by error reason
    error_groups: Dict[str, List[str]] = {}
    for rid, err in failed_records:
        if err not in error_groups:
            error_groups[err] = []
        error_groups[err].append(rid)

    # Build summary: "Unsupported format .mp4 (5 records); No images found (3 records)"
    group_summaries = []
    for err, rids in list(error_groups.items())[:max_groups]:
        if len(rids) == 1:
            group_summaries.append(f"{err} ({rids[0][:8]}...)")
        else:
            group_summaries.append(f"{err} ({len(rids)} records)")

    if len(error_groups) > max_groups:
        group_summaries.append(f"and {len(error_groups) - max_groups} more error types")

    return f"{base_message}. {len(failed_records)} records failed: {'; '.join(group_summaries)}"


def _mark_prep_failed_records_as_error(
    failed_records: List[tuple],
    status_field: str,
    error_message_prefix: str = "Preparation failed",
    error_detail_prefix: str = "Could not prepare record"
) -> int:
    """
    Mark records that failed during preparation as error in Cosmos DB.

    This prevents records from staying "pending" forever when they can't be processed.

    Args:
        failed_records: List of (record_id, error_message) tuples
        status_field: The status field to update (e.g., "resource_type_batch_status")
        error_message_prefix: Short error message prefix
        error_detail_prefix: Detailed error message prefix

    Returns:
        Number of records successfully marked as error
    """
    from . import content_source_client

    marked_count = 0
    for record_id, error_msg in failed_records:
        try:
            content_source_client.update_record_status(
                record_id,
                status_field,
                "error",
                None,
                error_reason={
                    "message": error_message_prefix,
                    "detail": f"{error_detail_prefix}: {error_msg}. Record ID: {record_id}"
                }
            )
            logger.info("Marked record %s batch status as error: %s", record_id, error_msg)
            marked_count += 1
        except Exception as mark_err:
            logger.exception("Failed to mark record %s as error: %s", record_id, mark_err)

    return marked_count


def categorize_assets_by_type(
    assets: List[Dict[str, Any]],
    sort_assets: bool = True
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Categorize assets into visual and non-visual types based on resource_type.

    Args:
        assets: List of asset dictionaries with resource_type field
        sort_assets: If True, sort assets by record_id then asset_id for consistent ordering

    Returns:
        Tuple of (visual_assets, non_visual_assets, visual_records_grouped_by_record_id)
    """
    # Sort assets for consistent idx values between batch creation and result parsing
    if sort_assets:
        sorted_assets = sorted(assets, key=lambda a: (a.get("record_id", ""), a.get("asset_id", "")))
    else:
        sorted_assets = assets

    visual_assets = []
    non_visual_assets = []

    for asset in sorted_assets:
        resource_type = asset.get("resource_type")
        if resource_type:
            resource_type = resource_type.lower().strip()
            if resource_type in VISUAL_RESOURCE_TYPES:
                visual_assets.append(asset)
            else:
                non_visual_assets.append(asset)
        else:
            non_visual_assets.append(asset)

    # Group visual assets by record_id
    visual_records = {}
    for asset in visual_assets:
        record_id = asset.get("record_id")
        if record_id:
            if record_id not in visual_records:
                visual_records[record_id] = []
            visual_records[record_id].append(asset)

    logger.info(
        "Asset categorization: %d visual assets (%d records), %d non-visual assets",
        len(visual_assets), len(visual_records), len(non_visual_assets)
    )

    return visual_assets, non_visual_assets, visual_records


def _process_generic_batch_errors(
    batch_id: str,
    error_file_path: str,
    record_ids: List[str],
    batch_type: str,
    custom_id_parser: callable,
    cosmos_container=None,
    skip_if_completed: bool = False,
    completed_field: str = None,
) -> Dict[str, Any]:
    """
    Generic function to process batch error files and update records with error status.

    Args:
        batch_id: Batch job ID
        error_file_path: Path to error file
        record_ids: List of record IDs in the batch
        batch_type: Type of batch (BatchType.OCR, BatchType.METADATA, BatchType.RESOURCE_TYPE)
        custom_id_parser: Function to parse custom_id and extract record_id
        cosmos_container: Optional Cosmos container for direct access (used for skip_if_completed check)
        skip_if_completed: If True, skip records that already have completed status
        completed_field: Field to check for completed status (e.g., "resource_type")

    Returns:
        Dictionary with processing results
    """
    from . import content_source_client

    config = BATCH_TYPE_CONFIG.get(batch_type, BATCH_TYPE_CONFIG[BatchType.OCR])
    status_field = config["status_field"]
    error_message = config["error_message"]

    try:
        logger.info("Processing %s batch error file for batch %s", batch_type, batch_id)

        # Parse error file (same format as results file - JSONL)
        error_results = parse_batch_results(error_file_path)

        # Clean up temp file
        _safe_cleanup_temp_file(error_file_path)

        # Track which records have errors
        errors_by_record = {}

        # Parse errors and map to records
        for error_result in error_results:
            custom_id = error_result.get("custom_id", "")
            record_id = custom_id_parser(custom_id)
            if record_id:
                error_msg = _extract_error_message_from_batch_result(error_result)
                errors_by_record[record_id] = error_msg

        # Update records in Cosmos DB with error status
        records_updated = 0
        records_skipped = 0
        errors = []

        for record_id in record_ids:
            try:
                # Check if we should skip completed or already-errored records
                if cosmos_container:
                    try:
                        record_data = cosmos_container.read_item(item=record_id, partition_key=record_id)
                        current_status = record_data.get(status_field)

                        # Skip records that already have error status (preserve specific error messages)
                        if current_status == "error":
                            logger.debug("Record %s already has error status, skipping", record_id)
                            records_skipped += 1
                            continue

                        # Skip records that are already completed (if skip_if_completed is enabled)
                        if skip_if_completed and completed_field:
                            if record_data.get(completed_field) and current_status == "completed":
                                logger.info(
                                    "Record %s already has %s='%s', skipping error marking",
                                    record_id, completed_field, record_data.get(completed_field)
                                )
                                records_skipped += 1
                                continue
                    except Exception as check_err:
                        logger.warning("Failed to check record %s before marking error: %s", record_id, check_err)

                error_msg = errors_by_record.get(record_id, "Batch processing failed")
                error_detail = f"{error_message}: {error_msg}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"

                content_source_client.update_record_status(
                    record_id, status_field, "error", None,
                    error_reason={"message": error_message, "detail": error_detail}
                )
                records_updated += 1
                logger.info("Marked record %s with error status from batch error file", record_id)

            except Exception as e:
                logger.exception("Error updating record %s with error status: %s", record_id, e)
                errors.append(f"Record {record_id}: {str(e)}")

        logger.info(
            "Processed %s batch errors: %d records marked as error, %d skipped",
            batch_type, records_updated, records_skipped
        )

        return {
            "status": "error",
            "batch_id": batch_id,
            "records_updated": records_updated,
            "records_skipped": records_skipped,
            "records_failed": len(record_ids),
            "errors": errors if errors else None,
            "error": "Batch had errors"
        }

    except Exception as e:
        logger.exception("Error processing %s batch error file for %s: %s", batch_type, batch_id, e)
        return {"status": "error", "error": str(e), "records_updated": 0}


# Tokenizer cache (using dict to avoid global statement)
_tokenizer_cache = {"instance": None}


def _get_tokenizer():
    """
    Get or initialize the tiktoken tokenizer for GPT-4 models.
    Uses cl100k_base encoding which is used by GPT-4, GPT-4o, etc.
    """
    if _tokenizer_cache["instance"] is None:
        try:
            import tiktoken
            _tokenizer_cache["instance"] = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.warning("tiktoken not installed, falling back to character-based estimation")
            return None
    return _tokenizer_cache["instance"]


def count_tokens(text: str) -> int:
    """
    Count the number of tokens in a text string.

    Args:
        text: The text to count tokens for

    Returns:
        Number of tokens (or estimated count if tiktoken unavailable)
    """
    tokenizer = _get_tokenizer()
    if tokenizer:
        return len(tokenizer.encode(text))
    # Fallback: estimate ~2 chars per token
    return len(text) // 2


def truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """
    Truncate text to fit within a token limit.

    Args:
        text: The text to truncate
        max_tokens: Maximum number of tokens allowed

    Returns:
        Truncated text that fits within the token limit
    """
    tokenizer = _get_tokenizer()
    if tokenizer:
        tokens = tokenizer.encode(text)
        if len(tokens) <= max_tokens:
            return text
        # Truncate tokens and decode back to text
        truncated_tokens = tokens[:max_tokens]
        return tokenizer.decode(truncated_tokens)

    # Fallback: estimate ~2 chars per token
    max_chars = max_tokens * 2
    return text[:max_chars]


# Note: Cosmos DB client is now accessed through the source container passed to functions
# No global container client needed since we update records in the base container

class JSONParsingError(Exception):
    """Raised when JSON parsing fails."""


def _safe_cleanup_temp_file(file_path: str) -> None:
    """
    Safely remove a temporary file if it exists.
    
    Args:
        file_path: Path to the temp file to remove
    """
    if file_path and os.path.exists(file_path):
        try:
            os.unlink(file_path)
        except Exception as e:
            logger.exception("Failed to cleanup temp file %s: %s", file_path, e)


async def create_and_upload_batch_jobs(
    batch_requests: List[Dict[str, Any]],
    batch_client: AzureOpenAIBatchClient,
    batch_type_prefix: str,
    log_description: str = "batch"
) -> List[Dict[str, Any]]:
    """
    Common helper to split requests by size, create JSONL files, upload to Azure OpenAI,
    and create batch jobs.

    Args:
        batch_requests: List of batch request dictionaries
        batch_client: Azure OpenAI batch client instance
        batch_type_prefix: Prefix for blob naming (e.g., "batch", "metadata_extraction", "resource_type_batch")
        log_description: Description for log messages (e.g., "OCR", "metadata extraction")

    Returns:
        List of created batch info dictionaries with keys:
            - batch_id: Azure OpenAI batch ID
            - file_id: Uploaded file ID
            - input_file_path: Blob URL of input file
            - output_file_path: Blob URL for output file
            - request_count: Number of requests in this batch
    """
    # Split into multiple batches based on file size (max 100MB per batch)
    # This prevents batch files from becoming too large (Azure OpenAI has 200MB limit)
    max_batch_size_mb = int(os.getenv("MAX_BATCH_SIZE_MB", "100"))
    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "content-assets")

    # Split batch_requests into chunks based on estimated file size
    request_chunks = AzureOpenAIBatchClient.split_requests_by_size(batch_requests, max_batch_size_mb)

    # Create batch jobs for each chunk
    created_batches = []
    for chunk_idx, chunk_requests in enumerate(request_chunks):
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        batch_suffix = f"_part{chunk_idx + 1}" if len(request_chunks) > 1 else ""

        input_blob_url = build_blob_url(
            container_name,
            f"batch-jobs/input/{batch_type_prefix}_{timestamp}{batch_suffix}.jsonl",
        )
        output_blob_url = build_blob_url(
            container_name,
            f"batch-jobs/output/{batch_type_prefix}_{timestamp}{batch_suffix}_results.jsonl",
        )

        # Create JSONL file and upload to blob storage
        input_file_path = await asyncio.to_thread(
            batch_client.create_jsonl_from_requests,
            chunk_requests, input_blob_url
        )

        logger.info("Created and uploaded input JSONL file to blob storage: %s", input_file_path)

        # Upload file to Azure OpenAI
        file_id = await asyncio.to_thread(
            batch_client.upload_input_file,
            input_file_path
        )
        logger.info("Uploaded %s input file to Azure OpenAI, file_id: %s", log_description, file_id)

        # Create batch job
        batch_id = await asyncio.to_thread(
            batch_client.create_batch_job,
            file_id
        )

        logger.info(
            "Created Azure OpenAI batch job for %s: %s (batch %d/%d, %d requests)",
            log_description, batch_id, chunk_idx + 1, len(request_chunks), len(chunk_requests)
        )

        created_batches.append({
            "batch_id": batch_id,
            "file_id": file_id,
            "input_file_path": input_blob_url,
            "output_file_path": output_blob_url,
            "request_count": len(chunk_requests)
        })

    return created_batches


def _repair_truncated_json(json_str: str) -> Optional[str]:
    """
    Attempt to repair truncated JSON by closing unclosed strings, arrays, and objects.
    
    Args:
        json_str: Potentially truncated JSON string
        
    Returns:
        Repaired JSON string or None if repair not possible
    """
    if not json_str:
        return None

    try:
        # First, try to close any unclosed string
        # Count quotes (excluding escaped ones)
        quote_count = 0
        i = 0
        while i < len(json_str):
            if json_str[i] == '"' and (i == 0 or json_str[i-1] != '\\'):
                quote_count += 1
            i += 1

        repaired = json_str

        # If odd number of quotes, we have an unclosed string
        if quote_count % 2 == 1:
            # Find the last unclosed quote and close it
            repaired = repaired.rstrip()
            # Remove any trailing incomplete escape sequences
            while repaired.endswith('\\'):
                repaired = repaired[:-1]
            repaired += '"'

        # Now balance brackets and braces
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')

        # Close arrays first (they're usually inside objects)
        repaired += ']' * open_brackets
        # Then close objects
        repaired += '}' * open_braces

        # Try to parse to validate
        json.loads(repaired)
        return repaired

    except (json.JSONDecodeError, Exception) as e:
        logger.exception("JSON repair attempt failed: %s", e)
        return None


def _extract_error_message_from_batch_result(error_result: Dict[str, Any]) -> str:
    """
    Extract error message from a batch error result object.
    
    Handles nested error structures like:
    - {"error": {"message": {"error": {"code": "BadRequest", "message": "..."}}}}
    - {"error": {"message": "..."}}
    - {"error": {"code": "...", "message": "..."}}
    - {"response": {"status_code": 400}}
    
    Args:
        error_result: Error result dictionary from batch processing
        
    Returns:
        Extracted error message string
    """
    error_message = "Unknown error"

    if "error" in error_result:
        error_obj = error_result.get("error", {})
        if isinstance(error_obj, dict):
            message = error_obj.get("message")
            if isinstance(message, dict):
                # Nested error structure
                nested_error = message.get("error", {})
                if isinstance(nested_error, dict):
                    error_message = nested_error.get("message", nested_error.get("code", str(nested_error)))
                else:
                    error_message = str(message)
            elif message:
                # Direct message string
                error_message = str(message)
            else:
                # Try to get code or fallback to string representation
                error_message = error_obj.get("code", str(error_obj))
        else:
            error_message = str(error_obj)
    elif "response" in error_result:
        response = error_result.get("response", {})
        if isinstance(response, dict):
            status_code = response.get("status_code")
            if status_code:
                error_message = f"Error in batch response (status: {status_code})"
            else:
                error_message = "Error in batch response"
        else:
            error_message = "Error in batch response"

    return error_message


def _parse_metadata_custom_id(custom_id: str) -> Optional[str]:
    """Parse record_id from metadata batch custom_id. Delegates to unified parser."""
    return parse_record_id_from_custom_id(custom_id)


def parse_batch_messages(batch: List[Any]) -> List[str]:
    """
    Parse batch messages to extract record IDs.

    Args:
        batch: List of messages that can be strings, dicts, or other types

    Returns:
        List of record ID strings
    """
    record_ids = []
    for msg in batch:
        try:
            record_id = None
            if isinstance(msg, str):
                # Try to parse as JSON first
                try:
                    record_data = json.loads(msg)
                    if isinstance(record_data, dict):
                        record_id = record_data.get("record_id") or record_data.get("id")
                    else:
                        record_id = msg  # Use string as-is if not a dict
                except json.JSONDecodeError:
                    # If not JSON, treat as plain record_id string
                    record_id = msg
            elif isinstance(msg, dict):
                record_id = msg.get("record_id") or msg.get("id")
            else:
                record_id = str(msg)

            if record_id:
                record_ids.append(record_id)
        except Exception as e:
            logger.exception("Error parsing message: %s, message: %s", e, msg)
            continue

    return record_ids


def fetch_assets_for_records(record_ids: List[str], cosmos_container) -> List[Dict[str, Any]]:
    """
    Fetch asset details from Cosmos DB for the given record IDs.

    Performance optimization: Uses ThreadPoolExecutor to parallelize Cosmos DB reads.

    Args:
        record_ids: List of content source record IDs
        cosmos_container: Cosmos DB container client

    Returns:
        List of asset dictionaries with record_id, asset_id, blob_url, and asset_name
    """
    import concurrent.futures

    def fetch_record_assets(record_id: str) -> List[Dict[str, Any]]:
        """Fetch assets for a single record."""
        try:
            # Fetch record from Cosmos DB to get asset details
            record_data = cosmos_container.read_item(
                item=record_id, partition_key=record_id
            )
            asset_details = record_data.get("asset_details", [])

            # Get resource_type from record metadata or record-level field
            resource_type = None
            metadata = record_data.get("metadata", {})
            if isinstance(metadata, dict):
                resource_type = metadata.get("Resource Type")
            if not resource_type:
                resource_type = record_data.get("resource_type")

            logger.debug(
                "Fetched assets for record %s: found %d assets, resource_type=%s",
                record_id, len(asset_details), resource_type
            )

            # Filter assets that have blob URLs (images ready for OCR)
            record_assets = []
            for asset in asset_details:
                blob_url = asset.get("blob_url") or asset.get("blob_thumbnail_url")
                if blob_url:
                    record_assets.append({
                        "record_id": record_id,
                        "asset_id": asset.get("asset_id"),
                        "blob_url": blob_url,
                        "asset_name": asset.get("name", ""),
                        "resource_type": resource_type,  # Include resource_type for proper categorization
                    })
            return record_assets
        except Exception as e:
            logger.exception("Error fetching record asset %s: %s", record_id, e)
            return []

    # Parallelize Cosmos DB reads using ThreadPoolExecutor
    all_assets = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(record_ids))) as executor:
        results = executor.map(fetch_record_assets, record_ids)
        for record_assets in results:
            all_assets.extend(record_assets)

    return all_assets


def parse_batch_results(output_file_path: str) -> List[Dict[str, Any]]:
    """
    Parse batch results from JSONL file.

    Args:
        output_file_path: Path to the JSONL results file (local or blob URL)

    Returns:
        List of parsed result dictionaries
    """
    results = []
    batch_client = AzureOpenAIBatchClient()

    # Download from blob if it's a blob URL
    if batch_client._is_blob_url(output_file_path):
        local_file = batch_client._download_from_blob(output_file_path)
    else:
        local_file = output_file_path

    try:
        with open(local_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result = json.loads(line)
                    results.append(result)
                except json.JSONDecodeError as e:
                    logger.exception("Error parsing JSON line: %s", e)
                    continue
    finally:
        # Clean up temporary file if it was downloaded
        if batch_client._is_blob_url(output_file_path) and os.path.exists(local_file):
            try:
                os.unlink(local_file)
            except Exception as e:
                logger.exception("Failed to cleanup temp file %s: %s", local_file, e)

    return results

def _aggressive_json_recovery(content: str, response_type: str) -> Dict[str, Any]:
    """
    Aggressively try to recover partial JSON response.
    
    Args:
        content: Partial or malformed JSON content
        response_type: Type of response (OCR or entity)
        
    Returns:
        Dictionary with recovered data or minimal structure
    """
    logger.warning("Attempting aggressive JSON recovery for %s response", response_type)

    # Try to extract key-value pairs manually
    result = {}

    try:
        # Extract document_metadata if present
        if '"document_metadata"' in content:
            logger.info("Found document_metadata section")
            # Try to extract the metadata object
            try:
                metadata_match = re.search(
                    r'"document_metadata"\s*:\s*\{[^}]*\}',
                    content,
                    re.DOTALL
                )
                if metadata_match:
                    metadata_str = "{" + metadata_match.group(0) + "}"
                    metadata_obj = json.loads(metadata_str)
                    result["document_metadata"] = metadata_obj.get("document_metadata", {})
            except Exception as e:
                # Fallback to minimal metadata
                logger.exception("Failed to extract document_metadata, using defaults: %s", e)
                result["document_metadata"] = {
                    "document_type": "UNKNOWN",
                    "handwriting_style": "UNKNOWN",
                    "image_quality": "MEDIUM",
                    "language": "en",
                    "has_obstructions": False,
                    "obstruction_types": []
                }

        # Try to extract ocr_text - this is the most critical field
        if '"ocr_text"' in content:
            logger.info("Found ocr_text field")
            # Try multiple extraction strategies
            extracted_text = None

            # Strategy 1: Match quoted string (handles escapes)
            match = re.search(r'"ocr_text"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', content, re.DOTALL)
            if match:
                extracted_text = match.group(1).replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
                logger.info("Extracted ocr_text using quoted string match (%d chars)", len(extracted_text))

            # Strategy 2: If no match, try to find text between "ocr_text": and next field or }
            if not extracted_text:
                match = re.search(r'"ocr_text"\s*:\s*"([^"]*)', content, re.DOTALL)
                if match:
                    extracted_text = match.group(1).replace('\\n', '\n').replace('\\t', '\t')
                    logger.info("Extracted partial ocr_text (%d chars)", len(extracted_text))

            if extracted_text:
                result["ocr_text"] = extracted_text
            else:
                result["ocr_text"] = "ERROR: Could not extract OCR text from malformed response"
                logger.error("Failed to extract ocr_text despite finding field name")
        else:
            result["ocr_text"] = "ERROR: OCR text field not found in response"
            logger.error("ocr_text field not found in content")

        # Try to extract confidence scores
        if "confidence_scores" in content:
            logger.info("Found confidence_scores section")
            try:
                # Try to extract the confidence object
                conf_match = re.search(
                    r'"confidence_scores"\s*:\s*\{[^}]*\}',
                    content,
                    re.DOTALL
                )
                if conf_match:
                    conf_str = "{" + conf_match.group(0) + "}"
                    conf_obj = json.loads(conf_str)
                    result["confidence_scores"] = conf_obj.get("confidence_scores", {})
            except Exception as e:
                logger.exception("Failed to extract confidence_scores: %s", e)

        # Add minimal confidence scores if not recovered
        if "confidence_scores" not in result:
            result["confidence_scores"] = {
                "overall_ocr_confidence": 0.5,
                "text_clarity_score": 0.5,
                "formatting_preservation_score": 0.5,
                "completeness_score": 0.5
            }

        # Try to extract entities
        if "entities" in content and response_type == "OCR":
            logger.info("Found entities section")
            try:
                # Try to extract entities array
                entities_match = re.search(
                    r'"entities"\s*:\s*\[[^\]]*\]',
                    content,
                    re.DOTALL
                )
                if entities_match:
                    entities_str = "{" + entities_match.group(0) + "}"
                    entities_obj = json.loads(entities_str)
                    result["entities"] = entities_obj.get("entities", [])
            except Exception as e:
                logger.exception("Failed to extract entities: %s", e)
                result["entities"] = []
        elif response_type == "OCR":
            result["entities"] = []

        # Add extraction notes about the recovery
        if "extraction_notes" not in result:
            result["extraction_notes"] = [
                "WARNING: JSON response was malformed and required aggressive recovery",
                "Some data may be incomplete or missing",
                "Consider increasing max_tokens in configuration or checking prompt complexity"
            ]

        logger.warning("Recovered partial data from malformed JSON: ocr_text=%d chars, entities=%d",
                      len(result.get("ocr_text", "")),
                      len(result.get("entities", [])))
        return result

    except Exception as recovery_error:
        logger.exception("Aggressive recovery also failed: %s", recovery_error)
        if response_type == "OCR":
            return {
                "ocr_text": "ERROR: Failed to parse OCR response - response was truncated or malformed",
                "entities": [],
                "confidence_scores": {
                    "overall_ocr_confidence": 0.0,
                    "entity_extraction_confidence": 0.0,
                },
                "extraction_notes": [
                    f"CRITICAL ERROR: Could not parse API response: {recovery_error}",
                    "Original response was severely truncated or corrupted"
                ]
            }
        return {
            "entities": [],
            "confidence_scores": {
                "overall_confidence": 0.0
            },
            "extraction_notes": [
                f"CRITICAL ERROR: Could not parse API response: {recovery_error}"
            ]
        }


def _parse_json_response(content: str, response_type: str = "response") -> Dict[str, Any]:
    """
    Parse JSON response with robust error handling.

    Args:
        content: JSON string to parse
        response_type: Type of response for error messages (e.g., "OCR", "entity")

    Returns:
        Parsed JSON dictionary

    Raises:
        JSONParsingError: If JSON cannot be parsed after all recovery attempts
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.exception("Initial JSON parse failed: %s. Attempting recovery...", e)

        try:
            # Clean up markdown code blocks
            cleaned_content = content.strip()
            if cleaned_content.startswith("```json"):
                cleaned_content = cleaned_content[7:]
            if cleaned_content.startswith("```"):
                cleaned_content = cleaned_content[3:]
            if cleaned_content.endswith("```"):
                cleaned_content = cleaned_content[:-3]

            cleaned_content = cleaned_content.strip()

            # Find complete JSON object
            if cleaned_content.startswith("{"):
                brace_count = 0
                json_end = 0
                in_string = False
                escape_next = False

                for i, char in enumerate(cleaned_content):
                    if escape_next:
                        escape_next = False
                        continue

                    if char == "\\":
                        escape_next = True
                        continue

                    if char == '"' and not escape_next:
                        in_string = not in_string
                    elif not in_string:
                        if char == "{":
                            brace_count += 1
                        elif char == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = i + 1
                                break

                if json_end > 0:
                    cleaned_content = cleaned_content[:json_end]
                    logger.info("Found complete JSON object at position %d", json_end)
                else:
                    # Incomplete JSON - try to close it properly
                    logger.warning("JSON appears incomplete. Attempting to close it...")

                    # If we're in a string, close it
                    if in_string:
                        cleaned_content += '"'
                        logger.info("Added missing closing quote")

                    # Add missing closing braces
                    while brace_count > 0:
                        cleaned_content += "}"
                        brace_count -= 1
                        logger.info("Added missing closing brace")

            try:
                result = json.loads(cleaned_content)
                logger.info("✓ Successfully recovered malformed JSON")
                return result
            except json.JSONDecodeError as parse_error:
                # If still failing, try more aggressive recovery
                logger.exception("Standard recovery failed: %s. Trying aggressive recovery...", parse_error)
                return _aggressive_json_recovery(cleaned_content, response_type)

        except Exception as e2:
            logger.exception("Failed to parse %s JSON response after all attempts: %s", response_type, e2)
            logger.info("Original error: %s", e)
            logger.info("Content preview (first 1000 chars): %s", content[:1000])
            # Last resort: try aggressive recovery on original content
            try:
                return _aggressive_json_recovery(content, response_type)
            except Exception as e3:
                logger.exception("Aggressive JSON recovery also failed: %s", e3)
                raise JSONParsingError(
                    f"Failed to parse {response_type} JSON response after all recovery attempts: {e2}. "
                    f"Content: {content[:500]}..."
                ) from e2

def extract_ocr_result_from_batch_response(batch_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract OCR result from batch response, matching the format from single record processing.

    Args:
        batch_result: Batch result dictionary from Azure OpenAI

    Returns:
        OCR result dictionary in the same format as single record processing, or None if error
    """
    try:
        # Check for error first
        if "error" in batch_result and batch_result.get("error") is not None:
            error = batch_result.get("error")
            error_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return {
                "error": error_msg,
                "processed_at": datetime.utcnow().isoformat(),
            }

        # Extract response body
        response = batch_result.get("response", {})
        body = response.get("body", {})

        # Extract choices and content
        choices = body.get("choices", [])
        if not choices:
            return {
                "error": "No choices in response",
                "processed_at": datetime.utcnow().isoformat(),
            }

        message = choices[0].get("message", {})
        content = message.get("content", "")

        if not content:
            return {
                "error": "Empty content in response",
                "processed_at": datetime.utcnow().isoformat(),
            }

        # Parse OCR content (should be JSON from the prompt)
        try:
            ocr_data = _parse_json_response(content, response_type="OCR")
        except JSONParsingError as e:
            logger.exception("Failed to parse OCR response after all recovery attempts: %s", e)
            return {
                "error": f"Failed to parse OCR response: {str(e)}",
                "ocr_text": "ERROR: Could not parse OCR response",
                "confidence_scores": {
                    "overall_confidence": 0.0,
                    "entity_extraction_confidence": 0.0,
                },
                "processed_at": datetime.utcnow().isoformat(),
            }

        # Check if this is a visual description response
        visual_description_possible = ocr_data.get("visual_description_possible", "N")

        if visual_description_possible == "Y":
            # Handle visual description response
            result = {
                "visual_detailed_description": ocr_data.get("visual_detailed_description", ""),
                "visual_summary_description": ocr_data.get("visual_summary_description", ""),
                "visual_description_possible": "Y",
                "processed_at": datetime.utcnow().isoformat(),
            }
        else:
            # Handle normal OCR response
            # Extract confidence scores
            confidence_scores = ocr_data.get("confidence_scores", {})
            overall_confidence = confidence_scores.get("overall_ocr_confidence", 0.0)

            # Extract entity confidence
            total_entity_confidence = 0.0
            entities = ocr_data.get("entities", [])
            for entity in entities:
                total_entity_confidence += entity.get("confidence", 0.0)

            if len(entities):
                entity_confidence = round(total_entity_confidence / len(entities), 2)
            else:
                entity_confidence = 0.0

            # Build result matching single record format
            result = {
                "ocr_text": ocr_data.get("ocr_text", ""),
                "confidence_scores": {
                    "overall_confidence": overall_confidence,
                    "entity_extraction_confidence": entity_confidence,
                },
                "visual_description_possible": "N",
                "processed_at": datetime.utcnow().isoformat(),
            }

            # Add entities if present
            if "entities" in ocr_data:
                result["entities"] = ocr_data.get("entities", [])

            # Add document metadata if present
            if "document_metadata" in ocr_data:
                result["document_metadata"] = ocr_data.get("document_metadata")

        return result

    except Exception as e:
        logger.exception("Error extracting OCR result from batch response: %s", e)
        return {
            "error": str(e),
            "processed_at": datetime.utcnow().isoformat(),
        }


def _validate_batch_status(batch_id: str, batch_client: AzureOpenAIBatchClient) -> str:
    """
    Validate that batch is completed.

    Returns:
        Status string if valid, raises exception if not completed
    """
    logger.info("Getting batch status for %s...", batch_id)
    batch = batch_client.get_batch_status(batch_id)
    status_str = str(batch.status) if hasattr(batch.status, 'value') else batch.status

    if status_str != "completed":
        error_msg = f"Batch {batch_id} is not completed. Status: {status_str}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    return status_str


def _retrieve_batch_error_file(
    batch_id: str,
    error_file_id: str,
    batch_client: AzureOpenAIBatchClient
) -> str:
    """
    Retrieve batch error file and return path.

    Args:
        batch_id: Batch job ID
        error_file_id: Error file ID from batch metadata
        batch_client: Azure OpenAI batch client

    Returns:
        Path to error file
    """
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as tmp_file:
        temp_error_path = tmp_file.name

    try:
        logger.info("Downloading error file %s for batch %s...", error_file_id, batch_id)

        # Download the error file using the file API
        error_file = batch_client.client.files.content(error_file_id)

        # Write error file to temporary file
        if hasattr(error_file, "read"):
            with open(temp_error_path, "wb") as f:
                f.write(error_file.read())
        elif hasattr(error_file, "iter_bytes"):
            with open(temp_error_path, "wb") as f:
                for chunk in error_file.iter_bytes():
                    f.write(chunk)
        elif isinstance(error_file, bytes):
            with open(temp_error_path, "wb") as f:
                f.write(error_file)
        else:
            content = getattr(error_file, "content", error_file)
            with open(temp_error_path, "wb") as f:
                if isinstance(content, bytes):
                    f.write(content)
                else:
                    f.write(str(content).encode("utf-8"))

        logger.info("Error file downloaded to: %s", temp_error_path)
        return temp_error_path

    except Exception as e:
        logger.exception("Error retrieving batch error file: %s", e)
        _safe_cleanup_temp_file(temp_error_path)
        raise Exception(f"Failed to retrieve batch error file: {e}") from e


def _retrieve_batch_results_file(batch_id: str, batch_client: AzureOpenAIBatchClient) -> str:
    """
    Retrieve batch results file and return path.

    Returns:
        Path to results file

    Raises:
        Exception: If neither output file nor error file is found
    """
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as tmp_file:
        temp_output_path = tmp_file.name

    try:
        output_file_path = batch_client.retrieve_batch_results(batch_id, temp_output_path)
        return output_file_path
    except Exception as e:
        logger.exception("Error retrieving batch output file: %s. Checking for error file...", e)

        # Check if error file exists
        try:
            batch = batch_client.get_batch_status(batch_id)
            error_file_id = getattr(batch, "error_file_id", None)

            if error_file_id:
                logger.info("Found error file for batch %s. Retrieving error file...", batch_id)
                # Retrieve error file instead
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                return error_file_path
            else:
                logger.error("No output file or error file found for batch %s", batch_id)
                raise Exception(f"No output file or error file found for batch {batch_id}") from e
        except Exception as error_check_exception:
            logger.exception("Error checking for error file: %s", error_check_exception)
            # Re-raise original exception if error file check fails
            raise Exception(f"Failed to retrieve batch results: {e}") from e
        finally:
            _safe_cleanup_temp_file(temp_output_path)


def _build_asset_mappings(assets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build mapping from custom_id to asset info.

    Handles both visual and non-visual assets:
    - Visual records: Uses first asset with idx=0 (ocr_{record_id}_{first_asset_id}_0)
    - Non-visual assets: Uses sequential idx starting from 0 (ocr_{record_id}_{asset_id}_{idx})

    Returns:
        Dictionary mapping custom_id patterns to asset dictionaries
    """
    asset_map = {}

    logger.info("Building asset mappings for %d assets", len(assets))

    # Use shared helper for asset categorization (sorts assets for consistent ordering)
    _, non_visual_assets, visual_records = categorize_assets_by_type(assets, sort_assets=True)

    # Log first few sorted assets for debugging
    if non_visual_assets:
        logger.debug("First 5 non-visual assets for mapping: %s", [
            f"{a.get('record_id')}/{a.get('asset_id')}" for a in non_visual_assets[:5]
        ])

    # For visual records, use first asset with idx=0 (matches process_visual_record)
    for record_id, record_assets in visual_records.items():
        if record_assets:
            first_asset = record_assets[0]
            rec_id = str(first_asset.get("record_id", ""))
            a_id = str(first_asset.get("asset_id", ""))
            # Match the format used in process_visual_record: ocr_{record_id}_{first_asset_id}_0
            custom_id = f"ocr_{rec_id}_{a_id}_0"
            asset_map[custom_id] = first_asset
            logger.debug(
                "Mapped visual record: custom_id=%s, record_id=%s, first_asset_id=%s, total_assets=%d",
                custom_id, record_id, a_id, len(record_assets)
            )

    # For non-visual assets, use sequential idx starting from 0 (matches process_asset)
    for idx, asset in enumerate(non_visual_assets):
        rec_id = str(asset.get("record_id", ""))
        a_id = str(asset.get("asset_id", ""))
        # Match the format used in process_asset: ocr_{record_id}_{asset_id}_{idx}
        # Chunked requests (for multi-page documents) use: ocr_{record_id}_{asset_id}_{idx}_chunk{chunk_idx}
        # The parsing function handles chunked custom_ids by removing the _chunk suffix
        custom_id = f"ocr_{rec_id}_{a_id}_{idx}"
        asset_map[custom_id] = asset
        logger.debug(
            "Mapped non-visual asset: custom_id=%s, record_id=%s, asset_id=%s, idx=%d",
            custom_id, rec_id, a_id, idx
        )

    logger.info(
        "Built asset map with %d entries (%d visual records, %d non-visual assets)",
        len(asset_map), len(visual_records), len(non_visual_assets)
    )
    return asset_map


def _parse_custom_id_to_record_asset(
    custom_id: str,
    asset_map: Dict[str, Dict[str, Any]]
) -> tuple[Optional[str], Optional[str]]:
    """
    Parse custom_id to extract record_id and asset_id using asset_map lookup.

    Handles both visual records and non-visual assets, with regular and chunked formats:
    - Visual records (base): ocr_{record_id}_{first_asset_id}_0
    - Visual records (chunked): ocr_{record_id}_{first_asset_id}_0_chunk{chunk_idx}
    - Non-visual assets (base): ocr_{record_id}_{asset_id}_{idx}
    - Non-visual assets (chunked): ocr_{record_id}_{asset_id}_{idx}_chunk{chunk_idx}

    Returns:
        Tuple of (record_id, asset_id) or (None, None) if not found
    """
    # Try direct lookup first
    asset = asset_map.get(custom_id)
    if asset:
        record_id = str(asset.get("record_id", ""))
        asset_id = str(asset.get("asset_id", ""))
        if record_id and asset_id:
            logger.debug(
                "Found asset mapping: custom_id=%s -> record_id=%s, asset_id=%s",
                custom_id, record_id, asset_id
            )
            return record_id, asset_id
    else:
        logger.warning(
            "Custom ID %s not found in asset map (direct lookup). Map has %d entries. Trying chunk removal...",
            custom_id, len(asset_map)
        )
        # Log sample of available custom_ids for debugging
        sample_ids = list(asset_map.keys())[:5]
        logger.debug("Sample custom_ids in map: %s", sample_ids)

    # If not found, try removing chunk suffix (for multi-page documents)
    # Format: ocr_{record_id}_{asset_id}_{idx}_chunk{chunk_idx}
    if "_chunk" in custom_id:
        base_custom_id = custom_id.rsplit("_chunk", 1)[0]
        logger.debug("Trying base custom_id (removed chunk suffix): %s", base_custom_id)
        asset = asset_map.get(base_custom_id)
        if asset:
            record_id = str(asset.get("record_id", ""))
            asset_id = str(asset.get("asset_id", ""))
            if record_id and asset_id:
                logger.debug(
                    "Found asset mapping (chunked): custom_id=%s, base=%s -> record_id=%s, asset_id=%s",
                    custom_id, base_custom_id, record_id, asset_id
                )
                return record_id, asset_id
        else:
            logger.warning(
                "Base custom_id %s (from chunked %s) not found in asset map",
                base_custom_id, custom_id
            )

    logger.error(
        "Failed to parse custom_id to record/asset: custom_id=%s, asset_map_size=%d",
        custom_id, len(asset_map)
    )
    return None, None


def _parse_batch_results_to_records(
    results: List[Dict[str, Any]],
    asset_map: Dict[str, Dict[str, Any]]
) -> tuple[Dict[str, Dict[str, Any]], int, List[str]]:
    """
    Parse batch results and map to records.

    Returns:
        Tuple of (records_to_update, results_written, errors)
    """
    records_to_update = {}
    results_written = 0
    errors = []

    logger.info(
        "Parsing %d batch results using asset map with %d entries",
        len(results), len(asset_map)
    )

    for result in results:
        custom_id = result.get("custom_id")
        if not custom_id:
            logger.warning("Result missing custom_id, skipping result: %s", result.get("id", "unknown"))
            continue

        try:
            logger.debug("Parsing result with custom_id: %s", custom_id)
            record_id, asset_id = _parse_custom_id_to_record_asset(
                custom_id, asset_map
            )

            if not record_id or not asset_id:
                logger.warning(
                    "Unable to determine record_id/asset_id from custom_id '%s'. Skipping.",
                    custom_id
                )
                errors.append(f"Could not map custom_id to asset: {custom_id}")
                continue

            logger.debug(
                "Successfully mapped custom_id=%s to record_id=%s, asset_id=%s",
                custom_id, record_id, asset_id
            )

            # Ensure record entry exists
            if record_id not in records_to_update:
                records_to_update[record_id] = {"record_id": record_id, "assets": {}}

            ocr_result = extract_ocr_result_from_batch_response(result)
            if ocr_result:
                # Check if there's an error in the OCR result
                if "error" in ocr_result and ocr_result.get("error"):
                    # Error occurred during extraction - skip blob upload but still record the error
                    logger.warning(
                        "OCR extraction error for record %s, asset %s: %s",
                        record_id, asset_id, ocr_result.get("error")
                    )
                    # Add OCR result with error to records_to_update (no blob upload)
                    records_to_update[record_id]["assets"][asset_id] = ocr_result
                    results_written += 1
                    continue

                # Check if this is a visual description result (no blob upload needed)
                visual_description_possible = ocr_result.get("visual_description_possible", "N")
                if visual_description_possible == "Y":
                    # Visual description result - skip blob upload, just store the result
                    logger.info(
                        "Visual description result for record %s, asset %s (skipping blob upload)",
                        record_id, asset_id
                    )
                    records_to_update[record_id]["assets"][asset_id] = ocr_result
                    results_written += 1
                    continue

                # Normal OCR result - extract OCR text and upload to blob storage
                ocr_text = ocr_result.get("ocr_text", "").strip()

                if ocr_text == "":
                    ocr_text = "NO_READABLE_TEXT"

                try:
                    storage_config = get_storage_config()
                    blob_container = os.getenv("AZURE_STORAGE_CONTAINER_NAME")

                    if not blob_container:
                        raise ValueError("AZURE_STORAGE_CONTAINER_NAME environment variable not set")

                    # Define blob paths
                    blob_flexible = f"{record_id}/ocr/flexible/{asset_id}/v1.txt"
                    blob_og = f"{record_id}/ocr/original/{asset_id}.txt"

                    # Upload OCR text to blob storage (both flexible and original paths)
                    upload_text_to_blob(
                        container_name=blob_container,
                        blob=blob_flexible,
                        content=ocr_text,
                    )
                    upload_text_to_blob(
                        container_name=blob_container,
                        blob=blob_og,
                        content=ocr_text,
                    )

                    # Generate blob URLs
                    ocr_flexible_path = build_blob_url(blob_container, blob_flexible)
                    ocr_og_path = build_blob_url(blob_container, blob_og)

                    # Also store blob URLs in separate fields for reference
                    ocr_result["ocr_text_flexible_blob_url"] = ocr_flexible_path
                    ocr_result["ocr_text_original_blob_url"] = ocr_og_path

                    del ocr_result["ocr_text"]

                    logger.info(
                        "Uploaded OCR text to blob storage for record %s, asset %s: %s",
                        record_id, asset_id, ocr_flexible_path
                    )

                except Exception as blob_error:
                    error_msg = f"Failed to upload OCR text to blob storage for record {record_id}, asset {asset_id}: {blob_error}"
                    logger.exception(error_msg)
                    errors.append(error_msg)
                    # Mark this result as having a blob upload error
                    ocr_result["blob_upload_error"] = str(blob_error)
                    # Keep the original OCR text in the result for debugging
                    # Don't add blob paths if upload failed

                # Add OCR result to records_to_update (even if blob upload failed)
                records_to_update[record_id]["assets"][asset_id] = ocr_result
                results_written += 1

        except Exception as e:
            logger.exception("Error processing result for custom_id %s: %s", custom_id, e)
            errors.append(f"Error processing custom_id {custom_id}: {e}")
            continue

    return records_to_update, results_written, errors


def _fetch_source_document(
    record_id: str,
    source_cosmos_container
) -> Optional[Dict[str, Any]]:
    """
    Fetch source document using multiple fallback strategies.

    Returns:
        Document dict or None if not found
    """
    tried_methods = []

    # Strategy A: direct read (id == record_id, partition_key == record_id)
    try:
        tried_methods.append(f"read_item(item={record_id}, pk={record_id})")
        return source_cosmos_container.read_item(item=record_id, partition_key=record_id)
    except CosmosHttpResponseError as e:
        if e.status_code != 404:
            raise
        # Not found, try next strategy

    # Strategy B: try common prefix "record_{id}"
    alt_id = f"record_{record_id}"
    try:
        tried_methods.append(f"read_item(item={alt_id}, pk={record_id})")
        return source_cosmos_container.read_item(item=alt_id, partition_key=record_id)
    except CosmosHttpResponseError as e:
        if e.status_code != 404:
            raise

    # Strategy C: query by record_id field
    try:
        tried_methods.append(f"query WHERE c.record_id = '{record_id}'")
        query = f"SELECT TOP 1 * FROM c WHERE c.record_id = '{record_id}'"
        items = list(source_cosmos_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        if items:
            return items[0]
    except Exception as e:
        logger.exception("Query by record_id failed: %s", e)

    # Strategy D: query by suffix on id
    try:
        tried_methods.append(f"query WHERE ENDSWITH(c.id, '{record_id}')")
        query = f"SELECT TOP 1 * FROM c WHERE ENDSWITH(c.id, '{record_id}')"
        items = list(source_cosmos_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        if items:
            return items[0]
    except Exception as e:
        logger.exception("Query by suffix failed: %s", e)

    logger.warning(
        "Document for record_id=%s not found after methods: %s",
        record_id, tried_methods
    )
    return None


def _update_record_with_ocr_results(
    source_item: Dict[str, Any],
    asset_results: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Update record with OCR results and calculate statistics.

    Returns:
        Updated record dict
    """
    asset_details = source_item.get("asset_details", [])

    if not asset_details:
        # No assets in source; mark as error
        source_item.update({
            "last_processed": datetime.utcnow().isoformat(),
            "ocr_processing_status": "error",
            "ocr_processing_status_error": {
                "message": "No assets found",
                "detail": "Record has no assets to process for OCR."
            },
            "asset_count": 0,
            "asset_avg_confidence": 0,
            "version": source_item.get("version", 1),
            "archivist_status": normalize_archivist_status(source_item.get("archivist_status")),
            "published_by": source_item.get("published_by", ""),
        })
        return source_item

    # Update asset OCR results where available
    total_confidence = 0.0
    assets_with_ocr = 0
    assets_with_confidence = 0  # Track non-visual assets for confidence calculation

    # Track visual description fields at record level
    visual_description_possible = "N"  # Default to "N"
    visual_detailed_description = None
    visual_summary_description = None

    for asset in asset_details:
        a_id = str(asset.get("asset_id", ""))
        if a_id and a_id in asset_results:
            ocr_result = asset_results[a_id]

            # Extract visual_description_possible from ocr_result (always present)
            asset_visual_description_possible = ocr_result.get("visual_description_possible", "N")

            # Only add ocr_result to asset_details for non-visual records
            # Visual records store descriptions at record level, not asset level
            if asset_visual_description_possible != "Y":
                asset["ocr_result"] = ocr_result
                logger.debug(
                    "Added ocr_result to asset %s (non-visual record)",
                    a_id
                )
            else:
                # Remove ocr_result if it exists (for visual records)
                if "ocr_result" in asset:
                    del asset["ocr_result"]
                    logger.debug(
                        "Removed ocr_result from asset %s (visual record - descriptions stored at record level)",
                        a_id
                    )

            # Update record-level visual_description_possible if any asset has "Y"
            if asset_visual_description_possible == "Y":
                visual_description_possible = "Y"
                # Extract visual description fields if not already set
                if visual_detailed_description is None:
                    visual_detailed_description = ocr_result.get("visual_detailed_description", "")
                if visual_summary_description is None:
                    visual_summary_description = ocr_result.get("visual_summary_description", "")

            if "error" not in ocr_result:
                assets_with_ocr += 1
                # Only calculate confidence for non-visual OCR results
                # Visual descriptions don't have confidence_scores
                if asset_visual_description_possible != "Y" and "confidence_scores" in ocr_result:
                    conf = ocr_result.get("confidence_scores", {}).get("overall_confidence", 0.0)
                    try:
                        total_confidence += float(conf)
                        assets_with_confidence += 1
                    except (ValueError, TypeError) as e:
                        logger.exception("Failed to convert confidence to float for asset %s: %s", a_id, e)

    # Calculate average confidence only for assets that have confidence scores (non-visual)
    asset_avg_confidence = (
        round(total_confidence / assets_with_confidence, 2) if assets_with_confidence > 0 else 0.0
    )

    # Build update dict with visual description fields at record level
    update_dict = {
        "last_processed": datetime.utcnow().isoformat(),
        "asset_count": len(asset_details),
        "ocr_processing_status": ("completed" if assets_with_ocr > 0 else "error"),
        "asset_avg_confidence": asset_avg_confidence,
        "version": source_item.get("version", 1),
        "archivist_status": normalize_archivist_status(source_item.get("archivist_status")),
        "published_by": source_item.get("published_by", ""),
        "visual_description_possible": visual_description_possible,
    }

    # Add error details if no OCR results
    if assets_with_ocr == 0:
        update_dict["ocr_processing_status_error"] = {
            "message": "No OCR results",
            "detail": "OCR processing completed but no text was extracted from any assets."
        }

    # Only add visual description fields if visual_description_possible = "Y"
    if visual_description_possible == "Y":
        record_id = source_item.get("record_id") or source_item.get("id", "")

        # Upload visual descriptions to blob storage
        try:
            storage_config = get_storage_config()
            blob_container = os.getenv("AZURE_STORAGE_CONTAINER_NAME")

            if not blob_container:
                raise ValueError("AZURE_STORAGE_CONTAINER_NAME environment variable not set")

            # Upload detailed description
            if visual_detailed_description:
                detailed_text = visual_detailed_description.strip()
                if detailed_text == "":
                    detailed_text = "NO_READABLE_TEXT"

                blob_detailed_flexible = f"{record_id}/visual/flexible/detailed/v1.txt"
                blob_detailed_og = f"{record_id}/visual/original/detailed.txt"

                upload_text_to_blob(
                    container_name=blob_container,
                    blob=blob_detailed_flexible,
                    content=detailed_text,
                )
                upload_text_to_blob(
                    container_name=blob_container,
                    blob=blob_detailed_og,
                    content=detailed_text,
                )

                detailed_flexible_path = build_blob_url(
                    blob_container, blob_detailed_flexible
                )
                detailed_og_path = build_blob_url(blob_container, blob_detailed_og)

                update_dict["visual_detailed_description_flexible_blob_url"] = detailed_flexible_path
                update_dict["visual_detailed_description_original_blob_url"] = detailed_og_path

                logger.info(
                    "Uploaded visual detailed description to blob storage for record %s: %s",
                    record_id, detailed_flexible_path
                )

            # Store summary description directly (not uploaded to blob)
            if visual_summary_description:
                update_dict["visual_summary_description_flexible"] = visual_summary_description
                update_dict["visual_summary_description_original"] = visual_summary_description

        except Exception as blob_error:
            error_msg = f"Failed to upload visual descriptions to blob storage for record {record_id}: {blob_error}"
            logger.exception(error_msg)
            # Mark this result as having a blob upload error
            update_dict["visual_description_blob_upload_error"] = str(blob_error)
            # Keep the original descriptions in the result for debugging
            update_dict["visual_detailed_description"] = visual_detailed_description or ""
            update_dict["visual_summary_description"] = visual_summary_description or ""
        # If upload succeeded: detailed description is stored as blob URLs only,
        # summary description is stored directly in the record (not uploaded to blob)

    source_item.update(update_dict)

    if update_dict.get("ocr_processing_status") == "completed":
        from .export_tracking import mark_pipeline_ocr

        mark_pipeline_ocr(source_item)

    return source_item


def write_batch_results_to_cosmos(
    batch_id: str,
    assets: List[Dict[str, Any]],
    source_cosmos_container
) -> Dict[str, Any]:
    """
    Wait for batch completion, retrieve results, and update existing records in the container.

    This version includes robust fallbacks for:
    - incomplete/malformed record_id values extracted from custom_id
    - Cosmos documents where the 'id' does not equal record_id (e.g., 'record_<id>')
    - querying by record_id field when direct reads fail

    Returns a summary dict with status, records_updated, results_written, and errors.
    """
    from . import content_source_client

    try:
        # 1. Initialize batch client and validate status
        batch_client = AzureOpenAIBatchClient()
        _validate_batch_status(batch_id, batch_client)

        # 2. Retrieve batch results file (or error file if output not found)
        # Helper to mark all records as error (using consolidated helper)
        def mark_all_records_error(error_message: str, error_detail: str):
            record_ids = list({asset.get("record_id") for asset in assets if asset.get("record_id")})
            _mark_records_error_safe(
                record_ids=record_ids,
                cosmos_container=source_cosmos_container,
                status_field="ocr_processing_status",
                error_message=error_message,
                error_detail=error_detail,
                batch_id=batch_id,
            )

        try:
            output_file_path = _retrieve_batch_results_file(batch_id, batch_client)
        except Exception as e:
            # Check if error file exists
            logger.exception("Failed to retrieve output file: %s. Checking for error file...", e)
            batch = batch_client.get_batch_status(batch_id)
            error_file_id = getattr(batch, "error_file_id", None)

            if error_file_id:
                logger.info("Found error file for batch %s. Processing errors...", batch_id)
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                return _process_batch_errors(
                    batch_id, error_file_path, assets, source_cosmos_container
                )
            else:
                # No error file either - mark all records as error and return error
                logger.error("No output file or error file found for OCR batch %s", batch_id)
                mark_all_records_error("Batch retrieval failed", f"Failed to retrieve batch results: {str(e)}. The batch files may have been deleted.")
                return {"status": "error", "error": f"Failed to get batch results: {str(e)}", "results_written": 0, "records_updated": 0}

        # 3. Check if this is actually an error file (by checking batch status)
        batch = batch_client.get_batch_status(batch_id)
        error_file_id = getattr(batch, "error_file_id", None)
        output_file_id = getattr(batch, "output_file_id", None)

        # If error file exists but output file doesn't, process errors
        if error_file_id and not output_file_id:
            logger.warning("Batch %s has error file but no output file. Processing errors...", batch_id)
            error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
            return _process_batch_errors(
                batch_id, error_file_path, assets, source_cosmos_container
            )

        # 3. Parse batch results (normal case)
        results = parse_batch_results(output_file_path)

        # Clean up temp file
        _safe_cleanup_temp_file(output_file_path)

        # 4. Build asset mappings
        logger.info("Building asset mappings for batch %s with %d assets", batch_id, len(assets))
        asset_map = _build_asset_mappings(assets)
        logger.info("Asset mapping complete: %d entries in map", len(asset_map))

        # 5. Parse results to records
        logger.info("Parsing %d batch results to records using asset map", len(results))
        records_to_update, results_written, errors = _parse_batch_results_to_records(
            results, asset_map
        )
        logger.info(
            "Parsed results: %d records to update, %d results written, %d errors",
            len(records_to_update), results_written, len(errors)
        )

        # 5a. Validate that all expected record IDs have results
        expected_record_ids = set(asset.get("record_id") for asset in assets if asset.get("record_id"))
        actual_record_ids = set(records_to_update.keys())
        missing_record_ids = expected_record_ids - actual_record_ids

        logger.info(
            "Result validation: expected %d record IDs, found %d in results, %d missing",
            len(expected_record_ids), len(actual_record_ids), len(missing_record_ids)
        )

        if missing_record_ids:
            logger.warning(
                "Batch %s is missing results for %d record IDs: %s",
                batch_id, len(missing_record_ids), list(missing_record_ids)[:10]
            )
            # Log detailed info about missing records
            for missing_record_id in list(missing_record_ids)[:5]:
                missing_assets = [a for a in assets if a.get("record_id") == missing_record_id]
                logger.warning(
                    "Missing record %s has %d assets: %s",
                    missing_record_id, len(missing_assets),
                    [a.get("asset_id") for a in missing_assets[:3]]
                )

            # Check if there's an error file to get actual error messages for missing records
            errors_from_file = {}
            if error_file_id:
                try:
                    logger.info("Checking error file for details on missing records...")
                    error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                    error_results = parse_batch_results(error_file_path)

                    # Parse errors and map to records
                    for error_result in error_results:
                        custom_id = error_result.get("custom_id")
                        if not custom_id:
                            continue
                        try:
                            rec_id, _ = _parse_custom_id_to_record_asset(custom_id, asset_map)
                            if rec_id and rec_id in missing_record_ids:
                                error_msg = _extract_error_message_from_batch_result(error_result)
                                if rec_id not in errors_from_file:
                                    errors_from_file[rec_id] = []
                                errors_from_file[rec_id].append(error_msg)
                        except Exception as parse_err:
                            logger.exception("Error parsing error result: %s", parse_err)

                    # Clean up error file
                    _safe_cleanup_temp_file(error_file_path)
                except Exception as e:
                    logger.exception("Failed to retrieve error file for missing records: %s", e)

            # Mark missing records as error with actual error message if available
            for record_id in missing_record_ids:
                try:
                    source_item = _fetch_source_document(record_id, source_cosmos_container)
                    if source_item:
                        # Check if this is a visual record by checking record-level resource_type first, then assets
                        is_visual_record = False
                        resource_type = None

                        # First check Resource Type from Metadata
                        metadata = source_item.get("metadata", {})
                        if isinstance(metadata, dict):
                            resource_type = metadata.get("Resource Type")

                        if not resource_type:
                            # Fall back to generated resource_type at record level
                            resource_type = source_item.get("resource_type")

                        if resource_type:
                            resource_type = str(resource_type).lower().strip()
                            if resource_type in VISUAL_RESOURCE_TYPES:
                                is_visual_record = True

                        # Use error from error file if available
                        if record_id in errors_from_file:
                            unique_errors = list(set(errors_from_file[record_id]))
                            error_from_file = "; ".join(unique_errors[:3])
                            if len(unique_errors) > 3:
                                error_from_file += f" (and {len(unique_errors) - 3} more)"
                            if is_visual_record:
                                error_message = "Visual description extraction failed"
                                error_detail = f"Visual description extraction failed: {error_from_file}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"
                            else:
                                error_message = "Text extraction failed"
                                error_detail = f"Text extraction failed: {error_from_file}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"
                        else:
                            if is_visual_record:
                                error_message = "No visual description extracted"
                                error_detail = f"No visual description could be extracted from the files. The images may not be suitable for visual description or processing failed. Batch ID: {batch_id}, Record ID: {record_id}"
                            else:
                                error_message = "No text extracted"
                                error_detail = f"No readable text could be extracted from the files. The images may not contain any text or the text was not recognizable. Batch ID: {batch_id}, Record ID: {record_id}"
                        source_item["ocr_processing_status"] = "error"
                        source_item["ocr_processing_status_error"] = {"message": error_message, "detail": error_detail}
                        source_item["last_processed"] = datetime.utcnow().isoformat()
                        result = upsert_with_retry(
                            cosmos_container=source_cosmos_container,
                            item=source_item,
                            record_id=record_id,
                            operation_name="ocr_error_status"
                        )
                        if result["success"]:
                            logger.warning("Marked record %s as error: %s", record_id, error_detail)
                        else:
                            logger.error("Failed to mark record %s as error: %s", record_id, result.get("error"))
                    else:
                        logger.warning("Could not find record %s to mark as error", record_id)
                except Exception as e:
                    logger.exception("Error marking record %s as error: %s", record_id, e)

        # 6. Update existing records in the base container
        records_updated = 0
        all_record_ids = set(records_to_update.keys())

        for record_id in all_record_ids:
            try:
                logger.info("Processing record_id=%s", record_id)

                # Fetch source document
                source_item = _fetch_source_document(record_id, source_cosmos_container)
                if not source_item:
                    errors.append(f"Record not found: {record_id}")
                    continue

                # Get OCR results for this record
                record_data = records_to_update.get(record_id, {})
                asset_results = record_data.get("assets", {})

                # Check if this is a visual record (any asset has visual_description_possible = "Y")
                is_visual_record = False
                for ocr_result in asset_results.values():
                    if ocr_result.get("visual_description_possible") == "Y":
                        is_visual_record = True
                        break

                # Verify blob uploads succeeded before updating Cosmos DB (only for non-visual records)
                blob_upload_failures = []
                error_messages = []  # Track just the error messages for grouping
                if not is_visual_record:
                    # Only check blob uploads for non-visual records
                    for asset_id, ocr_result in asset_results.items():
                        if ocr_result.get("blob_upload_error"):
                            err_msg = ocr_result.get('blob_upload_error')
                            blob_upload_failures.append(f"Asset {asset_id}: {err_msg}")
                            error_messages.append(err_msg)
                        elif not ocr_result.get("ocr_text_flexible_blob_url") or not ocr_result.get("ocr_text_flexible_blob_url", "").startswith("http"):
                            # OCR text should be uploaded to blob storage
                            err_msg = "OCR text not uploaded to blob (missing ocr_text_flexible_blob_url)"
                            blob_upload_failures.append(f"Asset {asset_id}: {err_msg}")
                            error_messages.append(err_msg)

                    if blob_upload_failures:
                        failed_asset_ids = [f.split(":")[0].replace("Asset ", "") for f in blob_upload_failures]
                        error_message = "Text save failed"
                        error_detail = f"We extracted the text but were unable to save it for {len(blob_upload_failures)} files. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}, Failed Asset IDs: {failed_asset_ids}"
                        logger.error("Blob upload failures for record %s. Failed asset IDs: %s", record_id, failed_asset_ids)
                        logger.error(error_detail)
                        errors.append(error_detail)
                        # Still update Cosmos DB but mark with error (with retry)
                        source_item["ocr_processing_status"] = "error"
                        source_item["ocr_processing_status_error"] = {"message": error_message, "detail": error_detail}
                        source_item["last_processed"] = datetime.utcnow().isoformat()
                        upsert_with_retry(
                            cosmos_container=source_cosmos_container,
                            item=source_item,
                            record_id=record_id,
                            operation_name="ocr_blob_error"
                        )
                        continue

                # Update record with OCR results (blob paths are already in asset_results)
                updated_item = _update_record_with_ocr_results(source_item, asset_results)

                # Update existing record in the base container (with retry)
                result = upsert_with_retry(
                    cosmos_container=source_cosmos_container,
                    item=updated_item,
                    record_id=record_id,
                    operation_name="ocr_results"
                )
                if result["success"]:
                    logger.info(
                        "✓ Updated document (record_id=%s, id=%s) in base container with OCR results from blob storage",
                        record_id, updated_item.get("id")
                    )
                    records_updated += 1
                else:
                    # Mark record as error when save fails
                    error_msg = result.get("error", "Unknown error")
                    logger.error("Failed to save OCR results for record %s: %s", record_id, error_msg)
                    try:
                        content_source_client.update_record_status(
                            record_id, "ocr_processing_status", "error", None,
                            error_reason={"message": "Save failed", "detail": f"We extracted the text but failed to save results: {error_msg}. Batch ID: {batch_id}, Record ID: {record_id}"}
                        )
                    except Exception as status_err:
                        logger.exception("Failed to update error status for record %s: %s", record_id, status_err)
                    errors.append(f"Failed to save OCR results for record {record_id}: {error_msg}")

            except CosmosHttpResponseError as e:
                error_msg = f"Cosmos DB error updating record {record_id}: {e}"
                logger.exception(error_msg)
                # Mark record as error when Cosmos DB operation fails
                try:
                    content_source_client.update_record_status(
                        record_id, "ocr_processing_status", "error", None,
                        error_reason={"message": "Database error", "detail": f"Failed to save OCR results due to database error: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                except Exception as status_err:
                    logger.exception("Failed to update error status for record %s: %s", record_id, status_err)
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error updating record {record_id}: {e}"
                logger.exception(error_msg)
                # Mark record as error when unexpected error occurs
                try:
                    content_source_client.update_record_status(
                        record_id, "ocr_processing_status", "error", None,
                        error_reason={"message": "Processing error", "detail": f"Failed to process OCR results: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                except Exception as status_err:
                    logger.exception("Failed to update error status for record %s: %s", record_id, status_err)
                errors.append(error_msg)

        logger.info(
            "Wrote batch results to Cosmos DB: %d records updated, %d OCR results",
            records_updated, results_written
        )

        return {
            "status": "success",
            "records_updated": records_updated,
            "results_written": results_written,
            "total_results": len(results),
            "errors": errors
        }

    except Exception as e:
        logger.exception("Error writing batch results to Cosmos DB: %s", e)
        return {"status": "error", "error": str(e), "results_written": 0, "records_updated": 0}


def _process_batch_errors(
    batch_id: str,
    error_file_path: str,
    assets: List[Dict[str, Any]],
    source_cosmos_container
) -> Dict[str, Any]:
    """
    Process batch error file and update records in Cosmos DB with error status.

    Args:
        batch_id: Batch job ID
        error_file_path: Path to error file
        assets: List of assets that were in the batch
        source_cosmos_container: Cosmos DB container client

    Returns:
        Dictionary with processing results
    """
    try:
        logger.info("Processing error file for batch %s", batch_id)

        # Parse error file (same format as results file - JSONL)
        error_results = parse_batch_results(error_file_path)

        # Build asset mappings to match errors to records
        asset_map = _build_asset_mappings(assets)

        # Track which records have errors
        records_with_errors = {}
        all_record_ids = set(asset.get("record_id") for asset in assets if asset.get("record_id"))

        # Parse errors and map to records
        for error_result in error_results:
            custom_id = error_result.get("custom_id")
            if not custom_id:
                continue

            try:
                record_id, asset_id = _parse_custom_id_to_record_asset(
                    custom_id, asset_map
                )

                if not record_id:
                    continue

                if record_id not in records_with_errors:
                    records_with_errors[record_id] = []

                error_message = _extract_error_message_from_batch_result(error_result)
                records_with_errors[record_id].append({
                    "asset_id": asset_id,
                    "error": error_message
                })
            except Exception as e:
                logger.warning("Error parsing error result for custom_id %s: %s", custom_id, e)
                continue

        # Update records in Cosmos DB with error status
        records_updated = 0
        errors = []

        for record_id in all_record_ids:
            try:
                # Fetch source document
                source_item = _fetch_source_document(record_id, source_cosmos_container)
                if not source_item:
                    errors.append(f"Record not found: {record_id}")
                    continue

                # Get errors for this record
                record_errors = records_with_errors.get(record_id, [])

                # Build error message including actual error from error file
                if record_errors:
                    failed_assets = [e.get("asset_id") for e in record_errors if e.get("asset_id")]
                    # Get unique error messages from the error file
                    unique_errors = list(set(e.get("error", "Unknown error") for e in record_errors if e.get("error")))
                    error_from_file = "; ".join(unique_errors[:3])  # Limit to first 3 unique errors
                    if len(unique_errors) > 3:
                        error_from_file += f" (and {len(unique_errors) - 3} more)"
                    error_message = "Text extraction failed"
                    error_detail = f"Text extraction failed for {len(record_errors)} files: {error_from_file}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}, Failed Asset IDs: {failed_assets}"
                    logger.error("OCR processing errors for record %s: %s. Failed asset IDs: %s", record_id, error_from_file, failed_assets)
                else:
                    error_message = "Text extraction failed"
                    error_detail = f"We were unable to extract text from the files. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"

                # Update record with error status
                source_item.update({
                    "last_processed": datetime.utcnow().isoformat(),
                    "ocr_processing_status": "error",
                    "ocr_processing_status_error": {"message": error_message, "detail": error_detail},
                    "batch_id": batch_id,
                    "version": source_item.get("version", 1),
                })

                # Update existing record in the base container (with retry)
                result = upsert_with_retry(
                    cosmos_container=source_cosmos_container,
                    item=source_item,
                    record_id=record_id,
                    operation_name="ocr_error_update"
                )
                if result["success"]:
                    logger.info("✓ Updated record %s with error status: %s", record_id, error_detail[:100])
                    records_updated += 1
                else:
                    logger.error("Failed to update error status for record %s: %s", record_id, result.get("error"))

            except CosmosHttpResponseError as e:
                error_msg = f"Cosmos DB error updating record {record_id}: {e}"
                logger.exception(error_msg)
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error updating record {record_id}: {e}"
                logger.exception(error_msg)
                errors.append(error_msg)

        logger.info(
            "Processed batch errors: %d records updated with error status",
            records_updated
        )

        return {
            "status": "error",
            "records_updated": records_updated,
            "total_records": len(all_record_ids),
            "records_with_errors": len(records_with_errors),
            "errors": errors
        }

    except Exception as e:
        logger.exception("Error processing batch errors: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "records_updated": 0
        }
    finally:
        # Clean up error file
        _safe_cleanup_temp_file(error_file_path)


def _recreate_batch_for_missing_records(
    missing_record_ids: set,
    original_batch_id: str,
    source_cosmos_container
) -> Dict[str, Any]:
    """
    Recreate a batch for record IDs that were missing from the original batch results.

    Args:
        missing_record_ids: Set of record IDs that were missing from batch results
        original_batch_id: The original batch ID that had missing records
        source_cosmos_container: Cosmos DB container client

    Returns:
        Dictionary with status and new_batch_id if successful
    """
    try:
        logger.info(
            "Recreating batch for %d missing record IDs from batch %s",
            len(missing_record_ids), original_batch_id
        )

        # Fetch assets for missing record IDs
        missing_record_ids_list = list(missing_record_ids)
        missing_assets = fetch_assets_for_records(missing_record_ids_list, source_cosmos_container)

        if not missing_assets:
            logger.warning(
                "No assets found for missing record IDs. They may have been removed or have no blob URLs."
            )
            return {
                "status": "skipped",
                "reason": "no_assets_found",
                "missing_record_ids": len(missing_record_ids)
            }

        logger.info(
            "Found %d assets for %d missing record IDs",
            len(missing_assets), len(missing_record_ids)
        )

        # Get instance_id from original batch if available
        original_batch_status = get_batch_status_from_cosmos(original_batch_id)
        instance_id = original_batch_status.get("instance_id") if original_batch_status else None

        # Create new batch job for missing assets
        batch_result = create_ocr_batch_job(missing_assets, instance_id=instance_id)
        new_batch_id = batch_result.get("batch_id")

        if not new_batch_id:
            return {
                "status": "error",
                "error": "Failed to create recovery batch - no batch_id returned"
            }

        # Store batch status with reference to original batch
        try:
            store_batch_status(
                new_batch_id,
                batch_result,
                missing_record_ids_list,
                missing_assets,
                instance_id=instance_id
            )

            # Update batch status to indicate it's a recovery batch
            update_batch_status_in_cosmos(new_batch_id, "created", {
                "recovery_batch": True,
                "original_batch_id": original_batch_id,
                "missing_record_count": len(missing_record_ids),
                "recovery_reason": "missing_records_in_original_batch"
            })

            logger.info(
                "Recovery batch %s created for %d missing record IDs from batch %s",
                new_batch_id, len(missing_record_ids), original_batch_id
            )

            return {
                "status": "success",
                "new_batch_id": new_batch_id,
                "missing_record_ids": len(missing_record_ids),
                "assets_processed": len(missing_assets)
            }
        except Exception as e:
            logger.exception("Error storing recovery batch status: %s", e)
            return {
                "status": "error",
                "error": f"Failed to store recovery batch status: {str(e)}",
                "new_batch_id": new_batch_id  # Batch was created but status storage failed
            }

    except Exception as e:
        logger.exception("Error recreating batch for missing records: %s", e)
        return {
            "status": "error",
            "error": str(e)
        }


def store_batch_status(
    batch_id: str,
    batch_info: Dict[str, Any],
    record_ids: List[str],
    assets: List[Dict[str, Any]],
    instance_id: Optional[str] = None,
    batch_type: str = "ocr"
) -> None:
    """
    Store batch job status in Cosmos DB batch status container.

    Args:
        batch_id: Azure OpenAI batch job ID
        batch_info: Batch job information from create_ocr_batch_job
        record_ids: List of record IDs in this batch
        assets: List of assets in this batch (for OCR) or empty for metadata extraction
        instance_id: Optional orchestration instance ID
        batch_type: Type of batch job ("ocr" or "metadata_extraction")
    """
    try:
        base_config = get_cosmosdb_config()

        # Create batch status container config
        batch_status_config = CosmosDBConfig(
            endpoint=base_config.endpoint,
            database_name=base_config.database_name,
            container_name=base_config.batch_status_container_name,
            connection_mode=base_config.connection_mode,
            enable_diagnostics=base_config.enable_diagnostics,
        )

        # Create batch status document
        # Store minimal asset info (record_id, asset_id, blob_url, resource_type) for later retrieval
        assets_summary = [
            {
                "record_id": asset.get("record_id"),
                "asset_id": asset.get("asset_id"),
                "blob_url": asset.get("blob_url"),
                "resource_type": asset.get("resource_type"),  # Include resource_type for proper categorization
            }
            for asset in assets
        ]

        batch_status_doc = {
            "id": batch_id,
            "batch_id": batch_id,
            "batch_type": batch_type,  # "ocr" or "metadata_extraction"
            "status": "created",  # Initial status
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "record_ids": record_ids,
            "asset_count": len(assets),
            "assets": assets_summary,  # Store asset summary for later use
            "file_id": batch_info.get("file_id"),
            "input_file_path": batch_info.get("input_file_path"),
            "output_file_path": batch_info.get("output_file_path"),
            "request_counts": batch_info.get("request_counts"),
            "metadata": batch_info.get("metadata", {}),
        }

        # Add instance_id if provided
        if instance_id:
            batch_status_doc["instance_id"] = instance_id

        with CosmosDBClient(batch_status_config) as client:
            client._container.upsert_item(body=batch_status_doc)
            logger.info("Stored batch status for batch_id: %s", batch_id)

    except Exception as e:
        logger.exception("Error storing batch status: %s", e)
        # Don't raise - this is not critical for batch creation


def get_batch_status_from_cosmos(batch_id: str) -> Optional[Dict[str, Any]]:
    """
    Get batch status from Cosmos DB batch status container.

    Args:
        batch_id: Azure OpenAI batch job ID

    Returns:
        Batch status document or None if not found
    """
    try:
        base_config = get_cosmosdb_config()

        batch_status_config = CosmosDBConfig(
            endpoint=base_config.endpoint,
            database_name=base_config.database_name,
            container_name=base_config.batch_status_container_name,
            connection_mode=base_config.connection_mode,
            enable_diagnostics=base_config.enable_diagnostics,
        )

        with CosmosDBClient(batch_status_config) as client:
            try:
                doc = client._container.read_item(
                    item=batch_id, partition_key=batch_id
                )
                return doc
            except CosmosHttpResponseError as e:
                if e.status_code == 404:
                    return None
                raise

    except Exception as e:
        logger.exception("Error getting batch status from Cosmos DB: %s", e)
        return None


def poll_and_update_batch_status(batch_id: str) -> Dict[str, Any]:
    """
    Poll Azure OpenAI for batch status (does not update Cosmos DB).

    Args:
        batch_id: Azure OpenAI batch job ID

    Returns:
        Dictionary with batch status information
    """
    try:
        batch_client = AzureOpenAIBatchClient()

        # Get current status from Azure OpenAI
        batch = batch_client.get_batch_status(batch_id)

        # Extract status string
        status_str = str(batch.status) if hasattr(batch.status, 'value') else batch.status

        # Do NOT update status in Cosmos DB - status will be updated after processing
        # This allows batches to be reprocessed if function times out

        return {
            "batch_id": batch_id,
            "status": status_str,
            "request_counts": batch.request_counts,
            "is_completed": status_str == "completed",
            "is_failed": status_str in ["failed", "cancelled", "expired"]
        }

    except Exception as e:
        logger.exception("Error polling batch status for %s: %s", batch_id, e)
        # Do NOT update status on error - let caller handle it
        return {"batch_id": batch_id, "status": "error", "error": str(e)}


def get_pending_batches(batch_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get all batches that are pending processing (status: created, validating, in_progress, finalizing).

    Args:
        batch_type: Optional filter by batch type ("ocr" or "metadata_extraction").
                   If None, returns all pending batches.

    Returns:
        List of batch status documents
    """
    try:
        base_config = get_cosmosdb_config()

        batch_status_config = CosmosDBConfig(
            endpoint=base_config.endpoint,
            database_name=base_config.database_name,
            container_name=base_config.batch_status_container_name,
            connection_mode=base_config.connection_mode,
            enable_diagnostics=base_config.enable_diagnostics,
        )

        with CosmosDBClient(batch_status_config) as client:
            # Query for pending batches
            if batch_type:
                # Filter by batch type (handle legacy batches without batch_type as "ocr")
                if batch_type == "ocr":
                    query = "SELECT * FROM c WHERE c.status IN (@status1, @status2, @status3, @status4) AND (NOT IS_DEFINED(c.batch_type) OR c.batch_type = @batch_type)"
                else:
                    query = "SELECT * FROM c WHERE c.status IN (@status1, @status2, @status3, @status4) AND c.batch_type = @batch_type"
                parameters = [
                    {"name": "@status1", "value": "created"},
                    {"name": "@status2", "value": "validating"},
                    {"name": "@status3", "value": "in_progress"},
                    {"name": "@status4", "value": "finalizing"},
                    {"name": "@batch_type", "value": batch_type},
                ]
            else:
                query = "SELECT * FROM c WHERE c.status IN (@status1, @status2, @status3, @status4)"
                parameters = [
                    {"name": "@status1", "value": "created"},
                    {"name": "@status2", "value": "validating"},
                    {"name": "@status3", "value": "in_progress"},
                    {"name": "@status4", "value": "finalizing"},
                ]

            items = list(client._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))

            return items

    except Exception as e:
        logger.exception("Error getting pending batches: %s", e)
        return []


def get_failed_batches() -> List[Dict[str, Any]]:
    """
    Get all batches that have failed and can be retried.


    Returns:
        List of failed batch status documents that haven't exceeded max retries
    """
    try:
        base_config = get_cosmosdb_config()

        batch_status_config = CosmosDBConfig(
            endpoint=base_config.endpoint,
            database_name=base_config.database_name,
            container_name=base_config.batch_status_container_name,
            connection_mode=base_config.connection_mode,
            enable_diagnostics=base_config.enable_diagnostics,
        )

        with CosmosDBClient(batch_status_config) as client:
            # Query for failed batches (failed, cancelled, expired, error)
            # Exclude batches that start with "failed_" (these are failed batch creation attempts)
            # Use IS_DEFINED for null checks and proper STARTSWITH syntax
            query = (
                "SELECT * FROM c WHERE c.status IN (@status1, @status2, @status3, @status4) "
                "AND (NOT IS_DEFINED(c.batch_id) OR STARTSWITH(c.batch_id, @failed_prefix)) "
            )
            parameters = [
                {"name": "@status1", "value": "failed"},
                {"name": "@status2", "value": "cancelled"},
                {"name": "@status3", "value": "expired"},
                {"name": "@status4", "value": "error"},
                {"name": "@failed_prefix", "value": "failed_"},
            ]

            items = list(client._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))

            return items

    except Exception as e:
        logger.exception("Error getting failed batches: %s", e)
        return []


def retry_failed_batch(
    failed_batch_id: str,
    _cosmos_container=None  # Unused but kept for API consistency
) -> Dict[str, Any]:
    """
    Retry a failed batch by recreating it with the same assets.

    Args:
        failed_batch_id: The failed batch ID
        _cosmos_container: Cosmos DB container client (unused, kept for API consistency)

    Returns:
        Dictionary with retry result containing new batch_id if successful
    """
    try:
        # Get the failed batch status from Cosmos DB
        batch_status = get_batch_status_from_cosmos(failed_batch_id)
        if not batch_status:
            logger.error("Failed batch status not found in Cosmos DB: %s", failed_batch_id)
            return {"status": "error", "error": "Batch status not found"}

        # Get assets from batch status
        assets_summary = batch_status.get("assets", [])
        if not assets_summary:
            logger.error("No assets found in failed batch status: %s", failed_batch_id)
            return {"status": "error", "error": "No assets found in batch status"}

        # Convert assets summary back to full asset format for batch creation
        # Assets summary has: record_id, asset_id, blob_url
        all_assets = assets_summary

        logger.info(
            "Retrying failed batch %s with %d assets",
            failed_batch_id, len(all_assets)
        )

        # Get instance_id from original batch if available
        instance_id = batch_status.get("instance_id")

        # Create new batch job
        batch_result = create_ocr_batch_job(all_assets, instance_id=instance_id)
        new_batch_id = batch_result.get("batch_id")

        if not new_batch_id:
            logger.error("Failed to create new batch job for retry of %s", failed_batch_id)
            return {"status": "error", "error": "Failed to create new batch job"}

        logger.info("Successfully created retry batch %s for failed batch %s", new_batch_id, failed_batch_id)

        # Update old batch status to mark it as retried
        try:
            batch_status["retried_at"] = datetime.utcnow().isoformat()
            batch_status["retry_batch_id"] = new_batch_id
            batch_status["status"] = "retried"
            batch_status["updated_at"] = datetime.utcnow().isoformat()

            update_batch_status_in_cosmos(failed_batch_id, "retried", {
                "retry_batch_id": new_batch_id
            })
        except Exception as e:
            logger.warning("Failed to update retry status for batch %s: %s", failed_batch_id, e)

        # Store new batch status
        record_ids = batch_status.get("record_ids", [])
        try:
            store_batch_status(new_batch_id, batch_result, record_ids, all_assets, instance_id=instance_id)
            logger.info("Stored retry batch status in Cosmos DB: %s", new_batch_id)
        except Exception as e:
            logger.warning("Error storing retry batch status: %s", e)

        return {
            "status": "success",
            "old_batch_id": failed_batch_id,
            "new_batch_id": new_batch_id
        }

    except Exception as e:
        logger.exception("Error retrying failed batch %s: %s", failed_batch_id, e)
        return {"status": "error", "error": str(e)}


def process_completed_batch(batch_id: str, cosmos_container, update_status: bool = True) -> Dict[str, Any]:
    """
    Process a completed batch by retrieving results and writing to Cosmos DB.

    Args:
        batch_id: Azure OpenAI batch job ID
        cosmos_container: Cosmos DB container client for source (content source) container
        update_status: If True, update batch status to "completed" in Cosmos DB after processing.
                       If False, only process records without updating batch status.
                       Default: True (for backward compatibility)

    Returns:
        Dictionary with processing results
    """
    try:
        # Get batch status from Cosmos DB
        batch_status = get_batch_status_from_cosmos(batch_id)
        if not batch_status:
            logger.error("Batch status not found in Cosmos DB: %s", batch_id)
            return {"status": "error", "error": "Batch status not found"}

        record_ids = batch_status.get("record_ids", [])

        # Get assets from batch status if available, otherwise fetch from source
        assets_summary = batch_status.get("assets", [])
        if assets_summary:
            # Use stored assets summary
            all_assets = assets_summary
            logger.info(
                "Using %d assets from batch status for batch %s",
                len(all_assets), batch_id
            )
        else:
            # Fallback: fetch from source container
            logger.warning(
                "Assets not found in batch status for batch %s, fetching from source container for %d records",
                batch_id, len(record_ids)
            )
            all_assets = fetch_assets_for_records(record_ids, cosmos_container)
            logger.info("Fetched %d assets from source container", len(all_assets))

        # Process the batch results
        write_result = write_batch_results_to_cosmos(
            batch_id, all_assets, cosmos_container
        )

        # Only update batch status if requested (allows caller to control when status is updated)
        if update_status:
            update_batch_status_in_cosmos(batch_id, "completed", {
                "request_counts": write_result.get("request_counts"),
                "status": "completed"
            })

        logger.info(
            "Completed batch processing: %d records updated, %d results written (status_update=%s)",
            write_result.get("records_updated", 0),
            write_result.get("results_written", 0),
            update_status
        )

        return {
            "status": "success",
            "batch_id": batch_id,
            **write_result
        }

    except Exception as e:
        logger.exception("Error processing completed batch %s: %s", batch_id, e)
        # Don't update batch status on error - allows reprocessing
        return {"status": "error", "error": str(e)}


def _process_metadata_batch_errors(
    batch_id: str,
    error_file_path: str,
    record_ids: List[str]
) -> Dict[str, Any]:
    """
    Process metadata batch error file and update records in Cosmos DB with error status.

    Args:
        batch_id: Batch job ID
        error_file_path: Path to error file
        record_ids: List of record IDs in the batch

    Returns:
        Dictionary with processing results
    """
    return _process_generic_batch_errors(
        batch_id=batch_id,
        error_file_path=error_file_path,
        record_ids=record_ids,
        batch_type=BatchType.METADATA,
        custom_id_parser=_parse_metadata_custom_id,
    )


def process_completed_metadata_batch(batch_id: str, cosmos_container, update_status: bool = True) -> Dict[str, Any]:
    """
    Process a completed metadata extraction batch by retrieving results and writing to Cosmos DB.

    Args:
        batch_id: Azure OpenAI batch job ID
        cosmos_container: Cosmos DB container client for source (content source) container
        update_status: If True, update batch status to "completed" in Cosmos DB after processing.

    Returns:
        Dictionary with processing results
    """
    from . import content_source_client

    try:
        # Get batch status from Cosmos DB
        batch_status = get_batch_status_from_cosmos(batch_id)
        if not batch_status:
            logger.error("Batch status not found in Cosmos DB: %s", batch_id)
            return {"status": "error", "error": "Batch status not found"}

        record_ids = batch_status.get("record_ids", [])

        # Helper function to mark all records as error (using consolidated helper)
        def mark_all_records_error(error_message: str, error_detail: str):
            _mark_records_error_safe(
                record_ids=record_ids,
                cosmos_container=cosmos_container,
                status_field="metadata_extraction_status",
                error_message=error_message,
                error_detail=error_detail,
                batch_id=batch_id,
            )

        # Initialize batch client
        batch_client = AzureOpenAIBatchClient()

        # 1. Validate batch status first (same as OCR)
        try:
            _validate_batch_status(batch_id, batch_client)
        except Exception as e:
            logger.exception("Batch validation failed for %s: %s", batch_id, e)
            mark_all_records_error("Batch validation failed", f"The batch job is not in a completed state: {str(e)}.")
            return {"status": "error", "error": f"Batch validation failed: {str(e)}"}

        # 2. Retrieve batch results file (or error file if output not found) - same as OCR
        try:
            output_file_path = _retrieve_batch_results_file(batch_id, batch_client)
        except Exception as e:
            # Check if error file exists
            logger.warning("Failed to retrieve output file for metadata batch: %s. Checking for error file...", e)
            batch = batch_client.get_batch_status(batch_id)
            error_file_id = getattr(batch, "error_file_id", None)

            if error_file_id:
                logger.info("Found error file for metadata batch %s. Processing errors...", batch_id)
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                result = _process_metadata_batch_errors(batch_id, error_file_path, record_ids)
                if update_status:
                    update_batch_status_in_cosmos(batch_id, "completed", {"status": "completed", "had_errors": True})
                return result
            else:
                # No error file either, mark all records as error
                logger.error("No output file or error file found for metadata batch %s", batch_id)
                mark_all_records_error("Batch retrieval failed", f"Failed to retrieve batch results: {str(e)}.")
                return {"status": "error", "error": f"Failed to get batch results: {str(e)}"}

        # 3. Check if this is actually an error file (by checking batch status) - same as OCR
        batch = batch_client.get_batch_status(batch_id)
        error_file_id = getattr(batch, "error_file_id", None)
        output_file_id = getattr(batch, "output_file_id", None)

        # If error file exists but output file doesn't, process errors
        if error_file_id and not output_file_id:
            logger.warning("Metadata batch %s has error file but no output file. Processing errors...", batch_id)
            error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
            result = _process_metadata_batch_errors(batch_id, error_file_path, record_ids)
            if update_status:
                update_batch_status_in_cosmos(batch_id, "completed", {"status": "completed", "had_errors": True})
            return result

        # 4. Parse batch results (normal case)
        results = parse_batch_results(output_file_path)

        # Clean up temp file
        _safe_cleanup_temp_file(output_file_path)

        if not results:
            logger.error("No results returned for metadata batch %s", batch_id)
            mark_all_records_error("No batch results", "The batch processing did not return any results.")
            return {"status": "error", "error": "No results from batch"}

        # Parse results and update records
        records_updated = 0
        records_failed = 0
        errors = []

        # Build a map of custom_id to result for quick lookup
        results_map = {}
        for result in results:
            custom_id = result.get("custom_id", "")
            results_map[custom_id] = result

        # Check for error file to get actual error messages for missing/failed records
        errors_from_file = {}
        if error_file_id:
            try:
                logger.info("Checking error file for details on failed records...")
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                error_results = parse_batch_results(error_file_path)

                # Parse errors and map to records
                for error_result in error_results:
                    custom_id = error_result.get("custom_id", "")
                    rec_id = _parse_metadata_custom_id(custom_id)
                    if rec_id:
                        error_msg = _extract_error_message_from_batch_result(error_result)
                        errors_from_file[rec_id] = error_msg

                # Clean up error file
                _safe_cleanup_temp_file(error_file_path)
            except Exception as e:
                logger.warning("Failed to retrieve error file for failed records: %s", e)

        for record_id in record_ids:
            try:
                # Find the result for this record (custom_id format: metadata_{record_id}_{idx})
                record_result = None
                for custom_id, result in results_map.items():
                    if custom_id.startswith(f"metadata_{record_id}_"):
                        record_result = result
                        break

                if not record_result:
                    logger.warning("No result found for record %s in batch %s", record_id, batch_id)
                    # Use error from error file if available
                    if record_id in errors_from_file:
                        error_from_file = errors_from_file[record_id]
                        error_detail = f"Metadata extraction failed: {error_from_file}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"
                    else:
                        error_detail = f"The batch processing did not return a result for this record. Batch ID: {batch_id}, Record ID: {record_id}"
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "No result in batch", "detail": error_detail}
                    )
                    records_failed += 1
                    continue

                # Extract metadata from response
                response = record_result.get("response", {})
                body = response.get("body", {})
                choices = body.get("choices", [])

                if not choices:
                    logger.error("No choices in response for record %s", record_id)
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "Invalid response", "detail": f"The AI model returned an empty response. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1
                    continue

                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    logger.error("Empty content in response for record %s", record_id)
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "Empty response", "detail": f"The AI model returned empty content. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1
                    continue

                # Check if response was truncated (finish_reason = "length")
                finish_reason = choices[0].get("finish_reason", "")
                if finish_reason == "length":
                    logger.warning("Response truncated for record %s (finish_reason=length), attempting recovery", record_id)

                # Parse JSON from content with recovery for truncated responses
                metadata = None
                parse_error = None

                # First, strip markdown code blocks if present (e.g., ```json ... ```)
                cleaned_content = content.strip()
                if cleaned_content.startswith("```"):
                    # Remove opening code fence (```json or ```)
                    lines = cleaned_content.split('\n')
                    if lines[0].startswith("```"):
                        lines = lines[1:]  # Remove first line
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]  # Remove last line
                    cleaned_content = '\n'.join(lines).strip()

                try:
                    metadata = json.loads(cleaned_content)
                except json.JSONDecodeError as e:
                    parse_error = str(e)
                    logger.warning("Initial JSON parse failed: %s. Attempting recovery...", parse_error)

                    # Try to extract JSON object from the response
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            metadata = json.loads(json_match.group())
                        except json.JSONDecodeError:
                            # Try to repair truncated JSON by closing unclosed structures
                            json_str = json_match.group()
                            repaired = _repair_truncated_json(json_str)
                            if repaired:
                                try:
                                    metadata = json.loads(repaired)
                                    logger.info("Successfully repaired truncated JSON for record %s", record_id)
                                except json.JSONDecodeError as repair_error:
                                    logger.exception("JSON repair failed for record %s: %s", record_id, repair_error)

                if metadata is None:
                    # All recovery attempts failed
                    error_detail = f"Could not parse the AI response as JSON"
                    if finish_reason == "length":
                        error_detail = "The AI response was truncated due to length limits and could not be recovered"
                    logger.error("Failed to parse metadata JSON for record %s: %s", record_id, parse_error)
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "Invalid JSON", "detail": f"{error_detail}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1
                    continue

                # Update record with extracted metadata
                try:
                    record_data = cosmos_container.read_item(
                        item=record_id, partition_key=record_id
                    )

                    record_data["extracted_metadata"] = metadata.get("extracted_metadata", {})
                    if record_data["extracted_metadata"]:
                        record_data["metadata_extraction_confidence"] = metadata.get("metadata_extraction_confidence")
                    record_data["metadata_extraction_status"] = "completed"
                    record_data["last_processed"] = datetime.utcnow().isoformat()

                    from .export_tracking import mark_pipeline_metadata

                    mark_pipeline_metadata(record_data)

                    # Upsert with retry for automatic 429 handling
                    result = upsert_with_retry(
                        cosmos_container=cosmos_container,
                        item=record_data,
                        record_id=record_id,
                        operation_name="metadata_results"
                    )
                    if result["success"]:
                        records_updated += 1
                        logger.info("Updated record %s with extracted metadata from batch", record_id)
                    else:
                        # Mark record as error when save fails
                        error_msg = result.get("error", "Unknown error")
                        logger.error("Failed to save metadata for record %s: %s", record_id, error_msg)
                        content_source_client.update_record_status(
                            record_id, "metadata_extraction_status", "error", None,
                            error_reason={"message": "Save failed", "detail": f"We extracted the metadata but failed to save: {error_msg}. Batch ID: {batch_id}, Record ID: {record_id}"}
                        )
                        records_failed += 1

                except Exception as e:
                    logger.exception("Failed to update record %s with metadata: %s", record_id, e)
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "Save failed", "detail": f"Failed to save extracted metadata: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1

            except Exception as e:
                logger.exception("Error processing record %s from batch %s: %s", record_id, batch_id, e)
                # Mark the record as error
                try:
                    content_source_client.update_record_status(
                        record_id, "metadata_extraction_status", "error", None,
                        error_reason={"message": "Processing error", "detail": f"An error occurred while processing the batch result: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                except Exception as status_err:
                    logger.exception("Failed to update error status for record %s: %s", record_id, status_err)
                records_failed += 1
                errors.append(f"Record {record_id}: {str(e)}")

        # Update batch status if requested
        if update_status:
            update_batch_status_in_cosmos(batch_id, "completed", {
                "request_counts": {
                    "records_updated": records_updated,
                    "records_failed": records_failed
                },
                "status": "completed"
            })

        logger.info(
            "Completed metadata batch processing: %d records updated, %d failed (status_update=%s)",
            records_updated,
            records_failed,
            update_status
        )

        return {
            "status": "success",
            "batch_id": batch_id,
            "records_updated": records_updated,
            "records_failed": records_failed,
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.exception("Error processing completed metadata batch %s: %s", batch_id, e)
        return {"status": "error", "error": str(e)}


def update_batch_status_in_cosmos(
    batch_id: str,
    status: str,
    batch_info: Optional[Dict[str, Any]] = None
) -> None:
    """
    Update batch status in Cosmos DB.

    Args:
        batch_id: Azure OpenAI batch job ID
        status: New status (e.g., "in_progress", "completed", "failed")
        batch_info: Optional batch information to update
    """
    try:
        base_config = get_cosmosdb_config()

        batch_status_config = CosmosDBConfig(
            endpoint=base_config.endpoint,
            database_name=base_config.database_name,
            container_name=base_config.batch_status_container_name,
            connection_mode=base_config.connection_mode,
            enable_diagnostics=base_config.enable_diagnostics,
        )

        with CosmosDBClient(batch_status_config) as client:
            try:
                # Read existing document
                doc = client._container.read_item(
                    item=batch_id, partition_key=batch_id
                )

                # Update status and timestamp
                doc["status"] = status
                doc["updated_at"] = datetime.utcnow().isoformat()

                # Update with batch info if provided
                if batch_info:
                    if "request_counts" in batch_info:
                        doc["request_counts"] = batch_info["request_counts"]
                    if "status" in batch_info:
                        doc["azure_status"] = batch_info["status"]

                client._container.upsert_item(body=doc)
                logger.info("Updated batch status for %s to: %s", batch_id, status)

            except CosmosHttpResponseError as e:
                if e.status_code == 404:
                    logger.warning("Batch status document not found: %s", batch_id)
                else:
                    raise

    except Exception as e:
        logger.exception("Error updating batch status: %s", e)


def process_batch_for_ocr(batch: List[Any], cosmos_container, instance_id: Optional[str] = None, assets: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Process a batch of messages and create an OCR batch job.

    This function orchestrates the full workflow:
    1. Parse batch messages to extract record IDs
    2. Fetch asset details from Cosmos DB (if not provided)
    3. Filter assets with blob URLs
    4. Create Azure OpenAI batch job (only for records with assets)


    Args:
        batch: List of batch messages (can be strings, dicts, etc.)
        cosmos_container: Cosmos DB container client
        instance_id: Optional orchestration instance ID
        assets: Optional pre-extracted assets list. If provided, skips fetching from Cosmos DB.

    Returns:
        Dictionary with processing results:
        - status: "success", "skipped", or "error"
        - batch_id: Batch job ID (if successful)
        - assets_processed: Number of assets processed
        - records_processed: Number of records processed
        - records_with_no_assets: Number of records updated in base container
        - reason: Reason for skipping (if skipped)
        - error: Error message (if error)
    """
    logger.info("Processing batch of %d messages", len(batch))

    try:
        # Step 1: Parse batch messages to extract record IDs
        record_ids = parse_batch_messages(batch)

        if not record_ids:
            logger.warning("No valid record IDs found in batch")
            return {"status": "skipped", "reason": "no_valid_records"}

        logger.info("Processing %d record IDs for batch job creation", len(record_ids))

        # Step 2: Fetch asset details for all records (only if not provided)
        if assets is not None:
            # Use pre-extracted assets to avoid redundant Cosmos DB fetch
            logger.info("Using %d pre-extracted assets (skipping Cosmos DB fetch)", len(assets))
            all_assets = assets
        else:
            # Fetch from Cosmos DB (backward compatibility)
            logger.info("Fetching assets from Cosmos DB for %d records", len(record_ids))
            all_assets = fetch_assets_for_records(record_ids, cosmos_container)

        # Step 3: Identify records with assets vs no assets (for batch creation)
        records_with_assets = set(asset.get("record_id") for asset in all_assets)
        records_with_no_assets = [rid for rid in record_ids if rid not in records_with_assets]

        # Step 3: Create batch job only if there are assets to process
        if not all_assets:
            logger.info(
                "No assets with blob URLs found. %d records have no assets.",
                len(records_with_no_assets)
            )
            return {
                "status": "success",
                "batch_id": None,
                "assets_processed": 0,
                "records_processed": len(record_ids),
                "records_with_no_assets": len(records_with_no_assets),
                "message": "All records had no assets to process"
            }

        logger.info("Found %d assets ready for OCR batch processing", len(all_assets))

        # Step 4: Create batch job using Azure OpenAI batch client (asynchronously)
        batch_result = create_ocr_batch_job(all_assets, instance_id=instance_id)

        batch_id = batch_result.get("batch_id")
        if not batch_id:
            return {
                "status": "error",
                "error": "Failed to create batch job - no batch_id returned"
            }

        logger.info("Batch job created successfully: %s", batch_id)

        # Step 5: Store batch status in Cosmos DB for async processing
        # Only store records that have assets (for batch processing)
        records_with_assets_list = list(records_with_assets)
        try:
            store_batch_status(batch_id, batch_result, records_with_assets_list, all_assets, instance_id=instance_id)
            logger.info("Batch status stored in Cosmos DB for async processing")
        except Exception as e:
            logger.warning("Error storing batch status: %s", e)
            # Continue even if storing status fails

        return {
            "status": "success",
            "batch_id": batch_id,
            "assets_processed": len(all_assets),
            "records_processed": len(record_ids),
            "records_with_no_assets": len(records_with_no_assets),
            "message": "Batch created and queued for async processing"
        }

    except Exception as e:
        logger.exception("Error in process_batch_for_ocr: %s", e)
        return {"status": "error", "error": str(e)}


def create_ocr_batch_job(assets: List[Dict[str, Any]], instance_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Create an Azure OpenAI batch job for OCR processing.

    Args:
        assets: List of asset dictionaries with record_id, asset_id, and blob_url
                Each asset should have:
                - record_id: The content source record ID
                - asset_id: The asset ID
                - blob_url: URL to the image in blob storage
                - asset_name: Optional asset name
        instance_id: Optional orchestration instance ID

    Returns:
        Dictionary with batch job information containing:
        - batch_id: The batch job ID
        - file_id: The input file ID
        - status: Batch job status
        - request_counts: Request count statistics
        - output_file_path: Path to output results file

    Raises:
        Exception: If batch job creation fails
    """
    try:
        # Initialize batch client and prompt loader
        batch_client = AzureOpenAIBatchClient()
        # PromptLoader will automatically find prompts directory relative to its location
        prompt_loader = PromptLoader()
        ocr_prompt = prompt_loader.load_merged_prompt()

        # Get Azure OpenAI config for other settings
        azure_config = AzureConfig.from_env()

        # Get storage config and generate SAS token for blob access
        try:
            sas_token = get_user_delegation_sas(expiry_hours=24)
            if sas_token:
                logger.info("SAS token configured for blob access")
            else:
                logger.warning("No SAS token available (blobs must be public)")
        except Exception as e:
            logger.warning("Failed to get storage config or generate SAS token: %s", e)
            sas_token = None

        # Use deployment name for model field if available, otherwise use model name
        # For Azure OpenAI batch jobs, the model field should be the deployment name
        model_name = batch_client.config.deployment_name or azure_config.model_name

        # The URL in batch requests must be a standard endpoint, not deployment-specific
        # Valid batch endpoint URLs: /v1/chat/completions, /chat/completions, /v1/responses, /responses
        # The deployment name is specified in the model field of the request body
        batch_request_url = "/v1/chat/completions"

        # Use shared helper for asset categorization (sorts assets for consistent ordering)
        _, non_visual_assets, visual_records = categorize_assets_by_type(assets, sort_assets=True)

        logger.info(
            "Grouped visual assets into %d visual records",
            len(visual_records)
        )

        # Track records that fail during preparation
        records_failed_prep: List[Tuple[str, str]] = []

        # Process assets in parallel with semaphore to limit concurrency
        async def process_all_assets() -> List[Dict[str, Any]]:
            """Process all assets in parallel with semaphore control."""
            # Increased semaphore limit for better throughput with high asset counts
            semaphore = asyncio.Semaphore(50)

            async def process_asset(asset: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
                """Process a single asset and return batch request or None if skipped."""
                async with semaphore:
                    custom_id = f"ocr_{asset['record_id']}_{asset['asset_id']}_{idx}"
                    logger.debug(
                        "Processing non-visual asset: record_id=%s, asset_id=%s, idx=%d, custom_id=%s",
                        asset.get("record_id"), asset.get("asset_id"), idx, custom_id
                    )

                    # Ensure blob URL is accessible with SAS token
                    accessible_blob_url = ensure_blob_accessible(asset["blob_url"], sas_token)

                    # Convert file to JPEG data URLs (returns list for multi-page documents)
                    # Run sync function in thread pool to avoid blocking
                    try:
                        image_data_urls = await asyncio.to_thread(
                            convert_file_url_to_jpeg_data_urls,
                            accessible_blob_url
                        )
                    except Exception as conv_err:
                        raise ValueError(
                            f"Record {asset.get('record_id')}, Asset {asset.get('asset_id')}: {conv_err}"
                        ) from conv_err

                    # Validate we got at least one image
                    if not image_data_urls:
                        raise ValueError(
                            f"Record {asset.get('record_id')}, Asset {asset.get('asset_id')}: No images extracted"
                        )

                    # Filter images by Azure OpenAI size limits (configurable via env vars)
                    image_data_urls, _, filter_error = filter_images_by_size(
                        image_data_urls,
                        context_id=asset.get("asset_id"),
                        context_type="asset"
                    )

                    if filter_error:
                        raise ValueError(
                            f"Record {asset.get('record_id')}, Asset {asset.get('asset_id')}: {filter_error}"
                        )

                    if not image_data_urls:
                        raise ValueError(
                            f"Record {asset.get('record_id')}, Asset {asset.get('asset_id')}: All images exceed size limit"
                        )

                    # Azure OpenAI has a limit of 50 images per request
                    # Split images into chunks of 50 if needed
                    MAX_IMAGES_PER_REQUEST = 50
                    image_chunks = []
                    for i in range(0, len(image_data_urls), MAX_IMAGES_PER_REQUEST):
                        image_chunks.append(image_data_urls[i:i + MAX_IMAGES_PER_REQUEST])

                    # If we have more than 50 images, we need to create multiple batch requests
                    if len(image_chunks) > 1:
                        logger.info(
                            "Asset %s has %d images, splitting into %d requests (max %d images per request)",
                            asset.get("asset_id"), len(image_data_urls), len(image_chunks), MAX_IMAGES_PER_REQUEST
                        )

                    # Create batch requests for each chunk
                    batch_requests = []
                    for chunk_idx, image_chunk in enumerate(image_chunks):
                        # Build content array with images from this chunk
                        image_content_items = []
                        for image_data_url in image_chunk:
                            image_content_items.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data_url
                                }
                            })

                        # Create unique custom_id for each chunk
                        chunk_custom_id = f"{custom_id}_chunk{chunk_idx}" if len(image_chunks) > 1 else custom_id

                        # Create the request body for chat completions with vision
                        request_body = {
                            "model": model_name,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": ocr_prompt
                                },
                                {
                                    "role": "user",
                                    "content": image_content_items
                                }
                            ],
                            **get_chat_completion_parameters(
                                azure_config, model_name
                            ),
                        }

                        # Add response_format if supported (API version 2023-12-01-preview or later)
                        api_version_str = azure_config.api_version
                        # Compare version strings (e.g., "2024-02-15-preview" >= "2023-12-01")
                        if api_version_str and api_version_str >= "2023-12-01":
                            request_body["response_format"] = {"type": "json_object"}

                        batch_request = {
                            "custom_id": chunk_custom_id,
                            "method": "POST",
                            "url": batch_request_url,
                            "body": request_body
                        }
                        batch_requests.append(batch_request)

                    # Return list of batch requests (will be flattened later)
                    return batch_requests

            async def process_visual_record(record_id: str, record_assets: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
                """Process a visual record by combining all images from all assets."""
                async with semaphore:
                    # Get resource_type from first asset (should be same for all assets in a record)
                    resource_type = record_assets[0].get("resource_type") if record_assets else None
                    first_asset_id = record_assets[0].get("asset_id") if record_assets else None
                    logger.debug(
                        "Processing visual record: record_id=%s, asset_count=%d, first_asset_id=%s, resource_type=%s",
                        record_id, len(record_assets), first_asset_id, resource_type
                    )

                    # Collect all images from all assets in this record
                    all_image_data_urls = []
                    asset_ids = []

                    for asset in record_assets:
                        asset_id = asset.get("asset_id")
                        asset_ids.append(asset_id)

                        # Ensure blob URL is accessible with SAS token
                        accessible_blob_url = ensure_blob_accessible(asset["blob_url"], sas_token)

                        # Convert file to JPEG data URLs (returns list for multi-page documents)
                        try:
                            image_data_urls = await asyncio.to_thread(
                                convert_file_url_to_jpeg_data_urls,
                                accessible_blob_url
                            )
                        except Exception as conv_err:
                            raise ValueError(
                                f"Record {record_id}, Asset {asset_id}: {conv_err}"
                            ) from conv_err

                        if image_data_urls:
                            all_image_data_urls.extend(image_data_urls)
                        else:
                            logger.warning(
                                "Record %s, Asset %s: No images extracted from %s",
                                record_id, asset_id, accessible_blob_url[:80]
                            )

                    # Filter images by Azure OpenAI size limits (configurable via env vars)
                    all_image_data_urls, _, filter_error = filter_images_by_size(
                        all_image_data_urls,
                        context_id=record_id,
                        context_type="record"
                    )

                    if filter_error:
                        raise ValueError(f"Record {record_id}: {filter_error}")

                    # Validate we got at least one image
                    if not all_image_data_urls:
                        raise ValueError(f"Record {record_id}: No images extracted from any asset")

                    # Azure OpenAI has a limit of 50 images per request
                    MAX_IMAGES_PER_REQUEST = 50
                    image_chunks = []
                    for i in range(0, len(all_image_data_urls), MAX_IMAGES_PER_REQUEST):
                        image_chunks.append(all_image_data_urls[i:i + MAX_IMAGES_PER_REQUEST])

                    if len(image_chunks) > 1:
                        logger.info(
                            "Record %s has %d images, splitting into %d requests (max %d images per request)",
                            record_id, len(all_image_data_urls), len(image_chunks), MAX_IMAGES_PER_REQUEST
                        )

                    # Create batch requests for each chunk
                    # Use first asset for custom_id (same format as _build_asset_mappings with idx=0)
                    first_asset = record_assets[0]
                    custom_id = f"ocr_{first_asset['record_id']}_{first_asset['asset_id']}_0"
                    logger.debug(
                        "Visual record custom_id: %s (using first asset %s with idx=0)",
                        custom_id, first_asset.get("asset_id")
                    )

                    batch_requests = []
                    for chunk_idx, image_chunk in enumerate(image_chunks):
                        image_content_items = []
                        for image_data_url in image_chunk:
                            image_content_items.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data_url,
                                    "detail": "high"  # High detail for better OCR accuracy
                                }
                            })

                        chunk_custom_id = f"{custom_id}_chunk{chunk_idx}" if len(image_chunks) > 1 else custom_id

                        request_body = {
                            "model": model_name,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": ocr_prompt
                                },
                                {
                                    "role": "user",
                                    "content": image_content_items
                                }
                            ],
                            **get_chat_completion_parameters(
                                azure_config, model_name
                            ),
                        }

                        # Add response_format if supported
                        api_version_str = azure_config.api_version
                        if api_version_str and api_version_str >= "2023-12-01":
                            request_body["response_format"] = {"type": "json_object"}

                        batch_request = {
                            "custom_id": chunk_custom_id,
                            "method": "POST",
                            "url": batch_request_url,
                            "body": request_body
                        }
                        batch_requests.append(batch_request)

                    return batch_requests

            # Create tasks for visual records and non-visual assets
            tasks = []

            # Add tasks for visual records (one task per record_id, uses idx=0)
            for record_id, record_assets in visual_records.items():
                tasks.append(process_visual_record(record_id, record_assets))
                logger.debug(
                    "Added visual record task: record_id=%s, asset_count=%d",
                    record_id, len(record_assets)
                )

            # Add tasks for non-visual assets (one task per asset, uses sequential idx)
            for idx, asset in enumerate(non_visual_assets):
                tasks.append(process_asset(asset, idx))
                logger.debug(
                    "Added non-visual asset task: record_id=%s, asset_id=%s, idx=%d",
                    asset.get("record_id"), asset.get("asset_id"), idx
                )

            # Process all assets in parallel - return_exceptions=True to handle errors per-record
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Convert visual_records dict keys to list for index mapping
            visual_record_ids = list(visual_records.keys())

            # Filter out None results, handle exceptions, and flatten nested lists
            # Each result can be either a single batch_request dict, a list of batch_request dicts,
            # or an Exception (if the asset failed)
            batch_requests = []
            for idx, req in enumerate(results):
                if req is None:
                    continue
                if isinstance(req, Exception):
                    # Determine if this is a visual record or non-visual asset based on idx
                    if idx < len(visual_record_ids):
                        # Visual record error - get record_id from dict keys list
                        record_id = visual_record_ids[idx]
                    else:
                        # Non-visual asset error
                        asset_idx = idx - len(visual_record_ids)
                        if asset_idx < len(non_visual_assets):
                            record_id = non_visual_assets[asset_idx].get("record_id")
                        else:
                            record_id = f"unknown_idx_{idx}"
                    logger.error("Record %s: Conversion failed, skipping: %s", record_id, req)
                    records_failed_prep.append((record_id, str(req)))
                    continue
                if isinstance(req, list):
                    # Multiple requests for one asset (split due to image limit)
                    batch_requests.extend(req)
                else:
                    # Single request for one asset
                    batch_requests.append(req)

            return batch_requests

        # Process all assets in parallel - exceptions will bubble up
        logger.info(
            "Processing %d visual records and %d non-visual assets in parallel...",
            len(visual_records), len(non_visual_assets)
        )

        # Handle event loop - check if one is already running
        try:
            # Try to get running loop - will raise RuntimeError if no loop is running
            asyncio.get_running_loop()
            # Event loop is already running, we need to run in a separate thread
            # This is safe because:
            # 1. Each thread gets its own event loop (isolated)
            # 2. Semaphore is created inside the async function (properly scoped)
            # 3. max_workers=1 ensures only one thread (prevents resource exhaustion)
            import concurrent.futures
            def run_in_new_loop():
                """Run async code in a completely new event loop in a separate thread.

                This is safe because:
                - Each thread has its own event loop (no conflicts)
                - Semaphore is scoped to the new event loop
                - Proper cleanup with finally block
                """
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(process_all_assets())
                finally:
                    # Properly close the event loop and clean up
                    try:
                        # Cancel any remaining tasks
                        pending = asyncio.all_tasks(new_loop)
                        for task in pending:
                            task.cancel()
                        # Wait for cancellation to complete
                        if pending:
                            new_loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                    except Exception as cleanup_error:
                        logger.exception("Event loop cleanup encountered an error (expected during cancellation): %s", cleanup_error)
                    finally:
                        new_loop.close()

            # Use ThreadPoolExecutor with max_workers=1 for safety
            # This ensures only one thread is used, preventing resource exhaustion
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_in_new_loop)
                # Wait for completion - this will block, but it's necessary for sync function
                # Exceptions will be properly propagated
                batch_requests = future.result(timeout=None)
        except RuntimeError:
            # No event loop running, safe to use asyncio.run()
            # This creates a new event loop and runs until complete
            batch_requests = asyncio.run(process_all_assets())
        except concurrent.futures.TimeoutError:
            logger.error("Timeout waiting for asset processing to complete")
            raise
        except Exception as e:
            # Ensure all exceptions are properly propagated
            logger.exception("Error processing assets: %s", e)
            raise

        logger.info("Created %d batch requests", len(batch_requests))

        # Mark records that failed during preparation as error in Cosmos DB
        if records_failed_prep:
            _mark_prep_failed_records_as_error(
                records_failed_prep,
                "ocr_batch_status",
                error_message_prefix="OCR preparation failed",
                error_detail_prefix="Could not prepare record for OCR extraction"
            )
            logger.warning(
                "Marked %d records as error due to preparation failures",
                len(records_failed_prep)
            )

        # Generate blob storage paths for input and output
        container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "content-assets")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        input_blob_url = build_blob_url(
            container_name,
            f"batch-jobs/input/batch_{timestamp}.jsonl",
        )
        output_blob_url = build_blob_url(
            container_name,
            f"batch-jobs/output/batch_{timestamp}_results.jsonl",
        )

        # Create JSONL file and upload to blob storage
        input_file_path = batch_client.create_jsonl_from_requests(
            batch_requests, input_blob_url
        )

        logger.info("Created input JSONL file: %s", input_file_path)

        # Upload input file to Azure OpenAI
        file_id = batch_client.upload_input_file(input_file_path)
        logger.info("Uploaded input file. File ID: %s", file_id)

        # Create batch job with retry logic (without waiting for completion)
        max_retries = 5
        batch_id = None
        last_error = None

        for attempt in range(max_retries):
            try:
                metadata = {
                    "source": "content_source_records",
                    "asset_count": str(len(assets)),
                    "created_at": timestamp
                }
                if instance_id:
                    metadata["instance_id"] = instance_id

                batch_id = batch_client.create_batch_job(
                    input_file_id=file_id,
                    completion_window="24h",
                    metadata=metadata
                )
                logger.info("Batch job created successfully: %s (attempt %d/%d)", batch_id, attempt + 1, max_retries)
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Failed to create batch job (attempt %d/%d): %s",
                    attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    # Wait before retrying (exponential backoff: 1s, 2s, 4s, 8s)
                    wait_time = 2 ** attempt
                    logger.info("Retrying in %d seconds...", wait_time)
                    time.sleep(wait_time)
                else:
                    logger.error(
                        "Failed to create batch job after %d attempts. Last error: %s",
                        max_retries, e, exc_info=True
                    )

        # If batch creation failed after all retries, write to Cosmos DB with failed status
        if not batch_id:
            error_message = f"Failed to create batch job after {max_retries} attempts: {str(last_error)}"
            logger.error(error_message)

            # Generate a temporary batch_id for tracking the failure
            failed_batch_id = f"failed_{timestamp}_{len(assets)}_assets"

            # Extract record IDs from assets
            record_ids = list(set(asset.get("record_id") for asset in assets if asset.get("record_id")))

            # Store failed batch status in Cosmos DB
            try:
                store_batch_status(
                    failed_batch_id,
                    {
                        "file_id": file_id,
                        "status": "failed",
                        "input_file_path": input_file_path,
                        "output_file_path": output_blob_url,
                        "error": error_message,
                        "retry_attempts": max_retries,
                    },
                    record_ids,
                    assets,
                    instance_id=instance_id
                )
                # Update status to "failed" explicitly
                update_batch_status_in_cosmos(failed_batch_id, "failed", {
                    "error": error_message,
                    "retry_attempts": max_retries
                })
                logger.info("Stored failed batch status in Cosmos DB: %s", failed_batch_id)
            except (CosmosHttpResponseError, TypeError, ValueError) as store_error:
                logger.exception("Failed to store failed batch status in Cosmos DB: %s", store_error)

            # Raise the exception to propagate the error
            raise Exception(error_message) from last_error

        # Return batch info (without waiting for completion)
        return {
            "batch_id": batch_id,
            "file_id": file_id,
            "status": "created",
            "input_file_path": input_file_path,
            "output_file_path": output_blob_url,
            "request_counts": None,  # Will be updated when polling
            "metadata": {
                "source": "content_source_records",
                "asset_count": str(len(assets)),
                "created_at": timestamp
            }
        }

    except Exception as e:
        logger.exception("Error creating OCR batch job: %s", e)
        raise


async def create_ocr_batch_job_async(assets: List[Dict[str, Any]]) -> Dict[str, Any]:  # noqa: ARG001, F841
    """
    Async version of create_ocr_batch_job that can be called directly from async context.

    This avoids the thread pool overhead and nested event loop creation.

    Asset: {
        "record_id": "",
        "asset_id": "",
        "blob_url": "",
        "asset_name": "",
        "resource_type": "",
    }

    Args:
        assets: List of asset dictionaries with record_id, asset_id, and blob_url
        instance_id: Optional orchestration instance ID

    Returns:
        Dictionary with batch job information containing:
        - batch_id: The batch job ID
        - file_id: The input file ID
        - status: Batch job status ("created" or "error")
        - request_counts: Request count statistics
        - output_file_path: Path to output results file
        - error: Error message if failed
    """
    try:
        # Initialize batch client and prompt loader
        batch_client = AzureOpenAIBatchClient()
        prompt_loader = PromptLoader()
        ocr_prompt = prompt_loader.load_merged_prompt()
        visual_prompt = prompt_loader.load_visual_prompt()

        # Get Azure OpenAI config for other settings
        azure_config = AzureConfig.from_env()

        # Get storage config and generate SAS token for blob access
        try:
            sas_token = get_user_delegation_sas(expiry_hours=24)
            if sas_token:
                logger.info("SAS token configured for blob access")
            else:
                logger.warning("No SAS token available (blobs must be public)")
        except Exception as e:
            logger.warning("Failed to get storage config or generate SAS token: %s", e)
            sas_token = None

        # Use deployment name for model field if available, otherwise use model name
        model_name = batch_client.config.deployment_name or azure_config.model_name
        batch_request_url = "/v1/chat/completions"

        # Process assets in parallel with semaphore to limit concurrency
        # Increased semaphore limit for better throughput
        semaphore = asyncio.Semaphore(50)

        async def process_asset(asset: Dict[str, Any], idx: int) -> Optional[List[Dict[str, Any]]]:
            """Process a single asset and return batch request(s) or None if skipped."""
            async with semaphore:
                custom_id = f"ocr_{asset['record_id']}_{asset['asset_id']}_{idx}"

                # Ensure blob URL is accessible with SAS token
                accessible_blob_url = ensure_blob_accessible(asset["blob_url"], sas_token)

                image_data_urls = await asyncio.to_thread(
                    convert_file_url_to_jpeg_data_urls,
                    accessible_blob_url
                )

                # Validate we got at least one image
                if not image_data_urls:
                    raise ValueError(
                        f"No images extracted from {accessible_blob_url} for asset {asset.get('asset_id')}"
                    )

                # Azure OpenAI has a limit of 50 images per request
                MAX_IMAGES_PER_REQUEST = 50
                image_chunks = []
                for i in range(0, len(image_data_urls), MAX_IMAGES_PER_REQUEST):
                    image_chunks.append(image_data_urls[i:i + MAX_IMAGES_PER_REQUEST])

                if len(image_chunks) > 1:
                    logger.info(
                        "Asset %s has %d images, splitting into %d requests (max %d images per request)",
                        asset.get("asset_id"), len(image_data_urls), len(image_chunks), MAX_IMAGES_PER_REQUEST
                    )

                # Create batch requests for each chunk
                batch_requests = []
                for chunk_idx, image_chunk in enumerate(image_chunks):
                    image_content_items = []
                    for image_data_url in image_chunk:
                        image_content_items.append({
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                                "detail": "high"  # High detail for better OCR accuracy
                            }
                        })

                    chunk_custom_id = f"{custom_id}_chunk{chunk_idx}" if len(image_chunks) > 1 else custom_id

                    request_body = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "system",
                                "content": ocr_prompt
                            },
                            {
                                "role": "user",
                                "content": image_content_items
                            }
                        ],
                        **get_chat_completion_parameters(
                            azure_config, model_name
                        ),
                    }

                    api_version_str = azure_config.api_version
                    if api_version_str and api_version_str >= "2023-12-01":
                        request_body["response_format"] = {"type": "json_object"}

                    batch_request = {
                        "custom_id": chunk_custom_id,
                        "method": "POST",
                        "url": batch_request_url,
                        "body": request_body
                    }
                    batch_requests.append(batch_request)

                return batch_requests

        async def process_visual_record(record_id: str, record_assets: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
            """Process a visual record by combining all images from all assets."""
            async with semaphore:
                # Get resource_type from first asset (should be same for all assets in a record)
                resource_type = record_assets[0].get("resource_type") if record_assets else None

                # Collect all images from all assets in this record
                all_image_data_urls = []
                asset_ids = []

                for asset in record_assets:
                    asset_id = asset.get("asset_id")
                    asset_ids.append(asset_id)

                    # Ensure blob URL is accessible with SAS token
                    accessible_blob_url = ensure_blob_accessible(asset["blob_url"], sas_token)

                    image_data_urls = await asyncio.to_thread(
                        convert_file_url_to_jpeg_data_urls,
                        accessible_blob_url
                    )

                    if image_data_urls:
                        all_image_data_urls.extend(image_data_urls)
                    else:
                        logger.warning(
                            "No images extracted from %s for asset %s in record %s.",
                            accessible_blob_url, asset_id, record_id
                        )

                # Validate we got at least one image
                if not all_image_data_urls:
                    raise ValueError(
                        f"No images extracted from any asset in record {record_id}"
                    )

                logger.info(
                    "Record %s (visual type: %s) has %d total images from %d assets",
                    record_id, resource_type, len(all_image_data_urls), len(record_assets)
                )

                # Azure OpenAI has a limit of 50 images per request
                MAX_IMAGES_PER_REQUEST = 50
                image_chunks = []
                for i in range(0, len(all_image_data_urls), MAX_IMAGES_PER_REQUEST):
                    image_chunks.append(all_image_data_urls[i:i + MAX_IMAGES_PER_REQUEST])

                if len(image_chunks) > 1:
                    logger.info(
                        "Record %s has %d images, splitting into %d requests (max %d images per request)",
                        record_id, len(all_image_data_urls), len(image_chunks), MAX_IMAGES_PER_REQUEST
                    )

                # Create batch requests for each chunk
                # Use first asset for custom_id (same format as process_asset with idx=0)
                first_asset = record_assets[0]
                custom_id = f"ocr_{first_asset['record_id']}_{first_asset['asset_id']}_0"

                batch_requests = []
                for chunk_idx, image_chunk in enumerate(image_chunks):
                    image_content_items = []
                    for image_data_url in image_chunk:
                        image_content_items.append({
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                                "detail": "high"  # High detail for better OCR accuracy
                            }
                        })

                    chunk_custom_id = f"{custom_id}_chunk{chunk_idx}" if len(image_chunks) > 1 else custom_id

                    request_body = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "system",
                                "content": visual_prompt
                            },
                            {
                                "role": "user",
                                "content": image_content_items
                            }
                        ],
                        **get_chat_completion_parameters(
                            azure_config, model_name
                        ),
                    }

                    api_version_str = azure_config.api_version
                    if api_version_str and api_version_str >= "2023-12-01":
                        request_body["response_format"] = {"type": "json_object"}

                    batch_request = {
                        "custom_id": chunk_custom_id,
                        "method": "POST",
                        "url": batch_request_url,
                        "body": request_body
                    }
                    batch_requests.append(batch_request)

                return batch_requests

        # Use shared helper for asset categorization (sorts assets for consistent ordering)
        _, non_visual_assets, visual_records = categorize_assets_by_type(assets, sort_assets=True)

        # Track records that fail during preparation
        records_failed_prep: List[Tuple[str, str]] = []

        logger.info("Grouped visual assets into %d visual records for async batch", len(visual_records))

        # Create tasks for visual records and non-visual assets
        tasks = []

        # Add tasks for visual records (one task per record_id)
        for record_id, record_assets in visual_records.items():
            tasks.append(process_visual_record(record_id, record_assets))
            logger.debug(
                "Added async visual record task: record_id=%s, asset_count=%d",
                record_id, len(record_assets)
            )

        # Add tasks for non-visual assets (one task per asset)
        for idx, asset in enumerate(non_visual_assets):
            tasks.append(process_asset(asset, idx))
            logger.debug(
                "Added async non-visual asset task: record_id=%s, asset_id=%s, idx=%d",
                asset.get("record_id"), asset.get("asset_id"), idx
            )

        logger.info(
            "Processing %d visual records and %d non-visual assets in parallel...",
            len(visual_records), len(non_visual_assets)
        )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert visual_records dict keys to list for index mapping
        visual_record_ids = list(visual_records.keys())

        # Filter out None results, handle exceptions, and flatten nested lists
        batch_requests = []
        for idx, req in enumerate(results):
            if req is None:
                continue
            if isinstance(req, Exception):
                # Determine if this is a visual record or non-visual asset based on idx
                if idx < len(visual_record_ids):
                    record_id = visual_record_ids[idx]
                else:
                    asset_idx = idx - len(visual_record_ids)
                    if asset_idx < len(non_visual_assets):
                        record_id = non_visual_assets[asset_idx].get("record_id")
                    else:
                        record_id = f"unknown_idx_{idx}"
                logger.error("Record %s: Conversion failed, skipping: %s", record_id, req)
                records_failed_prep.append((record_id, str(req)))
                continue
            if isinstance(req, list):
                batch_requests.extend(req)
            else:
                batch_requests.append(req)

        # Mark records that failed during preparation as error in Cosmos DB
        if records_failed_prep:
            _mark_prep_failed_records_as_error(
                records_failed_prep,
                "ocr_batch_status",
                error_message_prefix="OCR preparation failed",
                error_detail_prefix="Could not prepare record for OCR extraction"
            )
            logger.warning(
                "Marked %d records as error due to preparation failures",
                len(records_failed_prep)
            )

        logger.info("Created %d batch requests", len(batch_requests))

        # Use common helper to create and upload batch jobs
        created_batches = await create_and_upload_batch_jobs(
            batch_requests=batch_requests,
            batch_client=batch_client,
            batch_type_prefix="ocr_batch",
            log_description="OCR"
        )

        # Return single batch format for backwards compatibility if only one batch
        if len(created_batches) == 1:
            return {
                "batch_id": created_batches[0]["batch_id"],
                "file_id": created_batches[0]["file_id"],
                "status": "created",
                "request_counts": {
                    "total": len(batch_requests),
                    "assets_processed": len(assets)
                },
                "output_file_path": created_batches[0]["output_file_path"]
            }

        # Return multiple batches info
        return {
            "batch_id": created_batches[0]["batch_id"],  # Primary batch ID for backwards compatibility
            "batch_ids": [b["batch_id"] for b in created_batches],
            "file_ids": [b["file_id"] for b in created_batches],
            "status": "created",
            "request_counts": {
                "total": len(batch_requests),
                "assets_processed": len(assets),
                "batches_created": len(created_batches)
            },
            "batches": created_batches
        }

    except Exception as e:
        logger.exception("Error creating OCR batch job: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "batch_id": None
        }


async def create_metadata_extraction_batch_job_async(
    records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Create an Azure OpenAI batch job for metadata extraction using both images and OCR text.

    Uses vision capabilities for better accuracy by combining:
    - Direct image analysis (high detail) for visual context, layout, handwriting
    - OCR text for searchable content and text that may be clearer in OCR

    Args:
        records: List of record dictionaries with:
            - record_id: The record ID
            - ocr_text: The concatenated OCR text for the record (optional)
            - image_urls: List of image URLs for vision-based extraction (optional)

    Returns:
        Dictionary with batch job information containing:
        - batch_id: The batch job ID
        - file_id: The input file ID
        - status: Batch job status ("created" or "error")
        - request_counts: Request count statistics
        - error: Error message if failed
    """
    try:
        # Initialize batch client and prompt loader
        batch_client = AzureOpenAIBatchClient()
        prompt_loader = PromptLoader()
        system_prompt = prompt_loader.load_prompt("metadata_extr")

        # Get Azure OpenAI config
        azure_config = AzureConfig.from_env()

        # Use deployment name for model field if available
        model_name = batch_client.config.deployment_name or azure_config.model_name
        batch_request_url = "/v1/chat/completions"

        # Calculate token budget for OCR text
        # Model context: 128K tokens (configurable)
        # Reserve: system prompt tokens + max completion tokens + buffer + image tokens
        model_context_limit = int(os.getenv("MODEL_CONTEXT_LIMIT", "128000"))
        system_prompt_tokens = count_tokens(system_prompt)
        max_completion_tokens = azure_config.max_tokens
        # Reserve more buffer for images (high detail images can use ~1000+ tokens each)
        # Limit to 10 images per request to balance accuracy vs token usage
        max_images_per_request = int(os.getenv("METADATA_MAX_IMAGES_PER_REQUEST", "50"))
        image_token_reserve = max_images_per_request * 1500  # ~1500 tokens per high-detail image
        buffer_tokens = 1000  # Safety buffer for user prompt prefix and overhead
        max_ocr_tokens = model_context_limit - system_prompt_tokens - max_completion_tokens - image_token_reserve - buffer_tokens

        logger.info(
            "Token budget: model=%d, system_prompt=%d, max_completion=%d, image_reserve=%d, buffer=%d, available_for_ocr=%d",
            model_context_limit, system_prompt_tokens, max_completion_tokens, image_token_reserve, buffer_tokens, max_ocr_tokens
        )

        # Create batch requests for each record
        batch_requests = []
        records_skipped = []  # Track skipped records with reasons
        for idx, record in enumerate(records):
            record_id = record.get("record_id")
            ocr_text = record.get("ocr_text", "")
            image_urls = record.get("image_urls", [])

            if not ocr_text and not image_urls:
                logger.warning("Record %s has no OCR text or images, skipping", record_id)
                records_skipped.append((record_id, "No OCR text or images available"))
                continue

            # Build user message content (multimodal: images + text)
            user_content = []

            # Add images first (limit to max_images_per_request for token management)
            # Also enforce Azure OpenAI's limits (configurable via MAX_IMAGE_SIZE_MB and MAX_TOTAL_IMAGE_SIZE_MB env vars)
            # If total size exceeds limit after compression, skip images and use OCR text only
            images_to_include = []
            if image_urls:
                images_to_include, total_size_bytes, filter_error = filter_images_by_size(
                    image_urls[:max_images_per_request],
                    context_id=record_id,
                    context_type="record"
                )

                if filter_error:
                    # Images exceed size limit - try with first 5 images for better extraction
                    logger.warning(
                        "Record %s: Image processing failed (%s). Retrying with first 5 images.",
                        record_id, filter_error
                    )
                    images_to_include, total_size_bytes, filter_error_retry = filter_images_by_size(
                        image_urls[:5],
                        context_id=record_id,
                        context_type="record"
                    )
                    if filter_error_retry:
                        # Still failing with 5 images - use OCR text only if available
                        if ocr_text:
                            logger.warning(
                                "Record %s: First 5 images still exceed limit (%s). Using OCR text only.",
                                record_id, filter_error_retry
                            )
                            images_to_include = []
                        else:
                            logger.error("Record %s: Image processing failed and no OCR text available - %s", record_id, filter_error_retry)
                            records_skipped.append((record_id, f"Image size limit exceeded and no OCR text: {filter_error_retry}"))
                            continue

                elif len(images_to_include) < len(image_urls):
                    logger.info(
                        "Record %s: Limited images from %d to %d (%.2fMB) for size/token budget",
                        record_id, len(image_urls), len(images_to_include), total_size_bytes / (1024 * 1024)
                    )

            for img_url in images_to_include:
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": img_url,
                        "detail": "high"  # High detail for better metadata extraction
                    }
                })

            # Add OCR text if available (truncated to fit token budget)
            # Note: Use images_to_include (not image_urls) since images may have been skipped due to size limit
            text_instruction = "Extract metadata from the document"
            if ocr_text:
                original_tokens = count_tokens(ocr_text)
                truncated_text = truncate_to_token_limit(ocr_text, max_ocr_tokens)
                truncated_tokens = count_tokens(truncated_text)

                if original_tokens > max_ocr_tokens:
                    logger.info(
                        "Record %s: OCR text truncated from %d to %d tokens",
                        record_id, original_tokens, truncated_tokens
                    )

                if images_to_include:
                    # Both images and OCR text available
                    text_instruction = f"Extract metadata from the document images above. The following OCR text is also provided for reference:\n\n{truncated_text}"
                else:
                    # Only OCR text available (images may have been skipped due to size limit)
                    text_instruction = f"Extract metadata from the following OCR text:\n\n{truncated_text}"
            elif images_to_include:
                # Only images available
                text_instruction = "Extract metadata from the document images above. Analyze the visual content, layout, and any text visible in the images."

            user_content.append({
                "type": "text",
                "text": text_instruction
            })

            custom_id = f"metadata_{record_id}_{idx}"

            batch_request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": batch_request_url,
                "body": {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    **get_chat_completion_parameters(
                        azure_config, model_name
                    ),
                    "response_format": {"type": "json_object"}
                }
            }
            batch_requests.append(batch_request)

            logger.debug(
                "Record %s: Created metadata request with %d images and %s OCR text",
                record_id, len(images_to_include),
                "with" if ocr_text else "without"
            )

        if not batch_requests:
            # Build detailed error message using helper
            error_msg = _build_grouped_error_message(
                records_skipped,
                "No valid records for metadata extraction"
            ) if records_skipped else "No valid records for metadata extraction (no records provided)"

            logger.warning("No valid records for metadata extraction batch: %s", error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "batch_id": None,
                "records_skipped": len(records_skipped),
                "skipped_record_details": records_skipped[:10]
            }

        logger.info("Created %d batch requests for metadata extraction", len(batch_requests))

        # Use common helper to create and upload batch jobs
        created_batches = await create_and_upload_batch_jobs(
            batch_requests=batch_requests,
            batch_client=batch_client,
            batch_type_prefix="metadata_extraction",
            log_description="metadata extraction"
        )

        # Get IDs of records that were skipped during preparation
        skipped_record_ids = [rid for rid, _ in records_skipped]

        # Return single batch format for backwards compatibility if only one batch
        if len(created_batches) == 1:
            return {
                "batch_id": created_batches[0]["batch_id"],
                "file_id": created_batches[0]["file_id"],
                "status": "created",
                "request_counts": {
                    "total": len(batch_requests),
                    "records_processed": len(records) - len(records_skipped)
                },
                "output_file_path": created_batches[0]["output_file_path"],
                "records_skipped_prep": skipped_record_ids  # Records skipped during prep
            }

        # Return multiple batches info
        return {
            "batch_id": created_batches[0]["batch_id"],  # Primary batch ID for backwards compatibility
            "batch_ids": [b["batch_id"] for b in created_batches],
            "file_ids": [b["file_id"] for b in created_batches],
            "status": "created",
            "request_counts": {
                "total": len(batch_requests),
                "records_processed": len(records) - len(records_skipped),
                "batches_created": len(created_batches)
            },
            "batches": created_batches,
            "records_skipped_prep": skipped_record_ids  # Records skipped during prep
        }

    except Exception as e:
        logger.exception("Error creating metadata extraction batch job: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "batch_id": None
        }


def _parse_resource_type_custom_id(custom_id: str) -> Optional[str]:
    """Parse record_id from visual extraction batch custom_id. Delegates to unified parser."""
    return parse_record_id_from_custom_id(custom_id)


def _process_resource_type_batch_errors(batch_id: str, error_file_path: str, record_ids: List[str]) -> Dict[str, Any]:
    """
    Process error file for visual extraction batch and mark all records as error.

    Args:
        batch_id: Batch job ID
        error_file_path: Path to error file
        record_ids: List of record IDs in the batch

    Returns:
        Dictionary with processing results
    """
    from . import content_source_client

    return _process_generic_batch_errors(
        batch_id=batch_id,
        error_file_path=error_file_path,
        record_ids=record_ids,
        batch_type=BatchType.RESOURCE_TYPE,
        custom_id_parser=_parse_resource_type_custom_id,
        cosmos_container=content_source_client.container,
        skip_if_completed=True,
        completed_field="resource_type",
    )


def process_completed_resource_type_batch(batch_id: str, cosmos_container, update_status: bool = True) -> Dict[str, Any]:
    """
    Process a completed visual extraction batch by retrieving results and writing to Cosmos DB.

    Args:
        batch_id: Azure OpenAI batch job ID
        cosmos_container: Cosmos DB container client for source (content source) container
        update_status: If True, update batch status to "completed" in Cosmos DB after processing.

    Returns:
        Dictionary with processing results
    """
    from . import content_source_client

    try:
        # Get batch status from Cosmos DB
        batch_status = get_batch_status_from_cosmos(batch_id)
        if not batch_status:
            logger.error("Batch status not found in Cosmos DB: %s", batch_id)
            return {"status": "error", "error": "Batch status not found"}

        record_ids = batch_status.get("record_ids", [])

        # Helper function to mark all records as error (using consolidated helper)
        def mark_all_records_error(error_message: str, error_detail: str):
            _mark_records_error_safe(
                record_ids=record_ids,
                cosmos_container=cosmos_container,
                status_field="resource_type_processing_status",
                error_message=error_message,
                error_detail=error_detail,
                batch_id=batch_id,
                check_completed_field="resource_type",  # Also check if resource_type is populated
            )

        # Initialize batch client
        batch_client = AzureOpenAIBatchClient()

        # 1. Validate batch status first
        try:
            _validate_batch_status(batch_id, batch_client)
        except Exception as e:
            logger.exception("Batch validation failed for %s: %s", batch_id, e)
            mark_all_records_error("Batch validation failed", f"The batch job is not in a completed state: {str(e)}.")
            return {"status": "error", "error": f"Batch validation failed: {str(e)}"}

        # 2. Retrieve batch results file (or error file if output not found)
        try:
            output_file_path = _retrieve_batch_results_file(batch_id, batch_client)
        except Exception as e:
            # Check if error file exists
            logger.warning("Failed to retrieve output file for visual extraction batch: %s. Checking for error file...", e)
            batch = batch_client.get_batch_status(batch_id)
            error_file_id = getattr(batch, "error_file_id", None)
            output_file_id = getattr(batch, "output_file_id", None)

            # Check if records already have resource_type (results were already processed)
            records_already_processed = []
            for record_id in record_ids:
                try:
                    record_data = cosmos_container.read_item(
                        item=record_id, partition_key=record_id
                    )
                    if record_data.get("resource_type") and record_data.get("resource_type_processing_status") == "completed":
                        records_already_processed.append(record_id)
                        logger.info("Record %s already has resource_type, skipping", record_id)
                except Exception as check_err:
                    logger.warning("Failed to check record %s: %s", record_id, check_err)

            # If all records are already processed, return success
            if len(records_already_processed) == len(record_ids):
                logger.info("All records in batch %s are already processed, marking as completed", batch_id)
                if update_status:
                    update_batch_status_in_cosmos(batch_id, "completed", {
                        "status": "completed",
                        "records_updated": len(records_already_processed),
                        "already_processed": True
                    })
                return {
                    "status": "success",
                    "batch_id": batch_id,
                    "records_updated": len(records_already_processed),
                    "records_failed": 0,
                    "already_processed": True
                }

            if error_file_id:
                logger.info("Found error file for visual extraction batch %s. Processing errors...", batch_id)
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                result = _process_resource_type_batch_errors(batch_id, error_file_path, record_ids)
                if update_status:
                    update_batch_status_in_cosmos(batch_id, "completed", {"status": "completed", "had_errors": True})
                return result
            else:
                # Check if this is a 404 error and if we can proceed with already processed records
                error_str = str(e).lower()
                if "404" in error_str or "not found" in error_str:
                    if records_already_processed:
                        logger.warning(
                            "Batch %s output file not found (404), but %d/%d records already processed. "
                            "Marking processed records as success and others as error.",
                            batch_id, len(records_already_processed), len(record_ids)
                        )
                        # Mark unprocessed records as error (only if they don't already have resource_type)
                        unprocessed_records = [rid for rid in record_ids if rid not in records_already_processed]
                        for record_id in unprocessed_records:
                            try:
                                # Check if record already has resource_type
                                record_data = cosmos_container.read_item(item=record_id, partition_key=record_id)
                                if record_data.get("resource_type") and record_data.get("resource_type_processing_status") == "completed":
                                    logger.info("Record %s already has resource_type '%s', skipping error marking", record_id, record_data.get("resource_type"))
                                    records_already_processed.append(record_id)
                                    continue
                            except Exception as check_err:
                                logger.warning("Failed to check record %s before marking error: %s", record_id, check_err)

                            mark_all_records_error("Batch retrieval failed", f"Failed to retrieve batch results: {str(e)}.")

                        if update_status:
                            update_batch_status_in_cosmos(batch_id, "completed", {
                                "status": "completed",
                                "records_updated": len(records_already_processed),
                                "records_failed": len(unprocessed_records),
                                "partial_success": True
                            })
                        return {
                            "status": "partial_success",
                            "batch_id": batch_id,
                            "records_updated": len(records_already_processed),
                            "records_failed": len(unprocessed_records),
                            "error": f"Output file not found, but {len(records_already_processed)} records were already processed"
                        }

                # No error file either, mark records as error (skipping those already completed/error)
                logger.error("No output file or error file found for visual extraction batch %s", batch_id)

                records_marked, records_skipped = _mark_records_error_safe(
                    record_ids=record_ids,
                    cosmos_container=cosmos_container,
                    status_field="resource_type_processing_status",
                    error_message="Batch retrieval failed",
                    error_detail=f"Failed to retrieve batch results: {str(e)}.",
                    batch_id=batch_id,
                    check_completed_field="resource_type",
                )

                if records_skipped > 0:
                    return {
                        "status": "partial_success",
                        "batch_id": batch_id,
                        "records_updated": records_skipped,  # Records that were already completed
                        "records_failed": records_marked,
                        "error": f"Failed to get batch results: {str(e)}"
                    }

                return {"status": "error", "error": f"Failed to get batch results: {str(e)}"}

        # 3. Check if this is actually an error file
        batch = batch_client.get_batch_status(batch_id)
        error_file_id = getattr(batch, "error_file_id", None)
        output_file_id = getattr(batch, "output_file_id", None)

        # If error file exists but output file doesn't, process errors
        if error_file_id and not output_file_id:
            logger.warning("Visual extraction batch %s has error file but no output file. Processing errors...", batch_id)
            error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
            result = _process_resource_type_batch_errors(batch_id, error_file_path, record_ids)
            if update_status:
                update_batch_status_in_cosmos(batch_id, "completed", {"status": "completed", "had_errors": True})
            return result

        # 4. Parse batch results (normal case)
        results = parse_batch_results(output_file_path)

        # Clean up temp file
        _safe_cleanup_temp_file(output_file_path)

        if not results:
            logger.error("No results returned for visual extraction batch %s", batch_id)
            mark_all_records_error("No batch results", "The batch processing did not return any results.")
            return {"status": "error", "error": "No results from batch"}

        # Parse results and update records
        records_updated = 0
        records_failed = 0
        errors = []

        # Build a map of custom_id to result for quick lookup
        # For visual extraction, we may have multiple chunks per record, so we need to merge them
        results_by_record = {}
        for result in results:
            custom_id = result.get("custom_id", "")
            record_id = _parse_resource_type_custom_id(custom_id)
            if record_id:
                if record_id not in results_by_record:
                    results_by_record[record_id] = []
                results_by_record[record_id].append(result)

        # Check for error file to get actual error messages for missing/failed records
        errors_from_file = {}
        if error_file_id:
            try:
                logger.info("Checking error file for details on failed records...")
                error_file_path = _retrieve_batch_error_file(batch_id, error_file_id, batch_client)
                error_results = parse_batch_results(error_file_path)

                # Parse errors and map to records
                for error_result in error_results:
                    custom_id = error_result.get("custom_id", "")
                    rec_id = _parse_resource_type_custom_id(custom_id)
                    if rec_id:
                        error_msg = _extract_error_message_from_batch_result(error_result)
                        errors_from_file[rec_id] = error_msg

                # Clean up error file
                _safe_cleanup_temp_file(error_file_path)
            except Exception as e:
                logger.warning("Failed to retrieve error file for failed records: %s", e)

        for record_id in record_ids:
            try:
                # Check if record already has resource_type (may have been processed in a previous run)
                try:
                    existing_record = cosmos_container.read_item(
                        item=record_id, partition_key=record_id
                    )
                    if existing_record.get("resource_type") and existing_record.get("resource_type_processing_status") == "completed":
                        logger.info("Record %s already has resource_type '%s', skipping", record_id, existing_record.get("resource_type"))
                        records_updated += 1
                        continue
                except Exception as check_err:
                    logger.warning("Failed to check existing record %s: %s", record_id, check_err)
                    # Continue with processing

                # Find all results for this record (may have multiple chunks)
                record_results = results_by_record.get(record_id, [])

                if not record_results:
                    logger.warning("No result found for record %s in batch %s", record_id, batch_id)
                    # Check if record already has resource_type before marking as error
                    try:
                        check_record = cosmos_container.read_item(item=record_id, partition_key=record_id)
                        if check_record.get("resource_type") and check_record.get("resource_type_processing_status") == "completed":
                            logger.info("Record %s has no result but already has resource_type '%s', skipping error marking",
                                       record_id, check_record.get("resource_type"))
                            records_updated += 1
                            continue
                    except Exception as check_err:
                        logger.warning("Failed to check record %s: %s", record_id, check_err)

                    # Use error from error file if available
                    if record_id in errors_from_file:
                        error_from_file = errors_from_file[record_id]
                        error_detail = f"Visual extraction failed: {error_from_file}. Please contact support with Batch ID: {batch_id}, Record ID: {record_id}"
                    else:
                        error_detail = f"The batch processing did not return a result for this record. Batch ID: {batch_id}, Record ID: {record_id}"
                    content_source_client.update_record_status(
                        record_id, "resource_type_processing_status", "error", None,
                        error_reason={"message": "No result in batch", "detail": error_detail}
                    )
                    records_failed += 1
                    continue

                # Process all chunks and extract resource types
                # If multiple chunks, we'll use the first valid result (or merge if needed)
                resource_type = None
                for record_result in record_results:
                    response = record_result.get("response", {})
                    body = response.get("body", {})
                    choices = body.get("choices", [])

                    if not choices:
                        continue

                    content = choices[0].get("message", {}).get("content", "")
                    if not content:
                        continue

                    # Parse JSON from content
                    try:
                        # Strip markdown code blocks if present
                        cleaned_content = content.strip()
                        if cleaned_content.startswith("```"):
                            lines = cleaned_content.split('\n')
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].strip() == "```":
                                lines = lines[:-1]
                            cleaned_content = '\n'.join(lines).strip()

                        result_data = json.loads(cleaned_content)

                        # Extract resource_type from the result
                        # The prompt should return a JSON with "resource_type" field
                        extracted_resource_type = result_data.get("resource_type")
                        if extracted_resource_type:
                            resource_type = extracted_resource_type
                            break  # Use first valid result
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse JSON for record %s chunk: %s", record_id, e)
                        continue

                if not resource_type:
                    logger.error("No resource type extracted for record %s", record_id)
                    content_source_client.update_record_status(
                        record_id, "resource_type_processing_status", "error", None,
                        error_reason={"message": "Invalid response", "detail": f"The AI model did not return a valid resource type. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1
                    continue

                # Update record with extracted resource type
                try:
                    record_data = cosmos_container.read_item(
                        item=record_id, partition_key=record_id
                    )

                    record_data["resource_type"] = resource_type
                    record_data["resource_type_processing_status"] = "completed"
                    record_data["last_processed"] = datetime.utcnow().isoformat()

                    # Upsert with retry for automatic 429 handling
                    result = upsert_with_retry(
                        cosmos_container=cosmos_container,
                        item=record_data,
                        record_id=record_id,
                        operation_name="resource_type_results"
                    )
                    if result["success"]:
                        records_updated += 1
                        logger.info("Updated record %s with resource type: %s", record_id, resource_type)
                    else:
                        # Mark record as error when save fails
                        error_msg = result.get("error", "Unknown error")
                        logger.error("Failed to save resource type for record %s: %s", record_id, error_msg)
                        content_source_client.update_record_status(
                            record_id, "resource_type_processing_status", "error", None,
                            error_reason={"message": "Save failed", "detail": f"We extracted the resource type but failed to save: {error_msg}. Batch ID: {batch_id}, Record ID: {record_id}"}
                        )
                        records_failed += 1

                except Exception as e:
                    logger.exception("Failed to update record %s with resource type: %s", record_id, e)
                    content_source_client.update_record_status(
                        record_id, "resource_type_processing_status", "error", None,
                        error_reason={"message": "Save failed", "detail": f"Failed to save resource type: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                    records_failed += 1

            except Exception as e:
                logger.exception("Error processing record %s from batch %s: %s", record_id, batch_id, e)
                # Mark the record as error
                try:
                    content_source_client.update_record_status(
                        record_id, "resource_type_processing_status", "error", None,
                        error_reason={"message": "Processing error", "detail": f"An error occurred while processing the batch result: {str(e)}. Batch ID: {batch_id}, Record ID: {record_id}"}
                    )
                except Exception as status_err:
                    logger.exception("Failed to update error status for record %s: %s", record_id, status_err)
                records_failed += 1
                errors.append(f"Record {record_id}: {str(e)}")

        # Update batch status if requested
        if update_status:
            update_batch_status_in_cosmos(batch_id, "completed", {
                "request_counts": {
                    "records_updated": records_updated,
                    "records_failed": records_failed
                },
                "status": "completed"
            })

        logger.info(
            "Completed visual extraction batch processing: %d records updated, %d failed (status_update=%s)",
            records_updated,
            records_failed,
            update_status
        )

        return {
            "status": "success",
            "batch_id": batch_id,
            "records_updated": records_updated,
            "records_failed": records_failed,
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.exception("Error processing completed visual extraction batch %s: %s", batch_id, e)
        return {"status": "error", "error": str(e)}


async def create_resource_type_batch_job_async(
    records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Create an Azure OpenAI batch job for resource type classification from images.

    Args:
        records: List of full record dictionaries from Cosmos DB with:
            - record_id: The record ID
            - asset_details: Array of asset objects, each with:
                - asset_id: The asset ID
                - blob_url: The image blob URL

    Returns:
        Dictionary with batch job information containing:
        - batch_id: The batch job ID
        - file_id: The input file ID
        - status: Batch job status ("created" or "error")
        - request_counts: Request count statistics
        - error: Error message if failed
    """
    try:
        # Initialize batch client and prompt loader
        batch_client = AzureOpenAIBatchClient()
        prompt_loader = PromptLoader()
        system_prompt = prompt_loader.load_prompt("resource_type_extraction")

        # Get Azure OpenAI config
        azure_config = AzureConfig.from_env()

        # Get storage config and generate SAS token for blob access
        try:
            sas_token = get_user_delegation_sas(expiry_hours=24)
            if sas_token:
                logger.info("SAS token configured for blob access")
            else:
                logger.warning("No SAS token available (blobs must be public)")
        except Exception as e:
            logger.warning("Failed to get storage config or generate SAS token: %s", e)
            sas_token = None

        # Use deployment name for model field if available
        model_name = batch_client.config.deployment_name or azure_config.model_name
        batch_request_url = "/v1/chat/completions"

        # Process records in parallel with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(50)

        # Track records that fail during preparation so we can mark them as error
        # This prevents records from staying "pending" forever when they can't be processed
        from . import content_source_client
        records_failed_prep = []

        async def process_record(record: Dict[str, Any], idx: int) -> Optional[List[Dict[str, Any]]]:
            """Process a single record and return batch request(s) or None if skipped."""
            async with semaphore:
                record_id = record.get("record_id")
                asset_details = record.get("asset_details", [])

                if not asset_details:
                    logger.warning("Record %s has no asset_details, skipping", record_id)
                    records_failed_prep.append((record_id, "No asset_details available"))
                    return None

                # For resource type, limit to 5 images per record to keep batch size manageable
                MAX_IMAGES_PER_RECORD = 5
                all_image_data_urls = []

                # Get max file size limit from environment
                import requests as req
                max_file_size_mb = float(os.getenv("MAX_FILE_SIZE_MB", "3072"))

                for asset in asset_details:
                    blob_url = asset.get("blob_url")

                    # Only process assets that have blob_url
                    if not blob_url:
                        continue

                    # Ensure blob URL is accessible with SAS token
                    accessible_blob_url = ensure_blob_accessible(blob_url, sas_token)

                    # Pre-check file size to avoid OOM crashes
                    # This check happens BEFORE downloading to prevent memory exhaustion
                    try:
                        head_response = req.head(accessible_blob_url, timeout=30, allow_redirects=True)
                        content_length = head_response.headers.get("Content-Length")
                        if content_length:
                            file_size_mb = int(content_length) / (1024 * 1024)
                            if file_size_mb > max_file_size_mb:
                                raise ValueError(
                                    f"File too large: {file_size_mb:.1f}MB exceeds limit of {max_file_size_mb}MB"
                                )
                    except req.exceptions.RequestException:
                        pass  # If HEAD fails, proceed and let conversion handle it

                    # Convert file to JPEG data URLs (returns list for multi-page documents)
                    try:
                        converted_urls = await asyncio.to_thread(
                            convert_file_url_to_jpeg_data_urls,
                            accessible_blob_url
                        )
                        if converted_urls:
                            all_image_data_urls.extend(converted_urls)
                    except Exception as conv_err:
                        # Fail the record on conversion error
                        raise ValueError(
                            f"Record {record_id}, Asset {asset.get('asset_id')}: {conv_err}"
                        ) from conv_err

                # Filter images by Azure OpenAI size limits (configurable via env vars)
                # Also limit to MAX_IMAGES_PER_RECORD for resource type
                all_image_data_urls, _, filter_error = filter_images_by_size(
                    all_image_data_urls,
                    max_images=MAX_IMAGES_PER_RECORD,
                    context_id=record_id,
                    context_type="record"
                )

                if filter_error:
                    raise ValueError(f"Record {record_id}: Image size limit exceeded: {filter_error}")

                if not all_image_data_urls:
                    raise ValueError(f"Record {record_id}: No convertible images found")

                # Build image content items
                image_content_items = [
                    {"type": "image_url", "image_url": {"url": img_url}}
                    for img_url in all_image_data_urls
                ]

                custom_id = f"visual_{record_id}_{idx}"
                user_prompt = "Analyze the provided image(s) and determine the resource type of the document(s)."

                batch_request = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": batch_request_url,
                    "body": {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": user_prompt}
                                ] + image_content_items
                            }
                        ],
                        **get_chat_completion_parameters(
                            azure_config, model_name
                        ),
                        "response_format": {"type": "json_object"}
                    }
                }

                return [batch_request]

        # Process all records in parallel
        tasks = [process_record(record, idx) for idx, record in enumerate(records)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten results and handle errors
        batch_requests = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                record_id = records[idx].get("record_id")
                logger.exception("Error processing record %s: %s", record_id, result)
                records_failed_prep.append((record_id, str(result)))
                continue
            if result:
                batch_requests.extend(result)

        # Mark records that failed during preparation as error in Cosmos DB
        # This prevents them from staying "pending" forever
        _mark_prep_failed_records_as_error(
            records_failed_prep,
            "resource_type_batch_status",
            error_message_prefix="Preparation failed",
            error_detail_prefix="Could not prepare record for resource type extraction"
        )

        if not batch_requests:
            # Build detailed error message using helper
            error_msg = _build_grouped_error_message(
                records_failed_prep,
                "No valid records with images"
            ) if records_failed_prep else "No valid records with images (no records provided or all returned empty results)"

            logger.warning("No valid records for visual extraction batch: %s", error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "batch_id": None,
                "records_failed": len(records_failed_prep),
                "failed_record_details": records_failed_prep[:10]  # Include first 10 for debugging
            }

        logger.info("Created %d batch requests for visual extraction", len(batch_requests))

        # Use common helper to create and upload batch jobs
        created_batches = await create_and_upload_batch_jobs(
            batch_requests=batch_requests,
            batch_client=batch_client,
            batch_type_prefix="resource_type_batch",
            log_description="visual extraction"
        )

        # Get IDs of records that failed during preparation (already marked as error)
        failed_record_ids = [rid for rid, _ in records_failed_prep]

        # Return single batch format for backwards compatibility if only one batch
        if len(created_batches) == 1:
            return {
                "batch_id": created_batches[0]["batch_id"],
                "file_id": created_batches[0]["file_id"],
                "status": "created",
                "request_counts": {
                    "total": len(batch_requests),
                    "records_processed": len(records) - len(records_failed_prep)
                },
                "input_file_path": created_batches[0]["input_file_path"],
                "output_file_path": created_batches[0]["output_file_path"],
                "records_failed_prep": failed_record_ids  # Records already marked as error
            }

        # Return multiple batches info
        return {
            "batch_id": created_batches[0]["batch_id"],  # Primary batch ID for backwards compatibility
            "batch_ids": [b["batch_id"] for b in created_batches],
            "file_ids": [b["file_id"] for b in created_batches],
            "status": "created",
            "request_counts": {
                "total": len(batch_requests),
                "records_processed": len(records) - len(records_failed_prep),
                "batches_created": len(created_batches)
            },
            "batches": created_batches,
            "records_failed_prep": failed_record_ids  # Records already marked as error
        }

    except Exception as e:
        logger.exception("Error creating visual extraction batch job: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "batch_id": None
        }


def extract_resource_type_from_record(record_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract resource_type from record data with fallback logic.

    Args:
        record_data: Record data dictionary from Cosmos DB

    Returns:
        Resource type string or None if not found
    """
    metadata = record_data.get("metadata", {})
    if isinstance(metadata, dict) and "Resource Type" in metadata:
        return metadata.get("Resource Type")
    return record_data.get("resource_type")


def create_asset_dict(
    record_id: str,
    asset: Dict[str, Any],
    resource_type: Optional[str],
    use_thumbnail: bool = False
) -> Dict[str, Any]:
    """
    Create a standardized asset dictionary for OCR processing.

    Args:
        record_id: The content source record ID
        asset: Asset dictionary from record_data
        resource_type: Resource type from record metadata
        use_thumbnail: If True, fall back to blob_thumbnail_url if blob_url is not available

    Returns:
        Asset dictionary with record_id, asset_id, blob_url, asset_name, and resource_type
    """
    blob_url = asset.get("blob_url")
    if use_thumbnail and not blob_url:
        blob_url = asset.get("blob_thumbnail_url")

    return {
        "record_id": record_id,
        "asset_id": asset.get("asset_id"),
        "blob_url": blob_url,
        "asset_name": asset.get("name", ""),
        "resource_type": resource_type,
    }
