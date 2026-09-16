#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Verify synthetic adapter ingestion and the derived gallery without networking."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "Layer1_Data_foundations/apps/functions"
GALLERY = ROOT / "Layer3_Campfire/frontend/public/data/home-gallery-artifacts.json"
sys.path.insert(0, str(FUNCTIONS))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

from helper.content_source import (  # noqa: E402
    SyntheticContentSourceAdapter,
    build_gallery_projection,
    map_record_to_canonical,
)


def _validate_generated_png(data: bytes, label: str) -> None:
    if not isinstance(data, bytes) or not data.startswith(PNG_SIGNATURE):
        raise RuntimeError(f"invalid synthetic PNG signature for {label}")

    offset = len(PNG_SIGNATURE)
    chunks = []
    idat = []
    width = height = None
    while offset < len(data):
        if offset + 12 > len(data):
            raise RuntimeError(f"truncated synthetic PNG chunk for {label}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise RuntimeError(f"truncated synthetic PNG payload for {label}")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise RuntimeError(f"synthetic PNG checksum mismatch for {label}")
        chunks.append(kind)

        if kind == b"IHDR":
            if len(chunks) != 1 or length != 13:
                raise RuntimeError(f"invalid synthetic PNG header for {label}")
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if (
                not 1 <= width <= 2048
                or not 1 <= height <= 2048
                or (depth, color, compression, filtering, interlace)
                != (8, 2, 0, 0, 0)
            ):
                raise RuntimeError(f"unsupported synthetic PNG layout for {label}")
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            if length != 0 or chunk_end != len(data):
                raise RuntimeError(f"invalid synthetic PNG terminator for {label}")
            offset = chunk_end
            break
        offset = chunk_end

    if chunks[:1] != [b"IHDR"] or not idat or chunks[-1:] != [b"IEND"]:
        raise RuntimeError(f"incomplete synthetic PNG for {label}")
    try:
        pixels = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise RuntimeError(f"invalid synthetic PNG compression for {label}") from exc
    row_size = 1 + (width * 3)
    if len(pixels) != row_size * height or any(
        pixels[row * row_size] != 0 for row in range(height)
    ):
        raise RuntimeError(f"invalid synthetic PNG pixels for {label}")


async def _verify() -> None:
    adapter = SyntheticContentSourceAdapter()
    records = await adapter.all_records()
    content_by_record = {}
    for record in records:
        assets = (await adapter.query_assets(record.id)).items
        mapped = map_record_to_canonical(record, assets)
        if mapped["record_id"] != record.id or mapped["rights"] != record.rights.as_dict():
            raise RuntimeError(f"canonical mapping mismatch for {record.id}")
        if not record.asset_ids:
            raise RuntimeError(f"record {record.id} must contain an asset")
        asset_content = await adapter.get_asset_content(
            record.id,
            record.asset_ids[0],
        )
        if (
            asset_content.media_type != "image/png"
            or not asset_content.filename.endswith(".png")
        ):
            raise RuntimeError(f"unsupported synthetic asset format for {record.id}")
        _validate_generated_png(asset_content.data, record.id)
        repeated = await adapter.get_asset_content(record.id, record.asset_ids[0])
        if repeated.data != asset_content.data:
            raise RuntimeError(f"non-deterministic synthetic PNG for {record.id}")
        content_by_record[record.id] = asset_content
    actual = build_gallery_projection(records, content_by_record)
    expected = json.loads(GALLERY.read_text(encoding="utf-8"))
    if actual != expected:
        raise RuntimeError("Layer 3 gallery is not the canonical sample-pack projection")


def main() -> int:
    with patch.object(
        socket,
        "getaddrinfo",
        side_effect=RuntimeError("network access is disabled for offline verification"),
    ):
        asyncio.run(_verify())
    print("Offline content-source ingestion and gallery projection passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
