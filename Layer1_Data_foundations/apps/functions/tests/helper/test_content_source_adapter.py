# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract, pagination, mapping, asset, registry, and offline flow tests."""

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from helper.content_source import (
    ContentMetadata,
    ContentRecordQuery,
    ContentSourceError,
    ContentSourceErrorCode,
    SyntheticContentSourceAdapter,
    build_gallery_projection,
    get_content_source_adapter,
    map_record_to_canonical,
)
from helper import content_source_client
from helper.files_util import convert_file_url_to_jpeg_data_urls


ROOT = Path(__file__).resolve().parents[5]
FUNCTIONS = ROOT / "Layer1_Data_foundations/apps/functions"
GALLERY = ROOT / "Layer3_Campfire/frontend/public/data/home-gallery-artifacts.json"


def run(coro):
    return asyncio.run(coro)


def test_typed_record_query_uses_opaque_pagination():
    adapter = SyntheticContentSourceAdapter()
    first = run(adapter.query_records(ContentRecordQuery(page_size=2)))
    assert len(first.items) == 2
    assert first.total == 3
    assert first.next_cursor
    assert "offset" not in first.next_cursor

    second = run(
        adapter.query_records(
            ContentRecordQuery(page_size=2, cursor=first.next_cursor)
        )
    )
    assert [record.id for record in first.items + second.items] == [
        "synthetic-record-001",
        "synthetic-record-002",
        "synthetic-record-003",
    ]
    assert second.next_cursor is None


def test_query_filters_by_collection_and_updated_window():
    adapter = SyntheticContentSourceAdapter()
    page = run(
        adapter.query_records(
            ContentRecordQuery(collection_ids=("invented-ephemera",))
        )
    )
    assert [record.id for record in page.items] == ["synthetic-record-003"]


@pytest.mark.parametrize("field", ["updated_from", "updated_to"])
def test_query_rejects_naive_updated_bounds(field):
    with pytest.raises(ContentSourceError) as invalid:
        ContentRecordQuery(**{field: datetime(2026, 1, 6)})

    assert invalid.value.code is ContentSourceErrorCode.INVALID_QUERY
    assert invalid.value.details["fields"] == [field]


def test_pipeline_cursor_uses_the_same_filtered_opaque_query():
    adapter = SyntheticContentSourceAdapter()
    with patch.object(content_source_client, "_adapter", return_value=adapter):
        first = run(
            content_source_client.query_content_records(
                limit=1,
                date_from=datetime(2026, 1, 6, tzinfo=timezone.utc),
                date_to=datetime(2026, 1, 7, 23, 59, tzinfo=timezone.utc),
            )
        )
        page = run(
            content_source_client.query_content_records(
                limit=1,
                cursor=first["next_cursor"],
                date_from=datetime(2026, 1, 6, tzinfo=timezone.utc),
                date_to=datetime(2026, 1, 7, 23, 59, tzinfo=timezone.utc),
            )
        )
    assert [record["id"] for record in page["data"]] == ["synthetic-record-003"]
    assert page["total"] == 2


def test_structured_errors_cover_invalid_cursor_and_missing_asset():
    adapter = SyntheticContentSourceAdapter()
    with pytest.raises(ContentSourceError) as invalid:
        run(adapter.query_records(ContentRecordQuery(cursor="not-a-cursor")))
    assert invalid.value.code is ContentSourceErrorCode.INVALID_QUERY
    assert invalid.value.as_dict()["retryable"] is False

    with pytest.raises(ContentSourceError) as missing:
        run(adapter.get_asset("synthetic-record-001", "missing"))
    assert missing.value.code is ContentSourceErrorCode.NOT_FOUND
    assert missing.value.details["asset_id"] == "missing"


def test_assets_and_content_are_returned_without_remote_urls():
    adapter = SyntheticContentSourceAdapter()
    page = run(adapter.query_assets("synthetic-record-001"))
    assert page.total == 1
    asset = page.items[0]
    assert asset.media_type == "image/png"
    assert not hasattr(asset, "url")

    content = run(adapter.get_asset_content(asset.record_id, asset.id))
    assert content.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert content.filename.endswith(".png")
    with Image.open(io.BytesIO(content.data)) as image:
        assert image.format == "PNG"
        assert image.size == (320, 200)


