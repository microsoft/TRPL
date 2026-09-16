# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Response models for the Approved Records API.

Shapes follow the content source sync API design (Section 5).
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

IncludeContent = Literal["inline", "reference", "none"]
ContentType = Literal["ocr", "visual_description"]


class MetadataField(BaseModel):
    """A single archivist-editable, source-originated metadata field."""

    source_key: str = Field(description="Source system internal property key.")
    display_name: str = Field(description="Human-readable field name.")
    value: Optional[str] = Field(default=None, description="Field value.")


class RecordMetadata(BaseModel):
    fields: List[MetadataField] = Field(default_factory=list)


class OcrAsset(BaseModel):
    asset_id: str
    sequence: int
    ocr_text: Optional[str] = None
    ocr_text_url: Optional[str] = None
    size_bytes: int = 0
    confidence: Optional[float] = None


class OcrContent(BaseModel):
    assets: List[OcrAsset] = Field(default_factory=list)


class VisualContent(BaseModel):
    summary: Optional[str] = None
    detailed_description: Optional[str] = None
    summary_url: Optional[str] = None
    detailed_description_url: Optional[str] = None


class ApprovedRecord(BaseModel):
    """A single record entry returned by the collection / single-record endpoints."""

    record_id: str
    archivist_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    metadata: RecordMetadata = Field(default_factory=RecordMetadata)
    content_type: Optional[ContentType] = None
    content: Optional[Union[OcrContent, VisualContent]] = None


class ApprovedQueryEcho(BaseModel):
    approved_from: str
    approved_to: str
    include_content: IncludeContent
    page_size: int


class ApprovedRecordsResponse(BaseModel):
    query: ApprovedQueryEcho
    total_count: int
    records: List[ApprovedRecord] = Field(default_factory=list)
    continuation_token: Optional[str] = None
    has_more: bool = False


class UnapprovedRecord(BaseModel):
    record_id: str
    unapproved_at: Optional[str] = None
    current_status: Optional[str] = None


class UnapprovedQueryEcho(BaseModel):
    unapproved_from: str
    unapproved_to: str
    page_size: int


class UnapprovedRecordsResponse(BaseModel):
    query: UnapprovedQueryEcho
    total_count: int
    records: List[UnapprovedRecord] = Field(default_factory=list)
    continuation_token: Optional[str] = None
    has_more: bool = False


class HealthResponse(BaseModel):
    status: str = "healthy"
    service: str = "content-export-api"
    version: str = "1.0.0"
    checks: Optional[Dict[str, str]] = Field(
        default=None,
        description="Dependency readiness (e.g. cosmos=ok).",
    )
