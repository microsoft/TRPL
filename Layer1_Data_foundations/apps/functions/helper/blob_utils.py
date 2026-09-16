# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility functions for working with Azure Blob Storage URLs.
"""

import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import Optional
from datetime import datetime, timedelta, timezone
import logging
from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError

from .credential import get_credential
from .storage_client import (
    get_blob_client_from_url,
    get_blob_endpoint,
    get_blob_service_client,
    uses_local_storage,
)

logger = logging.getLogger(__name__)

try:
    from azure.storage.blob import (
        BlobSasPermissions,
        BlobServiceClient,
        ContainerSasPermissions,
        generate_blob_sas,
        generate_container_sas,
    )

    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    logger.warning(
        "azure-storage-blob not installed. SAS token generation unavailable."
    )


def download_trusted_blob_bytes(blob_url: str) -> bytes:
    """Read only from the deployment's configured Blob Storage account."""

    if not AZURE_STORAGE_AVAILABLE:
        raise RuntimeError("azure-storage-blob is required to read blob content")
    blob = get_blob_client_from_url(strip_sas_token_from_url(blob_url))
    return blob.download_blob().readall()


def append_sas_token_to_url(blob_url: str, sas_token: Optional[str]) -> str:
    """
    Append SAS token to a blob URL if not already present.

    Args:
        blob_url: The blob URL (with or without existing query parameters)
        sas_token: SAS token to append (without leading ?)

    Returns:
        URL with SAS token appended

    Examples:
        >>> append_sas_token_to_url("https://account.blob.core.windows.net/container/blob.jpg", "sv=2021&sig=abc")
        'https://account.blob.core.windows.net/container/blob.jpg?sv=2021&sig=abc'

        >>> append_sas_token_to_url("https://account.blob.core.windows.net/container/blob.jpg?existing=param", "sv=2021")
        'https://account.blob.core.windows.net/container/blob.jpg?existing=param&sv=2021'
    """
    if not sas_token:
        return blob_url

    # Clean up SAS token
    if sas_token.startswith("?"):
        sas_token = sas_token[1:]

    # Parse the URL
    parsed = urlparse(blob_url)

    # Parse existing query parameters
    query_params = parse_qs(parsed.query)

    # Parse SAS token parameters
    sas_params = parse_qs(sas_token)

    # Merge parameters (SAS token parameters take precedence)
    query_params.update(sas_params)

    # Flatten the parameters (parse_qs returns lists)
    flat_params = {}
    for key, value in query_params.items():
        flat_params[key] = value[0] if isinstance(value, list) else value

    # Rebuild query string
    new_query = urlencode(flat_params)

    # Rebuild URL
    new_url = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )

    return new_url


_SAS_QUERY_PARAMS = frozenset(
    [
        "sig",
        "sv",
        "se",
        "sp",
        "sr",
        "st",
        "skoid",
        "sktid",
        "skt",
        "ske",
        "sks",
        "skv",
    ]
)


def has_sas_token(blob_url: str) -> bool:
    """
    Check if a URL already has a SAS token.

    Args:
        blob_url: The blob URL to check

    Returns:
        True if URL appears to have a SAS token, False otherwise

    Note:
        Checks for common SAS token parameters like 'sig', 'sv', 'se', 'sp'
    """
    parsed = urlparse(blob_url)
    query_params = parse_qs(parsed.query)
    return any(param in query_params for param in _SAS_QUERY_PARAMS)


def is_sas_token_expired(blob_url: str, skew_minutes: int = 5) -> bool:
    """Return True when the URL has no SAS or the SAS expiry (se) is in the past."""
    if not has_sas_token(blob_url):
        return True

    parsed = urlparse(blob_url)
    query_params = parse_qs(parsed.query)
    expiry_raw = query_params.get("se", [None])[0]
    if not expiry_raw:
        return True

    try:
        expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc) + timedelta(minutes=skew_minutes)
    except (TypeError, ValueError):
        return True


def strip_sas_token_from_url(blob_url: str) -> str:
    """Remove SAS query parameters and return the unsigned blob URL."""
    if not blob_url:
        return blob_url

    parsed = urlparse(blob_url)
    query_params = parse_qs(parsed.query)
    flat_params = {}
    for key, value in query_params.items():
        if key not in _SAS_QUERY_PARAMS:
            flat_params[key] = value[0] if isinstance(value, list) else value

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(flat_params),
            parsed.fragment,
        )
    )


def ensure_blob_accessible(blob_url: str, sas_token: Optional[str] = None) -> str:
    """
    Ensure a blob URL is accessible by appending SAS token if needed.

    Args:
        blob_url: The blob URL
        sas_token: Optional SAS token to append

    Returns:
        URL that should be accessible (with SAS token if provided and not already present)
    """
    if has_sas_token(blob_url) and not is_sas_token_expired(blob_url):
        return blob_url

    base_url = strip_sas_token_from_url(blob_url)

    if not sas_token:
        return base_url

    return append_sas_token_to_url(base_url, sas_token)


def extract_blob_name(blob_url: str) -> str:
    """
    Extract the blob name from a blob URL.

    Args:
        blob_url: The blob URL

    Returns:
        The blob name (path after container)

    Example:
        >>> extract_blob_name("https://account.blob.core.windows.net/container/folder/blob.jpg")
        'folder/blob.jpg'
    """
    parsed = urlparse(blob_url)
    path_parts = parsed.path.strip("/").split("/", 1)

    # Path format: /container/blob/path
    if len(path_parts) > 1:
        return path_parts[1]
    return path_parts[0] if path_parts else ""


