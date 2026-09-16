# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic offline adapter backed by the repository's fictional sample pack."""

from __future__ import annotations

import base64
import binascii
import importlib.resources
import json
import struct
import zlib
from datetime import datetime
from typing import Any, Optional, Sequence

from .contracts import (
    AssetContent,
    ContentAsset,
    ContentMetadata,
    ContentRecord,
    ContentRecordQuery,
    ContentSourceError,
    ContentSourceErrorCode,
    OpaquePage,
    RightsMetadata,
)


DEFAULT_SAMPLE_PACK = importlib.resources.files(__package__).joinpath(
    "data",
    "fictional-content-source-pack.json",
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    )


def generate_synthetic_png(payload: object, label: str) -> bytes:
    """Generate a deterministic RGB PNG without shipping media or rasterizing SVG."""

    required = {"generator", "width", "height", "rgb"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{label}.content must define a supported PNG generator",
        )
    width = payload["width"]
    height = payload["height"]
    rgb = payload["rgb"]
    if (
        payload["generator"] != "solid_png"
        or isinstance(width, bool)
        or not isinstance(width, int)
        or not 1 <= width <= 2048
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 1 <= height <= 2048
        or not isinstance(rgb, list)
        or len(rgb) != 3
        or any(
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not 0 <= channel <= 255
            for channel in rgb
        )
    ):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{label}.content PNG generator settings are invalid",
        )

    scanline = b"\x00" + bytes(rgb) * width
    raw_pixels = scanline * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            PNG_SIGNATURE,
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(raw_pixels, level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


def _parse_datetime(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{field_name} must be an ISO-8601 datetime",
        ) from exc
    return parsed


def _rights(payload: object, label: str) -> RightsMetadata:
    if not isinstance(payload, dict):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{label}.rights must be an object",
        )
    required = ("status", "statement", "license_url", "credit_line")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{label}.rights is missing required fields",
            details={"fields": missing},
        )
    restrictions = payload.get("restrictions") or []
    if not isinstance(restrictions, list):
        raise ContentSourceError(
            ContentSourceErrorCode.INVALID_DATA,
            f"{label}.rights.restrictions must be an array",
        )
    return RightsMetadata(
        status=str(payload["status"]),
        statement=str(payload["statement"]),
        license_url=str(payload["license_url"]),
        credit_line=str(payload["credit_line"]),
        restrictions=tuple(str(value) for value in restrictions),
    )


