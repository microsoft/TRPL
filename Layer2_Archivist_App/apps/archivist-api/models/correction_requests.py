"""Pydantic models for correction requests API."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class CorrectionRequestCreate(BaseModel):
    """Intake body; accepts snake_case or camelCase from downstream clients."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    record_id: str = Field(..., min_length=1, validation_alias=AliasChoices("record_id", "recordId"))
    note: str = Field(..., min_length=1)
    source: Optional[str] = Field(
        default=None,
        description="Optional channel label (e.g. ReadingRoom); auth still required.",
    )


class CorrectionRequestCreated(BaseModel):
    """Response shape after creating a correction request (unread)."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str
    record_id: str = Field(validation_alias=AliasChoices("record_id", "recordId"))
    status: Literal["unread"] = "unread"
    timestamp: str
    received_at: str
    source_app_id: str
    source_display_name: str


class ActorInfo(BaseModel):
    """User who reviewed or dismissed a request (embedded in Cosmos)."""

    user_id: str = ""
    email: str = ""
    display_name: str = ""


class CorrectionRequestOut(BaseModel):
    """Single correction request as returned by list/detail APIs."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    record_id: str
    note: str
    source: str
    status: Literal["unread", "reviewed", "dismissed"]
    timestamp: str
    received_at: str
    is_deleted: bool = False
    source_app_id: str
    source_display_name: str
    record_available: bool = True
    caller_app_id: Optional[str] = None
    dismissed_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    dismissed_by: Optional[Dict[str, Any]] = None
    reviewed_by: Optional[Dict[str, Any]] = None
    dismissal_note: Optional[str] = None
    record_title: Optional[str] = None
    record_repository: Optional[str] = None
    record_collection: Optional[str] = None


class CorrectionRequestListResponse(BaseModel):
    """Paginated list of correction requests."""

    items: List[CorrectionRequestOut]
    next_cursor: Optional[str] = None
    has_more: bool = False
    total: Optional[int] = None
    offset: int = 0
    limit: int = 50
    sort: str = "desc"


class CorrectionRequestPatch(BaseModel):
    """Mark reviewed or dismiss (soft-delete). Legacy `addressed` accepted as alias for reviewed."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["reviewed", "dismissed", "addressed"]
    dismissal_note: Optional[str] = None