def extract_container_name(blob_url: str) -> str:
    """
    Extract the container name from a blob URL.

    Args:
        blob_url: The blob URL

    Returns:
        The container name

    Example:
        >>> extract_container_name("https://account.blob.core.windows.net/container/folder/blob.jpg")
        'container'
    """
    parsed = urlparse(blob_url)
    path_parts = parsed.path.strip("/").split("/", 1)

    return path_parts[0] if path_parts else ""


def get_user_delegation_sas(
    container_name: Optional[str] = None,
    blob_name: Optional[str] = None,
    account_url: Optional[str] = None,
    expiry_hours: int = 24,
    permissions: str = "r",
) -> Optional[str]:
    """Mint a short-lived User Delegation SAS using the Function App's MI.

    A *User Delegation* SAS is signed by an Entra ID key obtained from the
    storage account on behalf of the calling identity, so no account key is
    ever read. The Function MI must hold:

    * ``Storage Blob Delegator`` on the storage account — to call
      ``BlobServiceClient.get_user_delegation_key``.
    * A data-plane role (e.g. ``Storage Blob Data Reader``) that covers the
      permissions requested in ``permissions``.

    Both are granted to the function's managed identity at deployment time.

    Args:
        container_name: Container the SAS should grant access to. Defaults to
            ``AZURE_STORAGE_CONTAINER_NAME``. Ignored unless ``blob_name`` is
            ``None``.
        blob_name: Optional blob path. When provided, mints a blob-scoped SAS.
            When ``None``, mints a container-scoped SAS that grants access to
            every blob in ``container_name``.
        account_url: Optional Blob endpoint. Defaults to
            ``https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net``.
        expiry_hours: Hours until the SAS expires.
        permissions: Permission string composed of letters
            (``r``, ``w``, ``d``, ``l``, ``a``, ``c``).

    Returns:
        The SAS query string (without a leading ``?``), or ``None`` on failure.
    """
    if not AZURE_STORAGE_AVAILABLE:
        logger.warning(
            "azure-storage-blob not available; cannot mint user delegation SAS"
        )
        return None
    if uses_local_storage():
        return None

    try:
        resolved_url = account_url or _default_account_url()
        if not resolved_url:
            logger.error(
                "AZURE_STORAGE_ACCOUNT_NAME is not set; cannot mint user delegation SAS"
            )
            return None

        container = container_name or os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        if not container:
            logger.error(
                "Container name not provided and AZURE_STORAGE_CONTAINER_NAME is unset"
            )
            return None

        now = datetime.now(timezone.utc)
        # Start a few minutes in the past so callers tolerate clock skew.
        start = now - timedelta(minutes=5)
        expiry = now + timedelta(hours=expiry_hours)

        blob_service = BlobServiceClient(
            account_url=resolved_url, credential=get_credential()
        )
        user_delegation_key = blob_service.get_user_delegation_key(start, expiry)

        parsed = urlparse(resolved_url)
        account_name = parsed.netloc.split(".", 1)[0]

        perm_kwargs = {
            "read": "r" in permissions,
            "write": "w" in permissions,
            "delete": "d" in permissions,
            "add": "a" in permissions,
            "create": "c" in permissions,
        }

        if blob_name:
            permission_obj = BlobSasPermissions(**perm_kwargs)
            return generate_blob_sas(
                account_name=account_name,
                container_name=container,
                blob_name=blob_name,
                user_delegation_key=user_delegation_key,
                permission=permission_obj,
                expiry=expiry,
                start=start,
            )

        permission_obj = ContainerSasPermissions(
            list=("l" in permissions),
            **perm_kwargs,
        )
        return generate_container_sas(
            account_name=account_name,
            container_name=container,
            user_delegation_key=user_delegation_key,
            permission=permission_obj,
            expiry=expiry,
            start=start,
        )

    except (AzureError, ValueError, KeyError, AttributeError):
        logger.exception("Failed to mint user delegation SAS")
        return None




def upload_text_to_blob(
        container_name: str,
        blob: str,
        content: str,
        account_url: Optional[str] = None,
    ):
    """
    Uploads text content to an Azure Blob Storage container using managed identity.

    Note: This is different from content_source_client.upload_to_blob which is record-specific.
    This function is generic and authenticates via :func:`helper.credential.get_credential`.

    Args:
        container_name (str): Name of the blob container.
        blob (str): Blob name or blob path.
        content (str): Text or data to upload.
        account_url (Optional[str]): Blob endpoint, e.g.
            ``https://<account>.blob.core.windows.net``. Defaults to
            ``https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net``.

    Returns:
        bool: True if upload is successful, otherwise False.
    """
    try:
        resolved_url = account_url or _default_account_url()
        if not resolved_url:
            logger.error(
                "Cannot upload blob: AZURE_STORAGE_ACCOUNT_NAME is not set and "
                "account_url was not provided."
            )
            return False

        blob_service = (
            get_blob_service_client()
            if account_url is None
            else BlobServiceClient(account_url=resolved_url, credential=get_credential())
        )
        container_client = blob_service.get_container_client(container_name)

        try:
            container_client.get_container_properties()
        except ResourceNotFoundError:
            logger.exception("Container '%s' not found. Creating it...", container_name)
            container_client.create_container()

        blob_client = container_client.get_blob_client(blob)
        blob_client.upload_blob(content, overwrite=True)

        logger.info("Successfully uploaded blob: %s", blob)
        return True

    except ResourceExistsError:
        logger.exception("Blob '%s' already exists and overwrite=False.", blob)

    except ResourceNotFoundError:
        logger.exception("Invalid container or blob path.")

    except AzureError as ex:
        logger.exception("Azure Blob Storage error: %s", ex)

    except Exception as ex:
        logger.exception("Unexpected error: %s", ex)

    return False


def _default_account_url() -> Optional[str]:
    """Return the Blob endpoint derived from ``AZURE_STORAGE_ACCOUNT_NAME``."""
    return get_blob_endpoint()
