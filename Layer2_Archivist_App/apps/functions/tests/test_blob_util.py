from unittest.mock import MagicMock, patch

import pytest

from helper.blob_util import get_blob_client


LOCAL_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=local-key;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1"
)


def test_local_environment_reads_configured_azurite_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", LOCAL_CONNECTION_STRING)
    service = MagicMock()

    with patch(
        "helper.blob_util.BlobServiceClient.from_connection_string",
        return_value=service,
    ) as from_connection_string:
        get_blob_client(
            "http://azurite:10000/devstoreaccount1/content-assets/"
            "record-1/ocr/flexible/page-1/v1.txt"
        )

    from_connection_string.assert_called_once_with(LOCAL_CONNECTION_STRING)
    service.get_blob_client.assert_called_once_with(
        container="content-assets",
        blob="record-1/ocr/flexible/page-1/v1.txt",
    )


def test_local_environment_rejects_unconfigured_http_origin(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", LOCAL_CONNECTION_STRING)

    with pytest.raises(ValueError, match="Invalid blob URL format"):
        get_blob_client(
            "http://example.test:10000/devstoreaccount1/content-assets/record.txt"
        )


def test_non_local_environment_rejects_azurite_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", LOCAL_CONNECTION_STRING)

    with pytest.raises(ValueError, match="Invalid blob URL format"):
        get_blob_client(
            "http://azurite:10000/devstoreaccount1/content-assets/record.txt"
        )


def test_cloud_blob_url_uses_managed_identity(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    credential = MagicMock()
    service = MagicMock()

    with (
        patch(
            "helper.blob_util.DefaultAzureCredential",
            return_value=credential,
        ),
        patch(
            "helper.blob_util.BlobServiceClient",
            return_value=service,
        ) as service_class,
    ):
        get_blob_client(
            "https://archive.blob.core.windows.net/content-assets/record.txt"
        )

    service_class.assert_called_once_with(
        account_url="https://archive.blob.core.windows.net",
        credential=credential,
    )
    service.get_blob_client.assert_called_once_with(
        container="content-assets",
        blob="record.txt",
    )
