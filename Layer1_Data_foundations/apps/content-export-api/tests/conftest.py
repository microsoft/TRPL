# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pytest fixtures for outbound API."""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("COSMOS_DB_ACCOUNT_NAME", "test-cosmos-account")
os.environ.setdefault("OUTBOUND_API_KEY", "test-api-key")

from app import app  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_blob():
    """Keep OCR/visual content hydration offline in tests (no Azure blob calls)."""
    with patch("services.blob_service.read_text", return_value=None), patch(
        "services.blob_service.sas_url", side_effect=lambda url: url
    ):
        yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def api_key_headers() -> dict:
    return {"X-API-Key": os.environ["OUTBOUND_API_KEY"]}
