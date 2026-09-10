"""
Ingest scraped digital resources into Cosmos DB ``digital-items`` container.

Reads pre-scraped data from the scrapers output and upserts structured documents
into the ``digital-items`` container under database ``contentdb``.

Sources:
  1. Moore Chronology     — from Temp/Moore Chronology/records/
  2. TRA Cyclopedia       — from scrapers/tra_cyclopedia/tra_cyclopedia_out/
  3. Genealogy & Papers   — synthesized from Cyclopedia genealogy/chronology modules

Usage (from the DataFoundations/apps/functions directory):

  python ingest_digital_resources.py --source all
  python ingest_digital_resources.py --source moore
  python ingest_digital_resources.py --source cyclopedia
  python ingest_digital_resources.py --source genealogy
  python ingest_digital_resources.py --source genealogy --scrape
  python ingest_digital_resources.py --dry-run --source all
  python ingest_digital_resources.py --source moore --oid o310991 --with-pages
  python ingest_digital_resources.py --source moore --oid o310991 --with-pages --end-page 5
    python ingest_digital_resources.py --backfill-existing --skip-ingest --backfill-source moore --internalize-assets --storage-account <account> --storage-container <container>

Requires:
  - COSMOS_ENDPOINT set (or az login for DefaultAzureCredential)
  - COSMOS_DATABASE_NAME=contentdb
  - python-dotenv (reads local.settings.json automatically)
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
from urllib.parse import unquote, urlparse

import requests

from azure.core.exceptions import ResourceExistsError
from azure.cosmos.exceptions import CosmosHttpResponseError
from helper.cosmos_client import get_container
from helper.storage_client import get_blob_service_client, parse_configured_blob_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
TRPL_ROOT = SCRIPT_DIR.parent.parent.parent  # DataFoundations -> TRPL
TEMP_DIR = os.getenv("DATAFOUNDATIONS_TEMP_DIR", "")
if TEMP_DIR:
    TEMP_DIR = Path(TEMP_DIR)
else:
    TEMP_DIR = TRPL_ROOT / "Temp"

MOORE_RECORDS = TEMP_DIR / "Moore Chronology" / "records"
TRA_CYCLOPEDIA_OUT = (
    SCRIPT_DIR / "scrapers" / "tra_cyclopedia" / "tra_cyclopedia_out"
)

if os.getenv("MOORE_RECORDS_PATH"):
    MOORE_RECORDS = Path(os.getenv("MOORE_RECORDS_PATH"))
if os.getenv("TRA_CYCLOPEDIA_OUT_PATH"):
    TRA_CYCLOPEDIA_OUT = Path(os.getenv("TRA_CYCLOPEDIA_OUT_PATH"))

# Scraper scripts location
_SCRAPERS_DIR = SCRIPT_DIR / "scrapers" / "tra_cyclopedia"


def _run_tra_scrape() -> None:
    """Run the TRA Cyclopedia web scraping pipeline (phases 1-3).

    Invokes the scraping scripts as subprocesses so their output lands in
    ``TRA_CYCLOPEDIA_OUT`` before we attempt to build items from it.
    """
    import subprocess

    python = sys.executable
    outdir = str(TRA_CYCLOPEDIA_OUT)

    phase2 = str(_SCRAPERS_DIR / "tra_cyclopedia_phase2_scrape.py")
    phase3 = str(_SCRAPERS_DIR / "tra_cyclopedia_phase3_index_letters.py")

    logger.info("Running TRA Cyclopedia scraper phase 2 (main scrape) ...")
    result = subprocess.run(
        [python, phase2, "--outdir", outdir],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_SCRAPERS_DIR),
    )
    if result.returncode != 0:
        logger.error("Phase 2 scraper failed:\n%s", result.stderr)
        raise RuntimeError(f"TRA scraper phase 2 failed (exit {result.returncode})")
    logger.info("Phase 2 complete.\n%s", result.stdout[-500:] if result.stdout else "")

    logger.info("Running TRA Cyclopedia scraper phase 3 (index letters) ...")
    result = subprocess.run(
        [python, phase3, "--outdir", outdir],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_SCRAPERS_DIR),
    )
    if result.returncode != 0:
        logger.error("Phase 3 scraper failed:\n%s", result.stderr)
        raise RuntimeError(f"TRA scraper phase 3 failed (exit {result.returncode})")
    logger.info("Phase 3 complete.\n%s", result.stdout[-500:] if result.stdout else "")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_external_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    try:
        parse_configured_blob_url(url)
        return False
    except (RuntimeError, ValueError):
        return True


def _filename_from_url(url: str, fallback: str) -> str:
    name = Path(unquote(urlparse(url).path)).name.strip()
    return name or fallback


def _guess_content_type(url: str, response_content_type: str | None) -> str | None:
    if response_content_type:
        return response_content_type.split(";")[0].strip()
    guessed, _ = mimetypes.guess_type(url)
    return guessed


class AssetInternalizer:
    """Copies externally hosted assets into Azure Blob storage and returns internal URLs."""

    def __init__(self, storage_account: str, storage_container: str, blob_prefix: str):
        self._container_client = get_blob_service_client(
            storage_account
        ).get_container_client(storage_container)
        self._prefix = blob_prefix.strip("/")
        self._cache: dict[str, str] = {}

    def _download(self, url: str) -> tuple[bytes, str | None]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if "theodorerooseveltcenter" in url or "amazonaws.com" in url:
            headers["Referer"] = "https://www.theodorerooseveltcenter.org/"
        elif "theodoreroosevelt.org" in url or "clubexpress.com" in url:
            headers["Referer"] = "https://www.theodoreroosevelt.org/"
        for attempt in range(5):
            resp = requests.get(url, headers=headers, timeout=120)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10 * (attempt + 1)))
                logger.warning("Asset download 429 for %s (attempt %d/5), retrying in %ds", url, attempt + 1, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.content, resp.headers.get("Content-Type")
        resp.raise_for_status()  # raise after exhausting retries
        return resp.content, resp.headers.get("Content-Type")

    def _upload(self, blob_name: str, content: bytes, content_type: str | None) -> str:
        blob_client = self._container_client.get_blob_client(blob_name)
        kwargs: dict[str, Any] = {"overwrite": False}
        if content_type:
            kwargs["content_type"] = content_type
        try:
            blob_client.upload_blob(content, **kwargs)
        except ResourceExistsError:
            pass
        return blob_client.url

    def _blob_name(self, item: dict[str, Any], field: str, source_url: str) -> str:
        digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
        filename = _filename_from_url(source_url, f"{field}.bin")
        return f"{self._prefix}/{item.get('source', 'unknown')}/{item.get('id', 'unknown')}/{field}/{digest}-{filename}"

    def materialize(self, item: dict[str, Any], field: str, source_url: str) -> str:
        """Download source_url, upload to blob storage, cache and return the internal blob URL."""
        cached = self._cache.get(source_url)
        if cached:
            return cached

        content, response_type = self._download(source_url)
        content_type = _guess_content_type(source_url, response_type)
        blob_url = self._upload(self._blob_name(item, field, source_url), content, content_type)
        self._cache[source_url] = blob_url
        return blob_url


def internalize_item_assets(item: dict[str, Any], internalizer: AssetInternalizer) -> bool:
    """Rewrite external asset URLs on an item to internal blob URLs."""
    changed = False

    for field in ("primary_pdf_url", "thumbnail_url", "pdf_url", "audio_url", "image_url"):
        value = item.get(field)
        if isinstance(value, str) and _is_external_url(value):
            item[field] = internalizer.materialize(item, field, value)
            changed = True

    files = item.get("files")
    if isinstance(files, list):
        for idx, file_info in enumerate(files):
            if not isinstance(file_info, dict):
                continue
            url = file_info.get("url")
            if isinstance(url, str) and _is_external_url(url):
                file_info["url"] = internalizer.materialize(item, f"file-{idx}", url)
                changed = True

    # Internalize per-topic thumbnails stored under linked_topics[].thumbnail_url
    # (e.g. Wikipedia CDN images embedded in cyclopedia section documents)
    linked_topics = item.get("linked_topics")
    if isinstance(linked_topics, list):
        for idx, topic in enumerate(linked_topics):
            if not isinstance(topic, dict):
                continue
            url = topic.get("thumbnail_url")
            if isinstance(url, str) and _is_external_url(url):
                try:
                    topic["thumbnail_url"] = internalizer.materialize(item, f"topic-{idx}-thumbnail", url)
                    changed = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "  Could not internalize topic-%d thumbnail for %s: %s",
                        idx, item.get("id"), exc,
                    )

    return changed


def backfill_existing_items(
    *,
    internalizer: AssetInternalizer,
    source_filter: str,
    container_name: str,
    database_name: str,
) -> tuple[int, int]:
    """Backfill existing digital-items docs by rewriting external URLs to internal blob URLs."""
    container = get_container(container_name, database_name)

    if source_filter == "all":
        query = "SELECT * FROM c"
        parameters: list[dict[str, Any]] = []
    else:
        source_map = {
            "moore": "moore-chronology",
            "cyclopedia": "tr-cyclopedia",
            "genealogy": "genealogy-papers",
        }
        query = "SELECT * FROM c WHERE c.source = @source"
        parameters = [{"name": "@source", "value": source_map[source_filter]}]

    processed = 0
    updated = 0
    for item in container.query_items(
        query=query,
        parameters=parameters,
        enable_cross_partition_query=True,
        max_item_count=200,
    ):
        processed += 1
        if internalize_item_assets(item, internalizer):
            for _attempt in range(8):
                try:
                    container.upsert_item(item)
                    break
                except CosmosHttpResponseError as _exc:
                    if _exc.status_code in (429, 503):
                        _hw = float((_exc.headers or {}).get("x-ms-retry-after-ms", 0)) / 1000
                        _wait = max(_hw, min(2 ** _attempt, 30))
                        logger.warning("%d on %s (attempt %d/8), retrying in %.1fs", _exc.status_code, item.get("id"), _attempt + 1, _wait)
                        time.sleep(_wait)
                    else:
                        raise
            updated += 1

    return processed, updated


# ─── Moore Chronology ──────────────────────────────────────────────


def _discover_moore_volumes() -> list[dict[str, Any]]:
    """Dynamically discover Moore Chronology volumes from scraped records directory."""
    volumes: list[dict[str, Any]] = []
    if not MOORE_RECORDS.exists():
        logger.warning("Moore records directory not found at %s", MOORE_RECORDS)
        return volumes

    for entry_dir in sorted(MOORE_RECORDS.iterdir()):
        if not entry_dir.is_dir():
            continue
        record_path = entry_dir / "record.json"
        if not record_path.exists():
            continue
        record = _load_json(record_path)
        oid = record.get("id", "")
        if not oid:
            # Extract from directory name (e.g. "o310991_chronology-...")
            oid = entry_dir.name.split("_", 1)[0]

        title = record.get("title", entry_dir.name)
        metadata = record.get("metadata", {})

        # Determine item_type from resource_type metadata
        resource_type = (metadata.get("resource_type") or "").lower()
        if resource_type in ("list",) or "chronology" in title.lower():
            item_type = "chronology"
        elif resource_type == "letter":
            item_type = "letter"
        else:
            item_type = "document"

        # Extract date_range from title first (e.g. "Chronology October 1858 to December 1870")
        # then fall back to period metadata, then creation_date
        date_range = ""
        title_years = re.findall(r"\d{4}", title)
        if len(title_years) >= 2:
            date_range = f"{title_years[0]}-{title_years[-1]}"
        elif title_years:
            date_range = title_years[0]

        if not date_range:
            period = metadata.get("period", "")
            period_years = re.findall(r"\d{4}", period)
            if len(period_years) >= 2:
                date_range = f"{period_years[0]}-{period_years[-1]}"
            elif period_years:
                date_range = period_years[0]

        if not date_range:
            date_range = metadata.get("creation_date", "")

        volumes.append({
            "id": oid,
            "title": title,
            "date_range": date_range,
            "item_type": item_type,
        })

    return volumes


def _thumbnail_url_from_record(record_json: dict[str, Any]) -> str | None:
    for file_info in record_json.get("files", []):
        filename = (file_info.get("filename") or "").lower()
        if filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return file_info.get("url")
    return None


def build_moore_items(oid_filter: str | None = None) -> list[dict[str, Any]]:
    """Build digital-item documents from scraped Moore Chronology records."""
    items: list[dict[str, Any]] = []

    volumes = _discover_moore_volumes()
    if oid_filter:
        volumes = [vol for vol in volumes if vol["id"] == oid_filter]
        if not volumes:
            raise ValueError(f"Unknown Moore chronology oid: {oid_filter}")

    for vol in volumes:
        oid = vol["id"]
        slug_dirs = list(MOORE_RECORDS.glob(f"{oid}_*"))
        record_json: dict[str, Any] = {}
        if slug_dirs:
            rj = slug_dirs[0] / "record.json"
            if rj.exists():
                record_json = _load_json(rj)

        source_url = record_json.get(
            "source_url",
            f"https://www.theodorerooseveltcenter.org/digital-library/{oid}/",
        )

        item: dict[str, Any] = {
            "id": f"moore-{oid}",
            "source": "moore-chronology",
            "item_type": vol["item_type"],
            "title": record_json.get("title", vol["title"]),
            "date_range": vol["date_range"],
            "source_url": source_url,
            "source_id": oid,
            "metadata": record_json.get("metadata", {}),
            "files": [
                {
                    "filename": f.get("filename"),
                    "url": f.get("url"),
                    "bytes": f.get("bytes"),
                }
                for f in record_json.get("files", [])
            ],
        }

        if record_json.get("blob_url"):
            item["primary_pdf_url"] = record_json["blob_url"]

        thumbnail_url = _thumbnail_url_from_record(record_json)
        if thumbnail_url:
            item["thumbnail_url"] = thumbnail_url

        page_count_raw = item.get("metadata", {}).get("page_count")
        if page_count_raw:
            try:
                item["page_count"] = int(str(page_count_raw))
            except ValueError:
                pass

        items.append(item)

    return items


def build_moore_page_items_for_volume(
    oid: str,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[dict[str, Any]]:
    """Build page-level documents (OCR text per PDF page) for one Moore volume."""
    from helper.moore_page_ingest import build_moore_page_items

    volume_items = build_moore_items(oid_filter=oid)
    if not volume_items:
        raise ValueError(f"No Moore volume found for oid {oid}")

    volume = volume_items[0]
    pdf_url = volume.get("primary_pdf_url")
    if not pdf_url:
        raise ValueError(f"No PDF URL found for Moore volume {oid}")

    local_pdf: Path | None = None
    slug_dirs = list(MOORE_RECORDS.glob(f"{oid}_*"))
    if slug_dirs:
        files_dir = slug_dirs[0] / "files"
        if files_dir.exists():
            pdfs = sorted(files_dir.glob("*.pdf"))
            if pdfs:
                local_pdf = pdfs[0]

    page_items = build_moore_page_items(
        oid=oid,
        parent_id=volume["id"],
        pdf_url=pdf_url,
        local_pdf_path=local_pdf,
        start_page=start_page,
        end_page=end_page,
    )

    if page_items and not volume.get("page_count"):
        volume["page_count"] = page_items[0].get("page_count")

    return page_items


# ─── TRA Cyclopedia ────────────────────────────────────────────────

PATTERN_TYPE_MAP = {
    "prose": "document",
    "papers_toc": "papers",
    "index_hub": "index",
    "index_letter_page": "index-letter",
    "speeches_sections": "speeches",
}

# Audio download IDs for speeches (module_id -> docs.ashx?id=)
# Legacy fallback — now extracted dynamically from scraped HTML
_SPEECH_AUDIO_MAP_FALLBACK = {
    "338367": "480207",  # Social and Industrial Justice
    "338373": "480213",  # The Farmer and the Businessman
    "338381": "480234",  # The Right of the People to Rule
    "338399": "480240",  # Greeting to the American Boy
}

# Legacy fallback — now extracted dynamically from scraped HTML img tags
_MODULE_IMAGE_MAP_FALLBACK = {
    "339183": "https://images.clubexpress.com/991271/graphics/Theodore-Roosevelt-children-in-the-late-1890s_2091827655.jpg",
    "339184": "https://images.clubexpress.com/991271/graphics/TR-Geneology-2022_2048612092.png",
    "339175": "https://images.clubexpress.com/991271/graphics/TR-Cyclopedia-1_447808291.jpg",
}

_MODULE_IMAGE_ALT_FALLBACK = {
    "339183": "Theodore Roosevelt's children in the late 1890s",
    "339184": "Ancestors of Theodore Roosevelt — family tree",
    "339175": "Theodore Roosevelt Cyclopedia cover",
}


def _resolve_module_json(mid: str, parent_mid: str | None) -> Path | None:
    """Resolve the JSON file path for a module, handling nested directories."""
    # Direct path
    direct = TRA_CYCLOPEDIA_OUT / f"module_{mid}" / f"module_{mid}.json"
    if direct.exists():
        return direct

    # Nested under parent (e.g., speeches under 339335, index letters under 339476)
    if parent_mid:
        nested = TRA_CYCLOPEDIA_OUT / f"module_{parent_mid}" / f"module_{mid}" / f"module_{mid}.json"
        if nested.exists():
            return nested

    # Search for it recursively as fallback
    for candidate in TRA_CYCLOPEDIA_OUT.rglob(f"module_{mid}.json"):
        return candidate

    return None


def build_cyclopedia_items() -> list[dict[str, Any]]:
    """Build digital-item documents from scraped TRA Cyclopedia output."""
    items: list[dict[str, Any]] = []
    manifest_path = TRA_CYCLOPEDIA_OUT / "manifest.json"

    if not manifest_path.exists():
        logger.warning("TRA Cyclopedia manifest not found at %s", manifest_path)
        return items

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for idx, entry in enumerate(manifest):
        mid = entry.get("module_id", "")
        pattern = entry.get("pattern", "prose")
        parent_mid = entry.get("parent_module_id")

        # Resolve module JSON using directory structure
        json_file = _resolve_module_json(mid, parent_mid)
        page_data: dict[str, Any] = {}
        if json_file and json_file.exists():
            page_data = _load_json(json_file)

        structured = page_data.get("structured", {})
        item_type = PATTERN_TYPE_MAP.get(pattern, "document")

        item: dict[str, Any] = {
            "id": f"tr-cyclopedia-{mid}",
            "source": "tr-cyclopedia",
            "item_type": item_type,
            "module_id": mid,
            "title": page_data.get("h1") or entry.get("h1") or entry.get("hub_label", ""),
            "hub_label": entry.get("hub_label", ""),
            "pattern": pattern,
            "source_url": page_data.get("url", ""),
            "order": idx,
        }

        # Plain text content for rendering
        plain_text = page_data.get("plain_text", "")
        if plain_text:
            item["plain_text"] = plain_text
            # Short description for card previews
            item["description"] = plain_text[:200].rsplit(" ", 1)[0] if len(plain_text) > 200 else plain_text

        # HTML fragment for rich rendering
        html_fragment = page_data.get("html_fragment", "")
        if html_fragment:
            item["html_content"] = html_fragment

        if parent_mid:
            item["parent_module_id"] = parent_mid
            item["parent_id"] = f"tr-cyclopedia-{parent_mid}"

        # Audio URL — prefer scraped data, fallback to legacy map
        audio_links = structured.get("audio_links", [])
        if audio_links:
            item["audio_url"] = audio_links[0]["url"]
            item["has_audio"] = True
        elif mid in _SPEECH_AUDIO_MAP_FALLBACK:
            audio_id = _SPEECH_AUDIO_MAP_FALLBACK[mid]
            item["audio_url"] = f"https://www.theodoreroosevelt.org/docs.ashx?id={audio_id}"
            item["has_audio"] = True

        # Image URL — prefer scraped data, fallback to legacy map
        scraped_images = structured.get("images", [])
        if scraped_images:
            item["image_url"] = scraped_images[0]["src"]
            item["image_alt"] = scraped_images[0].get("alt", "")
        elif mid in _MODULE_IMAGE_MAP_FALLBACK:
            item["image_url"] = _MODULE_IMAGE_MAP_FALLBACK[mid]
            item["image_alt"] = _MODULE_IMAGE_ALT_FALLBACK.get(mid, "")

        summary = structured.get("extraction_summary", {})
        if summary:
            item["extraction_summary"] = summary

        if pattern == "index_letter_page":
            entries = structured.get("cyclopedia_entries", [])
            item["entry_count"] = len(entries)
            item["cyclopedia_entries"] = entries

        elif pattern == "speeches_sections":
            sections = structured.get("speech_sections", [])
            item["section_count"] = len(sections)
            item["speech_sections"] = [
                {
                    "title": s.get("title", ""),
                    "body": s.get("body", ""),
                    "links": s.get("links", []),
                }
                for s in sections
            ]

        elif pattern == "papers_toc":
            toc = structured.get("toc_items", []) or structured.get("toc_entries", [])
            item["toc_entry_count"] = len(toc)
            item["toc_entries"] = toc

        elif pattern == "prose":
            sections = structured.get("document_sections", [])
            item["section_count"] = len(sections)
            if sections:
                item["document_sections"] = [
                    {
                        "heading": s.get("heading"),
                        "level": s.get("level"),
                        "body": s.get("body", ""),
                    }
                    for s in sections
                ]

        items.append(item)

    return items


# ─── Genealogy & Papers ───────────────────────────────────────────

def build_genealogy_items() -> list[dict[str, Any]]:
    """Build digital-item documents for the Genealogy & Papers collection.

    These are sourced from two TRA Cyclopedia modules (Genealogy 339184 and
    Chronology 339177) plus a synthesized collection record for the Dailey work
    (physical materials pending digitization).
    """
    items: list[dict[str, Any]] = []

    genealogy_module = TRA_CYCLOPEDIA_OUT / "module_339184" / "module_339184.json"
    if genealogy_module.exists():
        data = _load_json(genealogy_module)
        structured = data.get("structured", {})
        sections = structured.get("document_sections", [])
        plain_text = data.get("plain_text", "")
        # Derive description from scraped content
        description = plain_text[:200].rsplit(" ", 1)[0] if len(plain_text) > 200 else plain_text

        item: dict[str, Any] = {
            "id": "genealogy-tra-genealogy",
            "source": "genealogy-papers",
            "item_type": "document",
            "title": data.get("h1") or data.get("hub_label") or "The Genealogy of Theodore Roosevelt",
            "description": description,
            "source_url": data.get("url", ""),
            "module_id": "339184",
            "section_count": len(sections),
            "document_sections": [
                {
                    "heading": s.get("heading"),
                    "level": s.get("level"),
                    "body": s.get("body", ""),
                }
                for s in sections
            ],
        }
        # Extract image from scraped data
        scraped_images = structured.get("images", [])
        if scraped_images:
            item["image_url"] = scraped_images[0]["src"]
            item["image_alt"] = scraped_images[0].get("alt", "")
        items.append(item)

    chronology_module = TRA_CYCLOPEDIA_OUT / "module_339177" / "module_339177.json"
    if chronology_module.exists():
        data = _load_json(chronology_module)
        structured = data.get("structured", {})
        sections = structured.get("document_sections", [])
        plain_text = data.get("plain_text", "")
        description = plain_text[:200].rsplit(" ", 1)[0] if len(plain_text) > 200 else plain_text

        item = {
            "id": "genealogy-tra-chronology",
            "source": "genealogy-papers",
            "item_type": "document",
            "title": data.get("h1") or data.get("hub_label") or "Chronology of the Life of Theodore Roosevelt",
            "description": description,
            "source_url": data.get("url", ""),
            "module_id": "339177",
            "section_count": len(sections),
            "document_sections": [
                {
                    "heading": s.get("heading"),
                    "level": s.get("level"),
                    "body": s.get("body", ""),
                }
                for s in sections
            ],
        }
        # Extract image from scraped data
        scraped_images = structured.get("images", [])
        if scraped_images:
            item["image_url"] = scraped_images[0]["src"]
            item["image_alt"] = scraped_images[0].get("alt", "")
        items.append(item)

    # The family module (339183) is also relevant to genealogy
    family_module = TRA_CYCLOPEDIA_OUT / "module_339183" / "module_339183.json"
    if family_module.exists():
        data = _load_json(family_module)
        structured = data.get("structured", {})
        sections = structured.get("document_sections", [])
        plain_text = data.get("plain_text", "")
        description = plain_text[:200].rsplit(" ", 1)[0] if len(plain_text) > 200 else plain_text

        item = {
            "id": "genealogy-tra-family",
            "source": "genealogy-papers",
            "item_type": "document",
            "title": data.get("h1") or data.get("hub_label") or "The Family of Theodore Roosevelt",
            "description": description,
            "source_url": data.get("url", ""),
            "module_id": "339183",
            "section_count": len(sections),
            "document_sections": [
                {
                    "heading": s.get("heading"),
                    "level": s.get("level"),
                    "body": s.get("body", ""),
                }
                for s in sections
            ],
        }
        # Extract image from scraped data
        scraped_images = structured.get("images", [])
        if scraped_images:
            item["image_url"] = scraped_images[0]["src"]
            item["image_alt"] = scraped_images[0].get("alt", "")
        items.append(item)

    return items


# ─── Main ──────────────────────────────────────────────────────────

def _load_local_settings() -> None:
    """Load values from local.settings.json if present (for local dev)."""
    settings_path = SCRIPT_DIR / "local.settings.json"
    if not settings_path.exists():
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        values = data.get("Values", {})
        for k, v in values.items():
            if v and k not in os.environ:
                os.environ[k] = str(v)
        logger.info("Loaded %d settings from local.settings.json", len(values))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load local.settings.json: %s", e)


def main() -> None:
    """CLI entry point: build and upsert digital-resource items into the Cosmos digital-items container."""
    ap = argparse.ArgumentParser(
        description="Ingest digital resources into Cosmos DB digital-items container"
    )
    ap.add_argument(
        "--source",
        choices=["all", "moore", "cyclopedia", "genealogy"],
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
        help="Moore chronology source id (e.g. o310991) — ingest only that volume",
    )
    ap.add_argument(
        "--with-pages",
        action="store_true",
        help="Also ingest per-page OCR documents for Moore volumes (requires --oid or --source moore)",
    )
    ap.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="First PDF page to ingest when --with-pages is set (default: 1)",
    )
    ap.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Last PDF page to ingest when --with-pages is set (default: all pages)",
    )
    ap.add_argument(
        "--internalize-assets",
        action="store_true",
        help="Copy external asset URLs to Azure Blob storage and rewrite item URLs to internal blob URLs",
    )
    ap.add_argument(
        "--storage-account",
        default=os.getenv("AZURE_STORAGE_ACCOUNT_NAME"),
        help="Azure Storage account for internalized assets (defaults to AZURE_STORAGE_ACCOUNT_NAME)",
    )
    ap.add_argument(
        "--storage-container",
        default=os.getenv("DIGITAL_ITEMS_STORAGE_CONTAINER")
        or os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        or "digital-resources",
        help="Blob container for internalized assets (default: DIGITAL_ITEMS_STORAGE_CONTAINER or AZURE_STORAGE_CONTAINER_NAME or digital-resources)",
    )
    ap.add_argument(
        "--asset-blob-prefix",
        default=os.getenv("DIGITAL_ITEMS_ASSET_BLOB_PREFIX", "digital-items"),
        help="Blob path prefix under the container (default: digital-items)",
    )
    ap.add_argument(
        "--backfill-existing",
        action="store_true",
        help="Backfill existing Cosmos digital-items docs by rewriting external asset URLs to internal blob URLs",
    )
    ap.add_argument(
        "--backfill-source",
        choices=["all", "moore", "cyclopedia", "genealogy"],
        default="all",
        help="Source filter for --backfill-existing (default: all)",
    )
    ap.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip normal ingest build/upsert and run only other selected operations",
    )
    ap.add_argument(
        "--scrape",
        action="store_true",
        help="Run TRA web scraping (phases 2-3) before building cyclopedia/genealogy items",
    )
    args = ap.parse_args()

    _load_local_settings()

    internalizer: AssetInternalizer | None = None
    if args.internalize_assets:
        if not args.storage_account:
            raise SystemExit("--internalize-assets requires --storage-account (or AZURE_STORAGE_ACCOUNT_NAME)")
        internalizer = AssetInternalizer(
            storage_account=args.storage_account,
            storage_container=args.storage_container,
            blob_prefix=args.asset_blob_prefix,
        )

    if args.backfill_existing:
        if not internalizer:
            raise SystemExit("--backfill-existing requires --internalize-assets")
        db_name = os.getenv("COSMOS_DATABASE_NAME", "contentdb")
        container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
        processed, updated = backfill_existing_items(
            internalizer=internalizer,
            source_filter=args.backfill_source,
            container_name=container_name,
            database_name=db_name,
        )
        logger.info(
            "Backfill completed for source=%s: processed=%d, updated=%d",
            args.backfill_source,
            processed,
            updated,
        )

    if args.skip_ingest:
        logger.info("Skipping ingest (--skip-ingest set)")
        return

    builders = {
        "moore": ("Moore Chronology", build_moore_items),
        "cyclopedia": ("TRA Cyclopedia", build_cyclopedia_items),
        "genealogy": ("Genealogy & Papers", build_genealogy_items),
    }

    sources = list(builders.keys()) if args.source == "all" else [args.source]
    if args.oid and "moore" not in sources:
        if args.source != "all":
            raise SystemExit("--oid is only valid with --source moore")
        sources = ["moore"]

    # Run web scraping if requested and building cyclopedia or genealogy
    if args.scrape and any(s in sources for s in ("cyclopedia", "genealogy")):
        _run_tra_scrape()

    total = 0
    for src in sources:
        label, builder = builders[src]
        logger.info("Building items for %s ...", label)
        if src == "moore":
            items = builder(args.oid) if args.oid else builder()
        else:
            if args.oid:
                raise SystemExit("--oid is only valid with --source moore")
            items = builder()
        logger.info("  Built %d items", len(items))

        if args.with_pages:
            if src != "moore":
                raise SystemExit("--with-pages is only valid with --source moore")
            oids = [args.oid] if args.oid else [vol["id"] for vol in _discover_moore_volumes()]
            for oid in oids:
                logger.info("Building page items for Moore volume %s ...", oid)
                page_items = build_moore_page_items_for_volume(
                    oid,
                    start_page=args.start_page,
                    end_page=args.end_page,
                )
                logger.info("  Built %d page items for %s", len(page_items), oid)
                items.extend(page_items)

        if internalizer:
            changed = 0
            for item in items:
                if internalize_item_assets(item, internalizer):
                    changed += 1
            logger.info("  Internalized assets for %d/%d built items", changed, len(items))

        if args.dry_run:
            for item in items:
                print(json.dumps(item, indent=2, ensure_ascii=False))
            total += len(items)
            continue

        from helper.digital_items_client import upsert_digital_items_batch

        logger.info("  Starting upsert of %d items for %s ...", len(items), label)
        count = upsert_digital_items_batch(items)
        if count < len(items):
            logger.warning(
                "  Upserted %d / %d items for %s (PARTIAL SUCCESS - %d items failed)",
                count, len(items), label, len(items) - count
            )
        else:
            logger.info("  Upserted %d / %d items for %s (SUCCESS)", count, len(items), label)
        total += count

    action = "printed" if args.dry_run else "ingested"
    logger.info("Done. Total %s: %d items", action, total)


if __name__ == "__main__":
    main()
