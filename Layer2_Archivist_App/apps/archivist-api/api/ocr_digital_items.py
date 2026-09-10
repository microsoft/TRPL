"""
Run GPT-4 Vision OCR on digital items that have PDF/image assets.

Queries the ``digital-items`` Cosmos container for items with PDF or image
URLs, converts them to JPEG, calls Azure AI Foundry GPT-4 Vision for OCR +
entity extraction, and stores results back in Cosmos with ``ocr_text_original``
and ``ocr_accuracy`` fields.

Usage (from ArchivistApp/apps/archivist-api/api):

  python ocr_digital_items.py --source all
  python ocr_digital_items.py --source moore
  python ocr_digital_items.py --source cyclopedia
  python ocr_digital_items.py --source genealogy
  python ocr_digital_items.py --dry-run --source all

Requires:
  - AZURE_AI_FOUNDRY_ENDPOINT set (or uses default from config)
  - COSMOS_ENDPOINT set (or az login for DefaultAzureCredential)
  - COSMOS_DATABASE_NAME=contentdb
  - COSMOS_DIGITAL_ITEMS_CONTAINER_NAME=digital-items
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests as http_requests

from azure.core.exceptions import AzureError
from azure.storage.blob import BlobServiceClient

from helper.config import get_azure_config, AzureConfig, get_storage_config  # pylint: disable=import-error
from helper.cosmos_client import get_container  # pylint: disable=import-error
from helper.credential import get_credential, get_cognitive_services_token_provider  # pylint: disable=import-error

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def upload_text_to_blob(
    container_name: str,
    blob: str,
    content: str,
    account_url: Optional[str] = None,
) -> bool:
    """Upload text content to blob storage using managed identity."""
    try:
        resolved_url = account_url
        if not resolved_url:
            account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
            if not account_name:
                logger.error("AZURE_STORAGE_ACCOUNT_NAME is not set")
                return False
            resolved_url = f"https://{account_name}.blob.core.windows.net"

        blob_service = BlobServiceClient(account_url=resolved_url, credential=get_credential())
        container_client = blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob)
        blob_client.upload_blob(content, overwrite=True)
        return True
    except AzureError as exc:
        logger.error("Blob upload failed for %s: %s", blob, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected blob upload failure for %s: %s", blob, exc)
        return False

# ─── Prompt ───────────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "helper" / "prompts"
_OCR_PROMPT_FILE = _PROMPTS_DIR / "04_ocr_plus_entity_extraction.md"


def _load_ocr_prompt() -> str:
    """Load the OCR + entity extraction prompt."""
    if not _OCR_PROMPT_FILE.exists():
        raise FileNotFoundError(f"OCR prompt not found: {_OCR_PROMPT_FILE}")
    return _OCR_PROMPT_FILE.read_text(encoding="utf-8")


# ─── Azure OpenAI Client ─────────────────────────────────────────────────────

_openai_client = None


def _get_openai_client(config: AzureConfig):
    """Lazily create the Azure OpenAI client with managed identity."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    from openai import AzureOpenAI

    token_provider = get_cognitive_services_token_provider()

    _openai_client = AzureOpenAI(
        azure_endpoint=config.endpoint,
        api_version=config.api_version,
        azure_ad_token_provider=token_provider,
    )
    logger.info("Azure OpenAI client initialized (endpoint: %s)", config.endpoint)
    return _openai_client


# ─── Image Conversion ─────────────────────────────────────────────────────────

def _pdf_bytes_to_data_urls(pdf_bytes: bytes, max_pages: int = 0) -> List[str]:
    """Convert PDF bytes to a list of JPEG data URLs (one per page)."""
    import pymupdf

    if len(pdf_bytes) < 100:
        raise ValueError(f"PDF too small ({len(pdf_bytes)} bytes), likely corrupt")

    data_urls = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        total_pages = doc.page_count
        pages_to_process = total_pages if max_pages == 0 else min(total_pages, max_pages)

        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            mat = pymupdf.Matrix(150 / 72, 150 / 72)  # 150 DPI
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            data_urls.append(f"data:image/jpeg;base64,{b64}")
    finally:
        doc.close()

    return data_urls


def _download_pdf(url: str) -> bytes:
    """Download a PDF from a URL (external or blob)."""
    credential = get_credential()

    if ".blob.core.windows.net" in url:
        from azure.storage.blob import BlobClient
        blob_client = BlobClient.from_blob_url(url, credential=credential)
        downloader = blob_client.download_blob()
        return downloader.readall()
    else:
        resp = http_requests.get(url, timeout=120, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.theodorerooseveltcenter.org/",
        })
        resp.raise_for_status()
        return resp.content


