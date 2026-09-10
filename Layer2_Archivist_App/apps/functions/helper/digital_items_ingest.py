"""
Digital Items Ingestion Helper — fetches items from external sources.

Sources:
  - cyclopedia: Wikipedia API (Theodore Roosevelt Cyclopedia article)
  - moore: TRC Digital Library (Moore Chronology search results)
  - genealogy: TRA Website (Genealogy & Papers module pages)

Each source returns a list of Cosmos DB-ready item dicts.

This module is designed to run inside Azure Functions activities (long-running,
retryable). It uses only stdlib + requests (no Cosmos writes — that's the
caller's job).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright

from helper.constants import KNOWN_SOURCES

logger = logging.getLogger(__name__)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------
_REQUEST_DELAY = float(os.getenv("TRC_REQUEST_DELAY", "0.3"))
_TRA_FETCH_WORKERS = int(os.getenv("TRA_FETCH_WORKERS", "3"))
_USER_AGENT = os.getenv(
    "TRC_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
)
_WAF_MAX_CONSECUTIVE_FAILS = int(os.getenv("TRA_WAF_MAX_FAILS", "5"))
_WAF_BACKOFF_BASE = float(os.getenv("TRA_WAF_BACKOFF_BASE", "10"))
_WIKI_HEADERS = {
    "User-Agent": "TRPLArchivistBot/1.0 (research project; contact@example.com)"
}


# ---------------------------------------------------------------------------
# Thumbnail internalization — download external image and upload to blob
# ---------------------------------------------------------------------------

def _internalize_thumbnail(item_id: str, thumb_url: str) -> str | None:
    """Download an external thumbnail and upload to Azure Blob Storage.

    Retries up to 3 times with backoff to handle rate-limiting from sources
    like Wikipedia/Wikimedia. Returns the Azure blob URL on success, or None
    on failure (original URL should be kept as fallback).
    """
    if not thumb_url or ".blob.core.windows.net" in thumb_url:
        return None  # Already internalized or empty

    from helper.constants import REFERER_MAP
    from helper.blob_util import upload_blob_content
    from urllib.parse import urlparse

    try:
        parsed = urlparse(thumb_url)
        referer = REFERER_MAP.get(
            parsed.netloc.lower(),
            f"{parsed.scheme}://{parsed.netloc}/",
        )
        download_headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        }

        # Retry with backoff (Wikipedia/Wikimedia rate-limits rapid requests)
        content: bytes | None = None
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.get(thumb_url, headers=download_headers, timeout=30)
                if resp.status_code == 429:
                    wait = 2 * (attempt + 1)
                    logger.info("  Rate-limited downloading %s, retrying in %ds", thumb_url[:60], wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                content = resp.content
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_err = e
                time.sleep(1 * (attempt + 1))

        if not content or len(content) < 100:
            if last_err:
                logger.warning("Failed to download thumbnail for %s: %s", item_id, last_err)
            return None

        # Determine extension — handle URLs like .svg.png (Wikimedia thumbnail renders)
        path_part = parsed.path.split("?")[0]
        filename = path_part.split("/")[-1]
        ext = "jpg"
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
                ext = "jpg"

        # Determine content type from response or extension
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not content_type or content_type == "application/octet-stream":
            ct_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "gif": "image/gif", "webp": "image/webp"}
            content_type = ct_map.get(ext, "image/jpeg")

        blob_path = f"{item_id}/assets/thumbnail.{ext}"
        blob_url = upload_blob_content(blob_path, content, content_type)
        if blob_url:
            logger.info("Internalized thumbnail for %s: %s -> %s", item_id, thumb_url[:80], blob_url)
            time.sleep(0.3)
        return blob_url
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to internalize thumbnail for %s from %s: %s", item_id, thumb_url[:80], exc)
        return None


def ingest_source(source_key: str) -> list[dict[str, Any]]:
    """Dispatch ingestion by source key. Returns list of Cosmos-ready items."""
    return list(iter_ingest_source(source_key))


def iter_ingest_source(source_key: str):
    """Dispatch ingestion by source key. Yields items one at a time."""
    if source_key in ("cyclopedia", "tr-cyclopedia"):
        yield from iter_ingest_cyclopedia()
    elif source_key in ("moore", "moore-chronology"):
        yield from iter_ingest_moore()
    elif source_key in ("genealogy", "genealogy-papers"):
        yield from iter_ingest_genealogy()
    else:
        raise ValueError(f"Unknown source: {source_key}. Valid: {sorted(KNOWN_SOURCES)}")


# ===========================================================================
# TR Cyclopedia — Wikipedia API
# ===========================================================================
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ARTICLE_TITLE = "Theodore_Roosevelt_Cyclopedia"
WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary"


def ingest_cyclopedia() -> list[dict[str, Any]]:
    """Fetch TR Cyclopedia from Wikipedia, transform into digital items."""
    return list(iter_ingest_cyclopedia())


def iter_ingest_cyclopedia():
    """Yield cyclopedia items one at a time from Wikipedia."""
    logger.info("Fetching Wikipedia article: %s", ARTICLE_TITLE)
    sections = _wiki_fetch_sections()
    logger.info("Parsed %d sections from Wikipedia", len(sections))

    # Collect unique links across all sections
    all_links: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for sec in sections:
        for link in sec.get("links", []):
            key = link["target"].lower()
            if key not in seen_targets:
                seen_targets.add(key)
                all_links.append(link)

    logger.info("Fetching summaries for %d wiki links...", len(all_links))
    topic_summaries = _wiki_fetch_topic_summaries(all_links)
    logger.info("Got %d topic summaries", len(topic_summaries))

    count = 0
    for item in _wiki_build_items(sections, topic_summaries):
        yield item
        count += 1
    logger.info("Cyclopedia: yielded %d items", count)


def _wiki_fetch_sections() -> list[dict[str, Any]]:
    """Fetch and parse Wikipedia article sections."""
    params = {
        "action": "parse",
        "page": ARTICLE_TITLE,
        "prop": "sections|wikitext",
        "format": "json",
    }
    data: dict[str, Any] | None = None
    for attempt in range(5):
        resp = requests.get(
            WIKIPEDIA_API, params=params, headers=_WIKI_HEADERS, timeout=30
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 10 * (attempt + 1)
            logger.warning(
                "Wikipedia %d (attempt %d/5), retrying in %ds",
                resp.status_code, attempt + 1, wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("parse")
        if data:
            break
        time.sleep(10 * (attempt + 1))
    if data is None:
        raise RuntimeError(f"Failed to fetch Wikipedia data for {ARTICLE_TITLE}")

    wikitext = data.get("wikitext", {}).get("*", "")
    section_pattern = re.compile(r"^(={2,})\s*(.+?)\s*\1\s*$", re.MULTILINE)

    parts: list[dict[str, Any]] = []
    matches = list(section_pattern.finditer(wikitext))

    # Lead section
    lead_text = wikitext[: matches[0].start()].strip() if matches else wikitext.strip()
    if lead_text:
        parts.append({
            "heading": "Overview",
            "level": 1,
            "body": _clean_wikitext(lead_text),
            "links": _extract_wiki_links(lead_text),
        })

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
        body = wikitext[start:end].strip()

        if heading.lower() in ("see also", "references", "external links"):
            continue

        cleaned = _clean_wikitext(body)
        if cleaned:
            parts.append({
                "heading": heading,
                "level": level,
                "body": cleaned,
                "links": _extract_wiki_links(body),
            })

    return parts


def _clean_wikitext(text: str) -> str:
    """Remove common wikitext markup."""
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[\[[^|\]]*\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+ ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\[\[Category:[^\]]+\]\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_wiki_links(text: str) -> list[dict[str, str]]:
    """Extract [[Link|Display]] patterns."""
    pattern = re.compile(r"\[\[([^|\]]+?)(?:\|([^\]]+?))?\]\]")
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for m in pattern.finditer(text):
        target = m.group(1).strip()
        display = (m.group(2) or target).strip()
        if ":" in target and target.split(":")[0].lower() in ("category", "file", "image"):
            continue
        if target.lower() not in seen:
            seen.add(target.lower())
            links.append({"target": target, "display": display})
    return links


def _wiki_fetch_topic_summaries(
    links: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Fetch Wikipedia REST summaries for linked topics."""
    summaries: dict[str, dict[str, Any]] = {}
    for link in links:
        target = link["target"].replace(" ", "_")
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{WIKI_SUMMARY_API}/{target}",
                    headers=_WIKI_HEADERS,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    entry: dict[str, Any] = {
                        "title": data.get("title", link["display"]),
                        "display": link["display"],
                        "summary": data.get("extract", ""),
                    }
                    thumb = data.get("thumbnail")
                    if thumb:
                        entry["thumbnail_url"] = thumb.get("source", "")
                        entry["thumbnail_width"] = thumb.get("width", 0)
                        entry["thumbnail_height"] = thumb.get("height", 0)
                    summaries[link["target"].lower()] = entry
                    break
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                else:
                    break
            except Exception:
                break
        time.sleep(0.2)
    return summaries


