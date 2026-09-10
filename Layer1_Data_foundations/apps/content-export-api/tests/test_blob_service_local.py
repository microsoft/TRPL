"""Tests for local Blob Storage safety boundaries."""

import pytest

from core.config import settings
from services.blob_service import sas_url


def test_sas_url_rejects_connection_string_outside_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        settings,
        "storage_connection_string",
        "DefaultEndpointsProtocol=http;AccountName=not-local;",
    )

    with pytest.raises(RuntimeError, match="only supported"):
        sas_url("https://example.test/container/blob.txt")


def test_sas_url_rejects_blob_endpoint_outside_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "storage_connection_string", None)
    monkeypatch.setattr(
        settings,
        "storage_blob_endpoint",
        "http://127.0.0.1:10000/devstoreaccount1",
    )

    with pytest.raises(RuntimeError, match="only supported"):
        sas_url(
            "http://127.0.0.1:10000/devstoreaccount1/content-assets/blob.txt"
        )