def _image_url_to_data_url(url: str) -> str:
    """Download an image and convert to a JPEG data URL."""
    from PIL import Image

    credential = get_credential()

    if ".blob.core.windows.net" in url:
        from azure.storage.blob import BlobClient
        blob_client = BlobClient.from_blob_url(url, credential=credential)
        downloader = blob_client.download_blob()
        content = downloader.readall()
    else:
        resp = http_requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        content = resp.content

    img = Image.open(io.BytesIO(content))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


# ─── GPT-4 Vision Call ────────────────────────────────────────────────────────

def _call_gpt4_vision(client, config: AzureConfig, data_url: str, prompt: str) -> Dict[str, Any]:
    """Call GPT-4 Vision with a single image and return parsed JSON response."""
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": "high"},
                },
                {
                    "type": "text",
                    "text": "Please perform OCR and entity extraction on this document image.",
                },
            ],
        },
    ]

    response = client.chat.completions.create(
        model=config.model_name,
        messages=messages,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout=config.timeout,
    )

    content = response.choices[0].message.content or ""

    # Parse JSON from response (may be wrapped in ```json ... ```)
    json_str = content.strip()
    if json_str.startswith("```"):
        lines = json_str.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        json_str = "\n".join(lines)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {
            "ocr_text": content,
            "confidence_scores": {"overall_ocr_confidence": 0.0},
            "parse_error": True,
        }


# ─── Per-Item OCR Processing ─────────────────────────────────────────────────