def _wiki_build_items(
    sections: list[dict[str, Any]], topic_summaries: dict[str, dict[str, Any]]
):
    """Yield Cosmos items from parsed sections one at a time."""
    for idx, sec in enumerate(sections):
        item_id = f"tr-cyclopedia-{hashlib.md5(sec['heading'].encode()).hexdigest()[:8]}"
        linked_topics = []
        for link in sec.get("links", []):
            key = link["target"].lower()
            if key in topic_summaries:
                linked_topics.append(topic_summaries[key])

        # Internalize linked_topics thumbnail URLs
        for topic_idx, topic in enumerate(linked_topics):
            thumb = topic.get("thumbnail_url")
            if thumb and ".blob.core.windows.net" not in thumb:
                internalized = _internalize_thumbnail(
                    f"{item_id}/topic-{topic_idx}", thumb
                )
                if internalized:
                    topic["thumbnail_url"] = internalized

        plain_text = sec["body"]
        description = (
            plain_text[:300].rsplit(" ", 1)[0] if len(plain_text) > 300 else plain_text
        )

        item: dict[str, Any] = {
            "id": item_id,
            "source": "tr-cyclopedia",
            "item_type": "document",
            "title": sec["heading"],
            "description": description,
            "plain_text": plain_text,
            "linked_topics": linked_topics,
            "order": idx,
            "source_url": f"https://en.wikipedia.org/wiki/{ARTICLE_TITLE}",
            "pattern": "prose",
        }
        first_thumb = next(
            (t.get("thumbnail_url") for t in linked_topics if t.get("thumbnail_url")),
            None,
        )
        if first_thumb:
            item["image_url"] = first_thumb
        yield item


# ===========================================================================
# Moore Chronology — TRC Digital Library
# ===========================================================================
TRC_BASE = "https://www.theodorerooseveltcenter.org"
MOORE_SEARCH_URL = os.getenv(
    "MOORE_SEARCH_URL",
    "https://www.theodorerooseveltcenter.org/digital-library/?collection=&s=Moore+Chronology&resource_type=&production_method=&publication=&date_from=&date_to=&post_type=digital-library&view=expanded&per_page=10&sort=relevance#results",
)


# TRC Playwright browser — shared across detail page fetches.
_trc_use_browser: bool = False
_trc_local = threading.local()
_trc_browser_lock = threading.Lock()
# Single-thread executor to run Playwright sync API outside the asyncio loop.
_trc_pw_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")


def _trc_get_browser_context():
    """Get or create a thread-local Playwright browser context for TRC."""
    ctx = getattr(_trc_local, "browser_context", None)
    if ctx is not None:
        return ctx
    with _trc_browser_lock:
        ctx = getattr(_trc_local, "browser_context", None)
        if ctx is not None:
            return ctx
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(user_agent=_USER_AGENT)
        context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )
        _trc_local.playwright_instance = pw
        _trc_local.browser = browser
        _trc_local.browser_context = context
        logger.info("TRC Playwright browser launched")
        return context


def _trc_close_browser():
    """Close the thread-local TRC browser if open."""
    _trc_pw_executor.submit(_trc_close_browser_sync).result()


def _trc_close_browser_sync():
    """Close the thread-local TRC browser (runs in executor thread)."""
    browser = getattr(_trc_local, "browser", None)
    pw = getattr(_trc_local, "playwright_instance", None)
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if pw:
        try:
            pw.stop()
        except Exception:
            pass
    _trc_local.browser_context = None
    _trc_local.browser = None
    _trc_local.playwright_instance = None


def _trc_playwright_fetch(url: str, max_retries: int = 3) -> str | None:
    """Fetch URL using Playwright browser, return HTML or None.

    Submits to a dedicated thread to avoid conflict with the asyncio event loop
    used by Azure Functions.
    """
    return _trc_pw_executor.submit(_trc_playwright_fetch_sync, url, max_retries).result()


