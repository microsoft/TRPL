# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pydantic models for API request/response schemas."""
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, ConfigDict, field_validator


class Item(BaseModel):
    """Model for item data."""

    item_id: int
    name: str


class ItemCreate(BaseModel):
    """Model for creating a new item."""

    name: str


class ItemResponse(BaseModel):
    """Response model for item operations."""

    item_id: int
    name: str
    status: str = "created"

# Document schemas for CosmosDB

class DocumentOCRResult(BaseModel):
    """OCR result structure for assets."""
    ocr_text_flexible_blob_url: Optional[str] = None
    ocr_text_original_blob_url: Optional[str] = None
    ocr_text: Optional[str] = None
    ocr_text_original: Optional[str] = None
    confidence: Optional[float] = None
    entities: Optional[List[Dict[str, Any]]] = None
    confidence_scores: Optional[Dict[str, float]] = None
    processing_time: Optional[float] = None
    processed_at: Optional[str] = None

class AssetDetail(BaseModel):
    """Individual asset within a document."""
    record_id: Optional[str] = None
    asset_id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    blob_url: Optional[str] = None
    blob_thumbnail_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    file_url: Optional[str] = None
    filename: Optional[str] = None
    ocr_result: Optional[DocumentOCRResult] = None
    metadata: Optional[Dict[str, Any]] = None

class DocumentMetadataFields(BaseModel):
    """Nested metadata structure matching Cosmos DB."""
    Title: Optional[str] = None
    Description: Optional[str] = None
    Creator: Optional[str] = None
    Recipient: Optional[str] = None
    Repository: Optional[str] = None
    Collection: Optional[str] = None
    Language: Optional[str] = None
    Period: Optional[str] = None
    Identifier: Optional[str] = None
    model_config = ConfigDict(extra="allow")  # Allow other metadata fields

