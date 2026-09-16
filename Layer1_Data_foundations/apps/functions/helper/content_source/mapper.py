# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical mapping shared by ingestion and the synthetic gallery projection."""

from __future__ import annotations

import base64
from typing import Mapping, Sequence

from .contracts import AssetContent, ContentAsset, ContentRecord


def map_asset_to_canonical(asset: ContentAsset) -> dict:
    """Map adapter asset metadata to the downstream record document shape."""

    return {
        "asset_id": asset.id,
        "record_id": asset.record_id,
        "name": asset.name,
        "type": asset.media_type,
        "sequence": asset.sequence,
        "metadata": asset.metadata.as_dict(),
        "rights": asset.rights.as_dict(),
    }


def map_record_to_canonical(
    record: ContentRecord,
    assets: Sequence[ContentAsset] = (),
) -> dict:
    """Map a typed record to the canonical storage shape used downstream."""

    ordered_assets = sorted(assets, key=lambda asset: (asset.sequence, asset.id))
    metadata = record.metadata.as_dict()
    metadata.update(
        {
            "Title": record.title,
            "Description": record.summary,
            "Resource Type": record.record_type,
            "Creation Date": record.created_at.date().isoformat(),
            "Source Record ID": record.source_identifier,
            "Rights Statement": record.rights.statement,
            "Rights Status": record.rights.status,
            "Rights License": record.rights.license_url,
            "Record URL": record.record_url,
        }
    )
    return {
        "id": record.id,
        "record_id": record.id,
        "source_record_id": record.source_identifier,
        "title": record.title,
        "type": record.record_type,
        "type_label": str(metadata.get("Display Type") or record.record_type),
        "summary": record.summary,
        "published": record.published,
        "source_created_at": record.created_at.isoformat(),
        "source_updated_at": record.updated_at.isoformat(),
        "metadata": metadata,
        "metadata_key": {key: key for key in metadata},
        "rights": record.rights.as_dict(),
        "related_assets": [asset.id for asset in ordered_assets],
        "asset_details": [map_asset_to_canonical(asset) for asset in ordered_assets],
        "asset_count": len(ordered_assets),
    }


def _as_text(metadata: Mapping[str, object], key: str, fallback: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None and str(value).strip() else fallback


def build_gallery_projection(
    records: Sequence[ContentRecord],
    content_by_record: Mapping[str, AssetContent],
) -> dict:
    """Derive the Layer 3 gallery manifest from canonical records and content."""

    artifacts = []
    for record in records:
        content = content_by_record[record.id]
        encoded = base64.b64encode(content.data).decode("ascii")
        metadata = record.metadata.as_dict()
        artifacts.append(
            {
                "id": record.id,
                "title": record.title,
                "type": _as_text(metadata, "Display Type", record.record_type.title()),
                "date": _as_text(metadata, "Display Date", record.created_at.date().isoformat()),
                "creator": _as_text(metadata, "Institution", "Fictional Archive"),
                "recordUrl": record.record_url,
                "category": _as_text(metadata, "Collection", record.collection_id),
                "placeholderDataUri": f"data:{content.media_type};base64,{encoded}",
            }
        )
    return {"artifacts": artifacts}