def _trc_playwright_fetch_sync(url: str, max_retries: int = 3) -> str | None:
    """Fetch URL using Playwright browser (must run outside asyncio loop)."""
    try:
        context = _trc_get_browser_context()
        for attempt in range(max_retries):
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                html = page.content()
                title = page.title().lower()
                # If Cloudflare challenge, wait for JS to solve
                if len(html) < 5000 or "challenge" in title:
                    page.wait_for_load_state("networkidle", timeout=60000)
                    page.wait_for_timeout(2000)
                    html = page.content()
                    title = page.title().lower()
                # Detect server error pages (504, 502, etc.)
                if "error code 5" in title or "502" in title or "504" in title:
                    wait = 10 * (attempt + 1)
                    logger.warning("  TRC Playwright got server error page (attempt %d/%d) — retrying in %ds", attempt + 1, max_retries, wait)
                    page.close()
                    time.sleep(wait)
                    continue
                if len(html) < 5000:
                    logger.error("TRC Playwright: page too small for %s (%d bytes)", url, len(html))
                    page.close()
                    return None
                return html
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        logger.error("TRC Playwright: server error persisted after %d retries for %s", max_retries, url)
        return None
    except Exception as exc:
        logger.error("TRC Playwright fetch failed for %s: %s", url, exc, exc_info=True)
        return None


def ingest_moore() -> list[dict[str, Any]]:
    """Fetch Moore Chronology items from TRC Digital Library."""
    return list(iter_ingest_moore())


def iter_ingest_moore():
    """Yield Moore Chronology items one at a time from TRC Digital Library."""
    logger.info("Fetching Moore Chronology from TRC (%s)", MOORE_SEARCH_URL)
    try:
        oids = _trc_search_all_oids(MOORE_SEARCH_URL)
        if not oids:
            logger.warning("No Moore items found at TRC search URL")
            return

        for idx, oid in enumerate(oids, 1):
            detail = _trc_fetch_item_detail(oid)
            cosmos_item = _trc_detail_to_item(detail)
            logger.info("  [%d/%d] Built: %s — %s", idx, len(oids), cosmos_item["id"], cosmos_item["title"])
            yield cosmos_item

        logger.info("Moore Chronology: yielded %d items", len(oids))
    finally:
        _trc_close_browser()


class _TRCSearchParser(HTMLParser):
    """Extract item OIDs from TRC search result pages."""

    OID_RE = re.compile(r"/digital-library/([a-zA-Z0-9]+)/?")

    def __init__(self):
        super().__init__()
        self.oids: list[str] = []
        self.next_page_url: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        ad = dict(attrs)
        href = ad.get("href", "")
        m = self.OID_RE.search(href)
        if m:
            oid = m.group(1)
            if oid not in self.oids and oid.lower() not in ("search", "about", "help", "page"):
                self.oids.append(oid)
        # Pagination
        if "next" in ad.get("class", "").lower() or "next" in ad.get("aria-label", "").lower():
            self.next_page_url = href