class SyntheticContentSourceAdapter:
    """The only adapter implementation shipped by this repository."""

    def __init__(self, sample_pack=DEFAULT_SAMPLE_PACK) -> None:
        self._sample_pack = sample_pack
        try:
            payload = json.loads(self._sample_pack.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentSourceError(
                ContentSourceErrorCode.CONFIGURATION,
                "The synthetic content-source sample pack could not be loaded",
                details={"path": str(self._sample_pack)},
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                "The synthetic sample pack must use schema_version 1.0",
            )
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                "The synthetic sample pack records field must be an array",
            )
        self._raw_records: dict[str, dict[str, Any]] = {}
        self._records: dict[str, ContentRecord] = {}
        self._assets: dict[tuple[str, str], ContentAsset] = {}
        for raw_record in raw_records:
            record = self._load_record(raw_record)
            if record.id in self._records:
                raise ContentSourceError(
                    ContentSourceErrorCode.INVALID_DATA,
                    "Synthetic record identifiers must be unique",
                    details={"record_id": record.id},
                )
            self._records[record.id] = record
            self._raw_records[record.id] = raw_record

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        payload = json.dumps({"offset": offset}, separators=(",", ":"), sort_keys=True)
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: Optional[str]) -> int:
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            offset = payload["offset"]
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("invalid offset")
            return offset
        except (
            binascii.Error,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "The continuation cursor is invalid",
            ) from exc

    def _load_record(self, payload: object) -> ContentRecord:
        if not isinstance(payload, dict):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                "Each synthetic record must be an object",
            )
        required = (
            "id",
            "source_identifier",
            "title",
            "record_type",
            "summary",
            "collection_id",
            "record_url",
            "created_at",
            "updated_at",
            "metadata",
            "rights",
            "assets",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                "A synthetic record is missing required fields",
                details={"fields": missing},
            )
        record_id = str(payload["id"])
        raw_assets = payload["assets"]
        if not isinstance(raw_assets, list):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"record {record_id} assets must be an array",
            )
        asset_ids = []
        for raw_asset in raw_assets:
            asset = self._load_asset(record_id, raw_asset)
            key = (record_id, asset.id)
            if key in self._assets:
                raise ContentSourceError(
                    ContentSourceErrorCode.INVALID_DATA,
                    "Synthetic asset identifiers must be unique within a record",
                    details={"record_id": record_id, "asset_id": asset.id},
                )
            self._assets[key] = asset
            asset_ids.append(asset.id)
        metadata = payload["metadata"]
        if not isinstance(metadata, dict):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"record {record_id} metadata must be an object",
            )
        return ContentRecord(
            id=record_id,
            source_identifier=str(payload["source_identifier"]),
            title=str(payload["title"]),
            record_type=str(payload["record_type"]),
            summary=str(payload["summary"]),
            collection_id=str(payload["collection_id"]),
            record_url=str(payload["record_url"]),
            published=bool(payload.get("published", False)),
            created_at=_parse_datetime(payload["created_at"], "created_at"),
            updated_at=_parse_datetime(payload["updated_at"], "updated_at"),
            metadata=ContentMetadata(metadata),
            rights=_rights(payload["rights"], f"record {record_id}"),
            asset_ids=tuple(asset_ids),
        )

    @staticmethod
    def _load_asset(record_id: str, payload: object) -> ContentAsset:
        if not isinstance(payload, dict):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"record {record_id} assets must contain objects",
            )
        required = ("id", "name", "media_type", "sequence", "metadata", "rights", "content")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"record {record_id} has an incomplete asset",
                details={"fields": missing},
            )
        metadata = payload["metadata"]
        if not isinstance(metadata, dict):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"asset {payload['id']} metadata must be an object",
            )
        name = str(payload["name"])
        media_type = str(payload["media_type"])
        if media_type != "image/png" or not name.lower().endswith(".png"):
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_DATA,
                f"asset {payload['id']} must be a pipeline-compatible PNG",
            )
        return ContentAsset(
            id=str(payload["id"]),
            record_id=record_id,
            name=name,
            media_type=media_type,
            sequence=int(payload["sequence"]),
            metadata=ContentMetadata(metadata),
            rights=_rights(payload["rights"], f"asset {payload['id']}"),
        )

    async def query_records(self, query: ContentRecordQuery) -> OpaquePage[ContentRecord]:
        records = sorted(self._records.values(), key=lambda record: record.id)
        collections = {value for value in query.collection_ids if value}
        if collections:
            records = [record for record in records if record.collection_id in collections]
        if query.updated_from is not None:
            records = [record for record in records if record.updated_at >= query.updated_from]
        if query.updated_to is not None:
            records = [record for record in records if record.updated_at <= query.updated_to]
        offset = self._decode_cursor(query.cursor)
        page_items = records[offset : offset + query.page_size]
        next_offset = offset + len(page_items)
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(records) else None
        return OpaquePage(items=tuple(page_items), next_cursor=next_cursor, total=len(records))

    async def get_record(self, record_id: str) -> ContentRecord:
        try:
            return self._records[record_id]
        except KeyError as exc:
            raise ContentSourceError(
                ContentSourceErrorCode.NOT_FOUND,
                "The requested record was not found",
                details={"record_id": record_id},
            ) from exc

    async def query_assets(
        self,
        record_id: str,
        *,
        page_size: int = 100,
        cursor: Optional[str] = None,
    ) -> OpaquePage[ContentAsset]:
        if not 1 <= page_size <= 500:
            raise ContentSourceError(
                ContentSourceErrorCode.INVALID_QUERY,
                "page_size must be between 1 and 500",
            )
        record = await self.get_record(record_id)
        assets = [self._assets[(record_id, asset_id)] for asset_id in record.asset_ids]
        assets.sort(key=lambda asset: (asset.sequence, asset.id))
        offset = self._decode_cursor(cursor)
        page_items = assets[offset : offset + page_size]
        next_offset = offset + len(page_items)
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(assets) else None
        return OpaquePage(items=tuple(page_items), next_cursor=next_cursor, total=len(assets))

    async def get_asset(self, record_id: str, asset_id: str) -> ContentAsset:
        await self.get_record(record_id)
        try:
            return self._assets[(record_id, asset_id)]
        except KeyError as exc:
            raise ContentSourceError(
                ContentSourceErrorCode.NOT_FOUND,
                "The requested asset was not found",
                details={"record_id": record_id, "asset_id": asset_id},
            ) from exc

    async def get_asset_content(self, record_id: str, asset_id: str) -> AssetContent:
        asset = await self.get_asset(record_id, asset_id)
        raw_record = self._raw_records[record_id]
        raw_asset = next(
            item for item in raw_record["assets"] if str(item.get("id")) == asset_id
        )
        content = raw_asset.get("content")
        data = generate_synthetic_png(content, f"asset {asset_id}")
        return AssetContent(
            asset_id=asset.id,
            filename=asset.name,
            media_type=asset.media_type,
            data=data,
        )

    async def all_records(self) -> Sequence[ContentRecord]:
        """Return all sample records for deterministic offline verification."""

        page = await self.query_records(ContentRecordQuery(page_size=500))
        return page.items
