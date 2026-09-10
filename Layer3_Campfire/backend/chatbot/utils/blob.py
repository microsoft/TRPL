import logging
import mimetypes
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    UserDelegationKey,
    generate_blob_sas,
)
from common_config import AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY

logger = logging.getLogger(__name__)

# A user-delegation key requires a network round-trip + an AAD token, so we
# request one with a generous lifetime and cache it, reusing it across many blob
# signings until it nears expiry. Azure caps the lifetime at 7 days.
_UDK_TTL = timedelta(hours=12)
# Refresh the cached key when it has less than this much life left, so it always
# outlives the short-lived SAS tokens we mint from it.
_UDK_REFRESH_BUFFER = timedelta(minutes=15)

_udk_lock = threading.Lock()
# account_name -> (user_delegation_key, expiry_datetime_utc)
_udk_cache: dict[str, tuple[UserDelegationKey, datetime]] = {}
# account_name -> BlobServiceClient (reused; holds the managed-identity credential)
_bsc_cache: dict[str, BlobServiceClient] = {}
_credential = None  # lazily-created DefaultAzureCredential (only when AAD path is used)


def _get_credential():
    global _credential
    if _credential is None:
        # Imported lazily so local dev (which uses the account-key path) never
        # needs an Azure credential available.
        from azure.identity import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    return _credential


def _get_user_delegation_key(account_name: str, min_valid_until: datetime) -> UserDelegationKey:
    """Return a cached user-delegation key for `account_name` that stays valid
    past `min_valid_until`, fetching a fresh one via the App Service managed
    identity when the cached one is missing or close to expiring."""
    with _udk_lock:
        cached = _udk_cache.get(account_name)
        if cached is not None:
            udk, exp = cached
            if exp - _UDK_REFRESH_BUFFER > min_valid_until:
                return udk

        now = datetime.now(timezone.utc)
        expiry = now + _UDK_TTL
        if expiry <= min_valid_until:
            expiry = min_valid_until + _UDK_REFRESH_BUFFER

        bsc = _bsc_cache.get(account_name)
        if bsc is None:
            account_url = f"https://{account_name}.blob.core.windows.net"
            bsc = BlobServiceClient(account_url, credential=_get_credential())
            _bsc_cache[account_name] = bsc

        udk = bsc.get_user_delegation_key(key_start_time=now, key_expiry_time=expiry)
        _udk_cache[account_name] = (udk, expiry)
        return udk


def sign_full_blob_url_shared_key(full_url: str, expiry_hours=1):
    """
    Generate a read-only SAS URL for a blob.

    Two signing modes, chosen by whether AZURE_STORAGE_KEY is set:

    * **Account-key SAS** (AZURE_STORAGE_KEY present) — used for local dev and any
      account that still allows shared-key auth.
    * **User-delegation SAS** (no AZURE_STORAGE_KEY) — signs with a key obtained
      via the App Service managed identity (Entra ID). Required for accounts with
      ``allowSharedKeyAccess = false`` (zero-trust prod), where account-key SAS is
      rejected with ``403 KeyBasedAuthenticationNotPermitted``. The identity needs
      the **Storage Blob Data Reader** role on the account.

    On any signing failure the raw URL is returned so the caller can degrade
    gracefully rather than fail the whole search.
    """
    parsed = urlparse(full_url)

    path_parts = parsed.path.lstrip("/").split("/", 1)
    if len(path_parts) != 2:
        raise ValueError(f"Invalid blob URL: {full_url}")

    container_name, blob_path = path_parts

    content_type, _ = mimetypes.guess_type(blob_path)
    if not content_type:
        content_type = "application/octet-stream"

    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=expiry_hours)

    assert parsed.hostname is not None, "Invalid blob URL; missing hostname"
    account_name = parsed.hostname.split(".")[0]

    # The Search index may store blob URLs pointing at a different storage
    # account than the one we serve (e.g. staging URLs baked into a prod index).
    # The blob path is account-relative and the file exists in our configured
    # account, so normalize the host to AZURE_STORAGE_ACCOUNT. This also keeps
    # the signed host aligned with the signing key and with the frontend proxy's
    # host allowlist (which only permits ${AZURE_STORAGE_ACCOUNT}.blob...).
    if AZURE_STORAGE_ACCOUNT and account_name != AZURE_STORAGE_ACCOUNT:
        host_suffix = parsed.hostname.split(".", 1)[1]  # e.g. blob.core.windows.net
        parsed = parsed._replace(netloc=f"{AZURE_STORAGE_ACCOUNT}.{host_suffix}")
        full_url = urlunparse(parsed)
        account_name = AZURE_STORAGE_ACCOUNT

    try:
        if AZURE_STORAGE_KEY:
            sas = generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_path,
                account_key=AZURE_STORAGE_KEY,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
                content_type=content_type,
                content_disposition="inline",
            )
        else:
            udk = _get_user_delegation_key(account_name, min_valid_until=expiry)
            sas = generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_path,
                user_delegation_key=udk,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
                content_type=content_type,
                content_disposition="inline",
            )
    except Exception:
        logger.warning("URL signing failed; using raw url. url=%r", full_url, exc_info=True)
        return full_url

    return f"{full_url}?{sas}"
