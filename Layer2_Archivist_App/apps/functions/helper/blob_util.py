# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Blob storage utility service using managed identity (DefaultAzureCredential).
"""
# pylint:disable = line-too-long

import logging
import os
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, BlobClient
    from azure.core.exceptions import AzureError, ResourceNotFoundError
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    class AzureError(Exception):  # type: ignore
        """Fallback for AzureError when azure-storage-blob is not installed."""

    class ResourceNotFoundError(AzureError):  # type: ignore
        """Fallback for ResourceNotFoundError when azure-storage-blob is not installed."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# pylint: disable=wrong-import-position
from helper.config import get_storage_config

logger = logging.getLogger(__name__)


class BlobDownloadError(Exception):
    """Raised when blob download fails."""


class BlobAuthenticationError(Exception):
    """Raised when blob authentication fails."""


class BlobNotFoundError(Exception):
    """Raised when blob is not found."""


def parse_blob_url(blob_url: str) -> tuple[str, str]:
    """
    Parse blob URL to extract container and blob name.

    Args:
        blob_url: The blob URL

    Returns:
        Tuple of (container_name, blob_name)

    Raises:
        ValueError: If URL format is invalid
    """
    parsed = urlparse(blob_url)

    # Extract container and blob name from path
    path_parts = parsed.path.strip('/').split('/', 1)
    if len(path_parts) < 2:
        raise ValueError(f"Invalid blob URL format - missing container or blob name: {blob_url}")

    container_name = path_parts[0]
    blob_name = path_parts[1]

    return container_name, blob_name


def _parse_local_blob_url(blob_url: str) -> Optional[tuple[str, str]]:
    """Parse a URL only when it matches the configured local Azurite endpoint."""
    config = get_storage_config()
    if (
        os.getenv("ENVIRONMENT", "production").strip().lower() != "local"
        or not config.connection_string
    ):
        return None

    endpoint = ""
    for part in config.connection_string.split(";"):
        key, separator, value = part.partition("=")
        if separator and key.strip().lower() == "blobendpoint":
            endpoint = value.strip()
            break
    if not endpoint:
        return None

    parsed = urlparse(blob_url)
    configured = urlparse(endpoint)
    if (
        parsed.scheme.lower() != configured.scheme.lower()
        or parsed.netloc.lower() != configured.netloc.lower()
    ):
        return None

    endpoint_path = configured.path.rstrip("/")
    path_prefix = f"{endpoint_path}/" if endpoint_path else "/"
    if not parsed.path.startswith(path_prefix):
        return None

    relative_path = unquote(parsed.path[len(path_prefix):])
    if "/" not in relative_path:
        return None
    container_name, blob_name = relative_path.split("/", 1)
    if not container_name or not blob_name:
        return None
    return container_name, blob_name


def get_blob_client(blob_url: str) -> BlobClient:
    """
    Get an authenticated BlobClient for the given URL using managed identity.

    Uses the storage account hostname from the URL when present so reads work
    against Data Foundations blobs even if AZURE_STORAGE_ACCOUNT_NAME differs.
    """
    if not AZURE_STORAGE_AVAILABLE:
        raise ImportError(
            "azure-storage-blob is required. "
            "Install with: pip install azure-storage-blob"
        )

    local_parts = _parse_local_blob_url(blob_url)
    if local_parts:
        config = get_storage_config()
        blob_service_client = BlobServiceClient.from_connection_string(
            config.connection_string
        )
        return blob_service_client.get_blob_client(
            container=local_parts[0],
            blob=local_parts[1],
        )

    parsed = urlparse(blob_url)
    host = (parsed.netloc or "").lower()
    if parsed.scheme.lower() != "https" or not host.endswith(
        ".blob.core.windows.net"
    ):
        raise ValueError(f"Invalid blob URL format: {blob_url}")

    account = host.removesuffix(".blob.core.windows.net")
    if not account:
        raise ValueError(f"Invalid blob URL format: {blob_url}")

    container_name, blob_name = parse_blob_url(blob_url)

    logger.debug("Using managed identity for blob authentication (account=%s)", account)
    blob_service_client = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    return blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )


