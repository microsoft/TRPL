# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ETag round-trip and OCR update helper tests."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import DocumentMetadata
from services.cosmos_service import (
    CosmosDBService,
    DocumentVersionConflictError,
    _assert_client_etag_matches,
)


def test_etag_json_roundtrip():
    """Cosmos quoted etag must match client value from JSON body."""
    cosmos_etag = '"abc123"'
    doc = DocumentMetadata(id="x", _etag=cosmos_etag)
    payload = doc.model_dump_json()
    import json

    client_etag = json.loads(payload)["_etag"]
    _assert_client_etag_matches(client_etag, cosmos_etag)


def test_patch_ocr_blob_url_uses_fresh_asset_index():
    """OCR patch must target asset on fresh document, not stale snapshot."""
    from services import cosmos_service as cs_mod

    fresh_doc = {
        "id": "doc-1",
        "version": 3,
        "_etag": '"live-etag"',
        "asset_details": [
            {"asset_id": "a1", "ocr_result": {}},
            {"asset_id": "a2", "ocr_result": {"ocr_text_flexible_blob_url": "old"}},
        ],
    }

    container = MagicMock()
    container.read_item.return_value = fresh_doc
    container.patch_item.return_value = {
        **fresh_doc,
        "version": 4,
        "_etag": '"new-etag"',
        "asset_details": [
            fresh_doc["asset_details"][0],
            {
                **fresh_doc["asset_details"][1],
                "ocr_result": {
                    "ocr_text_flexible_blob_url": "https://acct.blob.core.windows.net/c/d/ocr/v4.txt"
                },
            },
        ],
    }

    service = CosmosDBService.__new__(CosmosDBService)
    service.get_container = MagicMock(return_value=container)

    updated_doc, asset_index = service.patch_ocr_flexible_blob_url(
        document_id="doc-1",
        asset_id="a2",
        flexible_blob_url="https://acct.blob.core.windows.net/c/d/ocr/v4.txt",
        client_etag='"live-etag"',
    )

    assert updated_doc.version == 4
    assert asset_index == 1
    patch_ops = container.patch_item.call_args.kwargs["patch_operations"]
    paths = [op["path"] for op in patch_ops]
    assert "/asset_details/1/ocr_result" in paths
    assert container.patch_item.call_args.kwargs["if_match"] == '"live-etag"'


def test_patch_ocr_rejects_stale_client_etag():
    service = CosmosDBService.__new__(CosmosDBService)
    container = MagicMock()
    container.read_item.return_value = {
        "id": "doc-1",
        "version": 1,
        "_etag": '"server"',
        "asset_details": [{"asset_id": "a1", "ocr_result": {}}],
    }
    container.patch_item.side_effect = __import__(
        "azure.cosmos.exceptions", fromlist=["CosmosAccessConditionFailedError"]
    ).CosmosAccessConditionFailedError()
    service.get_container = MagicMock(return_value=container)

    with pytest.raises(DocumentVersionConflictError):
        service.patch_ocr_flexible_blob_url(
            document_id="doc-1",
            asset_id="a1",
            flexible_blob_url="https://x/y",
            client_etag="stale",
        )
