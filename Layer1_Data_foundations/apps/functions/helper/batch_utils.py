# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Batch processing utility functions.

Common utilities shared between batch processing modules to avoid code duplication.
"""

import json
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def parse_record_id_from_custom_id(custom_id: str) -> Optional[str]:
    """
    Parse record_id from any batch custom_id format.

    Supports all batch types:
    - OCR: ocr_{record_id}_{asset_id}_{idx} or ocr_{record_id}_{asset_id}_{idx}_chunk{N}
    - Metadata: metadata_{record_id}_{idx}
    - Visual/Resource type: visual_{record_id}_{idx} or visual_{record_id}_{idx}_chunk{N}

    Args:
        custom_id: Custom ID string from batch result

    Returns:
        Record ID string or None if parsing fails
    """
    if not custom_id:
        return None

    # Remove chunk suffix if present
    if "_chunk" in custom_id:
        custom_id = custom_id.split("_chunk")[0]

    parts = custom_id.split("_")
    if len(parts) < 2:
        return None

    prefix = parts[0]

    if prefix == "metadata":
        # Format: metadata_{record_id}_{idx}
        return "_".join(parts[1:-1]) if len(parts) > 2 else parts[1]

    if prefix == "visual":
        # Format: visual_{record_id}_{idx}
        if len(parts) >= 3:
            try:
                int(parts[-1])  # Check if last part is numeric (idx)
                return "_".join(parts[1:-1])
            except ValueError:
                pass
        return parts[1] if len(parts) > 1 else None

    if prefix == "ocr":
        # Format: ocr_{record_id}_{asset_id}_{idx}
        if len(parts) >= 4:
            return "_".join(parts[1:-2])
        return parts[1] if len(parts) > 1 else None

    return None


def split_requests_by_size(
    requests: List[Dict[str, Any]],
    max_size_mb: int = 100
) -> List[List[Dict[str, Any]]]:
    """
    Split batch requests into chunks based on estimated file size.
    Ensures all requests for the same record stay together in the same batch.

    Args:
        requests: List of request dictionaries
        max_size_mb: Maximum size per batch file in MB (default: 100)

    Returns:
        List of request chunks, each chunk will produce a file under max_size_mb
    """
    if not requests:
        return []

    max_size_bytes = max_size_mb * 1024 * 1024

    # Group requests by record_id to keep all requests for a record together
    from collections import OrderedDict
    record_groups = OrderedDict()  # Preserve order

    for request in requests:
        custom_id = request.get("custom_id", "")
        record_id = parse_record_id_from_custom_id(custom_id) or custom_id

        if record_id not in record_groups:
            record_groups[record_id] = []
        record_groups[record_id].append(request)

    # Calculate size for each record group
    record_sizes = {}
    for record_id, record_requests in record_groups.items():
        total_size = 0
        for req in record_requests:
            req_json = json.dumps(req, ensure_ascii=False)
            total_size += len(req_json.encode('utf-8')) + 1  # +1 for newline
        record_sizes[record_id] = total_size

    # Split into chunks, keeping records together
    chunks = []
    current_chunk = []
    current_size = 0

    for record_id, record_requests in record_groups.items():
        record_size = record_sizes[record_id]

        # Warn if a single record exceeds the max size
        if record_size > max_size_bytes:
            logger.warning(
                "Record %s has requests totaling %.2fMB which exceeds max batch size of %dMB. "
                "Consider reducing image count or quality for this record.",
                record_id, record_size / (1024 * 1024), max_size_mb
            )

        # If adding this record would exceed max size, start a new chunk
        # (unless current chunk is empty - we must include at least one record per chunk)
        if current_size + record_size > max_size_bytes and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0

        # Add all requests for this record to the current chunk
        current_chunk.extend(record_requests)
        current_size += record_size

    # Add the last chunk if not empty
    if current_chunk:
        chunks.append(current_chunk)

    if len(chunks) > 1:
        logger.info(
            "Split %d requests (%d records) into %d batches (max %d MB per batch)",
            len(requests), len(record_groups), len(chunks), max_size_mb
        )

    return chunks