def try_download_blob_text(blob_url: str) -> Optional[str]:
    """Download blob text; return None when the blob is missing or unreadable."""
    if not blob_url or not str(blob_url).strip():
        return None
    try:
        return download_blob_text(blob_url.strip())
    except BlobNotFoundError:
        return None
    except (BlobDownloadError, BlobAuthenticationError, ValueError) as exc:
        logger.warning("Could not read blob %s: %s", blob_url, exc)
        return None


def download_blob_text(blob_url: str) -> str:
    """
    Download blob content as text using connection string authentication.

    Args:
        blob_url: The blob URL

    Returns:
        Blob content as string

    Raises:
        BlobNotFoundError: If the blob doesn't exist (404)
        BlobAuthenticationError: If authentication fails (403)
        BlobDownloadError: For other download failures
        ValueError: If blob_url is invalid or connection string not configured
        ImportError: If azure-storage-blob is not installed
    """
    if not blob_url:
        raise ValueError("blob_url cannot be empty")

    try:
        blob_client = get_blob_client(blob_url)
        blob_data = blob_client.download_blob()
        data = blob_data.readall()
        text = data.decode("utf-8")

        logger.info("Successfully downloaded blob from %s", blob_url)
        return text

    except UnicodeDecodeError as e:
        logger.error("Failed to decode blob content from %s as UTF-8: %s", blob_url, e)
        raise BlobDownloadError(f"Blob content is not valid UTF-8: {blob_url}") from e

    except ResourceNotFoundError as e:
        logger.warning("Blob not found: %s", blob_url)
        raise BlobNotFoundError(f"Blob not found: {blob_url}") from e

    except AzureError as e:
        error_msg = str(e).lower()

        if any(x in error_msg for x in ["404", "not found", "blobnotfound"]):
            logger.warning("Blob not found: %s", blob_url)
            raise BlobNotFoundError(f"Blob not found: {blob_url}") from e

        if any(x in error_msg for x in ["403", "unauthorized", "authentication", "forbidden"]):
            logger.warning("Authentication failed for blob: %s", blob_url)
            raise BlobAuthenticationError(f"Authentication failed for blob: {blob_url}") from e

        logger.error("Failed to download blob from %s: %s", blob_url, e)
        raise BlobDownloadError(f"Failed to download blob from {blob_url}: {str(e)}") from e


def get_blob_service() -> tuple["BlobServiceClient", str] | None:
    """Return (BlobServiceClient, account_url) using env credentials, or None."""
    import os
    if not AZURE_STORAGE_AVAILABLE:
        return None

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")

    if conn_str:
        bsc = BlobServiceClient.from_connection_string(conn_str)
        return bsc, bsc.url.rstrip("/")
    if account_name:
        account_url = f"https://{account_name}.blob.core.windows.net"
        bsc = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        return bsc, account_url
    return None


def upload_blob_content(
    blob_path: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    container_name: str | None = None,
) -> str | None:
    """Upload binary content to Azure Blob Storage and return the full blob URL.

    Uses AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME with
    DefaultAzureCredential. Returns None if no credentials are configured.
    """
    import os
    container_name = container_name or os.getenv("DIGITAL_ITEMS_ASSETS_CONTAINER", "digital-resources")

    svc = get_blob_service()
    if not svc:
        return None
    bsc, account_url = svc

    try:
        from azure.storage.blob import ContentSettings
        container_client = bsc.get_container_client(container_name)
        if not container_client.exists():
            container_client.create_container()

        blob_client = container_client.get_blob_client(blob_path)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return f"{account_url}/{container_name}/{blob_path}"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to upload blob %s: %s", blob_path, exc)
        return None