def _trc_http_get(url: str, referer: str = "", max_retries: int = 3) -> requests.Response:
    """GET with custom headers, retry, and Playwright fallback for Cloudflare."""
    global _trc_use_browser

    # If already in browser-only mode, go straight to Playwright
    if _trc_use_browser:
        html = _trc_playwright_fetch(url)
        if html is None:
            raise RuntimeError(
                f"TRC Playwright fetch failed for {url}. "
                f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH', 'NOT SET')}"
            )
        resp = requests.models.Response()
        resp.status_code = 200
        resp._content = html.encode("utf-8")
        resp.encoding = "utf-8"
        time.sleep(_REQUEST_DELAY)
        return resp

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    verify = os.getenv("INGEST_SSL_VERIFY", "1") != "0"
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=60, verify=verify)
            # Retry on server errors (502/503/504)
            if resp.status_code >= 500:
                last_err = requests.exceptions.HTTPError(
                    f"{resp.status_code} Server Error", response=resp
                )
                wait = 5 * (attempt + 1)
                logger.warning("  TRC server error %d (attempt %d/%d) — retrying in %ds", resp.status_code, attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            # Check if Cloudflare returned a challenge page instead of real content
            if len(resp.content) < 5000 and ("challenge" in resp.text.lower() or "cf-" in resp.text.lower()):
                logger.info("  Cloudflare challenge detected, switching to Playwright for TRC")
                _trc_use_browser = True
                html = _trc_playwright_fetch(url)
                if html is None:
                    raise RuntimeError(f"TRC Playwright fetch failed for {url}")
                resolved = requests.models.Response()
                resolved.status_code = 200
                resolved._content = html.encode("utf-8")
                resolved.encoding = "utf-8"
                return resolved
            time.sleep(_REQUEST_DELAY)
            return resp
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            wait = 5 * (attempt + 1)
            logger.warning("  TRC request failed (attempt %d/%d): %s — retrying in %ds", attempt + 1, max_retries, type(e).__name__, wait)
            time.sleep(wait)
    # All HTTP retries exhausted — try Playwright as last resort
    logger.info("  HTTP retries exhausted for %s, falling back to Playwright", url)
    _trc_use_browser = True
    html = _trc_playwright_fetch(url)
    if html is None:
        raise last_err  # type: ignore[misc]
    resp = requests.models.Response()
    resp.status_code = 200
    resp._content = html.encode("utf-8")
    resp.encoding = "utf-8"
    return resp


def _trc_search_all_oids(search_url: str) -> list[str]:
    """Crawl all pages of TRC search results, return item OIDs."""
    oids: list[str] = []
    url: str | None = search_url
    page = 1
    while url:
        logger.info("  TRC search page %d: %s", page, url)
        resp = _trc_http_get(url, referer=search_url)
        parser = _TRCSearchParser()
        parser.feed(resp.text)
        new_oids = [o for o in parser.oids if o not in oids]
        oids.extend(new_oids)

        if page == 1 and not oids:
            raise RuntimeError(
                f"TRC search returned HTTP {resp.status_code} but 0 item OIDs found. "
                "Check MOORE_SEARCH_URL and TRC website structure."
            )

        next_url = parser.next_page_url
        if not next_url:
            # Try regex fallback for pagination
            for m in re.finditer(r'href="([^"]*[?&]page=(\d+)[^"]*)"', resp.text):
                candidate = int(m.group(2))
                if candidate == page + 1:
                    href = m.group(1)
                    next_url = (
                        urljoin(TRC_BASE, href)
                        if not href.startswith("http")
                        else href
                    )
                    break

        if next_url and next_url != url:
            url = next_url
            page += 1
        else:
            url = None

    logger.info("  Discovered %d TRC item OIDs", len(oids))
    return oids


class _TRCDetailParser(HTMLParser):
    """Parse a TRC item detail page for metadata, files, thumbnail."""

    _ASSET_RE = re.compile(
        r"\.(pdf|jpe?g|png|gif|webp|tiff?)(?:\?.*)?$", re.I
    )

    @classmethod
    def _is_asset_url(cls, url: str) -> bool:
        """Return True if URL looks like a downloadable asset (PDF/image)."""
        return bool(
            cls._ASSET_RE.search(url.split("?")[0])
            or "/s3/" in url
            or "/original/" in url
        )

    def __init__(self):
        super().__init__()
        self.title: str = ""
        self.description: str = ""
        self.thumbnail_url: str = ""
        self.file_urls: list[str] = []
        self.metadata: dict[str, str] = {}
        self._in_title = False
        self._in_meta_label = False
        self._in_meta_value = False
        self._current_label = ""
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        cls = ad.get("class", "")

        if tag == "h1":
            self._in_title = True
            self._buf = ""
        elif tag == "img":
            src = ad.get("src", "")
            cls_lower = cls.lower()
            if src and not self.thumbnail_url and ("thumbnail" in cls_lower or "thumbnails/" in src.lower()):
                self.thumbnail_url = src if src.startswith("http") else urljoin(TRC_BASE, src)
            # Capture full-size image URLs (skip thumbnails — those go to thumbnail_url)
            if src and "thumbnails/" not in src.lower():
                full = src if src.startswith("http") else urljoin(TRC_BASE, src)
                if self._is_asset_url(full) and full not in self.file_urls:
                    self.file_urls.append(full)
        elif tag == "a":
            href = ad.get("href", "")
            if href:
                full = href if href.startswith("http") else urljoin(TRC_BASE, href)
                if self._is_asset_url(full) and full not in self.file_urls:
                    self.file_urls.append(full)
        elif "label" in cls.lower() or "field-label" in cls.lower():
            self._in_meta_label = True
            self._buf = ""
        elif "value" in cls.lower() or "field-value" in cls.lower():
            self._in_meta_value = True
            self._buf = ""
        # WordPress block-based TRC pages: <h3 class="wp-block-heading">Label</h3>
        elif tag == "h3" and "wp-block-heading" in cls:
            self._in_meta_label = True
            self._buf = ""
        # WordPress block-based TRC pages: <p class="wp-block-paragraph">Value</p>
        elif tag == "p" and "wp-block-paragraph" in cls and self._current_label:
            self._in_meta_value = True
            self._buf = ""
        # Inline bold label pattern: <b>Subject(s):</b> value...
        elif tag == "b" and not self._in_meta_label and not self._in_meta_value:
            self._in_meta_label = True
            self._buf = ""

    def handle_data(self, data):
        if self._in_title or self._in_meta_label or self._in_meta_value:
            self._buf += data

    def handle_endtag(self, tag):
        if tag == "h1" and self._in_title:
            self.title = self._buf.strip()
            self._in_title = False
        elif self._in_meta_label and tag in ("span", "div", "dt", "td", "h3"):
            self._current_label = self._buf.strip().rstrip(":")
            self._in_meta_label = False
        elif self._in_meta_label and tag == "b":
            # Inline <b>Label:</b> — label ends, start capturing value inline
            self._current_label = self._buf.strip().rstrip(":")
            self._in_meta_label = False
            self._in_meta_value = True
            self._buf = ""
        elif self._in_meta_value and tag in ("span", "div", "dd", "td", "p"):
            if self._current_label:
                self.metadata[self._current_label] = self._buf.strip()
            self._in_meta_value = False
            self._current_label = ""


def _trc_fetch_item_detail(oid: str) -> dict[str, Any]:
    """Fetch and parse a TRC item detail page."""
    global _trc_use_browser
    item_url = f"{TRC_BASE}/digital-library/{oid}/"
    logger.info("  Fetching TRC item: %s", item_url)
    try:
        resp = _trc_http_get(item_url, referer=MOORE_SEARCH_URL)
    except Exception as exc:
        logger.warning("  Failed to fetch %s: %s", item_url, exc)
        return {
            "oid": oid,
            "source_url": item_url,
            "title": oid,
            "description": "",
            "thumbnail_url": "",
            "file_urls": [],
            "metadata": {},
        }

    parser = _TRCDetailParser()
    parser.feed(resp.text)

    title = parser.title
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).split("|")[0].strip()

    # If parsing yielded no title AND no metadata, the page was likely a
    # Cloudflare challenge.  Switch to browser mode and retry once.
    if not title and not parser.metadata and not _trc_use_browser:
        logger.info("  Empty detail for %s — retrying with Playwright", oid)
        _trc_use_browser = True
        return _trc_fetch_item_detail(oid)

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


def _trc_detail_to_item(detail: dict[str, Any]) -> dict[str, Any]:
    """Convert TRC detail dict to Cosmos item."""
    oid = detail["oid"]
    meta = detail.get("metadata", {})
    title = detail.get("title") or oid

    resource_type_raw = (meta.get("Resource Type") or "").lower()
    if "chronology" in title.lower():
        item_type = "chronology"
    elif resource_type_raw == "letter":
        item_type = "letter"
    elif resource_type_raw == "photograph":
        item_type = "photograph"
    else:
        item_type = "document"

    # Date range
    date_range = ""
    years_in_title = re.findall(r"\d{4}", title)
    if len(years_in_title) >= 2:
        date_range = f"{years_in_title[0]}-{years_in_title[-1]}"
    elif years_in_title:
        date_range = years_in_title[0]
    if not date_range:
        period_years = re.findall(r"\d{4}", meta.get("Period", ""))
        if period_years:
            date_range = (
                f"{period_years[0]}-{period_years[-1]}"
                if len(period_years) >= 2
                else period_years[0]
            )
    if not date_range:
        date_range = meta.get("Creation Date", "")

    page_count: int | None = None
    try:
        page_count = int(meta.get("Page Count", ""))
    except (ValueError, TypeError):
        pass

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
            "resource_type": meta.get("Resource Type", ""),
            "record_type": meta.get("Record Type", ""),
            "period": meta.get("Period", ""),
            "subjects": meta.get("Subject(s)", ""),
            "language": meta.get("Language", ""),
            "production_method": meta.get("Production Method", ""),
        },
    }

    thumb = detail.get("thumbnail_url") or (image_urls[0] if image_urls else "")
    if thumb:
        internalized = _internalize_thumbnail(item["id"], thumb)
        item["thumbnail_url"] = internalized or thumb
    if page_count:
        item["page_count"] = page_count
    if pdf_urls or image_urls:
        item["files"] = [{"url": u} for u in (pdf_urls or image_urls)]

    return item


