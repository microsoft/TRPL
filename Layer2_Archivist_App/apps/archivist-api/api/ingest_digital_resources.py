# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Ingest Moore Chronology and Genealogy & Papers digital items into Cosmos DB.

Both sources are fetched LIVE at ingestion time from their authoritative websites --
no pre-scraped files or Azure Blob source storage required.

  Moore Chronology:   TRC Digital Library
    Discovers all matching items from the search URL, then fetches each item's
    detail page for full metadata and file URLs.

  Genealogy & Papers: TRA website Cyclopedia module pages
    Discovers all linked content pages from the provided index URL, then fetches
    and parses the text of each module.

Usage:
  python ingest_digital_resources.py --source moore [--oid o310991]
  python ingest_digital_resources.py --source genealogy
  python ingest_digital_resources.py --source all
  python ingest_digital_resources.py --dry-run --source all
  python ingest_digital_resources.py --source moore --internalize-assets

Environment variables (all optional -- defaults shown):
  MOORE_SEARCH_URL      TRC search URL (default: https://www.theodorerooseveltcenter.org/digital-library/?s=Moore+chronology)
  GENEALOGY_INDEX_URL   TRA index URL  (default: https://www.theodoreroosevelt.org/content.aspx?page_id=22&club_id=991271&module_id=339179)
  TRC_REQUEST_DELAY     0.5  (seconds between HTTP requests -- polite crawl)
  TRC_USER_AGENT        TRPLArchivistBot/1.0
  ASSET_DOWNLOAD_DELAY  0.5  (seconds between asset downloads during internalization)
  WIKIMEDIA_USER_AGENT  policy-compliant UA for upload.wikimedia.org image downloads
  COSMOS_ENDPOINT
  COSMOS_DATABASE_NAME              contentdb
  COSMOS_DIGITAL_ITEMS_CONTAINER_NAME  digital-items
  AZURE_STORAGE_ACCOUNT_NAME        (required for --internalize-assets)
  AZURE_STORAGE_CONTAINER_NAME      digital-resources
  DIGITAL_ITEMS_ASSET_BLOB_PREFIX   digital-items
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.storage.blob import BlobServiceClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper.cosmos_client import get_container  # pylint: disable=import-error
from helper.credential import get_credential  # pylint: disable=import-error
from helper.blob_utils import (  # pylint: disable=import-error
    guess_content_type,
    filename_from_url,
    is_external_url,
    upload_blob_no_overwrite,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
# Suppress Azure SDK HTTP transport noise (request/response headers) that leaks
# into job output when AZURE_LOG_LEVEL=info is set in the App Service environment.
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Configurable entry-point URLs                                               #
# --------------------------------------------------------------------------- #
MOORE_SEARCH_URL = os.getenv(
    "MOORE_SEARCH_URL",
    "https://www.theodorerooseveltcenter.org/digital-library/?s=Moore+chronology",
)
GENEALOGY_INDEX_URL = os.getenv(
    "GENEALOGY_INDEX_URL",
    "https://www.theodoreroosevelt.org/content.aspx?page_id=22&club_id=991271&module_id=339179",
)

TRC_BASE = "https://www.theodorerooseveltcenter.org"
TRA_BASE = "https://www.theodoreroosevelt.org"

_REQUEST_DELAY = float(os.getenv("TRC_REQUEST_DELAY", "0.5"))
_USER_AGENT = os.getenv(
    "TRC_USER_AGENT",
    # ClubExpress (TRA) and many other CMS platforms block non-browser User-Agents.
    # Use a real Chrome UA so requests are not rejected with 403 Forbidden.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)

# Wikimedia (upload.wikimedia.org) enforces a User-Agent policy and rate-limits
# aggressively (HTTP 429 / "robot policy"). It rejects generic browser UAs for
# automated access and expects a descriptive UA identifying the bot with a contact.
# See https://meta.wikimedia.org/wiki/User-Agent_policy
_WIKIMEDIA_USER_AGENT = os.getenv(
    "WIKIMEDIA_USER_AGENT",
    "TRPLArchivistBot/1.0 (https://www.theodoreroosevelt.org/; archivist ingestion)",
)
# Polite delay (seconds) between successive asset downloads during internalization,
# to avoid tripping Wikimedia/third-party rate limits.
_ASSET_DOWNLOAD_DELAY = float(os.getenv("ASSET_DOWNLOAD_DELAY", "0.5"))

# 5xx gateway errors (502/503/504) from source sites (e.g. TRC behind Cloudflare)
# are transient origin overloads; back off longer to let the gateway recover.
_GATEWAY_BACKOFF_BASE = float(os.getenv("INGEST_GATEWAY_BACKOFF_BASE", "3"))
_GATEWAY_BACKOFF_MAX = float(os.getenv("INGEST_GATEWAY_BACKOFF_MAX", "30"))


from helper.local_settings import load_local_settings  # pylint: disable=import-error


def _load_local_settings() -> None:
    load_local_settings(__file__)


# --------------------------------------------------------------------------- #
#  HTTP helpers                                                                #
# --------------------------------------------------------------------------- #

# Set INGEST_SSL_VERIFY=0 to disable SSL certificate verification in dev/VPN environments
# where corporate SSL inspection rewrites certificates (e.g. ZScaler, Fiddler, Charles).
# Evaluated lazily so that _load_local_settings() (called in main) can take effect
# before the value is read for the first time.
def _get_ssl_verify() -> bool:
    return os.getenv("INGEST_SSL_VERIFY", "1").strip() not in ("0", "false", "no", "off")


# Shared session so the Cloudflare bot-management cookie (__cf_bm) handed back on
# the first response is carried on subsequent requests. Cloudflare uses this cookie
# to recognise a known client and stop re-challenging it; bare requests.get() calls
# are cookie-less and far more likely to be challenged (HTTP 403/503 or a JS
# "Just a moment" interstitial), which is the intermittent "could not be reached"
# / "no items fetched" failure seen against the TRC (Cloudflare-fronted) site.
_SESSION: requests.Session | None = None

# Cloudflare returns challenges either as 403/503/429 or, increasingly, as an
# HTTP 200 carrying a JS interstitial or an Access Denied page.  Treat all as
# retryable so we back off rather than immediately surfacing a dead response.
_CF_RETRY_STATUS = {403, 429, 503}
_CF_CHALLENGE_MARKERS = (
    # Standard JS challenge / CAPTCHA pages
    "just a moment",
    "cf-browser-verification",
    "challenge-platform",
    "/cdn-cgi/challenge-platform/",
    "attention required",
    # Access Denied / IP-blocked pages (Cloudflare error 1020, 1006, etc.)
    "access denied",
    "error 1020",
    "error 1006",
    "sorry, you have been blocked",
    "you have been blocked",
    "ip has been blocked",
    "this website is using a security service",
    # Cloudflare Ray ID is present on every CF-generated error page
    # but NOT on real content pages — safe to treat as a challenge marker
    # when combined with zero parseable OIDs.
    # AWS WAF challenge markers (theodoreroosevelt.org switched from CF to AWS WAF)
    "awswafintegration",
    "x-amzn-waf-action",
    "challenge.js",
    "awswaf.com",
    "window.gokuprops",
    "we need to verify that you're not a robot",
)


def _get_session() -> requests.Session:
    global _SESSION  # noqa: PLW0603 — module-level singleton by design
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        })
    return _SESSION


def _is_cf_challenge_text(text: str, cf_mitigated: str = "", content_type: str = "") -> bool:
    """Detect a Cloudflare challenge from raw text and optional response headers."""
    if cf_mitigated.lower() == "challenge":
        return True
    if content_type and "text/html" not in content_type:
        return False
    snippet = text[:4096].lower()
    return any(marker in snippet for marker in _CF_CHALLENGE_MARKERS)


def _is_cf_challenge(resp: requests.Response) -> bool:
    """Detect a Cloudflare bot-management challenge served as HTTP 200."""
    return _is_cf_challenge_text(
        resp.text,
        resp.headers.get("cf-mitigated", ""),
        resp.headers.get("Content-Type", ""),
    )


def _http_get(
    url: str,
    *,
    referer: str = "",
    retries: int = 4,
    timeout: int = 60,
) -> requests.Response:
    """GET with retry-backoff, Cloudflare-challenge handling and polite crawl delay."""
    session = _get_session()
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    ssl_verify = _get_ssl_verify()
    if not ssl_verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    last_exc: Exception | None = None
    for attempt in range(retries):
        last_attempt = attempt + 1 == retries
        try:
            resp = session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                verify=ssl_verify,
            )
            resp.raise_for_status()
            # Challenge served as HTTP 200 — retry with backoff so we don't hand a
            # JS interstitial to the parser (which would yield 0 items).
            if _is_cf_challenge(resp):
                if last_attempt:
                    raise RuntimeError(
                        f"Cloudflare challenge not cleared for {url} after {retries} attempts"
                    )
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(
                    "  Cloudflare challenge on %s, retrying in %.1fs (attempt %d/%d)",
                    url, wait, attempt + 1, retries,
                )
                time.sleep(wait)
                continue
            time.sleep(_REQUEST_DELAY)
            return resp
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in _CF_RETRY_STATUS and not last_attempt:
                if status == 429 and exc.response is not None:
                    wait = int(exc.response.headers.get("Retry-After", 10 * (attempt + 1)))
                else:
                    # 403/503 are Cloudflare challenge/throttle responses — back off
                    # (with jitter) rather than hammering the same challenge instantly.
                    wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(
                    "  %s on %s, retrying in %.1fs (attempt %d/%d)",
                    status, url, wait, attempt + 1, retries,
                )
                time.sleep(wait)
            else:
                raise
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("  Attempt %d/%d failed for %s: %s", attempt + 1, retries, url, exc)
            if last_attempt:
                raise
            time.sleep(2 ** attempt + random.uniform(0, 1))
    raise RuntimeError(f"Failed to fetch {url}") from last_exc


class _SimpleResponse:
    """Minimal response object returned by _tra_http_get (urllib-based fetch)."""
    def __init__(self, text: str, final_url: str, status: int) -> None:
        self.text = text
        self.url = final_url
        self.status_code = status


def _tra_http_get(
    url: str,
    *,
    referer: str = "",
    retries: int = 3,
    timeout: int = 60,
) -> _SimpleResponse:
    """Fetch a TRA / ClubExpress page using stdlib urllib.request.

    The `requests` library is blocked with HTTP 403 by the ClubExpress WAF
    because its TLS fingerprint (JA3) differs from real browsers. Python's
    stdlib ssl module uses a different TLS profile that passes the check.
    Set INGEST_SSL_VERIFY=0 to disable SSL verification in dev/VPN environments.

    AWS WAF bypass: Set TRA_WAF_COOKIES to a semicolon-separated cookie string
    (e.g. "aws-waf-token=...; AWSALBTG=...") obtained from a browser session
    that has already solved the WAF challenge. These cookies will be included
    in requests to bypass the JS challenge from server-side code.
    """
    import urllib.request as _urllib_req
    import urllib.error as _urllib_err
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    if not _get_ssl_verify():
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

    tra_headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        tra_headers["Referer"] = referer
    # AWS WAF bypass: include pre-solved cookies if available
    waf_cookies = os.getenv("TRA_WAF_COOKIES", "").strip()
    if waf_cookies:
        tra_headers["Cookie"] = waf_cookies

    last_exc: Exception | None = None
    for attempt in range(retries):
        last_attempt = attempt + 1 == retries
        try:
            req = _urllib_req.Request(url, headers=tra_headers)
            with _urllib_req.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                final_url = r.url
                status = r.status
                content_type = r.headers.get("Content-Type", "")
                cf_mitigated = r.headers.get("cf-mitigated", "")
                waf_action = r.headers.get("x-amzn-waf-action", "")
            text = raw.decode("utf-8", "replace")
            # ClubExpress (theodoreroosevelt.org) sits behind AWS WAF (previously
            # Cloudflare). A bot challenge / "Access Denied" interstitial is served
            # as HTTP 200/202 with no parseable content. Without this check the junk
            # page would be handed to the parser, which finds 0 module_id= links and
            # the run fails with a misleading "0 sections" error. Detect it and retry
            # with backoff so we either clear the challenge or fail loudly with an
            # accurate error instead of silently returning a content-less page.
            is_waf_challenge = (
                waf_action.lower() == "challenge"
                or status == 202
                or _is_cf_challenge_text(text, cf_mitigated, content_type)
            )
            if is_waf_challenge:
                if last_attempt:
                    raise RuntimeError(
                        f"WAF bot-challenge not cleared for {url} after {retries} "
                        "attempts (TRA/ClubExpress served an AWS WAF or Cloudflare "
                        "bot-challenge page instead of content). The site requires "
                        "JavaScript execution to pass the challenge."
                    )
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(
                    "  WAF challenge on %s (HTTP %d), retrying in %.1fs (attempt %d/%d)",
                    url, status, wait, attempt + 1, retries,
                )
                time.sleep(wait)
                continue
            time.sleep(_REQUEST_DELAY)
            return _SimpleResponse(text, final_url, status)
        except _urllib_err.HTTPError as exc:
            last_exc = exc
            status_code = exc.code
            # Real 4xx (not 429) are not transient — fail fast instead of wasting retries.
            if status_code < 500 and status_code not in _CF_RETRY_STATUS:
                raise
            logger.warning(
                "  Attempt %d/%d failed for %s: HTTP Error %s: %s",
                attempt + 1, retries, url, status_code, exc.reason,
            )
            if attempt + 1 == retries:
                raise
            # 5xx gateway errors (502/503/504) and 429 are transient — back off
            # longer (honoring Retry-After) to give the origin/gateway time to recover.
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and str(retry_after).isdigit():
                wait = float(retry_after)
            else:
                wait = min(
                    _GATEWAY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1),
                    _GATEWAY_BACKOFF_MAX,
                )
            time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            logger.warning("  Attempt %d/%d failed for %s: %s", attempt + 1, retries, url, exc)
            if attempt + 1 == retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch TRA URL {url}") from last_exc


