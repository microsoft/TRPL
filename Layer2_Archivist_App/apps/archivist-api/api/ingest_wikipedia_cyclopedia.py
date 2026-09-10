"""
Ingest the Wikipedia article on the Theodore Roosevelt Cyclopedia into Cosmos DB.

Fetches the article via Wikipedia's API, splits it into sections, and upserts
structured documents into the ``digital-items`` container with source="tr-cyclopedia".

Usage (from ArchivistApp/apps/archivist-api/api):
  python ingest_wikipedia_cyclopedia.py
  python ingest_wikipedia_cyclopedia.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Relative imports for helper modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper.cosmos_client import get_container  # pylint: disable=import-error
from helper.credential import get_credential  # pylint: disable=import-error
from helper.blob_utils import (  # pylint: disable=import-error
    guess_content_type as _guess_content_type,
    filename_from_url as _filename_from_url,
    is_external_url as _is_external_url,
    upload_blob_no_overwrite,
)

from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
# Suppress Azure SDK HTTP transport noise (request/response headers) that leaks
# into job output when AZURE_LOG_LEVEL=info is set in the App Service environment.
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ARTICLE_TITLE = "Theodore_Roosevelt_Cyclopedia"
SOURCE = "tr-cyclopedia"


from helper.local_settings import load_local_settings  # pylint: disable=import-error


def _load_local_settings():
    """Load local.settings.json Values into environment if present."""
    load_local_settings(__file__)


def fetch_article_sections() -> list[dict[str, Any]]:
    """Fetch article sections from Wikipedia API (parsed HTML and wikitext)."""
    # Get section list
    params = {
        "action": "parse",
        "page": ARTICLE_TITLE,
        "prop": "sections|wikitext",
        "format": "json",
    }
    headers = {"User-Agent": "TRPLArchivistBot/1.0 (research project; contact@example.com)"}
    data: dict[str, Any] | None = None
    for attempt in range(5):
        resp = requests.get(WIKIPEDIA_API, params=params, headers=headers, timeout=30)
        transient_status = resp.status_code == 429 or resp.status_code == 202 or resp.status_code >= 500
        if transient_status:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and str(retry_after).isdigit():
                wait = int(retry_after)
            else:
                wait = 10 * (attempt + 1)
            logger.warning(
                "Wikipedia transient response %s on article fetch (attempt %d/5), retrying in %ds",
                resp.status_code,
                attempt + 1,
                wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            if attempt == 4:
                raise
            wait = 10 * (attempt + 1)
            logger.warning(
                "Wikipedia returned a non-JSON response on article fetch (attempt %d/5), retrying in %ds",
                attempt + 1,
                wait,
            )
            time.sleep(wait)
            continue
        data = payload.get("parse")
        if data:
            break
        if attempt == 4:
            raise RuntimeError("Wikipedia API response did not include a parse payload")
        wait = 10 * (attempt + 1)
        logger.warning(
            "Wikipedia API response missing parse payload on article fetch (attempt %d/5), retrying in %ds",
            attempt + 1,
            wait,
        )
        time.sleep(wait)
    if data is None:
        raise RuntimeError(f"Failed to fetch Wikipedia parse data for {ARTICLE_TITLE}")

    wikitext = data.get("wikitext", {}).get("*", "")
    sections_meta = data.get("sections", [])

    wikitext = data.get("wikitext", {}).get("*", "")
    sections_meta = data.get("sections", [])

    # Split wikitext by == headings ==
    # Pattern: match lines like == Heading == or === Sub-heading ===
    section_pattern = re.compile(r'^(={2,})\s*(.+?)\s*\1\s*$', re.MULTILINE)

    parts: list[dict[str, Any]] = []
    matches = list(section_pattern.finditer(wikitext))

    # Lead section (before first heading)
    if matches:
        lead_text = wikitext[:matches[0].start()].strip()
    else:
        lead_text = wikitext.strip()

    if lead_text:
        # Clean wikitext markup
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

        # Skip "See also", "References", "External links" sections
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
    """Remove common wikitext markup, leaving readable plain text."""
    # Remove ref tags and their content
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^/]*/>', '', text)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Convert [[Link|Display]] to Display, [[Link]] to Link
    text = re.sub(r'\[\[[^|\]]*\|([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # Remove external links [url text] -> text
    text = re.sub(r'\[https?://[^\s\]]+ ([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\]]+\]', '', text)
    # Remove bold/italic markup
    text = re.sub(r"'{2,5}", '', text)
    # Remove {{cite...}} and other templates (simple single-level)
    text = re.sub(r'\{\{[^}]+\}\}', '', text)
    # Remove category links
    text = re.sub(r'\[\[Category:[^\]]+\]\]', '', text)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove blockquote markers
    text = text.replace('<blockquote>', '').replace('</blockquote>', '')
    return text.strip()


def _extract_wiki_links(text: str) -> list[dict[str, str]]:
    """Extract [[Link|Display]] and [[Link]] patterns from wikitext, returning unique links."""
    # Match [[Target|Display]] or [[Target]]
    pattern = re.compile(r'\[\[([^|\]]+?)(?:\|([^\]]+?))?\]\]')
    seen = set()
    links = []
    for m in pattern.finditer(text):
        target = m.group(1).strip()
        display = (m.group(2) or target).strip()
        # Skip category/file links
        if ':' in target and target.split(':')[0].lower() in ('category', 'file', 'image'):
            continue
        if target.lower() not in seen:
            seen.add(target.lower())
            links.append({"target": target, "display": display})
    return links


WIKI_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary"
HEADERS = {"User-Agent": "TRPLArchivistBot/1.0 (research project; contact@example.com)"}




class AssetInternalizer:
    """Copies external assets to Azure Blob storage and returns internal blob URLs."""

    def __init__(self, storage_account: str, storage_container: str, blob_prefix: str):
        account_url = f"https://{storage_account}.blob.core.windows.net"
        self._container_client = BlobServiceClient(
            account_url=account_url,
            credential=get_credential(),
        ).get_container_client(storage_container)
        self._prefix = blob_prefix.strip("/")
        self._cache: dict[str, str] = {}

    def _download(self, url: str) -> tuple[bytes, str | None]:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type")

    def _blob_name(self, item_id: str, source_url: str) -> str:
        digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
        filename = _filename_from_url(source_url, "thumbnail.bin")
        return f"{self._prefix}/{SOURCE}/{item_id}/linked-topic-thumbnail/{digest}-{filename}"

    def upload_content(self, item_id: str, payload: dict) -> str:
        """Upload item content JSON to blob and return the blob URL."""
        blob_name = f"{self._prefix}/{SOURCE}/{item_id}/content.json"
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        from azure.storage.blob import ContentSettings
        blob_client = self._container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        return blob_client.url

    def materialize(self, item_id: str, source_url: str) -> str:
        cached = self._cache.get(source_url)
        if cached:
            return cached

        content, response_type = self._download(source_url)
        content_type = _guess_content_type(source_url, response_type)
        blob_name = self._blob_name(item_id, source_url)

        blob_url = upload_blob_no_overwrite(
            self._container_client, blob_name, content, content_type
        )
        self._cache[source_url] = blob_url
        return blob_url


def internalize_linked_topic_assets(items: list[dict[str, Any]], internalizer: AssetInternalizer) -> int:
    """Rewrite external linked_topics[].thumbnail_url values to internal blob URLs."""
    rewritten = 0
    for item in items:
        topics = item.get("linked_topics")
        if not isinstance(topics, list):
            continue
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            thumb = topic.get("thumbnail_url")
            if not isinstance(thumb, str) or not _is_external_url(thumb):
                continue
            try:
                topic["thumbnail_url"] = internalizer.materialize(item["id"], thumb)
                rewritten += 1
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "  Could not internalize linked topic thumbnail for %s: %s",
                    item.get("id"),
                    exc,
                )
    return rewritten

def fetch_topic_summaries(links: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Fetch summaries and thumbnails for a list of wiki link targets.
    Returns dict keyed by lowercase target title."""
    summaries: dict[str, dict[str, Any]] = {}
    for link in links:
        target = link["target"].replace(" ", "_")
        for attempt in range(4):
            try:
                resp = requests.get(
                    f"{WIKI_SUMMARY_API}/{target}",
                    headers=HEADERS,
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
                    wait = int(resp.headers.get("Retry-After", 5 * (attempt + 1)))
                    logger.warning("  Wikipedia 429 for %s (attempt %d/4), retrying in %ds", target, attempt + 1, wait)
                    time.sleep(wait)
                else:
                    logger.debug("  No summary for %s (status %d)", target, resp.status_code)
                    break
            except Exception as e:
                logger.debug("  Failed to fetch summary for %s: %s", target, e)
                break
        # Polite crawl delay between topics
        time.sleep(0.2)
    return summaries


def build_items(sections: list[dict[str, Any]], topic_summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build Cosmos DB items from parsed sections with linked topic data."""
    items: list[dict[str, Any]] = []

    for idx, sec in enumerate(sections):
        item_id = f"tr-cyclopedia-{hashlib.md5(sec['heading'].encode()).hexdigest()[:8]}"

        # Build linked_topics for this section from pre-fetched summaries
        linked_topics = []
        for link in sec.get("links", []):
            key = link["target"].lower()
            if key in topic_summaries:
                linked_topics.append(topic_summaries[key])

        plain_text = sec["body"]
        description = plain_text[:300].rsplit(" ", 1)[0] if len(plain_text) > 300 else plain_text

        item: dict[str, Any] = {
            "id": item_id,
            "source": SOURCE,
            "item_type": "document",
            "title": sec["heading"],
            "description": description,
            # plain_text and linked_topics are stored in blob (content_url);
            # kept here temporarily so callers without blob storage still work.
            "plain_text": plain_text,
            "linked_topics": linked_topics,
            "order": idx,
            "source_url": f"https://en.wikipedia.org/wiki/{ARTICLE_TITLE}",
            "pattern": "prose",
        }
        # Use the first linked-topic thumbnail as the item's representative image
        # so it appears in list/card views and can be proxied/internalized.
        first_thumb = next(
            (t.get("thumbnail_url") for t in linked_topics if t.get("thumbnail_url")),
            None,
        )
        if first_thumb:
            item["image_url"] = first_thumb
        items.append(item)

    return items


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest Wikipedia TR Cyclopedia article into Cosmos DB")
    ap.add_argument("--dry-run", action="store_true", help="Print items instead of writing to Cosmos")
    ap.add_argument(
        "--internalize-assets",
        action="store_true",
        help="Copy linked topic thumbnails into Azure Blob and rewrite URLs",
    )
    ap.add_argument(
        "--storage-account",
        default=os.getenv("AZURE_STORAGE_ACCOUNT_NAME", ""),
        help="Azure Storage account name used for internalized assets",
    )
    ap.add_argument(
        "--storage-container",
        default=os.getenv("AZURE_STORAGE_CONTAINER_NAME", "digital-resources"),
        help="Blob container for internalized assets",
    )
    ap.add_argument(
        "--blob-prefix",
        default=os.getenv("DIGITAL_ITEMS_ASSET_BLOB_PREFIX", "digital-items"),
        help="Blob path prefix used for internalized assets",
    )
    args = ap.parse_args()

    _load_local_settings()

    logger.info("Fetching Wikipedia article: %s", ARTICLE_TITLE)
    sections = fetch_article_sections()
    logger.info("Parsed %d sections", len(sections))

    # Collect all unique links across all sections
    all_links: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for sec in sections:
        for link in sec.get("links", []):
            key = link["target"].lower()
            if key not in seen_targets:
                seen_targets.add(key)
                all_links.append(link)

    logger.info("Found %d unique wiki links, fetching summaries...", len(all_links))
    topic_summaries = fetch_topic_summaries(all_links)
    logger.info("Fetched %d topic summaries with thumbnails", sum(1 for s in topic_summaries.values() if s.get("thumbnail_url")))

    items = build_items(sections, topic_summaries)
    logger.info("Built %d items", len(items))

    if args.internalize_assets:
        if not args.storage_account:
            raise ValueError(
                "--internalize-assets requires --storage-account (or AZURE_STORAGE_ACCOUNT_NAME)"
            )
        logger.info(
            "Internalizing Cyclopedia linked-topic thumbnails to %s/%s (prefix=%s)",
            args.storage_account,
            args.storage_container,
            args.blob_prefix,
        )
        internalizer = AssetInternalizer(
            storage_account=args.storage_account,
            storage_container=args.storage_container,
            blob_prefix=args.blob_prefix,
        )
        rewritten = internalize_linked_topic_assets(items, internalizer)
        logger.info("Rewrote %d linked-topic thumbnail URL(s) to blob URLs", rewritten)

        # Upload full content (plain_text + linked_topics) to blob so Cosmos stores
        # only a reference (content_url) instead of the full text inline.
        logger.info("Uploading item content to blob storage (digital-resources/tr-cyclopedia/)")
        for item in items:
            try:
                payload = {
                    "plain_text": item.get("plain_text", ""),
                    "linked_topics": item.get("linked_topics", []),
                }
                content_url = internalizer.upload_content(item["id"], payload)
                item["content_url"] = content_url
                logger.info("  Uploaded content for %s", item["id"])
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("  Could not upload content for %s: %s", item.get("id"), exc)

        # Remove full plain_text and linked_topics from Cosmos doc — they live in blob now.
        for item in items:
            if item.get("content_url"):
                item.pop("plain_text", None)
                item.pop("linked_topics", None)

    if args.dry_run:
        for item in items:
            print(json.dumps(item, indent=2, ensure_ascii=False)[:500])
            print("---")
        return

    # Upsert to Cosmos
    db_name = os.getenv("COSMOS_DATABASE_NAME", "contentdb")
    container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
    container = get_container(container_name=container_name, database_name=db_name)

    from azure.cosmos.exceptions import CosmosHttpResponseError
    for item in items:
        for attempt in range(8):
            try:
                container.upsert_item(item)
                logger.info("  Upserted: %s — %s", item["id"], item["title"])
                break
            except CosmosHttpResponseError as exc:
                if exc.status_code in (429, 503):
                    header_wait = float((exc.headers or {}).get("x-ms-retry-after-ms", 0)) / 1000
                    backoff = min(2 ** attempt, 30)
                    wait = max(header_wait, backoff)
                    logger.warning(
                        "  %d on %s (attempt %d/8), retrying in %.1fs",
                        exc.status_code, item["id"], attempt + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    raise
        time.sleep(0.05)  # brief inter-upsert pause to stay within Cosmos RU budget

    logger.info("Done. Ingested %d items with source='%s'", len(items), SOURCE)


if __name__ == "__main__":
    main()