# ===========================================================================
# Genealogy & Papers — TRA Website
# ===========================================================================
TRA_BASE = "https://www.theodoreroosevelt.org"
GENEALOGY_INDEX_URL = os.getenv(
    "GENEALOGY_INDEX_URL",
    "https://www.theodoreroosevelt.org/content.aspx?page_id=22&club_id=991271&module_id=339179",
)


def ingest_genealogy() -> list[dict[str, Any]]:
    """Fetch Genealogy & Papers from TRA website."""
    return list(iter_ingest_genealogy())


def iter_ingest_genealogy():
    """Yield Genealogy & Papers items one at a time from TRA website.

    Strategy:
    1. Fetch the root index page via Playwright (bypasses AWS WAF).
    2. Parse the ClubExpress section-nav sidebar (div.resp-row) to discover
       all Cyclopedia sections dynamically — no hard-coded name matchers.
    3. Fetch each section page; if it has sub-items, fetch those too.
    4. Yield section hub items first, then sub-items within each section.
    """
    logger.info("Fetching Genealogy & Papers from TRA (%s)", GENEALOGY_INDEX_URL)

    try:
        # Fetch index page via Playwright — resolves AWS WAF JS challenge automatically
        resp = _tra_http_get(GENEALOGY_INDEX_URL)
        if not resp:
            raise RuntimeError(
                "Could not fetch TRA index page. Check GENEALOGY_INDEX_URL and connectivity."
            )

        # Still parse nav for sub-item discovery (nav-diff) later
        nav_parser = _TRANavParser()
        nav_parser.feed(resp.text)
        nav_parser.close()

        # Dynamic section discovery: extract from the section-nav sidebar (div.resp-row)
        sections = _discover_cyclopedia_sections(resp.text)
        if not sections:
            raise RuntimeError(
                "TRA index page returned 0 Cyclopedia sections from div.resp-row. "
                "Check GENEALOGY_INDEX_URL or TRA website structure."
            )

        logger.info("  Identified %d Cyclopedia sections", len(sections))

        # Track section module_ids to avoid treating them as sub-items
        seen_module_ids: set[str] = set()
        for _, sec_url, _ in sections:
            m = re.search(r"module_id=(\d+)", sec_url)
            if m:
                seen_module_ids.add(m.group(1))
        seen_module_ids.add(_CYCLOPEDIA_ROOT_MID)

        total = 0
        for order_idx, sec_url, section_title in sections:
            logger.info(
                "  [%d/%d] Fetching section: %s — %s",
                order_idx + 1, len(sections), section_title,
                sec_url.split("module_id=")[-1],
            )
            sec_items = _tra_fetch_section(
                sec_url, order_idx, section_title, seen_module_ids
            )
            for item in sec_items:
                yield item
                total += 1
            logger.info(
                "  [%d/%d] Got %d items (total: %d)",
                order_idx + 1, len(sections), len(sec_items), total,
            )

        logger.info("Genealogy & Papers: yielded %d items", total)
    finally:
        _tra_close_browser()


# Module-level WAF cookie cache — solved once per process, reused for all pages.
_tra_waf_cookies: dict[str, str] = {}
_tra_waf_fail_count: int = 0
_tra_waf_lock = threading.Lock()  # Serialize WAF cookie access across threads

# Thread-local browser context — Playwright greenlets are thread-bound,
# so each Azure Functions worker thread gets its own browser instance.

_tra_local = threading.local()
_tra_browser_lock = threading.Lock()
_tra_browser_active: bool = False  # Module-level flag; _tra_local is only visible in executor thread
# Single-thread executor to run Playwright sync API outside the asyncio loop.
_tra_pw_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright-tra")