def _trc_http_get(
    url: str,
    *,
    referer: str = "",
    retries: int = 6,
    timeout: int = 90,
) -> _SimpleResponse:
    """Fetch a TRC / Cloudflare-fronted page using stdlib urllib.request.

    The ``requests`` library's TLS fingerprint (JA3) is detected and blocked by
    Cloudflare's bot-management system, which silently serves HTTP 200
    "Access Denied" pages to known cloud-provider IPs (including Azure).
    Python's stdlib ``ssl`` module presents a different TLS profile that is not
    flagged, which is why this function uses ``urllib.request`` rather than the
    shared ``requests.Session`` used for non-Cloudflare calls.

    Set INGEST_SSL_VERIFY=0 to disable certificate verification in dev/VPN
    environments where corporate SSL inspection (ZScaler, etc.) rewrites certs.
    """
    import urllib.request as _urllib_req
    import urllib.error as _urllib_err
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    if not _get_ssl_verify():
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

    trc_headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # identity encoding avoids gzip decompression issues in urllib
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    if referer:
        trc_headers["Referer"] = referer

    last_exc: Exception | None = None
    for attempt in range(retries):
        last_attempt = attempt + 1 == retries
        try:
            req = _urllib_req.Request(url, headers=trc_headers)
            with _urllib_req.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                final_url = r.url
                status = r.status
                cf_mitigated = r.headers.get("cf-mitigated", "")
                content_type = r.headers.get("Content-Type", "")
            text = raw.decode("utf-8", "replace")
            # Cloudflare challenge / Access Denied served as HTTP 200 — retry
            if _is_cf_challenge_text(text, cf_mitigated, content_type):
                if last_attempt:
                    snippet = text[:400].replace("\n", " ").strip()
                    raise RuntimeError(
                        f"Cloudflare challenge/block not cleared for {url} after "
                        f"{retries} attempts. Response snippet: {snippet}"
                    )
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(
                    "  Cloudflare challenge on %s, retrying in %.1fs (attempt %d/%d)",
                    url, wait, attempt + 1, retries,
                )
                time.sleep(wait)
                continue
            time.sleep(_REQUEST_DELAY)
            return _SimpleResponse(text, final_url, status)
        except RuntimeError:
            raise
        except _urllib_err.HTTPError as exc:
            last_exc = exc
            status_code = exc.code
            # Real 4xx (not 429) are not transient — fail fast instead of wasting retries.
            if status_code < 500 and status_code not in _CF_RETRY_STATUS:
                raise
            logger.warning(
                "  Attempt %d/%d failed for %s: HTTP Error %s: %s",
                attempt + 1, retries, url, status_code, exc.reason,
            )
            if last_attempt:
                raise
            # 5xx gateway errors (502/503/504) and 429 are transient — back off
            # longer (honoring Retry-After) to give the origin/gateway time to recover.
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and str(retry_after).isdigit():
                wait = float(retry_after)
            else:
                wait = min(
                    _GATEWAY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1),
                    _GATEWAY_BACKOFF_MAX,
                )
            time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            logger.warning("  Attempt %d/%d failed for %s: %s", attempt + 1, retries, url, exc)
            if last_attempt:
                raise
            time.sleep(2 ** attempt + random.uniform(0, 1))
    raise RuntimeError(f"Could not fetch TRC URL {url}") from last_exc