def process_item(
    item: Dict[str, Any],
    container,
    client,
    config: AzureConfig,
    prompt: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run OCR on a single digital item and update Cosmos DB."""
    item_id = item["id"]
    source = item["source"]

    pdf_url = item.get("primary_pdf_url")
    image_urls = item.get("image_url") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    # Postcards and image-only items store URLs in files[].url, not image_url
    if not image_urls and not pdf_url:
        files = item.get("files", []) or []
        image_urls = [
            f["url"] for f in files
            if isinstance(f, dict) and "url" in f
            and not f["url"].lower().endswith(".pdf")
        ]

    # Max concurrent page/image workers (overridable via env)
    _page_workers = int(os.getenv("OCR_PAGE_WORKERS", "4"))

    results: List[Dict[str, Any]] = []

    def _ocr_page(args: Tuple) -> Dict[str, Any]:
        page_num, total, data_url = args
        logger.info("  OCR page %d/%d for %s ...", page_num, total, item_id)
        try:
            result = _call_gpt4_vision(client, config, data_url, prompt)
            return {
                "page_number": page_num,
                "ocr_text": result.get("ocr_text", ""),
                "confidence": result.get("confidence_scores", {}).get("overall_ocr_confidence", 0.0),
                "entities": result.get("entities", []),
                "document_metadata": result.get("document_metadata", {}),
            }
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("  OCR failed page %d for %s: %s", page_num, item_id, e)
            return {"page_number": page_num, "ocr_text": "", "confidence": 0.0, "error": str(e)}

    def _ocr_image(args: Tuple) -> Dict[str, Any]:
        idx, total, img_url = args
        logger.info("  OCR image %d/%d for %s ...", idx, total, item_id)
        try:
            data_url = _image_url_to_data_url(img_url)
            result = _call_gpt4_vision(client, config, data_url, prompt)
            return {
                "page_number": idx,
                "ocr_text": result.get("ocr_text", ""),
                "confidence": result.get("confidence_scores", {}).get("overall_ocr_confidence", 0.0),
                "entities": result.get("entities", []),
                "document_metadata": result.get("document_metadata", {}),
            }
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("  OCR failed image %d for %s: %s", idx, item_id, e)
            return {"page_number": idx, "ocr_text": "", "confidence": 0.0, "error": str(e)}

    if pdf_url:
        try:
            logger.info("  Downloading PDF for %s ...", item_id)
            pdf_bytes = _download_pdf(pdf_url)
            data_urls = _pdf_bytes_to_data_urls(pdf_bytes)
            logger.info("  PDF has %d pages", len(data_urls))

            if dry_run:
                return {"item_id": item_id, "status": "dry_run", "pages": len(data_urls)}

            page_args = [(i + 1, len(data_urls), du) for i, du in enumerate(data_urls)]
            with ThreadPoolExecutor(max_workers=_page_workers) as pool:
                results = list(pool.map(_ocr_page, page_args))
            results.sort(key=lambda r: r["page_number"])
        except Exception as e:
            logger.error("  Failed to process PDF for %s: %s", item_id, e)
            return {"item_id": item_id, "status": "failed", "error": str(e)}

    elif image_urls:
        if dry_run:
            return {"item_id": item_id, "status": "dry_run", "images": len(image_urls)}

        img_args = [(i + 1, len(image_urls), url) for i, url in enumerate(image_urls)]
        with ThreadPoolExecutor(max_workers=_page_workers) as pool:
            results = list(pool.map(_ocr_image, img_args))
        results.sort(key=lambda r: r["page_number"])
    else:
        return {"item_id": item_id, "status": "skipped", "reason": "no_assets"}

    # Compute overall OCR accuracy from model-reported confidence scores.
    # GPT-4 Vision does not natively emit confidence values — the field is
    # model-generated and frequently omitted or returned as 0.  When no
    # usable score is found, fall back to a text-yield rate: the proportion
    # of pages that contain at least some readable text.
    confidences = [
        r["confidence"] for r in results
        if isinstance(r.get("confidence"), (int, float)) and r["confidence"] > 0
    ]
    if confidences:
        ocr_accuracy = round(sum(confidences) / len(confidences), 3)
    elif results:
        pages_with_text = sum(
            1 for r in results
            if r.get("ocr_text", "").strip() and r.get("ocr_text") != "NO_READABLE_TEXT"
        )
        ocr_accuracy = round(pages_with_text / len(results), 3)
    else:
        ocr_accuracy = 0.0

    # Combine all page OCR text
    combined_text = "\n\n--- Page Break ---\n\n".join(
        r["ocr_text"] for r in results if r.get("ocr_text")
    )

    # Upload OCR text to blob storage (matching repos/collections pattern)
    try:
        storage_config = get_storage_config()
        blob_container = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        if not blob_container:
            raise ValueError("AZURE_STORAGE_CONTAINER_NAME environment variable not set")

        storage_account_name = storage_config.storage_account_name
        if not storage_account_name:
            raise ValueError("AZURE_STORAGE_ACCOUNT_NAME environment variable not set")

        # Define blob paths (same pattern as repos/collections)
        blob_original = f"{item_id}/ocr/original/{item_id}.txt"
        blob_flexible = f"{item_id}/ocr/flexible/{item_id}/v1.txt"

        # Upload OCR text to both paths (initial version: both are identical)
        upload_text_to_blob(
            container_name=blob_container,
            blob=blob_original,
            content=combined_text,
        )
        upload_text_to_blob(
            container_name=blob_container,
            blob=blob_flexible,
            content=combined_text,
        )

        # Generate blob URLs
        base_url = f"https://{storage_account_name}.blob.core.windows.net"
        ocr_original_url = f"{base_url}/{blob_container}/{blob_original}"
        ocr_flexible_url = f"{base_url}/{blob_container}/{blob_flexible}"

        logger.info("  Uploaded OCR text to blob storage: %s", blob_original)
    except Exception as e:
        logger.error("  Failed to upload OCR to blob for %s: %s", item_id, e)
        return {"item_id": item_id, "status": "failed", "error": f"Blob upload: {e}"}

    # Update the item in Cosmos DB with blob URLs (not inline text)
    try:
        item_doc = container.read_item(item=item_id, partition_key=source)
        # Remove inline ocr_text_original if it exists (migrating to blob pattern)
        item_doc.pop("ocr_text_original", None)
        # Store blob URL references
        item_doc["ocr_text_original_blob_url"] = ocr_original_url
        item_doc["ocr_text_flexible_blob_url"] = ocr_flexible_url
        item_doc["ocr_version"] = 1
        item_doc["ocr_accuracy"] = ocr_accuracy
        item_doc["ocr_page_count"] = len(results)
        item_doc["ocr_status"] = "completed"
        item_doc["publish_status"] = "pending"
        container.upsert_item(item_doc)
        logger.info("  ✓ OCR complete: accuracy=%.3f, pages=%d", ocr_accuracy, len(results))
    except Exception as e:
        logger.error("  Failed to update Cosmos for %s: %s", item_id, e)
        return {"item_id": item_id, "status": "failed", "error": f"Cosmos update: {e}"}

    return {
        "item_id": item_id,
        "status": "completed",
        "pages_processed": len(results),
        "ocr_accuracy": ocr_accuracy,
    }


# ─── Source Processing ────────────────────────────────────────────────────────

SOURCE_MAP = {
    "moore": "moore-chronology",
    "cyclopedia": "tr-cyclopedia",
    "genealogy": "genealogy-papers",
}


def get_eligible_items(container, cosmos_source: str, force: bool = False) -> List[Dict[str, Any]]:
    """Query items with PDF or image assets that are eligible for OCR.

    Includes items with:
      - primary_pdf_url  (PDF-based chronologies, letters, etc.)
      - image_url        (directly stored image URL)
      - files            (postcards and image-only items ingested via
                          ingest_digital_resources.py which stores non-PDF
                          asset URLs in files[].url rather than image_url)

    Excludes items whose ocr_status is already 'completed', unless *force* is
    True (which also re-processes items that completed with 0% accuracy).
    """
    query = (
        "SELECT c.id, c.source, c.item_type, c.title, c.primary_pdf_url, "
        "c.image_url, c.files, c.ocr_status, c.ocr_accuracy "
        "FROM c WHERE c.source = @source "
        "AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page') "
        "AND ("
        "  IS_DEFINED(c.primary_pdf_url) "
        "  OR IS_DEFINED(c.image_url) "
        "  OR IS_DEFINED(c.files)"
        ") "
    )
    if not force:
        query += "AND (NOT IS_DEFINED(c.ocr_status) OR c.ocr_status != 'completed')"
    params = [{"name": "@source", "value": cosmos_source}]

    items = list(container.query_items(
        query=query,
        parameters=params,
        partition_key=cosmos_source,
    ))
    return items


def run_ocr(source_key: str, dry_run: bool = False, force: bool = False):
    """Run OCR for all eligible items in a source.

    Args:
        source_key: The source key ("moore", "cyclopedia", "genealogy").
        dry_run: If True, count eligible items without calling GPT-4.
        force: If True, re-process items that already have ocr_status='completed'
               (e.g. a previous run that completed with 0% accuracy).
    """
    cosmos_source = SOURCE_MAP.get(source_key)
    if not cosmos_source:
        logger.error("Unknown source: %s", source_key)
        sys.exit(1)

    # Get config and clients
    config = get_azure_config()
    container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
    container = get_container(container_name)
    client = _get_openai_client(config)
    prompt = _load_ocr_prompt()

    # Query eligible items
    items = get_eligible_items(container, cosmos_source, force=force)
    logger.info("Found %d items with assets for source '%s' (cosmos: %s)%s",
                len(items), source_key, cosmos_source,
                " [force mode — includes already-completed items]" if force else "")

    if not items:
        logger.info("No items to process.")
        return

    processed = 0
    failed = 0
    skipped = 0

    # Process multiple items concurrently — each item handles its own page
    # parallelism internally, so item-level workers are kept conservative.
    _item_workers = int(os.getenv("OCR_ITEM_WORKERS", "3"))
    logger.info("Processing %d items with up to %d concurrent item workers",
                len(items), _item_workers)

    def _process_one(indexed_item: Tuple) -> Dict[str, Any]:
        idx, item = indexed_item
        logger.info("[%d/%d] Processing: %s", idx, len(items), item.get("title", item["id"]))
        return process_item(item, container, client, config, prompt, dry_run=dry_run)

    # Per-item timeout: 15 minutes max to prevent a single hung item from
    # blocking the entire run (e.g. slow PDF download or hung GPT-4 call).
    _item_timeout = int(os.getenv("OCR_ITEM_TIMEOUT", "900"))

    with ThreadPoolExecutor(max_workers=_item_workers) as pool:
        futures = {pool.submit(_process_one, (i, item)): item
                   for i, item in enumerate(items, start=1)}
        for future in as_completed(futures):
            try:
                result = future.result(timeout=_item_timeout)
            except TimeoutError:
                item = futures[future]
                logger.error(
                    "Item timed out after %ds: %s — skipping",
                    _item_timeout, item.get("id", "?"),
                )
                failed += 1
                # Emit progress so the monitor can update the UI counter
                logger.info("Items processed: %d/%d", processed + skipped + failed, len(items))
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Unhandled error processing item: %s", exc)
                failed += 1
                logger.info("Items processed: %d/%d", processed + skipped + failed, len(items))
                continue
            if result["status"] in ("completed", "dry_run"):
                processed += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1
            # Emit item-level progress after every completion so the job monitor
            # can display "X/Y items" rather than raw page counts.
            logger.info("Items processed: %d/%d", processed + skipped + failed, len(items))

    logger.info("═" * 60)
    logger.info("OCR COMPLETE for '%s'", source_key)
    logger.info("  Total:     %d", len(items))
    logger.info("  Processed: %d", processed)
    logger.info("  Failed:    %d", failed)
    logger.info("  Skipped:   %d", skipped)
    logger.info("═" * 60)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run GPT-4 Vision OCR on digital items")
    parser.add_argument("--source", required=True,
                        choices=["moore", "cyclopedia", "genealogy", "all"],
                        help="Which source to OCR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Query items and report counts without calling GPT-4")
    parser.add_argument("--force", action="store_true",
                        help="Re-process items that already have ocr_status=completed "
                             "(e.g. a previous run that produced 0%% accuracy)")
    args = parser.parse_args()

    # Load env from local.settings.json if present (same pattern as ingest scripts)
    settings_file = Path(__file__).parent / "local.settings.json"
    if settings_file.exists():
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        for k, v in settings.get("Values", {}).items():
            if not k.startswith("_") and k not in os.environ:
                os.environ[k] = v

    if args.source == "all":
        for src in ["moore", "cyclopedia", "genealogy"]:
            run_ocr(src, dry_run=args.dry_run, force=args.force)
    else:
        run_ocr(args.source, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