def _tra_get_browser_context():
    """Get or create a thread-local Playwright browser context."""
    ctx = getattr(_tra_local, "browser_context", None)
    if ctx is not None:
        return ctx
    with _tra_browser_lock:
        # Double-check after acquiring lock
        ctx = getattr(_tra_local, "browser_context", None)
        if ctx is not None:
            return ctx
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
            ],
        )
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/Chicago",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}};
        """)
        global _tra_browser_active
        _tra_local.playwright_instance = pw
        _tra_local.browser = browser
        _tra_local.browser_context = context
        _tra_browser_active = True
        logger.info("Playwright browser launched (thread-local persistent context)")
        return context


def _tra_close_browser():
    """Close the thread-local browser if open."""
    _tra_pw_executor.submit(_tra_close_browser_sync).result()


def _tra_close_browser_sync():
    """Close the thread-local browser (runs in executor thread)."""
    browser = getattr(_tra_local, "browser", None)
    pw = getattr(_tra_local, "playwright_instance", None)
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if pw:
        try:
            pw.stop()
        except Exception:
            pass
    global _tra_browser_active
    _tra_local.browser_context = None
    _tra_local.browser = None
    _tra_local.playwright_instance = None
    _tra_browser_active = False


def _tra_http_get(url: str) -> requests.Response | None:
    """Fetch a TRA page using Playwright browser (always).

    TRA (theodoreroosevelt.org) is protected by AWS WAF which blocks server-side
    HTTP clients regardless of headers — the WAF fingerprints the TLS handshake
    and IP origin, serving 'Access Denied' or JS challenges to non-browser clients.
    Playwright launches a real Chromium instance that passes these checks automatically.
    """
    result = _tra_playwright_fetch(url)
    if result is None:
        logger.warning("TRA Playwright fetch returned None for %s", url)
        return None
    html, cookies = result
    with _tra_waf_lock:
        _tra_waf_cookies.update(cookies)
    resolved = requests.models.Response()
    resolved.status_code = 200
    resolved._content = html.encode("utf-8")
    resolved.encoding = "utf-8"
    time.sleep(_REQUEST_DELAY)
    return resolved


def _tra_playwright_fetch(url: str) -> tuple[str, dict[str, str]] | None:
    """Fetch URL using persistent browser context (no launch/close per request).

    Returns (page_html, cookies_dict) or None on failure.
    Submits to a dedicated thread to avoid conflict with the asyncio event loop
    used by Azure Functions.
    """
    return _tra_pw_executor.submit(_tra_playwright_fetch_sync, url).result()


def _tra_playwright_fetch_sync(url: str) -> tuple[str, dict[str, str]] | None:
    """Fetch URL using Playwright browser (must run outside asyncio loop)."""
    try:
        context = _tra_get_browser_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="load", timeout=30000)
            # Wait briefly for JS to populate content (most TRA pages render fast)
            page.wait_for_timeout(1500)
            try:
                html = page.content()
            except Exception:
                # Page might still be navigating; wait and retry
                page.wait_for_load_state("load", timeout=10000)
                html = page.content()
            # If we got a WAF challenge page, wait for JS to solve it
            if len(html) < 5000 or "Human Verification" in page.title():
                # Wait longer for JS challenge + potential CAPTCHA token exchange
                page.wait_for_load_state("networkidle", timeout=90000)
                page.wait_for_timeout(5000)
                html = page.content()
                # Check if still challenged
                if len(html) < 5000 or "Human Verification" in page.title():
                    # Try one more navigation — sometimes cookies set on first attempt work on reload
                    page.reload(wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(3000)
                    html = page.content()
                    if len(html) < 5000 or "Human Verification" in page.title():
                        logger.error("Playwright could not solve WAF for %s (title=%s)", url, page.title())
                        return None
                logger.info("Playwright solved WAF challenge for %s (%d bytes)", url, len(html))
            # Extract cookies for potential HTTP fallback
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            return html, cookies
        finally:
            page.close()
    except Exception as exc:
        logger.error("Playwright fetch failed for %s: %s", url, exc)
        return None


class _TRANavParser(HTMLParser):
    """Discovers content module URLs and anchor text from TRA nav links."""

    _MODULE_RE = re.compile(r"module_id=(\d+)", re.I)

    def __init__(self):
        super().__init__()
        self._seen_mids: set[str] = set()
        self.module_urls: list[str] = []
        # Anchor text for each module URL — used to identify sections by title
        self.module_link_texts: dict[str, str] = {}  # canonical url -> anchor text
        self._in_link: bool = False
        self._current_link_url: str = ""
        self._link_buf: str = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        ad = dict(attrs)
        href = (ad.get("href") or "").strip()
        if not href:
            return
        if href.startswith("/"):
            href = TRA_BASE + href

        m = self._MODULE_RE.search(href)
        if m and "theodoreroosevelt.org" in href:
            mid = m.group(1)
            canonical = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={mid}"
            if mid not in self._seen_mids:
                self._seen_mids.add(mid)
                self.module_urls.append(canonical)
            # Track anchor text for this link
            self._in_link = True
            self._current_link_url = canonical
            self._link_buf = ""

    def handle_data(self, data):
        if self._in_link:
            self._link_buf += data

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            text = re.sub(r"\s+", " ", self._link_buf).strip()
            if text and self._current_link_url:
                existing = self.module_link_texts.get(self._current_link_url, "")
                # Keep the longest / most descriptive anchor text seen for this URL
                if len(text) > len(existing):
                    self.module_link_texts[self._current_link_url] = text
            self._in_link = False
            self._link_buf = ""
            self._current_link_url = ""


# ── Dynamic Cyclopedia section discovery ──────────────────────────────────── #
# Instead of hard-coded name matchers, we parse the section navigation sidebar
# that ClubExpress renders inside `div.resp-row` on the Cyclopedia index page.
# The LAST such div contains exactly the 10 section links (full titles, no dups).
# If TRA adds or renames a section it is picked up automatically.

_CYCLOPEDIA_ROOT_MID = "339179"  # The only hard-coded anchor: the index page


def _discover_cyclopedia_sections(html: str) -> list[tuple[int, str, str]]:
    """Parse the Cyclopedia root page HTML and return (order, url, title) for each section.

    Uses the ClubExpress section-navigation sidebar rendered inside `div.resp-row`.
    Filters out the root index itself (module_id=339179) and empty anchor text.
    Returns sections in DOM order (= display order on the website).
    """
    from bs4 import BeautifulSoup  # already in requirements
    soup = BeautifulSoup(html, "html.parser")
    _mod_re = re.compile(r"module_id=(\d+)", re.I)

    # The LAST div.resp-row is the section-specific sidebar nav (full, unambiguous titles).
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


def _item_type_from_title(title: str) -> str:
    """Classify item_type from canonical section title keywords."""
    t = title.lower()
    if "chronology" in t:
        return "chronology"
    if "genealogy" in t:
        return "genealogy"
    if "family" in t:
        return "family"
    if "speech" in t:
        return "speeches"
    if "bibliography" in t:
        return "bibliography"
    if "index" in t:
        return "index"
    if "public paper" in t or "papers" in t:
        return "papers"
    if "introduction" in t or "about" in t:
        return "introduction"
    return "document"


def _infer_hub_pattern(title: str) -> str:
    """Return the UI pattern string for a section hub (has sub-item children)."""
    t = title.lower()
    if "speech" in t:
        return "speeches_sections"
    if "index" in t:
        return "index_hub"
    return "content_hub"


def _extract_clean_text(html: str) -> str:
    """Extract readable text from HTML using BeautifulSoup, stripping all noise.

    For ClubExpress (TRA) pages: scopes to #TextSizeModify (the article content
    div) to exclude the left-nav sidebar, accessibility toolbar, and global
    site navigation. Falls back to the threequarter column (with sidebar stripped)
    when #TextSizeModify is too short, then to generic noise removal.
    """
    from bs4 import BeautifulSoup

    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Remove elements that never contain useful content
    for tag in soup.find_all(["script", "style", "noscript", "template", "iframe"]):
        tag.decompose()

    # --- ClubExpress-specific scoping ---
    # Prefer #TextSizeModify (article body) to avoid left-nav/toolbar pollution.
    content_col = soup.select_one("#content_column")
    if content_col:
        text_mod = content_col.select_one("#TextSizeModify")
        if text_mod and len(text_mod.get_text(strip=True)) >= 100:
            scope: Any = text_mod
        else:
            # Widen to threequarter column but strip the quarter sidebar
            tq = content_col.select_one("div.column.threequarter")
            if tq:
                for col in tq.select("div.column.quarter, div.column.onequarter"):
                    col.decompose()
                scope = tq
            else:
                scope = content_col
    else:
        # Generic fallback: remove standard nav/header/footer regions
        for tag in soup.find_all(attrs={"class": re.compile(
            r"\b(?:header|footer|breadcrumb|nav|menu|sidebar|loginbar|topbar|cookie|consent)\b", re.I
        )}):
            tag.decompose()
        for tag in soup.find_all(attrs={"id": re.compile(
            r"\b(?:header|footer|breadcrumb|nav|menu|sidebar|loginbar|topbar|cookie|consent)\b", re.I
        )}):
            tag.decompose()
        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()
        scope = soup

    # Get text with newlines between block elements
    text = scope.get_text(separator="\n")

    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]*', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class _TRAContentParser(HTMLParser):
    """Parse TRA content area for module links, text, and image/TRC references.

    Text extraction is delegated to BeautifulSoup (_extract_clean_text) for
    reliable noise removal. This parser focuses on structural link extraction.
    """

    _MODULE_RE = re.compile(r"module_id=(\d+)", re.I)
    _IMG_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|tiff?|bmp|svg)(?:\?.*)?$", re.I)
    _TRC_DL_RE = re.compile(
        r"https?://(?:www\.)?theodorerooseveltcenter\.org/digital-library/(o\d+)",
        re.I,
    )

    def __init__(self):
        super().__init__()
        self.module_urls: list[str] = []
        self._seen_mids: set[str] = set()
        self.text_content: str = ""
        # Capture TRC digital library links and S3 image URLs
        self.trc_oids: list[str] = []
        self._seen_trc: set[str] = set()
        self.image_urls: list[str] = []
        self._seen_imgs: set[str] = set()
        self._raw_html: str = ""
        # Audio files: [{"url": str, "label": str}]
        self.audio_files: list[dict[str, str]] = []
        self._seen_audio: set[str] = set()
        self._in_audio_tag: bool = False
        self._in_audio_link: bool = False
        self._audio_link_href: str = ""
        self._audio_link_buf: str = ""

    def feed(self, data):
        self._raw_html = data
        super().feed(data)

    @staticmethod
    def _is_audio_href(href: str) -> bool:
        path = href.lower().split("?")[0]
        return any(path.endswith(ext) for ext in (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")) \
            or "docs.ashx" in href.lower()

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)

        if tag == "audio":
            self._in_audio_tag = True
            src = (ad.get("src") or "").strip()
            if src and src not in self._seen_audio:
                self._seen_audio.add(src)
                self.audio_files.append({"url": src, "label": ""})

        elif tag == "source" and self._in_audio_tag:
            src = (ad.get("src") or "").strip()
            if src and src not in self._seen_audio:
                self._seen_audio.add(src)
                self.audio_files.append({"url": src, "label": ""})

        elif tag == "a":
            href = (ad.get("href") or "").strip()
            if href.startswith("/"):
                href = TRA_BASE + href
            m = self._MODULE_RE.search(href)
            if m and "theodoreroosevelt.org" in href:
                mid = m.group(1)
                canonical = f"{TRA_BASE}/content.aspx?page_id=22&club_id=991271&module_id={mid}"
                if mid not in self._seen_mids:
                    self._seen_mids.add(mid)
                    self.module_urls.append(canonical)
            # Capture links to TRC digital library
            trc_m = self._TRC_DL_RE.search(href)
            if trc_m:
                oid = trc_m.group(1)
                if oid not in self._seen_trc:
                    self._seen_trc.add(oid)
                    self.trc_oids.append(oid)
            # Capture external image links (any host, image extension)
            if self._IMG_EXT_RE.search(href) and href.startswith("http"):
                if href not in self._seen_imgs:
                    self._seen_imgs.add(href)
                    self.image_urls.append(href)
            # Capture direct audio file links (mp3, wav, docs.ashx, etc.)
            if href and self._is_audio_href(href):
                if href not in self._seen_audio:
                    self._seen_audio.add(href)
                    self.audio_files.append({"url": href, "label": ""})
                self._in_audio_link = True
                self._audio_link_href = href
                self._audio_link_buf = ""

        # Capture external images from <img> tags
        if tag == "img":
            src = (ad.get("src") or "").strip()
            if src.startswith("http") and src not in self._seen_imgs:
                low = src.lower()
                if not any(kw in low for kw in ("logo", "icon", "btn", "arrow", "navigation")):
                    self._seen_imgs.add(src)
                    self.image_urls.append(src)

    def handle_data(self, data):
        if self._in_audio_link:
            self._audio_link_buf += data

    def handle_endtag(self, tag):
        if tag == "audio":
            self._in_audio_tag = False
        elif tag == "a" and self._in_audio_link:
            label = self._audio_link_buf.strip()
            if label:
                for af in self.audio_files:
                    if af["url"] == self._audio_link_href and not af["label"]:
                        af["label"] = label
                        break
            self._in_audio_link = False
            self._audio_link_href = ""
            self._audio_link_buf = ""

    def close(self):
        super().close()
        self.text_content = _extract_clean_text(self._raw_html)


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


def _tra_fetch_section(
    section_url: str,
    order_idx: int,
    canonical_title: str,
    seen_module_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch a TRA section page and extract the section item plus any sub-items.

    Always returns the section/hub item itself as the FIRST entry, followed by
    sub-items (speeches, papers entries, etc.) when they exist.
    Captures images (image_url, images[]) and audio (audio_url, audio_files[], has_audio)
    from both the section page and each sub-item page.
    """
    resp = _tra_http_get(section_url)
    if not resp:
        logger.warning("  Section '%s' fetch failed, retrying after 3s", canonical_title)
        time.sleep(3)
        resp = _tra_http_get(section_url)
    if not resp:
        logger.error("  Section '%s' SKIPPED (failed after retry)", canonical_title)
        return []

    # Extract module_id from URL
    m = re.search(r"module_id=(\d+)", section_url)
    module_id = m.group(1) if m else str(order_idx)

    # Parse section page
    content_parser = _TRAContentParser()
    content_parser.feed(resp.text)
    content_parser.close()

    text = content_parser.text_content.strip()
    text = _sanitize_tra_text(text)
    item_type = _item_type_from_title(canonical_title)
    description = _sanitize_tra_text(text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text)

    # Build base section item
    section_item: dict[str, Any] = {
        "id": f"genealogy-{module_id}",
        "source": "genealogy-papers",
        "item_type": item_type,
        "title": canonical_title,
        "plain_text": text[:50000] if text else "",
        "description": description,
        "source_url": section_url,
        "module_id": module_id,
        "order": order_idx,
    }

    # Attach images from section page
    _attach_images(section_item, content_parser.image_urls)

    # Attach audio from section page
    _attach_audio(section_item, content_parser.audio_files)

    items: list[dict[str, Any]] = []

    # Discover sub-items: deduplicate against already-seen section module_ids
    sub_urls: list[str] = []
    if content_parser.module_urls:
        for u in content_parser.module_urls:
            m_sub = re.search(r"module_id=(\d+)", u)
            if m_sub:
                mid = m_sub.group(1)
                if seen_module_ids is not None and mid in seen_module_ids:
                    continue
                if seen_module_ids is not None:
                    seen_module_ids.add(mid)
            sub_urls.append(u)

    if sub_urls:
        # Hub section: has sub-items — mark pattern and add hub item first
        section_item["pattern"] = _infer_hub_pattern(canonical_title)
        items.append(section_item)
        logger.info(
            "    Section '%s' has %d sub-items (pattern=%s)",
            canonical_title, len(sub_urls), section_item["pattern"],
        )
        for sub_idx, sub_url in enumerate(sub_urls):
            result = _tra_fetch_sub_item(sub_url, module_id, canonical_title, sub_idx)
            if result:
                logger.info(
                    "      [%d/%d] sub-item OK: %s (%d items)",
                    sub_idx + 1, len(sub_urls), sub_url.split("module_id=")[-1][:10], len(result),
                )
                items.extend(result)
            else:
                logger.warning(
                    "      [%d/%d] sub-item EMPTY: %s",
                    sub_idx + 1, len(sub_urls), sub_url.split("module_id=")[-1][:10],
                )
    else:
        # Leaf section: the page itself IS the content
        section_item["pattern"] = "prose"
        if text and len(text) > 50:
            items.append(section_item)
        else:
            logger.warning("  Section '%s': page has no usable text", canonical_title)

    return items


