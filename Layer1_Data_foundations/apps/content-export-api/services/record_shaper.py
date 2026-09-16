# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Transforms raw Cosmos records into Approved Records API response shapes.

Implements the field mapping, content-type discriminator, and content delivery
modes exercised by the generic export API tests.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from core.config import EDITABLE_METADATA_FIELDS, settings
from models.schemas import (
    ApprovedRecord,
    ContentType,
    IncludeContent,
    MetadataField,
    OcrAsset,
    OcrContent,
    RecordMetadata,
    UnapprovedRecord,
    VisualContent,
)
from services import blob_service


def _record_id(doc: Dict[str, Any]) -> str:
    return str(doc.get("record_id") or doc.get("id") or "")


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def shape_metadata(doc: Dict[str, Any]) -> RecordMetadata:
    """Return only the archivist-editable, source-originated fields with source keys."""
    metadata = doc.get("metadata") or {}
    metadata_key = doc.get("metadata_key") or {}
    # Invert {source_key: display_name} -> {display_name: source_key}.
    display_to_source: Dict[str, str] = {}
    for source_key, display_name in metadata_key.items():
        if isinstance(display_name, str) and display_name not in display_to_source:
            display_to_source[display_name] = source_key

    fields: List[MetadataField] = []
    for display_name in EDITABLE_METADATA_FIELDS:
        source_key = display_to_source.get(display_name)
        if not source_key:
            # No mapping for this editable field on this record (Section 9.9): omit.
            continue
        fields.append(
            MetadataField(
                source_key=source_key,
                display_name=display_name,
                value=_str_or_none(metadata.get(display_name)),
            )
        )
    return RecordMetadata(fields=fields)


def _content_type(doc: Dict[str, Any]) -> ContentType:
    return "visual_description" if doc.get("visual_description_possible") == "Y" else "ocr"


def _ocr_content(doc: Dict[str, Any], include_content: IncludeContent) -> OcrContent:
    record_id = _record_id(doc)
    assets: List[OcrAsset] = []
    for sequence, asset in enumerate(doc.get("asset_details") or [], start=1):
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("asset_id") or "")
        ocr_result = asset.get("ocr_result") or {}
        confidence_scores = ocr_result.get("confidence_scores") or {}
        confidence = confidence_scores.get("overall_confidence")

        # OCR text lives in blob storage (ocr_result.ocr_text_flexible_blob_url),
        # not inline on the Cosmos record. Prefer the stored URL; fall back to the
        # derived path for older records that predate the stored field.
        blob_url = ocr_result.get(
            "ocr_text_flexible_blob_url"
        ) or blob_service.build_ocr_text_blob_url(record_id, asset_id)

        if include_content == "reference":
            # reference: null text, SAS-signed URL for parallel download.
            inline_text = None
        else:  # inline: hydrate full text from the blob (URL also populated).
            inline_text = blob_service.read_text(blob_url)
        url = blob_service.sas_url(blob_url)

        size_bytes = len(inline_text.encode("utf-8")) if isinstance(inline_text, str) else 0
        assets.append(
            OcrAsset(
                asset_id=asset_id,
                sequence=sequence,
                ocr_text=inline_text,
                ocr_text_url=url,
                size_bytes=size_bytes,
                confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            )
        )
    return OcrContent(assets=assets)


def _visual_content(doc: Dict[str, Any], include_content: IncludeContent) -> VisualContent:
    summary = doc.get("visual_summary_description_flexible")
    detailed_url = doc.get("visual_detailed_description_flexible_blob_url")
    summary_url = doc.get("visual_summary_description_flexible_blob_url")

    if include_content == "reference":
        return VisualContent(
            summary=None,
            detailed_description=None,
            summary_url=blob_service.sas_url(summary_url),
            detailed_description_url=blob_service.sas_url(detailed_url),
        )
    # inline: detailed description lives only as a blob — hydrate it.
    return VisualContent(
        summary=_str_or_none(summary),
        detailed_description=blob_service.read_text(detailed_url),
        summary_url=blob_service.sas_url(summary_url),
        detailed_description_url=blob_service.sas_url(detailed_url),
    )


def shape_record(doc: Dict[str, Any], include_content: IncludeContent) -> ApprovedRecord:
    content_type = _content_type(doc)
    content: Optional[Union[OcrContent, VisualContent]] = None
    if include_content != "none":
        if content_type == "visual_description":
            content = _visual_content(doc, include_content)
        else:
            content = _ocr_content(doc, include_content)

    return ApprovedRecord(
        record_id=_record_id(doc),
        archivist_status=_str_or_none(doc.get("archivist_status")),
        approved_by=_str_or_none(doc.get("published_by")),
        # External field approved_at is backed by the pipeline's published_at.
        approved_at=_str_or_none(doc.get("published_at")),
        metadata=shape_metadata(doc),
        content_type=content_type,
        content=content,
    )


def shape_unapproved(doc: Dict[str, Any]) -> UnapprovedRecord:
    return UnapprovedRecord(
        record_id=_record_id(doc),
        # External field unapproved_at is backed by the pipeline's unpublished_at.
        unapproved_at=_str_or_none(doc.get("unpublished_at")),
        current_status=_str_or_none(doc.get("archivist_status")),
    )
