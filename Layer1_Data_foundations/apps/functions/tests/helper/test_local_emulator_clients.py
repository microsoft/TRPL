# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local emulator client selection and safety tests."""

from unittest.mock import MagicMock, patch

import pytest

from helper import cosmos_client, servicebus_client, storage_client


def test_storage_connection_string_requires_local_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")

    with pytest.raises(RuntimeError, match="only supported"):
        storage_client.get_blob_service_client()


def test_storage_factory_uses_connection_string_locally(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv(
        "AZURE_STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=local;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
    )

    expected = MagicMock()
    with patch.object(
        storage_client.BlobServiceClient,
        "from_connection_string",
        return_value=expected,
    ) as factory:
        assert storage_client.get_blob_service_client() is expected

    factory.assert_called_once()


def test_local_blob_url_is_parsed_against_configured_endpoint(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv(
        "AZURE_STORAGE_BLOB_ENDPOINT",
        "http://127.0.0.1:10000/devstoreaccount1",
    )

    assert storage_client.parse_configured_blob_url(
        "http://127.0.0.1:10000/devstoreaccount1/content-assets/a/b.txt"
    ) == ("content-assets", "a/b.txt")

    with pytest.raises(ValueError, match="configured Blob Storage endpoint"):
        storage_client.parse_configured_blob_url(
            "http://example.test:10000/devstoreaccount1/content-assets/a.txt"
        )


def test_blob_url_parser_decodes_blob_name_once(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv(
        "AZURE_STORAGE_BLOB_ENDPOINT",
        "http://127.0.0.1:10000/devstoreaccount1",
    )

    assert storage_client.parse_configured_blob_url(
        "http://127.0.0.1:10000/devstoreaccount1/content-assets/a%20b.txt"
    ) == ("content-assets", "a b.txt")


def test_storage_account_override_selects_cloud_account(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_BLOB_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)

    with (
        patch.object(storage_client, "get_credential", return_value=MagicMock()),
        patch.object(storage_client, "BlobServiceClient") as client_type,
    ):
        storage_client.get_blob_service_client("explicit-account")

    client_type.assert_called_once()
    assert (
        client_type.call_args.kwargs["account_url"]
        == "https://explicit-account.blob.core.windows.net"
    )


def test_local_storage_detection_rejects_cloud_connection_string(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "AccountName=not-local;")

    with pytest.raises(RuntimeError, match="only supported"):
        storage_client.uses_local_storage()


def test_cosmos_factory_uses_local_connection_string(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("COSMOS_ENDPOINT", "http://127.0.0.1:8081/")
    monkeypatch.setenv(
        "COSMOS_CONNECTION_STRING",
        "AccountEndpoint=http://127.0.0.1:8081/;AccountKey=local;",
    )
    expected = MagicMock()
    with patch.object(
        cosmos_client.CosmosClient,
        "from_connection_string",
        return_value=expected,
    ) as factory:
        assert cosmos_client.CosmosClientManager._create_client() is expected

    factory.assert_called_once()


def test_cosmos_connection_string_requires_local_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("COSMOS_ENDPOINT", "http://127.0.0.1:8081/")
    monkeypatch.setenv(
        "COSMOS_CONNECTION_STRING",
        "AccountEndpoint=http://127.0.0.1:8081/;AccountKey=local;",
    )

    with pytest.raises(RuntimeError, match="only supported"):
        cosmos_client.CosmosClientManager._create_client()


def test_servicebus_factory_uses_local_connection_string(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv(
        "AZURE_SERVICEBUS_CONNECTION_STRING",
        "Endpoint=sb://localhost;UseDevelopmentEmulator=true;",
    )

    client = MagicMock()
    client.__enter__.return_value = client
    sender = MagicMock()
    sender.__enter__.return_value = sender
    client.get_queue_sender.return_value = sender

    with patch.object(
        servicebus_client.ServiceBusClient,
        "from_connection_string",
        return_value=client,
    ) as factory:
        servicebus_client.send_message("hello", "data-ingestion-queue")

    factory.assert_called_once()
    sender.send_messages.assert_called_once()