def _attach_images(item: dict[str, Any], image_urls: list[str]) -> None:
    """Attach image_url and images[] to an item from a list of image URLs."""
    if not image_urls:
        return
    # Filter noise URLs (logos, icons, navigation chrome)
    clean = [
        u for u in image_urls
        if not any(kw in u.lower() for kw in ("logo", "icon", "btn", "arrow", "navigation", "candid"))
    ]
    if not clean:
        return
    item["image_url"] = clean[0]
    if len(clean) > 1:
        item["images"] = [{"src": u, "alt": ""} for u in clean]


def _attach_audio(item: dict[str, Any], audio_files: list[dict[str, str]]) -> None:
    """Audio ingestion disabled — no-op."""
    return


def _tra_fetch_sub_item(
    url: str, parent_module_id: str, parent_title: str, order: int
) -> list[dict[str, Any]]:
    """Fetch a TRA sub-item page and convert to a digital item.

    Captures audio files (audio_url, audio_files[], has_audio) and images
    (image_url, images[]) from the TRA page. If the page also links to a TRC
    digital library record, enriches the item with TRC metadata and assets.
    """
    resp = _tra_http_get(url)
    if not resp:
        logger.warning("      Sub-item fetch returned None for %s", url.split("module_id=")[-1][:10])
        return []

    m = re.search(r"module_id=(\d+)", url)
    module_id = m.group(1) if m else f"{parent_module_id}-{order}"

    logger.debug("      Sub-item %s: response %d bytes", module_id, len(resp.text))
    content_parser = _TRAContentParser()
    content_parser.feed(resp.text)
    content_parser.close()

    title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
    title = ""
    if title_match:
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
        for suffix in (" - Theodore Roosevelt Association", " | TRA"):
            if title.endswith(suffix):
                title = title[: -len(suffix)]

    text = content_parser.text_content.strip()
    text = _sanitize_tra_text(text)
    if not text or len(text) < 20:
        # Audio-only pages may have no text but still have audio files
        if not content_parser.audio_files:
            logger.warning(
                "      Sub-item %s has insufficient text (%d chars) and no audio, skipping",
                module_id, len(text) if text else 0,
            )
            return []

    item_id = f"genealogy-{module_id}"
    item_type = _item_type_from_title(title or parent_title)
    item: dict[str, Any] = {
        "id": item_id,
        "source": "genealogy-papers",
        "item_type": item_type,
        "title": title or f"Item {order + 1}",
        "plain_text": text[:50000] if text else "",
        "description": _sanitize_tra_text(text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text),
        "source_url": url,
        "module_id": module_id,
        "parent_module_id": parent_module_id,
        "parent_id": f"genealogy-{parent_module_id}",
        "hub_label": parent_title,
        "order": order,
        "pattern": "prose",
    }

    # Attach images and audio from the TRA page
    _attach_images(item, content_parser.image_urls)
    _attach_audio(item, content_parser.audio_files)

    # --- Enrich with TRC digital library data (images + metadata) ---
    if content_parser.trc_oids:
        oid = content_parser.trc_oids[0]  # primary TRC record
        try:
            trc_detail = _trc_fetch_item_detail(oid)
            if trc_detail:
                item["trc_oid"] = oid
                item["trc_source_url"] = f"{TRC_BASE}/digital-library/{oid}/"
                if trc_detail.get("title"):
                    item["title"] = trc_detail["title"]
                if trc_detail.get("description"):
                    item["description"] = trc_detail["description"]
                if trc_detail.get("thumbnail_url"):
                    thumb_url = trc_detail["thumbnail_url"]
                    internalized = _internalize_thumbnail(item_id, thumb_url)
                    item["thumbnail_url"] = internalized or thumb_url
                file_urls = trc_detail.get("file_urls", [])
                if file_urls:
                    item["files"] = [{"url": u} for u in file_urls]
                meta = trc_detail.get("metadata", {})
                if meta:
                    item["metadata"] = {
                        "collection": meta.get("Collection", ""),
                        "creation_date": meta.get("Creation Date", ""),
                        "creators": meta.get("Creator(s)", ""),
                        "resource_type": meta.get("Resource Type", ""),
                        "record_type": meta.get("Record Type", ""),
                        "period": meta.get("Period", ""),
                        "subjects": meta.get("Subject(s)", ""),
                    }
                    if meta.get("Creation Date"):
                        item["date_range"] = meta["Creation Date"]
                    rt = meta.get("Resource Type", "").lower()
                    if rt:
                        item["item_type"] = rt
                page_count = meta.get("Page Count", "")
                if page_count:
                    try:
                        item["page_count"] = int(page_count)
                    except (ValueError, TypeError):
                        pass
                logger.info("    Enriched %s with TRC oid=%s (%d files)", item_id, oid, len(file_urls))
        except Exception as exc:
            logger.warning("    TRC enrichment failed for %s (oid=%s): %s", item_id, oid, exc)
    elif not content_parser.audio_files and content_parser.image_urls:
        # Only images (no audio, no TRC link) — attach as files/thumbnail
        thumbs = [u for u in content_parser.image_urls if "/thumbnail" in u.lower()]
        fulls = [u for u in content_parser.image_urls if "/thumbnail" not in u.lower()]
        asset_urls = fulls or thumbs
        item["files"] = [{"url": u} for u in asset_urls]
        if thumbs and not item.get("thumbnail_url"):
            internalized = _internalize_thumbnail(item_id, thumbs[0])
            item["thumbnail_url"] = internalized or thumbs[0]

    return [item]