def _strip_html(raw: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", raw)
    replacements = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
        "&#39;": "'", "&apos;": "'", "&nbsp;": " ",
        "&#8211;": "\u2013", "&#8212;": "\u2014",
        "&#8216;": "\u2018", "&#8217;": "\u2019",
        "&#8220;": "\u201c", "&#8221;": "\u201d",
        "&#8230;": "\u2026",
    }
    for ent, char in replacements.items():
        text = text.replace(ent, char)
    text = re.sub(r"&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Aliases for backward-compat with local references
_filename_from_url = filename_from_url
_guess_content_type = guess_content_type
_is_external_url = is_external_url


# --------------------------------------------------------------------------- #
#  Asset internalization (for --internalize-assets)                           #
# --------------------------------------------------------------------------- #

class AssetInternalizer:
    """Copies externally hosted assets into Azure Blob storage and returns internal URLs."""

    def __init__(self, storage_account: str, storage_container: str, blob_prefix: str):
        account_url = f"https://{storage_account}.blob.core.windows.net"
        self._container_client = BlobServiceClient(
            account_url=account_url, credential=get_credential()
        ).get_container_client(storage_container)
        self._prefix = blob_prefix.strip("/")
        self._cache: dict[str, str] = {}

    def _download(self, url: str) -> tuple[bytes, str | None]:
        hdrs = {"User-Agent": _USER_AGENT}
        if "wikimedia.org" in url or "wikipedia.org" in url:
            # Wikimedia requires a policy-compliant UA and serves images more
            # reliably with a Referer; generic browser UAs get 429'd as bots.
            hdrs["User-Agent"] = _WIKIMEDIA_USER_AGENT
            hdrs["Referer"] = "https://en.wikipedia.org/"
            hdrs["Accept"] = "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8"
        elif "theodorerooseveltcenter" in url or "amazonaws.com" in url:
            hdrs["Referer"] = "https://www.theodorerooseveltcenter.org/"
        elif "theodoreroosevelt.org" in url or "clubexpress.com" in url:
            hdrs["Referer"] = "https://www.theodoreroosevelt.org/"

        retries = 4
        last_exc: Exception | None = None
        for attempt in range(retries):
            last_attempt = attempt + 1 == retries
            try:
                resp = requests.get(url, headers=hdrs, timeout=120)
                resp.raise_for_status()
                # Polite spacing between downloads to stay under rate limits.
                time.sleep(_ASSET_DOWNLOAD_DELAY)
                return resp.content, resp.headers.get("Content-Type")
            except requests.exceptions.HTTPError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and not last_attempt:
                    retry_after = (
                        exc.response.headers.get("Retry-After")
                        if exc.response is not None
                        else None
                    )
                    if retry_after and str(retry_after).isdigit():
                        wait = int(retry_after)
                    else:
                        wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                    logger.warning(
                        "  429 rate-limited on %s, retrying in %.1fs (attempt %d/%d)",
                        url, wait, attempt + 1, retries,
                    )
                    time.sleep(wait)
                else:
                    raise
            except requests.RequestException as exc:
                last_exc = exc
                if last_attempt:
                    raise
                time.sleep(2 ** attempt + random.uniform(0, 1))
        raise RuntimeError(f"Failed to download {url}") from last_exc

    def _upload(self, blob_name: str, content: bytes, content_type: str | None) -> str:
        return upload_blob_no_overwrite(
            self._container_client, blob_name, content, content_type
        )

    def _blob_name(self, item: dict[str, Any], field: str, source_url: str) -> str:
        digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
        filename = _filename_from_url(source_url, f"{field}.bin")
        return (
            f"{self._prefix}/{item.get('source', 'unknown')}/"
            f"{item.get('id', 'unknown')}/{field}/{digest}-{filename}"
        )

    def materialize(self, item: dict[str, Any], field: str, source_url: str) -> str:
        cached = self._cache.get(source_url)
        if cached:
            return cached
        content, resp_type = self._download(source_url)
        ct = _guess_content_type(source_url, resp_type)
        blob_url = self._upload(self._blob_name(item, field, source_url), content, ct)
        self._cache[source_url] = blob_url
        return blob_url


def internalize_item_assets(item: dict[str, Any], internalizer: AssetInternalizer) -> bool:
    """Rewrite external asset URLs on an item to internal blob URLs. Returns True if anything changed."""
    changed = False
    for field in ("primary_pdf_url", "thumbnail_url", "pdf_url", "audio_url", "image_url"):
        value = item.get(field)
        if isinstance(value, str) and _is_external_url(value):
            try:
                item[field] = internalizer.materialize(item, field, value)
                changed = True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "  Could not internalize %s for %s: %s", field, item.get("id"), exc
                )
    files = item.get("files")
    if isinstance(files, list):
        for idx, file_info in enumerate(files):
            if not isinstance(file_info, dict):
                continue
            url = file_info.get("url")
            if isinstance(url, str) and _is_external_url(url):
                try:
                    file_info["url"] = internalizer.materialize(item, f"file-{idx}", url)
                    changed = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "  Could not internalize file-%d for %s: %s", idx, item.get("id"), exc
                    )
    # Cyclopedia (and similar) nest per-topic thumbnails under linked_topics[].
    # Internalize those too so the UI renders them from our storage account
    # instead of proxying the third-party (Wikipedia) URL on every request.
    linked_topics = item.get("linked_topics")
    if isinstance(linked_topics, list):
        for idx, topic in enumerate(linked_topics):
            if not isinstance(topic, dict):
                continue
            url = topic.get("thumbnail_url")
            if isinstance(url, str) and _is_external_url(url):
                try:
                    topic["thumbnail_url"] = internalizer.materialize(
                        item, f"topic-{idx}-thumbnail", url
                    )
                    changed = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "  Could not internalize topic-%d thumbnail for %s: %s",
                        idx, item.get("id"), exc
                    )
    # Multi-image items store the full gallery under images[]; the UI prefers
    # this array over the singular image_url, so internalize every src too --
    # otherwise the gallery renders raw third-party URLs that the asset proxy
    # cannot reliably fetch (Cloudflare/host-allowlist), and the item shows no
    # image even though image_url itself was internalized.
    images = item.get("images")
    if isinstance(images, list):
        for idx, img in enumerate(images):
            if not isinstance(img, dict):
                continue
            url = img.get("src")
            if isinstance(url, str) and _is_external_url(url):
                try:
                    img["src"] = internalizer.materialize(item, f"image-{idx}", url)
                    changed = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "  Could not internalize image-%d for %s: %s", idx, item.get("id"), exc
                    )
    # Items with multiple audio assets store them under audio_files[]; the UI
    # prefers this array over the singular audio_url, so internalize each url.
    audio_files = item.get("audio_files")
    if isinstance(audio_files, list):
        for idx, af in enumerate(audio_files):
            if not isinstance(af, dict):
                continue
            url = af.get("url")
            if isinstance(url, str) and _is_external_url(url):
                try:
                    af["url"] = internalizer.materialize(item, f"audio-{idx}", url)
                    changed = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "  Could not internalize audio-%d for %s: %s", idx, item.get("id"), exc
                    )
    return changed


# =========================================================================== #
#  TRC Digital Library -- Moore Chronology                                    #
# =========================================================================== #

class _TRCSearchParser(HTMLParser):
    """Extracts /digital-library/oNNNNNN/ item OIDs from a TRC search results page."""

    _ITEM_RE = re.compile(
        r"^(?:https?://(?:www\.)?theodorerooseveltcenter\.org)?/digital-library/(o\d+)/?$"
    )

    def __init__(self) -> None:
        super().__init__()
        self._seen: set[str] = set()
        self.item_oids: list[str] = []
        self.next_page_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        ad = dict(attrs)
        href = (ad.get("href") or "").strip()
        if not href:
            return

        m = self._ITEM_RE.match(href)
        if m:
            oid = m.group(1)
            if oid not in self._seen:
                self._seen.add(oid)
                self.item_oids.append(oid)
            return

        # Pagination: next page link
        if "page=" in href and not self.next_page_url:
            label = (
                (ad.get("aria-label") or "")
                + (ad.get("rel") or "")
                + (ad.get("class") or "")
            ).lower()
            if "next" in label:
                self.next_page_url = (
                    urljoin(TRC_BASE, href) if not href.startswith("http") else href
                )


class _TRCDetailParser(HTMLParser):
    """Parses a TRC item detail page: title, metadata <h3>/<p> and <dt>/<dd>, S3 file URLs, thumbnail.

    The TRC site renders item metadata as <h3> headings followed by <p> content blocks.
    Legacy <dt>/<dd> support is retained for any items that still use that structure.
    Subject(s) sometimes appears as an inline <p> with a "Subject(s): ..." prefix.
    """

    _S3_RE = re.compile(
        r"theodorerooseveltcenter\.s3|s3(?:-us-west-2)?\.amazonaws\.com/theodorerooseveltcenter",
        re.I,
    )
    _ASSET_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    # Known <h3> headings that introduce metadata values on TRC item detail pages.
    _METADATA_H3_KEYS = frozenset({
        "Comments and Context",
        "Collection",
        "Creation Date",
        "Creator(s)",
        "Language",
        "Period",
        "Page Count",
        "Production Method",
        "Record Type",
        "Repository",
        "Resource Type",
        "Subject(s)",
    })

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.metadata: dict[str, str] = {}
        self.description: str = ""
        self.file_urls: list[str] = []
        self.thumbnail_url: str = ""
        self._in_h1 = False
        self._in_h3 = False
        self._in_dt = False
        self._in_dd = False
        self._in_p = False
        self._in_meta_p = False  # True when collecting <p> that follows a metadata <h3>
        self._last_dt = ""
        self._last_h3 = ""       # set to the key name when an <h3> matches a metadata key
        self._buf_h1 = ""
        self._buf_h3 = ""
        self._buf_dt = ""
        self._buf_dd = ""
        self._buf_p = ""
        self._buf_meta_p = ""

    def _is_s3_asset(self, url: str) -> bool:
        return bool(self._S3_RE.search(url))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if tag == "h1":
            self._in_h1 = True
            self._buf_h1 = ""
        elif tag == "h3":
            self._in_h3 = True
            self._buf_h3 = ""
            # A new <h3> ends any active metadata-<p> collection for the previous key.
            self._in_meta_p = False
            self._last_h3 = ""
        elif tag == "dt":
            self._in_dt = True
            self._buf_dt = ""
        elif tag == "dd":
            self._in_dd = True
            self._buf_dd = ""
        elif tag == "p":
            if self._last_h3:
                # First <p> after a metadata <h3> captures the field value.
                self._in_meta_p = True
                self._buf_meta_p = ""
            else:
                self._in_p = True
                self._buf_p = ""
        elif tag == "a":
            href = (ad.get("href") or "").strip()
            if href and self._is_s3_asset(href):
                ext = Path(urlparse(href).path).suffix.lower()
                if ext in self._ASSET_EXTS and href not in self.file_urls:
                    self.file_urls.append(href)
        elif tag == "img":
            src = (ad.get("src") or "").strip()
            if src and not self.thumbnail_url and self._is_s3_asset(src):
                self.thumbnail_url = src

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._buf_h1 += data
        elif self._in_h3:
            self._buf_h3 += data
        elif self._in_meta_p:
            self._buf_meta_p += data
        elif self._in_dt:
            self._buf_dt += data
        elif self._in_dd:
            self._buf_dd += data
        elif self._in_p:
            self._buf_p += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_h1 = False
            self.title = _strip_html(self._buf_h1).strip()
        elif tag == "h3":
            self._in_h3 = False
            key = _strip_html(self._buf_h3).strip()
            self._last_h3 = key if key in self._METADATA_H3_KEYS else ""
        elif tag == "dt":
            self._in_dt = False
            self._last_dt = _strip_html(self._buf_dt).strip()
        elif tag == "dd":
            self._in_dd = False
            val = _strip_html(self._buf_dd).strip()
            if self._last_dt and val:
                self.metadata[self._last_dt] = val
        elif tag == "p":
            if self._in_meta_p:
                self._in_meta_p = False
                val = _strip_html(self._buf_meta_p).strip()
                if self._last_h3 and val and self._last_h3 not in self.metadata:
                    self.metadata[self._last_h3] = val
                # Consume the key — only the first <p> after the <h3> is captured.
                self._last_h3 = ""
            else:
                self._in_p = False
                text = _strip_html(self._buf_p).strip()
                # Handle Subject(s) rendered as an inline paragraph: "Subject(s): A, B, C"
                subj_m = re.match(r"^Subject\(s\)\s*:\s*(.+)$", text, re.S)
                if subj_m and "Subject(s)" not in self.metadata:
                    self.metadata["Subject(s)"] = subj_m.group(1).strip()
                elif not self.description and len(text) > 60:
                    self.description = text


