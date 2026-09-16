# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provider-neutral content-source contracts for the TRPL showcase.

These interfaces intentionally describe only behavior exercised by this reference
implementation. Security maintenance preserves this documented behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, Mapping, Optional, Protocol, Sequence, TypeAlias, TypeVar


JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonPrimitive | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
)


class ContentSourceErrorCode(str, Enum):
    """Stable categories for adapter failures; messages remain implementation-owned."""

    CONFIGURATION = "configuration"
    INVALID_DATA = "invalid_data"
    INVALID_QUERY = "invalid_query"
    NOT_FOUND = "not_found"
    CONTENT_UNAVAILABLE = "content_unavailable"
    SOURCE_FAILURE = "source_failure"
    UNSUPPORTED_ADAPTER = "unsupported_adapter"


class ContentSourceError(Exception):
    """Structured adapter error safe to pass between orchestration stages."""

    def __init__(
        self,
        code: ContentSourceErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ContentMetadata:
    """JSON-compatible, provider-neutral metadata keyed by display name."""

    values: Mapping[str, JsonValue] = field(default_factory=dict)

    def as_dict(self) -> dict[str, JsonValue]:
        return dict(self.values)


@dataclass(frozen=True)
class RightsMetadata:
    """Rights information required for every record and asset."""

    status: str
    statement: str
    license_url: str
    credit_line: str
    restrictions: Sequence[str] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "statement": self.statement,
            "license_url": self.license_url,
            "credit_line": self.credit_line,
            "restrictions": list(self.restrictions),
        }


@dataclass(frozen=True)
class ContentAsset:
    """Asset metadata. Binary content is read separately by identifier."""

    id: str
    record_id: str
    name: str
    media_type: str
    sequence: int
    metadata: ContentMetadata
    rights: RightsMetadata


@dataclass(frozen=True)
class AssetContent:
    """Bytes returned directly by an adapter, never a caller-fetchable URL."""

    asset_id: str
    filename: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ContentRecord:
    """Canonical record returned by every content-source adapter."""

    id: str
    source_identifier: str
    title: str
    record_type: str
    summary: str
    collection_id: str
    record_url: str
    published: bool
    created_at: datetime
    updated_at: datetime
    metadata: ContentMetadata
    rights: RightsMetadata
    asset_ids: Sequence[str] = ()


@dataclass(frozen=True)
class ContentRecordQuery:
    """Record query with an adapter-owned opaque continuation cursor."""

    page_size: int = 100
    cursor: Optional[str] = None
    collection_ids: Sequence[str] = ()
    updated_from: Optional[datetime] = None
    updated_to: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 500:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "page_size must be between 1 and 500",
                details={"page_size": self.page_size},
            )
        naive_bounds = [
            name
            for name, value in (
                ("updated_from", self.updated_from),
                ("updated_to", self.updated_to),
            )
            if value is not None
            and (value.tzinfo is None or value.utcoffset() is None)
        ]
        if naive_bounds:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "updated date bounds must include a UTC offset",
                details={"fields": naive_bounds},
            )
        if (
            self.updated_from is not None
            and self.updated_to is not None
            and self.updated_from > self.updated_to
        ):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "updated_from must not be later than updated_to",
            )


T = TypeVar("T")


@dataclass(frozen=True)
class OpaquePage(Generic[T]):
    """One page whose cursor is interpreted only by the producing adapter."""

    items: Sequence[T]
    next_cursor: Optional[str]
    total: Optional[int] = None


class ContentSourceAdapter(Protocol):
    """Contract adopters implement from their own authoritative source semantics."""

    async def query_records(self, query: ContentRecordQuery) -> OpaquePage[ContentRecord]:
        ...

    async def get_record(self, record_id: str) -> ContentRecord:
        ...

    async def query_assets(
        self,
        record_id: str,
        *,
        page_size: int = 100,
        cursor: Optional[str] = None,
    ) -> OpaquePage[ContentAsset]:
        ...

    async def get_asset(self, record_id: str, asset_id: str) -> ContentAsset:
        ...

    async def get_asset_content(
        self,
        record_id: str,
        asset_id: str,
    ) -> AssetContent:
        ...
