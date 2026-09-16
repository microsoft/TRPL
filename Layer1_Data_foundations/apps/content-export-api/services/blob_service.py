# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Blob storage helpers for content hydration.

- inline mode reads OCR/visual text already stored on the Cosmos record; this module
  is used to read detailed visual descriptions that live only as blobs, and to mint
  short-lived SAS URLs for reference mode.
- SAS minting uses a user-delegation key (managed identity), so no account key is
  required. When storage is not configured, the plain blob URL is returned unsigned.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote, urlparse

from core.config import settings
from services.azure_credential import get_azure_credential

logger = logging.getLogger(__name__)


def build_ocr_text_blob_url(record_id: str, asset_id: str) -> Optional[str]:
    """Derived path for an asset's flexible OCR text blob."""
    endpoint = _blob_endpoint()
    container = settings.storage_container_name
    if not endpoint or not container:
        return None
    return f"{endpoint}/{container}/{record_id}/ocr/flexible/{asset_id}/v1.txt"


def _blob_service_client():
    from azure.storage.blob import BlobServiceClient

    if settings.storage_connection_string:
        if settings.environment != "local":
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING is only supported in local mode"
            )
        return BlobServiceClient.from_connection_string(
            settings.storage_connection_string
        )

    endpoint = _blob_endpoint()
    if not endpoint:
        raise RuntimeError("Blob Storage endpoint is not configured")
    return BlobServiceClient(endpoint, credential=get_azure_credential())


def _blob_endpoint() -> Optional[str]:
    if settings.storage_blob_endpoint:
        if settings.environment != "local":
            raise RuntimeError(
                "AZURE_STORAGE_BLOB_ENDPOINT is only supported in local mode"
            )
        return settings.storage_blob_endpoint.rstrip("/")
    if settings.storage_connection_string:
        return _blob_service_client().url.rstrip("/")
    if settings.storage_account_name:
        return f"https://{settings.storage_account_name}.blob.core.windows.net"
    return None


def _parse_blob_url(blob_url: str):
    parsed = urlparse(blob_url)
    endpoint = _blob_endpoint()
    if not endpoint:
        raise ValueError("Blob Storage endpoint is not configured")
    expected = urlparse(endpoint)
    if (
        parsed.scheme != expected.scheme
        or parsed.hostname != expected.hostname
        or parsed.port != expected.port
    ):
        raise ValueError("Blob URL does not use the configured endpoint")

    path = parsed.path.lstrip("/")
    endpoint_path = expected.path.strip("/")
    if endpoint_path:
        prefix = f"{endpoint_path}/"
        if not path.startswith(prefix):
            raise ValueError("Blob URL does not use the configured account path")
        path = path[len(prefix) :]
    container, _, blob_name = path.partition("/")
    if not container or not blob_name:
        raise ValueError("Blob URL must include a container and blob")
    return unquote(container), unquote(blob_name)


def sas_url(blob_url: Optional[str]) -> Optional[str]:
    """Return blob_url with a read-only user-delegation SAS appended.

    Falls back to the unsigned URL if SAS minting is unavailable.
    """
    if not blob_url:
        return None
    if settings.storage_connection_string:
        if settings.environment != "local":
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING is only supported in local mode"
            )
        return blob_url
    if settings.storage_blob_endpoint and settings.environment != "local":
        raise RuntimeError(
            "AZURE_STORAGE_BLOB_ENDPOINT is only supported in local mode"
        )
    try:
        from azure.storage.blob import (
            BlobSasPermissions,
            BlobServiceClient,
            generate_blob_sas,
        )

        container, blob_name = _parse_blob_url(blob_url)
        service = _blob_service_client()

        now = datetime.now(timezone.utc)
        expiry = now + timedelta(hours=settings.sas_expiry_hours)
        delegation_key = service.get_user_delegation_key(now - timedelta(minutes=5), expiry)

        token = generate_blob_sas(
            account_name=settings.storage_account_name,
            container_name=container,
            blob_name=blob_name,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
            start=now - timedelta(minutes=5),
        )
        return f"{blob_url}?{token}"
    except Exception as exc:  # noqa: BLE001 - SAS is best-effort; never fail the response
        logger.warning("Could not mint SAS for %s: %s", blob_url, exc)
        return blob_url


def read_text(blob_url: Optional[str]) -> Optional[str]:
    """Download and return a blob's text content, or None on failure/absence."""
    if not blob_url:
        return None
    try:
        from azure.storage.blob import BlobClient

        container, blob_name = _parse_blob_url(blob_url)
        client = _blob_service_client().get_blob_client(
            container=container,
            blob=blob_name,
        )
        data = client.download_blob().readall()
        return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception as exc:  # noqa: BLE001 - missing/unreadable blob is non-fatal
        logger.warning("Could not read blob text %s: %s", blob_url, exc)
        return None