class DocumentMetadata(BaseModel):
    """Complete document schema matching Cosmos DB structure."""
    id: str
    record_id: Optional[str] = None
    title: Optional[str] = None  # Denormalized from metadata.Title
    type: Optional[str] = None
    type_label: Optional[str] = None
    published: Optional[bool] = False

    # Nested metadata - Dict to allow all fields including custom ones like "Surrogate.Reel"
    metadata: Optional[Dict[str, Any]] = None

    # Asset information
    related_assets: Optional[List[str]] = None
    asset_details: Optional[List[AssetDetail]] = None
    asset_count: Optional[int] = None
    asset_avg_confidence: Optional[float] = None

    # Status fields
    ocr_processing_status: Optional[str] = None

    # Archivist workflow
    archivist_notes: Optional[str] = None
    archivist_status: Optional[str] = None
    archivist_modified_ts: Optional[str] = None

    @field_validator("archivist_status", mode="before")
    @classmethod
    def lowercase_archivist_status(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s.lower()
    
    # Review/Publish tracking
    validated_by: Optional[str] = None  # Display name of validator
    validated_at: Optional[str] = None  # ISO timestamp when validated
    published_by: Optional[str] = None  # Display name of publisher
    published_at: Optional[str] = None  # ISO timestamp when published
    archivist_publish_started_at: Optional[str] = None  # ISO when publish pipeline started (stuck detection)
    archivist_error_message: Optional[str] = None  # Last publish/ingestion failure message

    # Versioning
    version: Optional[int] = 1

    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    processed_at: Optional[str] = None
    last_processed: Optional[str] = None

    # Legacy fields (for backward compatibility)
    image_url: Optional[str] = None
    storage_path: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    error_message: Optional[str] = None

    # CosmosDB system fields
    _rid: Optional[str] = None
    _self: Optional[str] = None
    _etag: Optional[str] = None
    _attachments: Optional[str] = None
    _ts: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class DocumentList(BaseModel):
    """Response model for list of documents."""

    documents: list[DocumentMetadata]
    count: int


class DocumentStatistics(BaseModel):
    """Statistics about documents in the database."""

    total: int
    pending: int
    processing: int
    completed: int
    failed: int

class CollectionSummary(BaseModel):
    """Summary statistics for a collection/repository pair."""

    repository: str
    collection: str
    totalItems: int
    pending: int
    reviewed: int = 0
    publishing: int = 0
    published: int
    errors: int = 0
    completionRate: float
    avgOcrConfidence: Optional[float] = None

class CollectionSummariesResponse(BaseModel):
    """Response model for collection summaries."""

    summaries: list[CollectionSummary]
    count: int

class RepositoryStatistics(BaseModel):
    """Statistics for a repository."""

    repository: str
    totalItems: int
    collections: int
    reviewed: int
    pending: int
    publishing: int = 0
    published: int
    errors: int = 0
    completionRate: float

class RepositoryStatisticsResponse(BaseModel):
    """Response model for repository statistics."""

    repositories: list[RepositoryStatistics]
    count: int

class RepositoryStatisticsSummary(BaseModel):
    """Overall statistics summary for repositories page."""

    totalRepositories: int
    totalItems: int
    totalCollections: int
    totalPending: int
    totalReviewed: int
    totalPublishing: int = 0
    totalPublished: int
    totalErrors: int = 0
    overallCompletionRate: float

class CollectionDetailsResponse(BaseModel):
    """Response model for collection details."""

    collections: list[CollectionSummary]
    count: int

class RepositoryCollectionStatisticsSummary(BaseModel):
    """Overall statistics summary for a repository's collections."""

    totalCollections: int
    totalItems: int
    totalPending: int
    totalReviewed: int = 0
    totalPublishing: int = 0
    totalPublished: int
    totalErrors: int = 0
    overallCompletionRate: float

class RecordIdsResponse(BaseModel):
    """Response model for record IDs."""

    record_ids: list[str]
    count: int

class DashboardStatistics(BaseModel):
    """Dashboard statistics."""

    totalItems: int
    totalCollections: int
    totalPending: int
    totalPublishing: int = 0
    totalPublished: int
    totalRepositories: int
    totalReviewed: int
    totalErrors: int = 0
    overallCompletionRate: float


# =============================================================================
# EPUB Processing Schemas
# =============================================================================


class EpubSection(BaseModel):
    """Represents a section of an EPUB book."""
    type: str  # 'prologue', 'chapter', 'epilogue', 'foreword', 'afterword', 'introduction', 'other'
    title: str
    content: str = ""
    word_count: int = 0
    order: int = 0
    selected: bool = True  # Whether user selected this section for ingestion
    html_content: Optional[str] = None
    level: int = 1  # Heading level (1 = top, 2 = subsection, etc.)
    parent_order: Optional[int] = None  # Order of parent section (for nested)
    href: str = ""  # Link to content (for lazy loading)
    children: List["EpubSection"] = []  # Nested sub-sections


# Rebuild model to resolve forward reference for children
EpubSection.model_rebuild()


class CreatorInfo(BaseModel):
    """Author/contributor information with sorting and role."""
    name: str
    fileAs: Optional[str] = ""  # Sort name (e.g., "Morris, Edmund")
    role: Optional[str] = ""  # Role code (e.g., "aut" for author)


class EpubMetadata(BaseModel):
    """Comprehensive EPUB book metadata from OPF file."""
    # Core metadata
    title: str
    titleSort: Optional[str] = ""
    author: str  # Alias for creator
    creator: Optional[str] = ""
    creatorInfo: Optional[CreatorInfo] = None
    contributors: Optional[List[CreatorInfo]] = []
    publisher: Optional[str] = ""
    language: Optional[str] = ""
    description: Optional[str] = ""
    subjects: Optional[List[str]] = []
    rights: Optional[str] = ""
    date: Optional[str] = ""

    # Identifiers
    identifier: Optional[str] = ""
    isbn: Optional[str] = ""
    uuid: Optional[str] = ""
    asin: Optional[str] = ""
    googleId: Optional[str] = ""
    calibreId: Optional[str] = ""
    identifiers: Optional[Dict[str, str]] = {}

    # Series info
    series: Optional[str] = ""
    seriesIndex: Optional[str] = ""

    # Calibre metadata
    calibreTimestamp: Optional[str] = ""

    # Additional DC fields
    source: Optional[str] = ""
    relation: Optional[str] = ""
    coverage: Optional[str] = ""
    type: Optional[str] = ""
    format: Optional[str] = ""

    # Custom metadata
    custom: Optional[Dict[str, str]] = {}
    # Computed fields (set during parsing)
    total_word_count: int = 0
    chapter_count: int = 0


class EpubFilterConfig(BaseModel):
    """Filter configuration for EPUB content extraction."""
    exclude_tags: List[str] = []
    exclude_classes: List[str] = []
    exclude_ids: List[str] = []
    class_patterns: List[str] = []

    model_config = ConfigDict(extra="allow")


class EpubDocument(BaseModel):
    """Complete EPUB document for CosmosDB storage."""
    id: str  # Document ID (UUID)
    filename: str
    blob_url: str  # URL to the original EPUB file in storage
    # Status flow: uploaded -> parsing -> parsed -> extracting -> validate -> processing -> completed
    status: str = "uploaded"
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    parsed_at: Optional[str] = None
    extracted_at: Optional[str] = None
    validated_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Metadata
    metadata: Optional[EpubMetadata] = None

    # Parsed sections
    sections: Optional[List[EpubSection]] = None

    # Processing info
    total_sections: int = 0
    selected_sections: int = 0
    selected_word_count: int = 0

    # Extracted text URLs (populated after extraction)
    original_text_url: Optional[str] = None  # URL to original (unfiltered) text
    filtered_text_url: Optional[str] = None  # URL to filtered text (for ingestion)

    # Custom filter configuration (optional, uses defaults if not set)
    filter_config: Optional[EpubFilterConfig] = None

    model_config = ConfigDict(extra="allow")


class EpubUploadResponse(BaseModel):
    """Response after EPUB upload."""
    id: str
    filename: str
    status: str
    message: str


class EpubDocumentList(BaseModel):
    """List of EPUB documents."""
    documents: List[EpubDocument]
    count: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class EpubSectionSelection(BaseModel):
    """Request to update section selections."""
    sections: List[Dict[str, Any]]  # List of {order: int, selected: bool}


class EpubIngestRequest(BaseModel):
    """Request to start ingestion of selected sections."""
    document_id: str


# =============================================================================
# Field Mapping Configuration Schemas
# =============================================================================


class FieldMapping(BaseModel):
    """Field mapping configuration for dynamic identifier display per repository/collection."""
    id: str  # Format: field_mapping_{repository}_{collection} or field_mapping_{repository}
    type: str = "field_mapping"  # Document type identifier
    repository: str  # Repository name
    collection: Optional[str] = None  # Optional collection name (if None, applies to all collections in repository)
    identifier_field: str  # The metadata field path to use for Identifier column (e.g., "Identifier", "record_id", "source_record_id")
    display_name: Optional[str] = None  # Optional custom display name for the identifier column
    created_at: str
    updated_at: str
    created_by: Optional[str] = None  # User who created the mapping

    model_config = ConfigDict(extra="allow")


class FieldMappingCreate(BaseModel):
    """Request model for creating a new field mapping."""
    repository: str
    collection: Optional[str] = None
    identifier_field: str
    display_name: Optional[str] = None


class FieldMappingUpdate(BaseModel):
    """Request model for updating a field mapping."""
    identifier_field: Optional[str] = None
    display_name: Optional[str] = None


class FieldMappingResponse(BaseModel):
    """Response model for a single field mapping."""
    mapping: FieldMapping


class FieldMappingListResponse(BaseModel):
    """Response model for list of field mappings."""
    mappings: List[FieldMapping]
    count: int


class MetadataFieldsResponse(BaseModel):
    """Response model for available metadata fields."""
    fields: List[str]
    count: int
