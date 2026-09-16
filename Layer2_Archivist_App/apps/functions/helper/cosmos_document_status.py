# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Document status categorization for statistics rebuilds in Azure Functions.

Mirrors ``archivist-api/services/status_helpers.py`` (keep in sync when pipeline fields change).
"""

from typing import Any, Dict

from helper.archivist_status import normalize_archivist_status

PIPELINE_STATUS_FIELDS = [
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
    stages = []
    for lowercase_key, pascal_key in PIPELINE_STATUS_FIELDS:
        value = doc.get(pascal_key) or doc.get(lowercase_key)
        stages.append(value)
    return all(
        (s or "").lower() == "completed"
        for s in stages
        if s is not None and s != ""
    )


def categorize_document_status(doc: Dict[str, Any]) -> str:
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
    if all_pipeline_stages_completed(doc):
        return "pending"
    return "error"
