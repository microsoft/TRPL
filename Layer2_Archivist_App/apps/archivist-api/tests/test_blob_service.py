# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Blob service unit tests."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from services.blob_service import (
    _parse_local_blob_storage_url,
    has_sas_token,
    append_sas_token_to_url,
    add_sas_token_to_url,
    download_blob_text,
    is_sas_token_expired,
    settings,
    strip_sas_token_from_url,
    upload_text_to_blob_url,
)


def test_has_sas_token():
    """Test detection of SAS token in URL."""
    # URL without SAS token
    url_without_sas = (
        "https://account.blob.core.windows.net/container/blob.jpg"
    )
    assert not has_sas_token(url_without_sas)

    # URL with SAS token
    url_with_sas = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        "?sv=2021-06-08&sig=abc123"
    )
    assert has_sas_token(url_with_sas)

    # URL with only sig parameter
    url_with_sig = "https://account.blob.core.windows.net/container/blob.jpg?sig=abc123"
    assert has_sas_token(url_with_sig)


def test_append_sas_token_to_url():
    """Test appending SAS token to URL."""
    base_url = "https://account.blob.core.windows.net/container/blob.jpg"
    sas_token = "sv=2021-06-08&sig=abc123"
    # Append SAS token to clean URL
    result = append_sas_token_to_url(base_url, sas_token)
    assert "sv=2021-06-08" in result
    assert "sig=abc123" in result
    assert "?" in result
    # Append SAS token to URL with existing params
    url_with_params = base_url + "?existing=param"
    result = append_sas_token_to_url(url_with_params, sas_token)
    assert "existing=param" in result
    assert "sv=2021-06-08" in result
    assert "sig=abc123" in result
    # SAS token with leading ?
    result = append_sas_token_to_url(base_url, "?sv=2021-06-08&sig=abc123")
    assert "sv=2021-06-08" in result
    assert "sig=abc123" in result
    # Empty SAS token
    result = append_sas_token_to_url(base_url, "")
    assert result == base_url


def test_is_sas_token_expired():
    """Expired or not-yet-valid SAS tokens should be detected."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expired_url = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        f"?sv=2021-06-08&se={quote(past)}&sig=existing"
    )
    assert is_sas_token_expired(expired_url)

    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_url = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        f"?sv=2021-06-08&se={quote(future)}&sig=existing"
    )
    assert not is_sas_token_expired(valid_url)

    future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_yet_valid_url = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        f"?sv=2021-06-08&st={quote(future_start)}&se={quote(future)}&sig=existing"
    )
    assert is_sas_token_expired(not_yet_valid_url)


def test_strip_sas_token_from_url():
    """SAS parameters should be removed while preserving other query params."""
    url = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        "?foo=bar&sv=2021-06-08&sig=existing"
    )
    result = strip_sas_token_from_url(url)
    assert "foo=bar" in result
    assert "sig=" not in result
    assert "sv=" not in result


def test_add_sas_token_to_url_with_valid_existing_token():
    """Valid existing SAS tokens should be reused."""
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url_with_sas = (
        "https://account.blob.core.windows.net/container/blob.jpg"
        f"?sv=2021-06-08&se={quote(future)}&sig=existing"
    )

    result = add_sas_token_to_url(url_with_sas)
    assert result == url_with_sas


def test_add_sas_token_to_url_empty_url():
    """Test handling of empty URL."""
    result = add_sas_token_to_url("")
    assert result == ""
    result = add_sas_token_to_url(None)
    assert result is None


def test_parse_local_blob_storage_url_requires_configured_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(
        settings,
        "azure_storage_connection_string",
        "AccountName=devstoreaccount1;"
        "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
    )

    assert _parse_local_blob_storage_url(
        "http://azurite:10000/devstoreaccount1/content-assets/record/ocr.txt"
    ) == ("devstoreaccount1", "content-assets", "record/ocr.txt")
    assert (
        _parse_local_blob_storage_url(
            "http://untrusted:10000/devstoreaccount1/content-assets/record/ocr.txt"
        )
        is None
    )


def test_parse_local_blob_storage_url_accepts_native_loopback_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(
        settings,
        "azure_storage_connection_string",
        "AccountName=devstoreaccount1;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
    )

    assert _parse_local_blob_storage_url(
        "http://127.0.0.1:10000/devstoreaccount1/content-assets/record/ocr.txt"
    ) == ("devstoreaccount1", "content-assets", "record/ocr.txt")


def test_parse_local_blob_storage_url_is_disabled_outside_local(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        settings,
        "azure_storage_connection_string",
        "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
    )

    assert (
        _parse_local_blob_storage_url(
            "http://azurite:10000/devstoreaccount1/content-assets/record/ocr.txt"
        )
        is None
    )


@pytest.mark.asyncio
async def test_download_blob_text_uses_local_emulator_client(monkeypatch):
    class Downloader:
        def readall(self):
            return b"local OCR text"

    class BlobClient:
        def download_blob(self):
            return Downloader()

    class BlobService:
        def get_blob_client(self, container_name, blob_path):
            assert container_name == "content-assets"
            assert blob_path == "record/ocr.txt"
            return BlobClient()

    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(
        settings,
        "azure_storage_connection_string",
        "AccountName=devstoreaccount1;"
        "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
    )
    monkeypatch.setattr(
        "services.blob_service._get_blob_service_client",
        lambda: BlobService(),
    )

    content = await download_blob_text(
        "http://azurite:10000/devstoreaccount1/content-assets/record/ocr.txt"
    )

    assert content == "local OCR text"


def test_upload_text_to_blob_url_uses_local_emulator_client(monkeypatch):
    uploaded = {}

    class BlobClient:
        def upload_blob(self, content, **kwargs):
            uploaded["content"] = content
            uploaded.update(kwargs)

    class BlobService:
        def get_blob_client(self, container_name, blob_path):
            assert container_name == "content-assets"
            assert blob_path == "record/ocr.txt"
            return BlobClient()

    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(
        settings,
        "azure_storage_connection_string",
        "AccountName=devstoreaccount1;"
        "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
    )
    monkeypatch.setattr(
        "services.blob_service._get_blob_service_client",
        lambda: BlobService(),
    )

    upload_text_to_blob_url(
        "http://azurite:10000/devstoreaccount1/content-assets/record/ocr.txt",
        "updated OCR",
    )

    assert uploaded["content"] == b"updated OCR"
    assert uploaded["overwrite"] is True
    assert uploaded["content_settings"].content_type == "text/plain; charset=utf-8"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