def _trc_search_all_oids(search_url: str) -> list[str]:
    """Return all TRC item OIDs from all pages of the search results."""
    seen: set[str] = set()
    oids: list[str] = []
    url: str | None = search_url
    page = 1

    while url:
        logger.info("  TRC search page %d: %s", page, url)
        try:
            # Use urllib-based fetcher to bypass Cloudflare JA3 fingerprint detection.
            # Cloudflare silently serves HTTP 200 "Access Denied" to requests from
            # Azure IPs when using the requests library (known TLS fingerprint).
            resp = _trc_http_get(url, referer=TRC_BASE)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("  Could not fetch TRC search page: %s", exc)
            raise RuntimeError(f"Could not fetch TRC search page: {exc}") from exc

        html_text = resp.text

        # HTMLParser-based extraction
        sparser = _TRCSearchParser()
        sparser.feed(html_text)
        for oid in sparser.item_oids:
            if oid not in seen:
                seen.add(oid)
                oids.append(oid)

        # Regex fallback to catch any the parser missed
        for m in re.finditer(r"/digital-library/(o\d+)/", html_text):
            oid = m.group(1)
            if oid not in seen:
                seen.add(oid)
                oids.append(oid)

        # If the first page returned 0 OIDs and it wasn't a detected CF challenge,
        # log a response snippet to help diagnose silent failures (e.g. empty pages,
        # undetected bot-blocks, or URL changes on the TRC website).
        if page == 1 and not oids:
            snippet = html_text[:800].replace("\n", " ").strip()
            logger.error(
                "  TRC search page returned HTTP %d but contained 0 item OIDs. "
                "The MOORE_SEARCH_URL may have changed, or the page content was "
                "altered by bot-detection middleware. Response snippet: %s",
                resp.status_code,
                snippet,
            )
            raise RuntimeError(
                f"TRC search returned HTTP {resp.status_code} but 0 item OIDs found. "
                "Check MOORE_SEARCH_URL and TRC website structure. "
                f"Response snippet: {snippet[:300]}"
            )

        # Pagination
        next_url = sparser.next_page_url
        if not next_url:
            for m in re.finditer(r'href="([^"]*[?&]page=(\d+)[^"]*)"', html_text):
                candidate = int(m.group(2))
                if candidate == page + 1:
                    href = m.group(1)
                    next_url = urljoin(TRC_BASE, href) if not href.startswith("http") else href
                    break

        if next_url and next_url != url:
            url = next_url
            page += 1
        else:
            url = None

    logger.info("  Discovered %d TRC item OIDs", len(oids))
    return oids


def _trc_fetch_item_detail(oid: str) -> dict[str, Any]:
    """Fetch and parse TRC item detail page. Returns structured metadata dict."""
    item_url = f"{TRC_BASE}/digital-library/{oid}/"
    logger.info("  Fetching TRC item: %s", item_url)
    try:
        # Use urllib-based fetcher to bypass Cloudflare JA3 fingerprint detection.
        resp = _trc_http_get(item_url, referer=MOORE_SEARCH_URL)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("  Failed to fetch %s: %s", item_url, exc)
        return {
            "oid": oid, "source_url": item_url, "title": oid,
            "description": "", "thumbnail_url": "", "file_urls": [], "metadata": {},
        }

    parser = _TRCDetailParser()
    parser.feed(resp.text)

    title = parser.title
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
        if m:
            title = _strip_html(m.group(1)).split("|")[0].strip()

    description = (
        parser.metadata.get("Comments and Context")
        or parser.metadata.get("Description")
        or parser.description
        or ""
    )

    return {
        "oid": oid,
        "source_url": item_url,
        "title": title,
        "description": description,
        "thumbnail_url": parser.thumbnail_url,
        "file_urls": parser.file_urls,
        "metadata": parser.metadata,
    }


def _trc_detail_to_cosmos_item(detail: dict[str, Any]) -> dict[str, Any]:
    """Convert a parsed TRC detail dict into a Cosmos DB digital-item document."""
    oid = detail["oid"]
    meta = detail.get("metadata", {})
    title = detail.get("title") or oid

    resource_type_raw = (meta.get("Resource Type") or "").lower()
    if resource_type_raw in ("list",) or "chronology" in title.lower():
        item_type = "chronology"
    elif resource_type_raw == "letter":
        item_type = "letter"
    elif resource_type_raw == "postcard":
        item_type = "postcard"
    elif resource_type_raw == "photograph":
        item_type = "photograph"
    else:
        item_type = "document"

    # Date range from title, then Period, then Creation Date
    date_range = ""
    years_in_title = re.findall(r"\d{4}", title)
    if len(years_in_title) >= 2:
        date_range = f"{years_in_title[0]}-{years_in_title[-1]}"
    elif years_in_title:
        date_range = years_in_title[0]
    if not date_range:
        period_years = re.findall(r"\d{4}", meta.get("Period", ""))
        if len(period_years) >= 2:
            date_range = f"{period_years[0]}-{period_years[-1]}"
        elif period_years:
            date_range = period_years[0]
    if not date_range:
        date_range = meta.get("Creation Date", "")

    page_count: int | None = None
    try:
        page_count = int(meta.get("Page Count", ""))
    except (ValueError, TypeError):
        pass

    subjects = [
        s.strip()
        for s in re.split(r"[,;]", meta.get("Subject(s)", ""))
        if s.strip()
    ]

    file_urls = detail.get("file_urls", [])
    pdf_urls = [u for u in file_urls if u.lower().endswith(".pdf")]
    image_urls = [u for u in file_urls if not u.lower().endswith(".pdf")]

    item: dict[str, Any] = {
        "id": f"moore-{oid}",
        "source": "moore-chronology",
        "item_type": item_type,
        "title": title,
        "date_range": date_range,
        "source_url": detail["source_url"],
        "source_id": oid,
        "description": detail.get("description", ""),
        "metadata": {
            "collection": meta.get("Collection", ""),
            "creation_date": meta.get("Creation Date", ""),
            "creators": meta.get("Creator(s)", ""),
            "language": meta.get("Language", ""),
            "period": meta.get("Period", ""),
            "resource_type": meta.get("Resource Type", ""),
            "production_method": meta.get("Production Method", ""),
            "record_type": meta.get("Record Type", ""),
            "repository": meta.get("Repository", ""),
        },
        "subjects": subjects,
    }

    thumb = detail.get("thumbnail_url") or (image_urls[0] if image_urls else "")
    if thumb:
        item["thumbnail_url"] = thumb

    if page_count:
        item["page_count"] = page_count

    if pdf_urls:
        item["primary_pdf_url"] = pdf_urls[0]
        item["files"] = [
            {"filename": _filename_from_url(u, "document.pdf"), "url": u}
            for u in pdf_urls
        ]
    elif image_urls:
        item["files"] = [
            {"filename": _filename_from_url(u, "image.jpg"), "url": u}
            for u in image_urls
        ]

    return item


def build_moore_items(oid_filter: str | None = None) -> list[dict[str, Any]]:
    """Fetch Moore Chronology items live from TRC Digital Library."""
    logger.info(
        "Fetching Moore Chronology from TRC Digital Library (%s)", MOORE_SEARCH_URL
    )
    oids = _trc_search_all_oids(MOORE_SEARCH_URL)

    if not oids:
        logger.warning(
            "No Moore Chronology items found at '%s'. "
            "Check MOORE_SEARCH_URL or TRC website connectivity.",
            MOORE_SEARCH_URL,
        )
        return []

    if oid_filter:
        if oid_filter not in oids:
            raise ValueError(
                f"OID '{oid_filter}' not found in TRC search results. Found: {oids}"
            )
        oids = [oid_filter]

    items: list[dict[str, Any]] = []
    for oid in oids:
        detail = _trc_fetch_item_detail(oid)
        cosmos_item = _trc_detail_to_cosmos_item(detail)
        items.append(cosmos_item)
        logger.info("  Built: %s -- %s", cosmos_item["id"], cosmos_item["title"])

    logger.info("Moore Chronology: built %d items", len(items))
    return items


# =========================================================================== #
#  TRA Website -- Genealogy & Papers                                          #
# =========================================================================== #

