# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Blob Storage client construction for cloud and local emulator environments."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

from azure.storage.blob import BlobServiceClient

from .credential import get_credential


def _require_local_environment(setting_name: str) -> None:
    if os.getenv("ENVIRONMENT", "production").strip().lower() != "local":
        raise RuntimeError(f"{setting_name} is only supported when ENVIRONMENT=local")


def get_blob_service_client(account_name: str | None = None) -> BlobServiceClient:
    """Create a Blob client using Azurite locally or Entra ID in Azure."""

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if connection_string:
        _require_local_environment("AZURE_STORAGE_CONNECTION_STRING")
        return BlobServiceClient.from_connection_string(connection_string)

    account_url = get_blob_endpoint(account_name)
    if not account_url:
        raise RuntimeError(
            "AZURE_STORAGE_ACCOUNT_NAME or AZURE_STORAGE_BLOB_ENDPOINT is required"
        )
    return BlobServiceClient(account_url=account_url, credential=get_credential())


def get_blob_endpoint(account_name: str | None = None) -> str | None:
    """Return the configured Blob service endpoint without a trailing slash."""

    explicit_endpoint = os.getenv("AZURE_STORAGE_BLOB_ENDPOINT", "").strip()
    if explicit_endpoint:
        _require_local_environment("AZURE_STORAGE_BLOB_ENDPOINT")
        return explicit_endpoint.rstrip("/")

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if connection_string:
        _require_local_environment("AZURE_STORAGE_CONNECTION_STRING")
        return BlobServiceClient.from_connection_string(connection_string).url.rstrip("/")

    resolved_account_name = (
        account_name or os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
    ).strip()
    if not resolved_account_name:
        return None
    return f"https://{resolved_account_name}.blob.core.windows.net"


def build_blob_url(container_name: str, blob_name: str) -> str:
    """Build a URL for the configured cloud Blob service or Azurite."""

    endpoint = get_blob_endpoint()
    if not endpoint:
        raise RuntimeError("Blob Storage endpoint is not configured")
    return f"{endpoint}/{container_name.strip('/')}/{blob_name.lstrip('/')}"


def parse_configured_blob_url(blob_url: str) -> tuple[str, str]:
    """Validate and split a URL belonging to the configured Blob endpoint."""

    endpoint = get_blob_endpoint()
    if not endpoint:
        raise RuntimeError("Blob Storage endpoint is not configured")

    expected = urlparse(endpoint)
    actual = urlparse(blob_url)
    if (
        actual.scheme != expected.scheme
        or actual.hostname != expected.hostname
        or actual.port != expected.port
    ):
        raise ValueError("Blob URL must use the configured Blob Storage endpoint")

    endpoint_path = expected.path.strip("/")
    actual_path = actual.path.strip("/")
    if endpoint_path:
        prefix = f"{endpoint_path}/"
        if not actual_path.startswith(prefix):
            raise ValueError("Blob URL must use the configured Blob Storage account path")
        actual_path = actual_path[len(prefix) :]

    container_name, separator, blob_name = actual_path.partition("/")
    if not separator or not container_name or not blob_name:
        raise ValueError("Blob URL must identify both a container and blob")
    return unquote(container_name), unquote(blob_name)


def get_blob_client_from_url(blob_url: str):
    """Return a BlobClient after enforcing the configured endpoint boundary."""

    container_name, blob_name = parse_configured_blob_url(blob_url)
    return get_blob_service_client().get_blob_client(
        container=container_name,
        blob=blob_name,
    )


def uses_local_storage() -> bool:
    """Return whether the application is configured to use Azurite."""

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    explicit_endpoint = os.getenv("AZURE_STORAGE_BLOB_ENDPOINT", "").strip()
    if connection_string:
        _require_local_environment("AZURE_STORAGE_CONNECTION_STRING")
    if explicit_endpoint:
        _require_local_environment("AZURE_STORAGE_BLOB_ENDPOINT")
    return bool(connection_string or explicit_endpoint)
