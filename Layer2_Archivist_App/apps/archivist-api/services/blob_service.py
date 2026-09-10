"""
Blob storage utility service for generating SAS tokens.
"""

import json
import logging
import asyncio
import mimetypes
import threading
from typing import Optional, List, Tuple, Any, Dict
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
from datetime import datetime, timedelta, timezone

try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import (
        generate_blob_sas,
        BlobSasPermissions,
        BlobClient,
        BlobServiceClient,
        ContentSettings,
    )
    from azure.core.exceptions import AzureError, HttpResponseError, ResourceExistsError, ResourceNotFoundError

    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    AzureError = Exception  # Fallback for type hints

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings

logger = logging.getLogger(__name__)


def _get_blob_service_client(account_name: Optional[str] = None) -> "BlobServiceClient":
    """Build a Blob client using local emulator credentials or managed identity."""
    if not AZURE_STORAGE_AVAILABLE:
        raise ImportError("azure-storage-blob is required")
    if settings.azure_storage_connection_string:
        if settings.environment != "local":
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is allowed only when "
                "ENVIRONMENT=local"
            )
        return BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        )
    resolved = (account_name or settings.azure_storage_account_name or "").strip()
    if resolved:
        return BlobServiceClient(
            account_url=f"https://{resolved}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
    raise ValueError(
        "Azure Storage is not configured: set AZURE_STORAGE_ACCOUNT_NAME "
        "(DF content-assets; use ARCHIVIST_STORAGE_ACCOUNT_NAME for EPUB when split)"
    )


def _get_archivist_blob_service_client() -> "BlobServiceClient":
    """Blob client for Archivist-owned storage (EPUB files and section JSON)."""
    account = (settings.archivist_storage_account_name or "").strip()
    if not account:
        raise ValueError(
            "Archivist storage is not configured: set ARCHIVIST_STORAGE_ACCOUNT_NAME"
        )
    return _get_blob_service_client(account)


def _parse_blob_storage_url(blob_url: str) -> Optional[Tuple[str, str, str]]:
    """Return (account_name, container_name, blob_path) for https://*.blob.core.windows.net URLs."""
    if not blob_url.startswith("https://"):
        return None
    try:
        parsed = urlparse(blob_url)
        host = (parsed.netloc or "").lower()
        if ".blob.core.windows.net" not in host:
            return None
        account = host.split(".blob.")[0]
        path = parsed.path.lstrip("/")
        if "/" not in path:
            return None
        container_name, blob_path = path.split("/", 1)
        if not container_name or not blob_path:
            return None
        return account, container_name, blob_path
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _parse_local_blob_storage_url(
    blob_url: str,
) -> Optional[Tuple[str, str, str]]:
    """Return local Azurite account, container, and blob path when configured."""
    if (
        settings.environment != "local"
        or not settings.azure_storage_connection_string
    ):
        return None

    endpoint = None
    for part in settings.azure_storage_connection_string.split(";"):
        key, separator, value = part.partition("=")
        if separator and key.strip().lower() == "blobendpoint":
            endpoint = value.strip()
            break
    if not endpoint:
        return None

    try:
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
        container_name, blob_path = relative_path.split("/", 1)
        if not container_name or not blob_path:
            return None
        account_name = (
            endpoint_path.rsplit("/", 1)[-1]
            or settings.azure_storage_account_name
            or ""
        )
        return account_name, container_name, blob_path
    except (TypeError, ValueError):
        return None


def _local_blob_client_for_url(blob_url: str) -> Optional[BlobClient]:
    """Build a local-emulator BlobClient only for the configured BlobEndpoint."""
    parsed = _parse_local_blob_storage_url(blob_url)
    if not parsed:
        return None
    _, container_name, blob_path = parsed
    return _get_blob_service_client().get_blob_client(container_name, blob_path)


# Cache user delegation keys per storage account. The key is account-scoped and
# valid for hours, so caching avoids a get_user_delegation_key network round trip
# per blob URL when signing many URLs in a single response (e.g. digital-items
# pages with dozens of thumbnails).
_UDK_CACHE: Dict[str, Tuple[Any, datetime]] = {}
_UDK_CACHE_LOCK = threading.Lock()


def _get_cached_user_delegation_key(account_name: str) -> Tuple[Any, datetime]:
    """Return a TTL-cached user delegation key and its expiry (refreshed before expiry).

    The expiry is returned so callers can cap the SAS expiry to it: a user
    delegation SAS whose ``se`` exceeds the key's ``ske`` is rejected by Azure
    Storage with AuthenticationFailed (403).
    """
    now = datetime.now(timezone.utc)
    with _UDK_CACHE_LOCK:
        cached = _UDK_CACHE.get(account_name)
        if cached is not None and now < cached[1]:
            return cached[0], cached[2]
    svc = BlobServiceClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    key_start = now - timedelta(minutes=5)
    key_expiry = now + timedelta(hours=min(settings.sas_token_expiry_hours, 7 * 24))
    udk = svc.get_user_delegation_key(key_start, key_expiry)
    # Refresh ~10 minutes before the key actually expires.
    refresh_after = key_expiry - timedelta(minutes=10)
    with _UDK_CACHE_LOCK:
        _UDK_CACHE[account_name] = (udk, refresh_after, key_expiry)
    return udk, key_expiry


def _user_delegation_read_sas_for_blob_url(blob_url: str) -> Optional[str]:
    """Read-only blob SAS signed with a user delegation key (no account keys)."""
    if not AZURE_STORAGE_AVAILABLE:
        return None
    parsed = _parse_blob_storage_url(blob_url)
    if not parsed:
        return None
    account_name, container_name, blob_name = parsed
    try:
        now = datetime.now(timezone.utc)
        udk, key_expiry = _get_cached_user_delegation_key(account_name)
        # The SAS expiry must not exceed the delegation key expiry (ske), or Azure
        # rejects it with AuthenticationFailed. Cap to the key expiry.
        sas_expiry = min(now + timedelta(hours=settings.sas_token_expiry_hours), key_expiry)
        return generate_blob_sas(
            account_name,
            container_name,
            blob_name,
            user_delegation_key=udk,
            permission=BlobSasPermissions(read=True),
            expiry=sas_expiry,
            start=now,
        )
    except HttpResponseError as e:
        if getattr(e, "error_code", None) == "AuthorizationPermissionMismatch":
            logger.warning(
                "Cannot mint user delegation SAS for account %s (API MI needs Storage Blob Delegator)",
                account_name,
            )
            return None
        logger.exception("Failed to generate user delegation SAS for blob URL")
        return None
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to generate user delegation SAS for blob URL")
        return None


def _blob_client_for_url_managed_identity(blob_url: str) -> Optional[BlobClient]:
    """Return a BlobClient using MI when URL targets *.blob.core.windows.net and path has container/blob."""
    if not AZURE_STORAGE_AVAILABLE or not blob_url.startswith("https://"):
        return None
    try:
        parsed = urlparse(blob_url)
        host = (parsed.netloc or "").lower()
        if ".blob.core.windows.net" not in host:
            return None
        account = host.split(".blob.")[0]
        path = parsed.path.lstrip("/")
        if "/" not in path:
            return None
        container_name, blob_path = path.split("/", 1)
        if not container_name or not blob_path:
            return None
        svc = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        return svc.get_blob_client(container_name, blob_path)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("MI blob client not usable for URL", exc_info=True)
        return None


class BlobDownloadError(Exception):
    """Raised when blob download fails."""


class BlobAuthenticationError(BlobDownloadError):
    """Raised when blob authentication fails."""


class BlobNotFoundError(BlobDownloadError):
    """Raised when blob is not found."""


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
    """
    parsed = urlparse(blob_url)
    query_params = parse_qs(parsed.query)
    return any(param in query_params for param in _SAS_QUERY_PARAMS)


def is_sas_token_expired(blob_url: str, skew_minutes: int = 5) -> bool:
    """Return True when the URL has no usable SAS (missing, not yet valid, or expired)."""
    if not has_sas_token(blob_url):
        return True

    parsed = urlparse(blob_url)
    query_params = parse_qs(parsed.query)
    now = datetime.now(timezone.utc)
    skew = timedelta(minutes=skew_minutes)

    start_raw = query_params.get("st", [None])[0]
    if start_raw:
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if now + skew < start:
                return True
        except (TypeError, ValueError):
            return True

    expiry_raw = query_params.get("se", [None])[0]
    if not expiry_raw:
        return True

    try:
        expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= now + skew
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


def append_sas_token_to_url(blob_url: str, sas_token: str) -> str:
    """
    Append SAS token to a blob URL if not already present.

    Args:
        blob_url: The blob URL (with or without existing query parameters)
        sas_token: SAS token to append (without leading ?)

    Returns:
        URL with SAS token appended
    """
    if not sas_token:
        return blob_url

    # Clean up SAS token
    if sas_token.startswith('?'):
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
    new_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))
    return new_url


def add_sas_token_to_url(blob_url: str) -> str:
    """
    Add SAS token to a blob URL if credentials are configured and URL doesn't have one.

    Args:
        blob_url: The blob URL

    Returns:
        URL with SAS token appended (if applicable)
    """
    if not blob_url:
        return blob_url

    if _parse_local_blob_storage_url(blob_url):
        return strip_sas_token_from_url(blob_url)

    # Reuse a still-valid SAS to avoid redundant delegation key calls.
    if has_sas_token(blob_url) and not is_sas_token_expired(blob_url):
        return blob_url

    base_url = strip_sas_token_from_url(blob_url)
    sas_token = _user_delegation_read_sas_for_blob_url(base_url)
    if sas_token:
        return append_sas_token_to_url(base_url, sas_token)

    logger.warning("Could not mint SAS token for blob URL: %s", base_url[:120])
    return base_url


def guess_blob_content_type(blob_path: str, content_type_from_blob: Optional[str] = None) -> str:
    """Resolve a response Content-Type for blob bytes."""
    if content_type_from_blob:
        return content_type_from_blob
    guessed, _ = mimetypes.guess_type(blob_path)
    return guessed or "application/octet-stream"


def _content_type_from_downloader(downloader, blob_path: str) -> str:
    props = downloader.properties
    content_settings = getattr(props, "content_settings", None)
    content_type = (
        getattr(content_settings, "content_type", None) if content_settings else None
    )
    return guess_blob_content_type(blob_path, content_type)


def _map_azure_blob_error(blob_url: str, error: AzureError) -> Exception:
    error_msg = str(error).lower()
    if "404" in error_msg or "not found" in error_msg or "blobnotfound" in error_msg:
        logger.warning("Blob not found: %s", blob_url)
        return BlobNotFoundError(f"Blob not found: {blob_url}")
    if "403" in error_msg or "unauthorized" in error_msg or "authentication" in error_msg:
        logger.warning("Authentication failed for blob: %s", blob_url)
        return BlobAuthenticationError(f"Authentication failed for blob: {blob_url}")
    logger.error("Failed to download blob from %s: %s", blob_url, error, exc_info=error)
    return BlobDownloadError(f"Failed to download blob from {blob_url}: {str(error)}")


def _download_blob_bytes_with_mi(blob_url: str, blob_path: str) -> Tuple[bytes, str]:
    mi_client = _blob_client_for_url_managed_identity(blob_url)
    if mi_client is None:
        raise BlobAuthenticationError(f"Managed identity blob client unavailable: {blob_url}")
    downloader = mi_client.download_blob()
    return downloader.readall(), _content_type_from_downloader(downloader, blob_path)


def _download_blob_bytes_with_sas(blob_url: str, blob_path: str) -> Tuple[bytes, str]:
    signed_url = add_sas_token_to_url(blob_url)
    if not has_sas_token(signed_url):
        raise BlobAuthenticationError(f"Could not sign blob URL for download: {blob_url}")
    blob_client = BlobClient.from_blob_url(signed_url)
    downloader = blob_client.download_blob()
    return downloader.readall(), _content_type_from_downloader(downloader, blob_path)


async def download_blob_bytes(blob_url: str) -> Tuple[bytes, str]:
    """
    Download blob content as raw bytes (for streaming to authenticated clients).

    Tries managed identity first, then user-delegation SAS (same path as document URL signing).
    """
    if not blob_url:
        raise ValueError("blob_url cannot be empty")

    clean_url = strip_sas_token_from_url(blob_url)
    local_client = _local_blob_client_for_url(clean_url)
    if local_client is not None:
        local_parsed = _parse_local_blob_storage_url(clean_url)
        blob_path = local_parsed[2] if local_parsed else ""
        loop = asyncio.get_event_loop()

        def _local_download() -> Tuple[bytes, str]:
            downloader = local_client.download_blob()
            return (
                downloader.readall(),
                _content_type_from_downloader(downloader, blob_path),
            )

        try:
            return await loop.run_in_executor(None, _local_download)
        except AzureError as error:
            raise _map_azure_blob_error(clean_url, error) from error

    if not blob_url.startswith("https://"):
        raise ValueError(f"Invalid blob URL format: {blob_url}")

    parsed = _parse_blob_storage_url(strip_sas_token_from_url(blob_url))
    blob_path = parsed[2] if parsed else ""
    base_url = clean_url
    loop = asyncio.get_event_loop()

    if not has_sas_token(base_url):
        mapped_mi_error: Optional[Exception] = None
        try:
            data, content_type = await loop.run_in_executor(
                None, _download_blob_bytes_with_mi, base_url, blob_path
            )
            logger.debug("Downloaded blob bytes with managed identity: %s", base_url[:100])
            return data, content_type
        except BlobAuthenticationError as mi_error:
            logger.warning(
                "MI blob download unavailable for %s, trying user-delegation SAS: %s",
                base_url[:100],
                mi_error,
            )
            mapped_mi_error = mi_error
        except AzureError as mi_error:
            logger.warning(
                "MI blob download failed for %s, trying user-delegation SAS: %s",
                base_url[:100],
                mi_error,
            )
            mapped_mi_error = _map_azure_blob_error(base_url, mi_error)

        if mapped_mi_error is not None:
            try:
                data, content_type = await loop.run_in_executor(
                    None, _download_blob_bytes_with_sas, base_url, blob_path
                )
                logger.debug("Downloaded blob bytes with SAS fallback: %s", base_url[:100])
                return data, content_type
            except BlobAuthenticationError as sas_error:
                raise mapped_mi_error from sas_error
            except AzureError as sas_error:
                logger.warning("SAS blob download also failed for %s: %s", base_url[:100], sas_error)
                raise _map_azure_blob_error(base_url, sas_error) from sas_error

    try:
        data, content_type = await loop.run_in_executor(
            None, _download_blob_bytes_with_sas, base_url, blob_path
        )
        logger.debug("Successfully downloaded blob bytes: %s", base_url[:100])
        return data, content_type
    except AzureError as error:
        raise _map_azure_blob_error(base_url, error) from error


async def download_blob_text(blob_url: str) -> str:
    """
    Download blob content as text asynchronously, automatically adding SAS token if needed.
    
    This function:
    1. Validates the blob URL format
    2. Automatically adds SAS token if not present
    3. Downloads the blob content in a thread pool (non-blocking)
    4. Decodes content as UTF-8 text
    
    Args:
        blob_url: The blob URL
    
    Returns:
        Blob content as string
        
    Raises:
        BlobNotFoundError: If the blob doesn't exist (404)
        BlobAuthenticationError: If authentication fails (403)
        BlobDownloadError: For other download failures
        ValueError: If blob_url is invalid or empty
    """
    if not blob_url:
        raise ValueError("blob_url cannot be empty")

    clean_url = strip_sas_token_from_url(blob_url)
    local_client = _local_blob_client_for_url(clean_url)
    if local_client is not None:
        try:
            loop = asyncio.get_event_loop()

            def _local_download() -> str:
                return local_client.download_blob().readall().decode("utf-8")

            return await loop.run_in_executor(None, _local_download)
        except UnicodeDecodeError as error:
            raise BlobDownloadError(
                f"Blob content is not valid UTF-8: {blob_url}"
            ) from error
        except AzureError as error:
            raise _map_azure_blob_error(clean_url, error) from error

    if not blob_url.startswith("https://"):
        raise ValueError(f"Invalid blob URL format: {blob_url}")

    try:
        loop = asyncio.get_event_loop()

        if not has_sas_token(blob_url) or is_sas_token_expired(blob_url):
            mi_client = _blob_client_for_url_managed_identity(clean_url)
            if mi_client is not None:

                def _mi_download():
                    data = mi_client.download_blob().readall()
                    return data.decode("utf-8")

                result = await loop.run_in_executor(None, _mi_download)
                logger.debug("Downloaded blob with managed identity: %s", clean_url[:100])
                return result

        signed_url = add_sas_token_to_url(clean_url)
        if not has_sas_token(signed_url):
            raise BlobAuthenticationError(
                f"Cannot read blob (managed identity/SAS unavailable): {clean_url[:120]}"
            )

        def _download():
            blob_client = BlobClient.from_blob_url(signed_url)
            data = blob_client.download_blob().readall()
            return data.decode("utf-8")

        result = await loop.run_in_executor(None, _download)
        logger.debug("Successfully downloaded blob: %s", clean_url[:100])
        return result

    except UnicodeDecodeError as e:
        logger.error(
            "Failed to decode blob content from %s as UTF-8",
            blob_url,
            exc_info=e,
        )
        raise BlobDownloadError(f"Blob content is not valid UTF-8: {blob_url}") from e

    except AzureError as e:
        raise _map_azure_blob_error(blob_url, e) from e


async def download_multiple_blobs(blob_urls: List[str]) -> List[Optional[str]]:
    """
    Download multiple blobs concurrently.

    This function downloads multiple blobs in parallel and handles errors gracefully.
    Failed downloads return None instead of raising exceptions.

    Args:
        blob_urls: List of blob URLs to download

    Returns:
        List of blob contents (or None for failed downloads) in same order as input

    Note:
        - This function swallows exceptions and returns None for failed downloads
        - Check the logs for specific error details
        - Expected failures (404, 403) use warning level logging
        - Unexpected failures use error level logging
    """
    async def safe_download(url: str) -> Optional[str]:
        """Download with exception handling."""
        try:
            return await download_blob_text(url)
        except (BlobNotFoundError, BlobAuthenticationError) as e:
            logger.warning("Expected blob download failure for %s: %s", url, e)
            return None
        except (BlobDownloadError, ValueError) as e:
            logger.error("Unexpected blob download failure for %s: %s", url, e, exc_info=e)
            return None

    if not blob_urls:
        logger.debug("No blob URLs to download")
        return []

    logger.info("Starting concurrent download of %d blobs", len(blob_urls))
    tasks = [safe_download(url) for url in blob_urls]
    results = await asyncio.gather(*tasks)

    # Log summary
    success_count = sum(1 for r in results if r is not None)
    logger.info(
        "Completed blob downloads: %d successful, %d failed out of %d total",
        success_count,
        len(results) - success_count,
        len(results)
    )

    return results

def upload_to_blob(
        container_name: str,
        blob: str,
        content: str
    ):
    """
    Uploads content to an Azure Blob Storage container.

    Args:
        container_name (str): Name of the container.
        blob (str): Destination blob name or path.
        content (str): The text / data to upload.

    Returns:
        bool: True if the upload succeeds.

    Raises:
        ResourceExistsError: If the blob exists and overwrite=False.
        ResourceNotFoundError: If the container or path is invalid.
        AzureError: For other Azure storage failures.
    """
    try:
        blob_service = _get_blob_service_client()
        container_client = blob_service.get_container_client(container_name)

        try:
            container_client.get_container_properties()
        except ResourceNotFoundError:
            logger.warning("Container '%s' not found. Creating it...", container_name)
            container_client.create_container()

        # Upload blob
        blob_client = container_client.get_blob_client(blob)
        blob_client.upload_blob(content, overwrite=True)

        logger.info("Successfully uploaded blob: %s", blob)
        return True

    except ResourceExistsError as exc:
        logger.error("Blob '%s' already exists and overwrite=False.", blob, exc_info=exc)
        raise

    except ResourceNotFoundError as exc:
        logger.error("Invalid container or blob path for upload.", exc_info=exc)
        raise

    except AzureError as exc:
        logger.error("Failed to upload blob '%s'.", blob, exc_info=exc)
        raise


def upload_text_to_blob_url(blob_url: str, content: str) -> None:
    """
    Upload UTF-8 text to the storage account/container/blob encoded in blob_url.

    Uses the account hostname from the URL (e.g. DF content-assets), not only
    AZURE_STORAGE_ACCOUNT_NAME, so recorded URLs match where bytes are stored.
    """
    if not AZURE_STORAGE_AVAILABLE:
        raise ImportError("azure-storage-blob is required")

    clean_url = strip_sas_token_from_url(blob_url)
    local_parsed = _parse_local_blob_storage_url(clean_url)
    if local_parsed:
        account_name, _, _ = local_parsed
        blob_client = _local_blob_client_for_url(clean_url)
        if blob_client is None:
            raise ValueError(f"Invalid local blob URL for upload: {blob_url}")
    else:
        parsed = _parse_blob_storage_url(clean_url)
        if not parsed:
            raise ValueError(f"Invalid blob URL for upload: {blob_url}")
        account_name, container_name, blob_path = parsed
        blob_service = _get_blob_service_client(account_name)
        blob_client = blob_service.get_blob_client(
            container=container_name,
            blob=blob_path,
        )

    try:
        blob_client.upload_blob(
            content.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="text/plain; charset=utf-8"),
        )
    except AzureError as e:
        if getattr(e, "error_code", None) == "AuthorizationPermissionMismatch":
            raise BlobAuthenticationError(
                f"Cannot write blob to {account_name} (API MI needs Storage Blob Data Contributor)"
            ) from e
        raise
    logger.info("Successfully uploaded blob to %s", clean_url[:120])


def upload_binary_to_blob(
    container_name: str,
    blob_path: str,
    content: bytes,
    content_type: str = "application/octet-stream"
) -> str:
    """
    Uploads binary content to an Azure Blob Storage container.

    Args:
        container_name: Name of the container (must already exist).
        blob_path: Destination blob name or path.
        content: The binary data to upload.
        content_type: MIME type of the content (default: application/octet-stream).

    Returns:
        str: The URL of the uploaded blob.

    Raises:
        AzureError: For Azure storage failures.
        ValueError: If content is empty.
    """
    if not content:
        raise ValueError("Content cannot be empty")

    blob_service = _get_archivist_blob_service_client()
    container_client = blob_service.get_container_client(container_name)

    # Upload blob with content type
    blob_client = container_client.get_blob_client(blob_path)
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type)
    )

    logger.info("Successfully uploaded binary blob: %s/%s", container_name, blob_path)
    return blob_client.url


def get_container_name(blob_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract storage blob origin, container name, and blob path from an Azure Blob Storage URL.
    
    Args:
        blob_url: Azure Blob Storage URL
        
    Returns:
        Tuple of (origin_url, container_name, blob_path) or (None, None, None) if parsing fails
        origin_url: The base URL (e.g., https://account.blob.core.windows.net)
    """
    parsed = urlparse(blob_url)

    # Extract origin (scheme + netloc)
    origin_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None

    path_parts = parsed.path.lstrip('/').split('/')

    if not path_parts:
        return origin_url, None, None

    container_name = path_parts[0]
    # The blob path is everything after the container name
    blob_path = '/'.join(path_parts[1:]) if len(path_parts) > 1 else None

    return origin_url, container_name, blob_path


async def download_json_from_blob(blob_url: str) -> List[Dict[str, Any]]:
    """
    Download and parse JSON content from blob storage.
    
    Args:
        blob_url: URL to the JSON blob
        
    Returns:
        Parsed JSON data (typically a list of sections)
        
    Raises:
        BlobNotFoundError: If blob doesn't exist
        BlobDownloadError: If download or parsing fails
    """
    try:
        json_content = await download_blob_text(blob_url)
        return json.loads(json_content)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON from blob %s", blob_url, exc_info=e)
        raise BlobDownloadError(f"Invalid JSON in blob: {blob_url}") from e


def upload_json_to_blob(
    container_name: str,
    blob_path: str,
    data: Any
) -> str:
    """
    Upload JSON data to blob storage.
    
    Args:
        container_name: Name of the container
        blob_path: Path/name for the blob
        data: Data to serialize as JSON
        
    Returns:
        URL of the uploaded blob
    """
    json_content = json.dumps(data, ensure_ascii=False, indent=2)

    blob_service = _get_archivist_blob_service_client()
    container_client = blob_service.get_container_client(container_name)

    # Upload with JSON content type
    blob_client = container_client.get_blob_client(blob_path)
    blob_client.upload_blob(
        json_content,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json")
    )

    logger.info(
        "Uploaded JSON blob: %s/%s (%d bytes)",
        container_name, blob_path, len(json_content)
    )
    return blob_client.url