class _TRANavParser(HTMLParser):
    """Discovers content module URLs linked from a TRA Cyclopedia navigation page."""

    _MODULE_RE = re.compile(r"module_id=(\d+)", re.I)
    _OLD_RE = re.compile(
        r"https?://(?:www\.)?theodoreroosevelt\.org/site/[^\"' ]+\.htm", re.I
    )

    def __init__(self) -> None:
        super().__init__()
        self._seen_mids: set[str] = set()
        self.module_urls: list[str] = []
        # Anchor text for each discovered module URL — used to identify sections by title
        self.module_link_texts: dict[str, str] = {}  # canonical url -> anchor text
        self.old_style_urls: list[str] = []
        self._in_module_link: bool = False
        self._current_module_url: str = ""
        self._link_text_buf: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        ad = dict(attrs)
        href = (ad.get("href") or "").strip()
        if not href:
            return
        if href.startswith("/"):
            href = TRA_BASE + href
        elif not href.startswith("http"):
            return

        m = self._MODULE_RE.search(href)
        if m and "theodoreroosevelt.org" in href:
            mid = m.group(1)
            canonical = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={mid}"
            if mid not in self._seen_mids:
                self._seen_mids.add(mid)
                self.module_urls.append(canonical)
            # Track anchor text for this link (may be called multiple times for same mid)
            self._in_module_link = True
            self._current_module_url = canonical
            self._link_text_buf = ""
            return

        if self._OLD_RE.match(href) and href not in self.old_style_urls:
            self.old_style_urls.append(href)

    def handle_data(self, data: str) -> None:
        if self._in_module_link:
            self._link_text_buf += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_module_link:
            text = re.sub(r"\s+", " ", self._link_text_buf).strip()
            if text and self._current_module_url:
                existing = self.module_link_texts.get(self._current_module_url, "")
                # Keep the longest / most descriptive anchor text seen for this URL
                if len(text) > len(existing):
                    self.module_link_texts[self._current_module_url] = text
            self._in_module_link = False
            self._link_text_buf = ""
            self._current_module_url = ""