def test_canonical_mapper_preserves_rights_and_source_identifier():
    adapter = SyntheticContentSourceAdapter()
    record = run(adapter.get_record("synthetic-record-001"))
    assets = run(adapter.query_assets(record.id)).items
    mapped = map_record_to_canonical(record, assets)
    assert mapped["source_record_id"] == "FRC-LETTER-001"
    assert mapped["metadata"]["Source Record ID"] == "FRC-LETTER-001"
    assert mapped["metadata"]["Title"] == record.title
    assert mapped["metadata"]["Description"] == record.summary
    assert mapped["metadata"]["Repository"] == "Fictional Reference Collections"
    assert mapped["metadata"]["Resource Type"] == record.record_type
    assert mapped["metadata"]["Creation Date"] == "1903-01-15"
    assert mapped["rights"]["status"] == "public-domain-dedication"
    assert mapped["asset_details"][0]["sequence"] == 1


def test_canonical_mapper_overwrites_conflicting_provider_metadata():
    adapter = SyntheticContentSourceAdapter()
    record = run(adapter.get_record("synthetic-record-001"))
    conflicting = replace(
        record,
        metadata=ContentMetadata(
            {
                **record.metadata.as_dict(),
                "Source Record ID": "conflicting-id",
                "Rights Statement": "conflicting statement",
                "Rights Status": "conflicting-status",
                "Rights License": "https://example.invalid/conflicting-license",
                "Record URL": "https://example.invalid/conflicting-record",
            }
        ),
    )

    metadata = map_record_to_canonical(conflicting)["metadata"]

    assert metadata["Source Record ID"] == record.source_identifier
    assert metadata["Rights Statement"] == record.rights.statement
    assert metadata["Rights Status"] == record.rights.status
    assert metadata["Rights License"] == record.rights.license_url
    assert metadata["Record URL"] == record.record_url


def test_registry_is_fixed_and_rejects_caller_selected_modules():
    assert isinstance(get_content_source_adapter("synthetic"), SyntheticContentSourceAdapter)
    with pytest.raises(ContentSourceError) as unknown:
        get_content_source_adapter("package.module:Adapter")
    assert unknown.value.code is ContentSourceErrorCode.UNSUPPORTED_ADAPTER


def test_offline_ingestion_to_gallery_matches_derived_projection():
    with patch.object(
        socket,
        "getaddrinfo",
        side_effect=AssertionError("network access is forbidden in offline verification"),
    ):
        adapter = SyntheticContentSourceAdapter()
        records = run(adapter.all_records())
        content = {
            record.id: run(adapter.get_asset_content(record.id, record.asset_ids[0]))
            for record in records
        }
        projection = build_gallery_projection(records, content)

    assert projection == json.loads(GALLERY.read_text(encoding="utf-8"))
    assert all(
        artifact["placeholderDataUri"].startswith("data:image/png;base64,")
        for artifact in projection["artifacts"]
    )


def test_synthetic_png_traverses_the_real_conversion_path():
    adapter = SyntheticContentSourceAdapter()
    asset_content = run(
        adapter.get_asset_content("synthetic-record-001", "synthetic-asset-001")
    )

    class Response:
        headers = {"Content-Length": str(len(asset_content.data))}
        content = asset_content.data

        @staticmethod
        def raise_for_status():
            return None

    with patch("helper.files_util.requests.head", return_value=Response()), patch(
        "helper.files_util.requests.get",
        return_value=Response(),
    ):
        converted = convert_file_url_to_jpeg_data_urls(
            "https://example.org/generated/synthetic-record-001.png"
        )
    assert len(converted) == 1
    assert converted[0].startswith("data:image/jpeg;base64,")


def test_function_archive_contains_and_package_loads_canonical_pack(tmp_path):
    archive = tmp_path / "functions-package.zip"
    pack_member = (
        "helper/content_source/data/fictional-content-source-pack.json"
    )
    with zipfile.ZipFile(archive, "w") as package:
        for candidate in FUNCTIONS.rglob("*"):
            if candidate.is_file() and "__pycache__" not in candidate.parts:
                package.write(candidate, candidate.relative_to(FUNCTIONS))
    with zipfile.ZipFile(archive) as package:
        assert pack_member in package.namelist()
        assert json.loads(package.read(pack_member))["schema_version"] == "1.0"

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(archive)
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio; "
                "from helper.content_source import SyntheticContentSourceAdapter; "
                "a = SyntheticContentSourceAdapter(); "
                "print(len(asyncio.run(a.all_records())))"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert loaded.stdout.strip() == "3"
