# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[4]
FUNCTIONS = ROOT / "Layer1_Data_foundations/apps/functions"
sys.path.insert(0, str(FUNCTIONS))

from helper.content_source import SyntheticContentSourceAdapter, map_record_to_canonical
from services import cosmos_service
from services.record_shaper import shape_record


@contextmanager
def _mock_query(docs, total):
    with patch(
        "services.retrieval_service.cosmos_service.query_offset",
        return_value=(docs, total),
    ) as mock:
        yield mock


@contextmanager
def _mock_get_by_id(doc):
    with patch(
        "services.retrieval_service.cosmos_service.get_record_by_id",
        return_value=doc,
    ) as mock:
        yield mock


def _approved_doc(record_id: str, status: str = "published") -> dict:
    return {
        "id": record_id,
        "record_id": record_id,
        "archivist_status": status,
        "published_by": "Jane Smith",
        "published_at": "2026-03-12T14:30:00Z",
        "metadata": {"Title": "A letter", "Creator": "TR", "Repository": "HCL"},
        "metadata_key": {
            "sample-title-key": "Title",
            "sample-creator-key": "Creator",
            "sample-repository-key": "Repository",
        },
        "asset_details": [
            {
                "asset_id": "a1",
                "ocr_result": {
                    "ocr_text_flexible_blob_url": (
                        "https://acct.blob.core.windows.net/content-assets/"
                        f"{record_id}/ocr/flexible/a1/v1.txt"
                    ),
                    "confidence_scores": {"overall_confidence": 0.9},
                },
            },
        ],
    }


_RANGE = "approved_from=2026-03-01T00:00:00Z&approved_to=2026-03-16T00:00:00Z"


def test_approved_records_basic(client, api_key_headers):
    # inline mode hydrates OCR text from the asset's blob URL.
    with _mock_query([_approved_doc("rec-001")], 1), patch(
        "services.blob_service.read_text", return_value="hello"
    ):
        resp = client.get(f"/api/v1/approved-records?{_RANGE}", headers=api_key_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["has_more"] is False
    assert body["continuation_token"] is None
    rec = body["records"][0]
    assert rec["record_id"] == "rec-001"
    assert rec["archivist_status"] == "published"
    assert rec["approved_by"] == "Jane Smith"
    assert rec["approved_at"] == "2026-03-12T14:30:00Z"
    assert rec["content_type"] == "ocr"
    asset = rec["content"]["assets"][0]
    assert asset["ocr_text"] == "hello"
    assert asset["ocr_text_url"].endswith("/ocr/flexible/a1/v1.txt")
    assert asset["size_bytes"] == 5
    # Only editable fields with a source_key mapping are returned (Repository excluded).
    names = {f["display_name"] for f in rec["metadata"]["fields"]}
    assert names == {"Title", "Creator"}


def test_approved_records_query_echo_and_filter(client, api_key_headers):
    with _mock_query([], 0) as mock:
        resp = client.get(
            f"/api/v1/approved-records?{_RANGE}&include_content=none&page_size=25",
            headers=api_key_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"]["include_content"] == "none"
    assert body["query"]["page_size"] == 25
    where = mock.call_args.args[0]
    assert "c.archivist_status IN ('published')" in where
    assert "c.published_at >= @from" in where
    assert mock.call_args.kwargs["order_by"] == "c.published_at ASC"


def test_approved_records_pagination_token(client, api_key_headers):
    docs = [_approved_doc(f"rec-{i}") for i in range(50)]
    with _mock_query(docs, 120):
        resp = client.get(
            f"/api/v1/approved-records?{_RANGE}&page_size=50",
            headers=api_key_headers,
        )
    body = resp.json()
    assert body["has_more"] is True
    assert body["continuation_token"] is not None
    # token decodes to skip=50
    import base64, json
    assert json.loads(base64.b64decode(body["continuation_token"]))["skip"] == 50


def test_approved_records_range_too_large(client, api_key_headers):
    resp = client.get(
        "/api/v1/approved-records?approved_from=2026-01-01T00:00:00Z&approved_to=2026-12-31T00:00:00Z",
        headers=api_key_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "DATE_RANGE_TOO_LARGE"


def test_unapproved_records(client, api_key_headers):
    doc = {"id": "rec-9", "record_id": "rec-9", "archivist_status": "reviewed", "unpublished_at": "2026-03-13T10:00:00Z"}
    with _mock_query([doc], 1) as mock:
        resp = client.get(
            "/api/v1/unapproved-records?unapproved_from=2026-03-01T00:00:00Z&unapproved_to=2026-03-16T00:00:00Z",
            headers=api_key_headers,
        )
    assert resp.status_code == 200
    rec = resp.json()["records"][0]
    assert rec == {"record_id": "rec-9", "unapproved_at": "2026-03-13T10:00:00Z", "current_status": "reviewed"}
    where = mock.call_args.args[0]
    assert "c.archivist_status IN ('pending', 'reviewed')" in where
    assert "c.unpublished_at >= @from" in where


def test_get_single_record(client, api_key_headers):
    with _mock_get_by_id(_approved_doc("rec-77", status="pending")):
        resp = client.get("/api/v1/records/rec-77", headers=api_key_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == "rec-77"
    assert body["archivist_status"] == "pending"


def test_get_single_record_not_found(client, api_key_headers):
    with _mock_get_by_id(None):
        resp = client.get("/api/v1/records/missing", headers=api_key_headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_get_record_by_id_queries_source_record_id():
    container = MagicMock()
    container.query_items.return_value = [
        {
            "id": "canonical-1",
            "record_id": "canonical-1",
            "source_record_id": "SOURCE-1",
        }
    ]

    with patch(
        "services.cosmos_service.get_records_container",
        return_value=container,
    ):
        record = cosmos_service.get_record_by_id("SOURCE-1")

    assert record["id"] == "canonical-1"
    query = container.query_items.call_args.kwargs["query"]
    assert "c.source_record_id = @id" in query
    assert container.query_items.call_args.kwargs["parameters"] == [
        {"name": "@id", "value": "SOURCE-1"}
    ]


def test_synthetic_adapter_mapping_exports_typed_core_fields():
    adapter = SyntheticContentSourceAdapter()
    record = asyncio.run(adapter.get_record("synthetic-record-001"))
    assets = asyncio.run(adapter.query_assets(record.id)).items
    stored = map_record_to_canonical(record, assets)

    shaped = shape_record(stored, "none")
    fields = {
        field.display_name: field.value
        for field in shaped.metadata.fields
    }
    assert [field.display_name for field in shaped.metadata.fields].count("Title") == 1
    assert fields["Title"] == record.title
    assert fields["Description"] == record.summary
    assert fields["Resource Type"] == record.record_type
    assert fields["Creation Date"] == "1903-01-15"