class _TRAContentParser(HTMLParser):
    """Parses a TRA ClubExpress module page: title, headings, paragraphs, images.

    Also collects <a> links with module_id= found in the CONTENT area (i.e.
    outside nav/sidebar/header/footer skip zones).  These content-area links
    are used to distinguish the true top-level sections from navigation links.
    """

    # Match nav/chrome elements even when the keyword is embedded in a compound
    # HTML class/id like "LeftMenu", "TopNav", "NavigationBar", "navlinks", etc.
    # \b is used only for words that are too short or generic to safely match
    # as substrings (header, footer, breadcrumb, skip, copyright).
    _SKIP_WORDS = re.compile(
        r"\b(?:header|footer|breadcrumb|skip|copyright)\b|"
        r"(?:nav(?:igation|bar|col|links|list|menu|block|wrap|item|section)?|"
        r"menu(?:bar|nav|item|list|wrap|block)?|sidebar|loginbar|topbar|"
        r"loginbox|searchbar|searchbox|topmenu|leftmenu|rightmenu|"
        r"sitenav|topnav|mainnav|subnav|leftnav|rightnav|pagenav|"
        r"siteheader|sitefooter|sitebanner|pageheader|pagefooter)",
        re.I,
    )
    # Void elements have no closing tag — do NOT increment skip_depth for them.
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img",
        "input", "link", "meta", "param", "source", "track", "wbr",
    })
    _MODULE_RE = re.compile(r"module_id=(\d+)", re.I)

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        # Each element is (tag, text_or_src)
        self._elements: list[tuple[str, str]] = []
        self._skip_depth: int = 0
        self._cur_tag: str = ""
        self._cur_buf: str = ""
        self._in_content: bool = False
        # Links found in the CONTENT area (not nav/sidebar) pointing to module pages.
        # On the root index page these are the 10 top-level sections; on a section
        # page they are the sub-items belonging to that section.
        self.content_module_urls: list[str] = []
        self._seen_content_mids: set[str] = set()
        # Audio files collected from <audio src>, <source src>, and <a href="*.mp3">
        # Each entry: {"url": str, "label": str}  — label is the visible anchor text
        self.audio_files: list[dict[str, str]] = []
        self._in_audio: bool = False  # True while inside an <audio> element
        self._in_audio_link: bool = False  # True while inside <a href="*.mp3">
        self._current_audio_href: str = ""
        self._audio_link_buf: str = ""  # collects anchor text for audio links

    def _is_skip(self, attrs: dict[str, str | None]) -> bool:
        combined = " ".join(filter(None, [attrs.get("id"), attrs.get("class")]))
        return bool(self._SKIP_WORDS.search(combined))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if self._is_skip(ad):
            self._skip_depth += 1
            return
        if self._skip_depth:
            # Track nesting inside the skip container so handle_endtag can
            # correctly detect when we've exited the skip zone.
            if tag not in self._VOID:
                self._skip_depth += 1
            return

        if tag == "h1":
            self._flush()
            self._cur_tag, self._cur_buf, self._in_content = "h1", "", True
        elif tag in ("h2", "h3", "h4"):
            self._flush()
            self._cur_tag, self._cur_buf, self._in_content = tag, "", True
        elif tag == "p":
            self._flush()
            self._cur_tag, self._cur_buf, self._in_content = "p", "", True
        elif tag == "img":
            src = (ad.get("src") or "").strip()
            alt = (ad.get("alt") or "").strip()
            if src and src.startswith("http"):
                low = src.lower()
                if not any(kw in low for kw in ("logo", "icon", "btn", "arrow", "candid", "navigation")):
                    self._elements.append(("img", f"{src}||{alt}"))
        elif tag == "audio":
            # <audio src="..."> — may have src directly on the tag
            self._in_audio = True
            src = (ad.get("src") or "").strip()
            if src and not any(f["url"] == src for f in self.audio_files):
                self.audio_files.append({"url": src, "label": ""})
        elif tag == "source" and self._in_audio:
            # <source src="..."> child of <audio> — primary audio file reference
            src = (ad.get("src") or "").strip()
            if src and not any(f["url"] == src for f in self.audio_files):
                self.audio_files.append({"url": src, "label": ""})
        elif tag == "a":
            # Capture <a href="file.mp3"> direct audio file links;
            # the anchor text (e.g. speech title) becomes the label.
            href = (ad.get("href") or "").strip()
            if href:
                # Collect module page links from the CONTENT area (not nav/sidebar).
                # These identify the direct children of the current page.
                mod_m = self._MODULE_RE.search(href)
                if mod_m and "theodoreroosevelt.org" in href:
                    mid = mod_m.group(1)
                    if mid not in self._seen_content_mids:
                        self._seen_content_mids.add(mid)
                        self.content_module_urls.append(
                            f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={mid}"
                        )

                path = href.lower().split("?")[0]
                # Capture direct audio file links AND TRA ClubExpress docs.ashx
                # download links (used for speech audio on theodoreroosevelt.org).
                is_audio_link = any(
                    path.endswith(ext)
                    for ext in (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")
                ) or "docs.ashx" in href.lower()
                if is_audio_link:
                    if not any(f["url"] == href for f in self.audio_files):
                        self.audio_files.append({"url": href, "label": ""})
                    self._in_audio_link = True
                    self._current_audio_href = href
                    self._audio_link_buf = ""

    def _flush(self) -> None:
        if not self._in_content or not self._cur_tag:
            return
        text = _strip_html(self._cur_buf).strip()
        if text:
            if self._cur_tag == "h1":
                self.title = text
            else:
                self._elements.append((self._cur_tag, text))
        self._cur_tag, self._cur_buf, self._in_content = "", "", False

    def handle_data(self, data: str) -> None:
        if self._in_content and not self._skip_depth:
            self._cur_buf += data
        if self._in_audio_link:
            self._audio_link_buf += data

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            # Decrement for any closing tag — this correctly unwinds skip container
            # nesting regardless of whether the closing tag is itself skip-marked.
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "audio":
            self._in_audio = False
        elif tag == "a" and self._in_audio_link:
            # Store the anchor text as the label for this audio file
            label = _strip_html(self._audio_link_buf).strip()
            for entry in self.audio_files:
                if entry["url"] == self._current_audio_href and not entry["label"]:
                    entry["label"] = label
                    break
            self._in_audio_link = False
            self._audio_link_buf = ""
            self._current_audio_href = ""
        if tag in ("h1", "h2", "h3", "h4", "p"):
            self._flush()

    def handle_entityref(self, name: str) -> None:
        if self._in_content and not self._skip_depth:
            self._cur_buf += f"&{name};"

    def handle_charref(self, name: str) -> None:
        if self._in_content and not self._skip_depth:
            self._cur_buf += f"&#{name};"


# ---------------------------------------------------------------------------
# BeautifulSoup-based content extraction for TRA module pages.
# Uses the same approach as the phase2 scraper: targets #TextSizeModify for
# speech transcripts (which store text in <div> blocks, not <p> tags) and
# extracts heading→body sections for prose articles.
# ---------------------------------------------------------------------------

_BS4_SKIP_RE = re.compile(
    r"\b(?:header|footer|breadcrumb|skip|copyright)\b|"
    r"(?:nav(?:igation|bar|col|links|list|menu|block|wrap|item|section)?|"
    r"menu(?:bar|nav|item|list|wrap|block)?|sidebar|loginbar|topbar|"
    r"loginbox|searchbar|topmenu|leftmenu|rightmenu|"
    r"sitenav|topnav|mainnav|subnav|leftnav|rightnav|pagenav|"
    r"siteheader|sitefooter|sitebanner|pageheader|pagefooter)",
    re.I,
)


def _bs4_main_root(soup: Any) -> Any:
    """Find the main article root: #content_column → .threequarter → soup."""
    col = soup.select_one("#content_column")
    if col:
        tq = col.select_one("div.column.threequarter")
        return tq if tq else col
    return soup


def _bs4_body_scope(root: Any) -> Any:
    """Return primary content scope: #TextSizeModify > div.inner-column > resp-row > root."""
    rr = root.select_one("div.resp-row") or root
    ts = rr.select_one("#TextSizeModify")
    if ts:
        return ts
    ic = rr.select_one("div.inner-column")
    return ic if ic else rr


def _bs4_extract_sections_and_images(
    soup: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Extract document sections and images from a parsed TRA Cyclopedia page.

    Mirrors the phase2 scraper logic:
    - When no h2/h3/h4 headings are found (speech transcripts): captures all text
      from the #TextSizeModify scope (which often uses <div> blocks, not <p> tags).
    - When headings exist (prose articles): splits into heading→body sections.
    """
    try:
        from bs4 import BeautifulSoup as _BS4, NavigableString  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.warning("beautifulsoup4 not installed; falling back to empty extraction")
        return [], []

    root = _bs4_main_root(soup)

    # Collect images before any tree mutation
    images: list[dict[str, str]] = []
    for img in root.find_all("img"):
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").strip()
        if src.startswith("//"):
            src = "https:" + src  # normalize protocol-relative URLs
        if src and src.startswith("http"):
            low = src.lower()
            if not any(kw in low for kw in ("logo", "icon", "btn", "arrow", "candid", "navigation")):
                images.append({"src": src, "alt": alt})

    rr = root.select_one("div.resp-row") or root
    scope = _bs4_body_scope(root)

    # If the primary scope yields very little text, widen to the full content column.
    # Some ClubExpress pages (e.g. book-info pages with table/image layouts) keep
    # their content outside #TextSizeModify but within the threequarter column.
    if len(scope.get_text(strip=True)) < 100 and scope is not root:
        scope = root

    # Headings found within the body scope only (avoids nav-sidebar headings like
    # "About TR" / "Speeches" that exist in the ClubExpress left-nav and would
    # otherwise suppress extraction of the actual speech content in #TextSizeModify).
    def _heading_in_skip_zone(h: Any) -> bool:
        """True if the heading or any ancestor up to scope is inside a nav/sidebar zone."""
        if _BS4_SKIP_RE.search(" ".join(h.get("class") or []) + " " + (h.get("id") or "")):
            return True
        node = getattr(h, "parent", None)
        while node is not None and node != scope and node != rr:
            cls = " ".join(node.get("class") or [])
            nid = node.get("id") or ""
            if _BS4_SKIP_RE.search(cls + " " + nid):
                return True
            node = getattr(node, "parent", None)
        return False

    hs = [
        h for h in scope.select("h2, h3, h4")
        if not _heading_in_skip_zone(h)
    ]

    sections: list[dict[str, Any]] = []

    if not hs:
        # No structural headings — treat as a single section (speech transcripts,
        # simple prose).  Use scope.get_text() to capture ALL text nodes including
        # those inside <div> blocks (which <p>-only parsers miss).
        raw = scope.get_text("\n", strip=True)
        paragraphs = [ln.strip() for ln in raw.splitlines() if len(ln.strip()) > 5]
        body = "\n\n".join(paragraphs)
        if body:
            sections.append({"heading": "", "level": 0, "body": body})
    else:
        for h in hs:
            heading_text = h.get_text(" ", strip=True)
            level = int(h.name[1])
            body_parts: list[str] = []
            for sib in h.next_siblings:
                if hasattr(sib, "name") and sib.name in ("h2", "h3", "h4"):
                    break
                if hasattr(sib, "get_text"):
                    t = sib.get_text(" ", strip=True)
                    if t:
                        body_parts.append(t)
                elif isinstance(sib, NavigableString):
                    t = str(sib).strip()
                    if t:
                        body_parts.append(t)
            body = "\n\n".join(body_parts)
            if heading_text or body:
                sections.append({"heading": heading_text, "level": level, "body": body})

    return sections, images


def _tra_playwright_get_html(url: str, timeout: int = 60) -> str | None:
    """Fetch a TRA page using Playwright (Chromium headless browser).

    TRA / ClubExpress is protected by AWS WAF which blocks server-side HTTP
    clients. Playwright uses a real browser that passes JS challenges.
    Returns the rendered HTML string, or None on failure.
    Requires: playwright Python package + `playwright install chromium`.
    """
    try:
        from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.debug("playwright not installed; cannot use _tra_playwright_get_html")
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="load", timeout=timeout * 1000)
                page.wait_for_timeout(2000)
                html = page.content()
                # If WAF challenge detected, wait for JS to resolve it
                title = page.title()
                if len(html) < 5000 or "Human Verification" in title or "challenge" in title.lower():
                    logger.info("  Playwright WAF challenge detected, waiting for resolution...")
                    page.wait_for_load_state("networkidle", timeout=90000)
                    page.wait_for_timeout(5000)
                    html = page.content()
                    if len(html) < 5000:
                        page.reload(wait_until="networkidle", timeout=60000)
                        page.wait_for_timeout(3000)
                        html = page.content()
                logger.info("  Playwright fetched %s (%d bytes)", url, len(html))
                return html
            finally:
                page.close()
                browser.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("  Playwright fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# TRA text sanitization — fix mojibake from ClubExpress CMS encoding issues
# ---------------------------------------------------------------------------
_TRA_MOJIBAKE_MAP: dict[str, str] = {
    "\u0393\u00c7\u00f6": "\u2014",  # ΓÇö → — em dash
    "\u0393\u00c7\u00d6": "\u2019",  # ΓÇÖ → ' right single quote
    "\u0393\u00c7\u00a3": "\u201c",  # ΓÇ£ → " left double quote
    "\u0393\u00c7\u00a5": "\u201d",  # ΓÇ¥ → " right double quote
    "\u0393\u00c7\u00f4": "\u2013",  # ΓÇô → – en dash
    "\u0393\u00c7\u00ff": "\u2018",  # ΓÇÿ → ' left single quote
    "\u0393\u00c7\u00aa": "\u2026",  # ΓÇª → … ellipsis
    "\u252c\xe1": " ",               # ┬á → non-breaking space → space
    "\u252c\u2556": "\u00b7",        # ┬╖ → · middle dot
    "\u00c2\xa0": " ",               # Â\xa0 → non-breaking space → space
}


def _sanitize_tra_text(text: str) -> str:
    """Fix encoding artifacts from TRA's ClubExpress CMS.

    Replaces mojibake sequences (ΓÇö→—, ΓÇÖ→', ┬á→space, etc.) that arise
    from the CMS's double-encoding of Windows-1252 smart punctuation.
    Also strips C0/C1 control characters and normalises whitespace.
    """
    if not text:
        return text
    for bad, good in _TRA_MOJIBAKE_MAP.items():
        text = text.replace(bad, good)
    # Non-breaking space and zero-width chars
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\u200c", "")
    # Strip control characters (keep newline, tab, carriage return)
    text = "".join(c for c in text if c >= " " or c in "\n\r\t")
    # Collapse runs of spaces and excessive blank lines
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _tra_fetch_module(url: str) -> dict[str, Any] | None:
    """Fetch a TRA module page and return parsed content, or None if page has no content."""
    logger.info("  Fetching TRA module: %s", url)

    # Try Playwright first (bypasses AWS WAF JS challenges that block urllib/requests)
    html_text: str | None = _tra_playwright_get_html(url)

    if html_text is None or _is_cf_challenge_text(html_text):
        # Fallback to urllib if Playwright is not available or failed
        try:
            resp = _tra_http_get(url, referer=GENEALOGY_INDEX_URL)
            html_text = resp.text
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("  Failed to fetch %s: %s", url, exc)
            return None

    # _TRAContentParser handles URL and audio discovery (TLS-compatible urllib backend).
    cparser = _TRAContentParser()
    cparser.feed(html_text)

    # BeautifulSoup-based extraction for rich content — handles speech transcripts
    # where text lives inside <div> blocks rather than <p> tags.
    try:
        from bs4 import BeautifulSoup as _BS4  # pylint: disable=import-outside-toplevel
        soup = _BS4(html_text, "html.parser")
    except ImportError:
        soup = None

    # --- Page title (h1 preferred, fallback <title>) ---
    title = ""
    if soup is not None:
        h1_el = soup.find("h1")
        title = h1_el.get_text(" ", strip=True) if h1_el else ""
        if not title:
            t_el = soup.find("title")
            if t_el:
                title = t_el.get_text(strip=True).split("|")[0].strip()
    if not title:
        title = cparser.title
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
        if m:
            title = _strip_html(m.group(1)).split("|")[0].strip()

    if not title:
        logger.debug("  Skipping %s -- no title", url)
        return None

    # --- Content extraction ---
    if soup is not None:
        sections, images = _bs4_extract_sections_and_images(soup)
    else:
        # Fallback: use _TRAContentParser elements (captures <p> tags only)
        sections = []
        images = []
        current: dict[str, Any] | None = None
        for tag, content in cparser._elements:
            if tag == "img":
                parts = content.split("||", 1)
                images.append({"src": parts[0], "alt": parts[1] if len(parts) > 1 else ""})
            elif tag in ("h2", "h3", "h4"):
                if current and (current.get("body") or current.get("heading")):
                    sections.append(current)
                current = {"heading": content, "level": int(tag[1]), "body": ""}
            elif tag == "p":
                if current is None:
                    current = {"heading": "", "level": 0, "body": ""}
                existing = current.get("body", "")
                current["body"] = (existing + ("\n" if existing else "") + content).strip()
        if current and (current.get("body") or current.get("heading")):
            sections.append(current)

    # If the title is a generic CMS placeholder (e.g. page duplicated from a template
    # but h1 never updated), fall back to the first meaningful section heading.
    _GENERIC_TITLE_PREFIXES = ("page template", "new page", "untitled page", "copy of page")
    if title.lower().strip().rstrip(".").rstrip(")") in {
        "page template", "page template (copy", "new page", "untitled"
    } or any(title.lower().startswith(p) for p in _GENERIC_TITLE_PREFIXES):
        first_heading = next(
            (s.get("heading", "") for s in sections
             if s.get("heading", "").strip() and len(s["heading"].strip()) > 5),
            ""
        )
        if first_heading:
            logger.info("  Replacing generic title %r with first section heading %r", title, first_heading)
            title = first_heading

    # Sanitize mojibake in every section's heading and body
    for s in sections:
        s["heading"] = _sanitize_tra_text(s.get("heading", ""))
        s["body"] = _sanitize_tra_text(s.get("body", ""))

    plain_text = "\n\n".join(
        (f"{s['heading']}\n{s['body']}" if s.get("heading") else s.get("body", "")).strip()
        for s in sections
        if s.get("body") or s.get("heading")
    ).strip()

    if not plain_text and not images:
        logger.debug("  Skipping %s -- no parseable content", url)
        return None

    # If every section is an unheaded level-0 block the page has no real
    # structure — store only plain_text and leave document_sections empty.
    if sections and all(not s.get("heading") and s.get("level", 0) == 0 for s in sections):
        sections = []

    description = _sanitize_tra_text(
        plain_text[:200].rsplit(" ", 1)[0] if len(plain_text) > 200 else plain_text
    )

    # Collect any additional content module URLs found in this page's nav.
    # Section-specific sub-pages (e.g. individual speeches under the Speeches
    # listing) only appear in the sub-section nav, not in the root 339179 nav.
    # Returning them here lets build_genealogy_items do a second-pass BFS.
    sub_nav = _TRANavParser()
    sub_nav.feed(html_text)

    return {
        "title": title,
        "url": url,
        "plain_text": plain_text,
        "description": description,
        "document_sections": sections,
        "images": images,
        "discovered_urls": sub_nav.module_urls,
        # Links found in the content area (not nav/sidebar) — direct children of this page.
        "content_module_urls": cparser.content_module_urls,
    }


def _tra_module_to_cosmos_item(module_data: dict[str, Any]) -> dict[str, Any]:
    """Convert parsed TRA module content into a Cosmos DB digital-item document."""
    url = module_data["url"]
    title = module_data["title"]

    mid_m = re.search(r"module_id=(\d+)", url, re.I)
    mid = mid_m.group(1) if mid_m else hashlib.md5(url.encode()).hexdigest()[:8]
    item_id = f"genealogy-tra-{mid}"

    # Classify item_type from title keywords
    title_lower = title.lower()
    if "chronology" in title_lower:
        item_type = "chronology"
    elif "genealogy" in title_lower:
        item_type = "genealogy"
    elif "family" in title_lower:
        item_type = "family"
    elif "speech" in title_lower:
        item_type = "speeches"
    elif "bibliography" in title_lower:
        item_type = "bibliography"
    elif "index" in title_lower:
        item_type = "index"
    elif "public paper" in title_lower:
        item_type = "papers"
    elif "introduction" in title_lower or "about" in title_lower:
        item_type = "introduction"
    else:
        item_type = "document"

    item: dict[str, Any] = {
        "id": item_id,
        "source": "genealogy-papers",
        "item_type": item_type,
        "title": title,
        "description": module_data.get("description", ""),
        "plain_text": module_data.get("plain_text", ""),
        "source_url": url,
        "section_count": len(module_data.get("document_sections", [])),
        "document_sections": module_data.get("document_sections", []),
    }

    if mid_m:
        item["module_id"] = mid

    images = module_data.get("images", [])
    if images:
        item["image_url"] = images[0]["src"]
        item["image_alt"] = images[0].get("alt", "")
        if len(images) > 1:
            item["images"] = images  # store full list for UI multi-image display

    return item


def _infer_hub_pattern(title: str) -> str:
    """Return the UI pattern string for a section that has sub-item children."""
    t = title.lower()
    if "speech" in t:
        return "speeches_sections"
    if "index" in t:
        return "index_hub"
    return "content_hub"


# ── Dynamic Cyclopedia section discovery ─────────────────────────────────── #

_CYCLOPEDIA_ROOT_MID = "339179"  # The only hard-coded anchor: the index page


def _discover_cyclopedia_sections(html: str) -> list[tuple[int, str, str]]:
    """Parse the Cyclopedia root page and return (order, url, title) for each section.

    Uses the LAST ``div.resp-row`` element which is the ClubExpress section-nav
    sidebar containing only the Cyclopedia-specific links with full titles.
    Filters out the root index itself and empty anchor text.
    Returns sections in DOM order (= visual display order on the website).
    """
    from bs4 import BeautifulSoup  # already in requirements
    soup = BeautifulSoup(html, "html.parser")
    _mod_re = re.compile(r"module_id=(\d+)", re.I)

    resp_rows = soup.find_all("div", class_="resp-row")
    target = resp_rows[-1] if resp_rows else None
    if target is None:
        return []

    sections: list[tuple[int, str, str]] = []
    seen_mids: set[str] = set()
    for a in target.find_all("a", href=_mod_re):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        m = _mod_re.search(href)
        if not m:
            continue
        mid = m.group(1)
        if not title or mid == _CYCLOPEDIA_ROOT_MID or mid in seen_mids:
            continue
        seen_mids.add(mid)
        url = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={mid}"
        sections.append((len(sections), url, title))

    return sections


def _extract_cyclopedia_module_links(html: str) -> dict[str, str]:
    """Extract TRA Cyclopedia module links from raw HTML as a parser fallback."""
    links: dict[str, str] = {}

    try:
        from bs4 import BeautifulSoup  # pylint: disable=import-outside-toplevel
    except ImportError:
        BeautifulSoup = None

    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select('main a[href*="module_id="], #page_content a[href*="module_id="], a[href*="module_id="]'):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("/"):
                href = f"{TRA_BASE}{href}"
            if not href.startswith("http") or "theodoreroosevelt.org" not in href:
                continue
            module_match = re.search(r"module_id=(\d+)", href, re.I)
            if not module_match:
                continue
            canonical = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={module_match.group(1)}"
            text = anchor.get_text(" ", strip=True)
            existing = links.get(canonical, "")
            if len(text) >= len(existing):
                links[canonical] = text

    if links:
        return links

    for match in re.finditer(r'<a[^>]+href=["\']([^"\']*module_id=\d+[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = match.group(1).strip()
        if href.startswith("/"):
            href = f"{TRA_BASE}{href}"
        if not href.startswith("http") or "theodoreroosevelt.org" not in href:
            continue
        module_match = re.search(r"module_id=(\d+)", href, re.I)
        if not module_match:
            continue
        canonical = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={module_match.group(1)}"
        text = _strip_html(match.group(2))
        existing = links.get(canonical, "")
        if len(text) >= len(existing):
            links[canonical] = text

    return links


def build_genealogy_items() -> list[dict[str, Any]]:
    """Fetch TRA Cyclopedia sections and their sub-items (speeches, papers, etc.).

    Strategy
    --------
    1.  Fetch the root index (GENEALOGY_INDEX_URL).
    2.  Discover sections dynamically from the ClubExpress section-nav sidebar
        (div.resp-row) — no hard-coded name matchers required.
    3.  For each section, fetch its page and discover sub-items:
        •  Primary:  content_module_urls from that section's page.
        •  Fallback: nav-diff — module IDs unique to that section's sidebar nav.
    """
    logger.info("Fetching Genealogy & Papers from TRA website (%s)", GENEALOGY_INDEX_URL)

    # ── Step 1: Fetch root index page ─────────────────────────────────────── #
    root_html: str | None = _tra_playwright_get_html(GENEALOGY_INDEX_URL)
    if root_html is None or _is_cf_challenge_text(root_html):
        try:
            _resp = _tra_http_get(GENEALOGY_INDEX_URL, referer=TRA_BASE)
            root_html = _resp.text
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("  Could not fetch TRA root index page: %s", exc)
            return []

    # Parse nav for sub-item discovery (nav-diff) later
    root_nparser = _TRANavParser()
    root_nparser.feed(root_html)
    root_nav_mids: set[str] = set()
    for _url in root_nparser.module_urls:
        _m = re.search(r"module_id=(\d+)", _url, re.I)
        if _m:
            root_nav_mids.add(_m.group(1))

    logger.info("  Root index: %d nav links", len(root_nparser.module_urls))

    # ── Step 2: Discover sections from div.resp-row sidebar ────────────────── #
    sections = _discover_cyclopedia_sections(root_html)
    if not sections:
        logger.warning(
            "Genealogy & Papers: could not identify any Cyclopedia sections from "
            "'%s'. Check GENEALOGY_INDEX_URL or website connectivity.",
            GENEALOGY_INDEX_URL,
        )
        return []

    logger.info("  Identified %d Cyclopedia sections", len(sections))

    section_mids: set[str] = set()
    for _, _url, _ in sections:
        _m = re.search(r"module_id=(\d+)", _url, re.I)
        if _m:
            section_mids.add(_m.group(1))
    section_mids.add(_CYCLOPEDIA_ROOT_MID)

    # ── Step 3: Fetch each section and its sub-items ─────────────────────── #
    items: list[dict[str, Any]] = []

    globally_processed: set[str] = {url for _, url, _ in sections}
    globally_processed.add(GENEALOGY_INDEX_URL)

    for order_idx, section_url, canonical_title in sections:
        logger.info(
            "  Fetching section %d (%s): %s", order_idx, canonical_title, section_url,
        )

        section_data = _tra_fetch_module(section_url)
        if section_data is None:
            logger.warning(
                "  Section %d (%s): page returned no content — skipping",
                order_idx, canonical_title,
            )
            continue

        section_item = _tra_module_to_cosmos_item(section_data)
        section_mid = section_item.get("module_id")
        section_item["order"] = order_idx
        if not section_item.get("title"):
            section_item["title"] = canonical_title

        # ── Discover sub-items for THIS section only ──────────────────────── #
        # Each section gets its own candidate set — never shared with other sections.
        sub_urls: list[str] = []
        seen_sub_mids: set[str] = set()

        # Primary: content-area links on THIS section's page.
        # The page body explicitly lists its direct children (speeches, papers, etc.)
        # and these links are in the content area, NOT the nav sidebar.
        for sub_url in section_data.get("content_module_urls", []):
            sub_m = re.search(r"module_id=(\d+)", sub_url, re.I)
            sub_mid = sub_m.group(1) if sub_m else None
            if (sub_mid
                    and sub_mid not in section_mids
                    and sub_mid not in seen_sub_mids
                    and sub_url not in globally_processed):
                seen_sub_mids.add(sub_mid)
                sub_urls.append(sub_url)

        # Fallback: nav-diff.  Module IDs in THIS section's sidebar nav that did
        # NOT appear in the root page's nav are section-specific sub-pages.
        # Only applied when content-area discovery found nothing.
        if not sub_urls and section_mid:
            for nav_url in section_data.get("discovered_urls", []):
                nav_m = re.search(r"module_id=(\d+)", nav_url, re.I)
                nav_mid = nav_m.group(1) if nav_m else None
                if (nav_mid
                        and nav_mid not in root_nav_mids
                        and nav_mid not in section_mids
                        and nav_mid not in seen_sub_mids
                        and nav_url not in globally_processed):
                    seen_sub_mids.add(nav_mid)
                    sub_urls.append(nav_url)
            if sub_urls:
                logger.info(
                    "  Section %d: 0 content-area links — nav-diff found %d sub-item candidates",
                    order_idx, len(sub_urls),
                )

        # Claim these URLs globally so another section cannot also fetch them.
        for sub_url in sub_urls:
            globally_processed.add(sub_url)

        if sub_urls and section_mid:
            section_item["pattern"] = _infer_hub_pattern(canonical_title)
            logger.info(
                "  Section %d has %d sub-items (pattern=%s)",
                order_idx, len(sub_urls), section_item["pattern"],
            )
            for sub_url in sub_urls:
                sub_data = _tra_fetch_module(sub_url)
                if sub_data is None:
                    continue
                sub_item = _tra_module_to_cosmos_item(sub_data)
                sub_item["parent_module_id"] = section_mid
                items.append(sub_item)
                logger.info(
                    "    Sub-item: %s -- %s  [parent: %s]",
                    sub_item["id"], sub_item["title"], section_mid,
                )
        else:
            logger.info("  Section %d: no sub-items found", order_idx)

        items.append(section_item)
        logger.info(
            "  Built section %d: %s -- %s",
            order_idx, section_item["id"], section_item["title"],
        )

    top_count = sum(1 for i in items if not i.get("parent_module_id"))
    sub_count = len(items) - top_count
    logger.info(
        "Genealogy & Papers: built %d items (%d top-level sections, %d sub-items)",
        len(items), top_count, sub_count,
    )
    return items


# =========================================================================== #
#  Main entry point                                                            #
# =========================================================================== #

def main() -> None:
    # Write dry-run output as raw UTF-8 bytes to stdout.buffer so the content
    # is readable regardless of the terminal or shell's output encoding.
    # (PowerShell 5.x reads subprocess stdout using the OEM code page by default,
    # which garbles multi-byte UTF-8 sequences when using the default text wrapper.)
    if hasattr(sys.stdout, "buffer"):
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )

    ap = argparse.ArgumentParser(
        description=(
            "Ingest Moore Chronology and Genealogy & Papers into Cosmos DB digital-items. "
            "Data is fetched LIVE from TRC and TRA websites at run time."
        )
    )
    ap.add_argument(
        "--source",
        choices=["all", "moore", "genealogy"],
        default="all",
        help="Which source(s) to ingest (default: all)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print documents to stdout instead of writing to Cosmos DB",
    )
    ap.add_argument(
        "--oid",
        help="Moore Chronology TRC OID (e.g. o310991) -- ingest only that item",
    )
    ap.add_argument(
        "--internalize-assets",
        action="store_true",
        help="Copy external asset URLs to Azure Blob storage and rewrite item URLs",
    )
    ap.add_argument(
        "--storage-account",
        default=os.getenv("AZURE_STORAGE_ACCOUNT_NAME"),
        help="Azure Storage account for internalized assets",
    )
    ap.add_argument(
        "--storage-container",
        default=(
            os.getenv("DIGITAL_ITEMS_STORAGE_CONTAINER")
            or os.getenv("AZURE_STORAGE_CONTAINER_NAME")
            or "digital-resources"
        ),
        help="Blob container for internalized assets (default: digital-resources)",
    )
    ap.add_argument(
        "--asset-blob-prefix",
        default=os.getenv("DIGITAL_ITEMS_ASSET_BLOB_PREFIX", "digital-items"),
        help="Blob path prefix under the container (default: digital-items)",
    )
    args = ap.parse_args()

    _load_local_settings()

    internalizer: AssetInternalizer | None = None
    if args.internalize_assets:
        if not args.storage_account:
            raise SystemExit(
                "--internalize-assets requires --storage-account (or AZURE_STORAGE_ACCOUNT_NAME)"
            )
        internalizer = AssetInternalizer(
            storage_account=args.storage_account,
            storage_container=args.storage_container,
            blob_prefix=args.asset_blob_prefix,
        )

    builders: dict[str, tuple[str, Any]] = {
        "moore": ("Moore Chronology", build_moore_items),
        "genealogy": ("Genealogy & Papers", build_genealogy_items),
    }

    sources = list(builders.keys()) if args.source == "all" else [args.source]
    if args.oid:
        if "moore" not in sources:
            raise SystemExit("--oid is only valid with --source moore")
        sources = ["moore"]

    total = 0
    for src in sources:
        label, builder = builders[src]
        logger.info("Building items for %s ...", label)

        try:
            if src == "moore":
                items = builder(args.oid) if args.oid else builder()
            else:
                if args.oid:
                    raise SystemExit("--oid is only valid with --source moore")
                items = builder()
        except (RuntimeError, OSError) as exc:
            logger.error(
                "FATAL: Could not fetch %s from source website: %s",
                label,
                exc,
            )
            sys.exit(1)

        logger.info("  Built %d items", len(items))

        if not items and not args.dry_run:
            logger.error(
                "FATAL: 0 items built for %s. "
                "Check website connectivity and URL configuration. "
                "See the errors/warnings above for the specific cause.",
                label,
            )
            sys.exit(1)

        if internalizer:
            changed = sum(1 for item in items if internalize_item_assets(item, internalizer))
            logger.info("  Internalized assets for %d/%d items", changed, len(items))

        if args.dry_run:
            for item in items:
                print(json.dumps(item, indent=2, ensure_ascii=False))
            total += len(items)
            continue

        db_name = os.getenv("COSMOS_DATABASE_NAME", "contentdb")
        container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
        container = get_container(container_name=container_name, database_name=db_name)

        # Fields set by the OCR substep that must be preserved across re-ingestion
        # runs.  upsert_item() does a full document replacement in Cosmos, so we
        # read the existing doc first and carry forward any OCR-generated fields so
        # that a re-scrape doesn't force a full re-OCR of already-processed items.
        _OCR_PRESERVE_FIELDS = (
            "ocr_status",
            "ocr_text_original_blob_url",
            "ocr_text_flexible_blob_url",
            "ocr_accuracy",
            "ocr_page_count",
            "ocr_version",
        )

        count = 0
        for item in items:
            item_id = item.get("id", "?")
            item_source = item.get("source", "")

            # Preserve OCR fields from any previous run so re-ingestion does not
            # force a full re-OCR of already-processed items.
            try:
                existing = container.read_item(item=item_id, partition_key=item_source)
                for field in _OCR_PRESERVE_FIELDS:
                    if field in existing and field not in item:
                        item[field] = existing[field]
            except CosmosHttpResponseError as exc:
                if exc.status_code != 404:
                    logger.warning("  Could not read existing doc for %s: %s", item_id, exc)
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # New item — nothing to preserve

            for attempt in range(4):
                try:
                    container.upsert_item(item)
                    count += 1
                    logger.info("  Upserted: %s", item_id)
                    break
                except CosmosHttpResponseError as exc:
                    if exc.status_code in (429, 503):
                        wait = (
                            float((exc.headers or {}).get("x-ms-retry-after-ms", 1000 * (attempt + 1)))
                            / 1000
                        )
                        logger.warning(
                            "  %d on %s (attempt %d/4), retrying in %.1fs",
                            exc.status_code, item_id, attempt + 1, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.error("  Failed to upsert %s: %s", item_id, exc)
                        break
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.error("  Failed to upsert %s: %s", item_id, exc)
                    break

        logger.info("  Upserted %d / %d items for %s", count, len(items), label)
        total += count

    action = "printed" if args.dry_run else "ingested"
    logger.info("Done. Total %s: %d items", action, total)


if __name__ == "__main__":
    main()
