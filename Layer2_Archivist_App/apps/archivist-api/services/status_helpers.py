# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Status helper functions for document categorization.

Shared utilities for categorizing document status based on archivist_status
and pipeline completion status. Used by both cosmos_service and statistics_service.
"""

from typing import Dict, Any, Optional


def normalize_archivist_status(value: Any) -> str:
    """
    Canonical archivist_status in Cosmos and API responses: trimmed lowercase.

    Missing, null, or whitespace-only input becomes 'pending' (aligned with Data Foundations ingest).
    """
    if value is None:
        return "pending"
    if isinstance(value, str):
        s = value.strip().lower()
        return s if s else "pending"
    s = str(value).strip().lower()
    return s if s else "pending"

# Pipeline status fields to check for completion
PIPELINE_STATUS_FIELDS = [
    # Standard field names (lowercase)
    ("related_assets_status", "RelatedAssetsStatus"),
    ("asset_details_status", "AssetDetailsStatus"),
    ("original_file_status", "OriginalFileStatus"),
    ("ocr_batch_status", "OcrBatchStatus"),
    ("ocr_processing_status", "OcrProcessingStatus"),
    ("metadata_extraction_status", "MetadataExtractionStatus"),
    ("resource_type_batch_status", "ResourceTypeBatchStatus"),
    ("resource_type_processing_status", "ResourceTypeProcessingStatus"),
    ("metadata_batchs_status", "MetadataBatchsStatus"),
]


def all_pipeline_stages_completed(doc: Dict[str, Any]) -> bool:
    """
    Check if all pipeline stages are completed.
    
    Args:
        doc: Document dictionary with pipeline status fields
        
    Returns:
        True if all pipeline stages have 'completed' status
    """
    stages = []
    for lowercase_key, pascal_key in PIPELINE_STATUS_FIELDS:
        # Support both naming conventions (PascalCase from queries, lowercase from documents)
        value = doc.get(pascal_key) or doc.get(lowercase_key)
        stages.append(value)
    
    # All defined stages must be 'completed'
    return all(
        (s or "").lower() == "completed" 
        for s in stages 
        if s is not None and s != ""
    )


def categorize_document_status(doc: Dict[str, Any]) -> str:
    """
    Categorize a document into one of: published, publishing, reviewed, pending, or error.
    
    For published/publishing/reviewed statuses, returns as-is.
    For pending/empty status:
      - Returns 'pending' if all pipeline stages are completed
      - Returns 'error' if any pipeline stage is not completed
    
    Args:
        doc: Document dictionary with archivist_status and pipeline status fields
        
    Returns:
        Normalized status category (lowercase)
    """
    # Support both naming conventions
    status = normalize_archivist_status(
        doc.get("ArchivistStatus") or doc.get("archivist_status")
    )
    
    if status == "published":
        return "published"
    if status == "publishing":
        return "publishing"
    if status == "reviewed":
        return "reviewed"
    if status == "failed":
        return "error"

    # For pending (including missing archivist_status, normalized to pending):
    if all_pipeline_stages_completed(doc):
        return "pending"
    return "error"


def increment_status_counters(
    counters: Dict[str, int], 
    doc: Dict[str, Any]
) -> None:
    """
    Increment the appropriate status counter based on document status.
    
    Modifies counters dict in place. Expects counters to have 'pending', 
    'publishing', 'reviewed', 'published', and 'error' keys.
    
    Args:
        counters: Dict with status keys
        doc: Document dictionary with archivist_status and pipeline status fields
    """
    category = categorize_document_status(doc)
    counters[category] = counters.get(category, 0) + 1
