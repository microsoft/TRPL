"""
Ingest the Wikipedia article on the Theodore Roosevelt Cyclopedia into Cosmos DB.

Fetches the article via Wikipedia's API, splits it into sections, and upserts
structured documents into the ``digital-items`` container with source="tr-cyclopedia".

Usage (from DataFoundations/apps/functions):
  python ingest_wikipedia_cyclopedia.py
  python ingest_wikipedia_cyclopedia.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

from helper.cosmos_client import get_container
from helper.credential import get_credential

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ARTICLE_TITLE = "Theodore_Roosevelt_Cyclopedia"
SOURCE = "tr-cyclopedia"


def _load_local_settings():
    """Load local.settings.json Values into environment if present."""
    settings_path = Path(__file__).resolve().parent / "local.settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        for k, v in data.get("Values", {}).items():
            os.environ.setdefault(k, v)


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
    resp = requests.get(WIKIPEDIA_API, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()["parse"]

    wikitext = data.get("wikitext", {}).get("*", "")

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


def fetch_topic_summaries(links: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Fetch summaries and thumbnails for a list of wiki link targets.
    Returns dict keyed by lowercase target title."""
    summaries: dict[str, dict[str, Any]] = {}
    for link in links:
        target = link["target"].replace(" ", "_")
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
            else:
                logger.debug("  No summary for %s (status %d)", target, resp.status_code)
        except Exception as e:
            logger.debug("  Failed to fetch summary for %s: %s", target, e)
        # Rate-limit: Wikipedia asks for polite crawling
        time.sleep(0.1)
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

        item: dict[str, Any] = {
            "id": item_id,
            "source": SOURCE,
            "item_type": "document",
            "title": sec["heading"],
            "plain_text": sec["body"],
            "order": idx,
            "source_url": f"https://en.wikipedia.org/wiki/{ARTICLE_TITLE}",
            "pattern": "prose",
            "linked_topics": linked_topics,
        }
        items.append(item)

    return items


def main() -> None:
    """CLI entry point: fetch the Wikipedia TR Cyclopedia article and upsert sections into Cosmos DB."""
    ap = argparse.ArgumentParser(description="Ingest Wikipedia TR Cyclopedia article into Cosmos DB")
    ap.add_argument("--dry-run", action="store_true", help="Print items instead of writing to Cosmos")
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

    if args.dry_run:
        for item in items:
            print(json.dumps(item, indent=2, ensure_ascii=False)[:500])
            print("---")
        return

    # Upsert to Cosmos
    db_name = os.getenv("COSMOS_DATABASE_NAME", "contentdb")
    container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
    container = get_container(database_name=db_name, container_name=container_name)

    from azure.cosmos.exceptions import CosmosHttpResponseError

    for item in items:
        for _attempt in range(6):
            try:
                container.upsert_item(item)
                logger.info("  Upserted: %s — %s", item["id"], item["title"])
                break
            except CosmosHttpResponseError as _exc:
                if _exc.status_code in (429, 503) and _attempt < 5:
                    _hw = float((_exc.headers or {}).get("x-ms-retry-after-ms", 0)) / 1000
                    _wait = max(_hw, min(2 ** _attempt, 30))
                    logger.warning("%d on %s (attempt %d/6), retrying in %.1fs", _exc.status_code, item["id"], _attempt + 1, _wait)
                    time.sleep(_wait)
                else:
                    raise

    logger.info("Done. Ingested %d items with source='%s'", len(items), SOURCE)


if __name__ == "__main__":
    main()
