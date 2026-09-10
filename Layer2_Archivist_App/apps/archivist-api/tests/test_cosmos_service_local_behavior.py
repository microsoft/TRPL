"""Local Cosmos credential and status-filter regression tests."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.cosmos_service import (  # noqa: E402
    CosmosDBService,
    _resolve_cosmos_credential,
    settings,
)


def test_cosmos_account_key_is_local_only(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(ValueError, match="ENVIRONMENT=local"):
        _resolve_cosmos_credential("account-key")


def test_local_cosmos_account_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")

    assert _resolve_cosmos_credential(" account-key ") == "account-key"


def test_cosmos_uses_managed_identity_without_key(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    credential = object()

    with patch(
        "services.cosmos_service.DefaultAzureCredential",
        return_value=credential,
    ) as credential_factory:
        assert _resolve_cosmos_credential(None) is credential

    credential_factory.assert_called_once_with(
        exclude_interactive_browser_credential=True
    )


def test_failed_status_filter_uses_canonical_archivist_status():
    service = CosmosDBService.__new__(CosmosDBService)

    _, where_clause, _ = service.build_query_filters(
        status_filter="failed",
        exclude_errors=True,
        exclude_zero_assets=True,
    )

    assert "LOWER(c.archivist_status) = 'failed'" in where_clause
    assert "c.ocr_processing_status != 'failed'" not in where_clause
    assert "ARRAY_LENGTH(c.asset_details) > 0" not in where_clause


def test_non_failed_filter_still_excludes_pipeline_errors():
    service = CosmosDBService.__new__(CosmosDBService)

    _, where_clause, _ = service.build_query_filters(
        status_filter="pending",
        exclude_errors=True,
    )

    assert "LOWER(c.archivist_status) = 'pending'" in where_clause
    assert "c.ocr_processing_status != 'failed'" in where_clause
