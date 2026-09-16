# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Run OCR on digital items using Azure OpenAI Batch API.

Queries the ``digital-items`` Cosmos container for items with PDF or image
assets, converts them to JPEG, creates a JSONL batch job for Azure OpenAI,
and returns a batch job ID for async processing.

Batch results are processed by DigitalItemsOcrPoller (timer-triggered function)
which monitors job completion, extracts OCR text, uploads to blob storage, and
updates Cosmos DB with results.

**Model**: Configured via AZURE_OPENAI_BATCH_DEPLOYMENT_NAME (uses batch API)
**Deployment**: gpt-4.1 batch (or whatever is configured)

Usage (from the DataFoundations/apps/functions directory):

  python ocr_digital_items.py --source all
  python ocr_digital_items.py --source moore
  python ocr_digital_items.py --source cyclopedia
  python ocr_digital_items.py --source genealogy
  python ocr_digital_items.py --dry-run --source all

Requires:
  - AZURE_OPENAI_ENDPOINT or AZURE_AI_FOUNDRY_ENDPOINT
  - AZURE_OPENAI_BATCH_DEPLOYMENT_NAME (e.g., 'gpt-4.1-batch')
  - COSMOS_ENDPOINT
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as http_requests

from helper.config import (
    AzureConfig,
    get_azure_config,
    get_chat_completion_parameters,
    get_storage_config,
)
from helper.cosmos_client import get_container
from helper.credential import get_azure_openai_auth_kwargs
from helper.blob_utils import upload_text_to_blob
from helper.storage_client import get_blob_client_from_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

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
    """Lazily create the Azure OpenAI client."""
    global _openai_client  # pylint: disable=global-statement
    if _openai_client is not None:
        return _openai_client

    from openai import AzureOpenAI

    _openai_client = AzureOpenAI(
        azure_endpoint=config.endpoint,
        api_version=config.api_version,
        **get_azure_openai_auth_kwargs(),
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
    try:
        blob_client = get_blob_client_from_url(url)
        downloader = blob_client.download_blob()
        return downloader.readall()
    except (RuntimeError, ValueError):
        resp = http_requests.get(url, timeout=120, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.theodorerooseveltcenter.org/",
        })
        resp.raise_for_status()
        return resp.content


def _image_url_to_data_url(url: str) -> str:
    """Download an image and convert to a JPEG data URL."""
    from PIL import Image

    try:
        blob_client = get_blob_client_from_url(url)
        downloader = blob_client.download_blob()
        content = downloader.readall()
    except (RuntimeError, ValueError):
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


# ─── Batch Request Building ──────────────────────────────────────────────────

def _build_batch_request(item_id: str, page_num: int, data_url: str, prompt: str) -> Dict[str, Any]:
    """Build a single batch request for OCR of one page/image.
    
    Returns a request object compatible with Azure OpenAI Batch API.
    """
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

    config = get_azure_config()
    deployment_name = os.getenv(
        "AZURE_OPENAI_BATCH_DEPLOYMENT_NAME", config.model_name
    )

    return {
        "custom_id": f"{item_id}::page-{page_num}",
        "method": "POST",
        "url": "/chat/completions",
        "body": {
            "model": deployment_name,
            "messages": messages,
            **get_chat_completion_parameters(config, deployment_name),
        }
    }


# ─── Per-Item Batch Request Building ──────────────────────────────────────

def build_item_batch_requests(
    item: Dict[str, Any],
    prompt: str,
    dry_run: bool = False,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build batch requests for a single digital item.
    
    Returns (requests_list, item_metadata) where:
    - requests_list: List of batch requests for each page/image
    - item_metadata: Tracking metadata for result processing
    """
    item_id = item["id"]
    source = item["source"]

    pdf_url = item.get("primary_pdf_url")
    image_urls = item.get("image_url") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]

    # Fall back to non-PDF entries in the files[] array
    if not pdf_url and not image_urls:
        image_urls = [
            f["url"]
            for f in (item.get("files") or [])
            if f.get("url") and not f["url"].lower().endswith(".pdf")
        ]

    requests = []
    page_count = 0

    if pdf_url:
        try:
            logger.info("  Downloading PDF for %s ...", item_id)
            pdf_bytes = _download_pdf(pdf_url)
            data_urls = _pdf_bytes_to_data_urls(pdf_bytes)
            logger.info("  PDF has %d pages", len(data_urls))
            page_count = len(data_urls)

            if dry_run:
                return [], {
                    "item_id": item_id,
                    "source": source,
                    "asset_type": "pdf",
                    "page_count": page_count,
                    "status": "dry_run",
                }

            for page_num, data_url in enumerate(data_urls, start=1):
                req = _build_batch_request(item_id, page_num, data_url, prompt)
                requests.append(req)

        except Exception as e:
            logger.error("  Failed to download/convert PDF for %s: %s", item_id, e)
            return [], {
                "item_id": item_id,
                "source": source,
                "status": "failed",
                "error": str(e),
            }

    elif image_urls:
        page_count = len(image_urls)

        if dry_run:
            return [], {
                "item_id": item_id,
                "source": source,
                "asset_type": "images",
                "page_count": page_count,
                "status": "dry_run",
            }

        for idx, img_url in enumerate(image_urls, start=1):
            try:
                logger.info("  Converting image %d/%d ...", idx, len(image_urls))
                data_url = _image_url_to_data_url(img_url)
                req = _build_batch_request(item_id, idx, data_url, prompt)
                requests.append(req)
            except Exception as e:
                logger.warning("  Failed to convert image %d: %s", idx, e)
                # Continue with remaining images

    else:
        return [], {
            "item_id": item_id,
            "source": source,
            "status": "skipped",
            "reason": "no_assets",
        }

    return requests, {
        "item_id": item_id,
        "source": source,
        "asset_type": "pdf" if pdf_url else "images",
        "page_count": page_count,
        "status": "batched",
    }


# ─── Source Processing ────────────────────────────────────────────────────────

SOURCE_MAP = {
    "moore": "moore-chronology",
    # NOTE: the Cyclopedia is ingested/stored with Cosmos source (partition key)
    # "tr-cyclopedia" by the Archivist app. The external route/CLI key stays
    # "cyclopedia", but it MUST resolve to the "tr-cyclopedia" partition or OCR
    # finds 0 items.
    "cyclopedia": "tr-cyclopedia",
    "genealogy": "genealogy-papers",
}


def get_eligible_items(container, cosmos_source: str) -> List[Dict[str, Any]]:
    """Query items with PDF or image assets that are eligible for OCR."""
    query = (
        "SELECT c.id, c.source, c.item_type, c.title, c.primary_pdf_url, "
        "c.image_url, c.files, c.ocr_status, c.ocr_accuracy "
        "FROM c WHERE c.source = @source "
        "AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page') "
        "AND (IS_DEFINED(c.primary_pdf_url) OR IS_DEFINED(c.image_url) OR IS_DEFINED(c.files))"
    )
    params = [{"name": "@source", "value": cosmos_source}]

    items = list(container.query_items(
        query=query,
        parameters=params,
        partition_key=cosmos_source,
    ))
    return items


def run_ocr(source_key: str, dry_run: bool = False) -> str:
    """Submit batch job for OCR processing of all eligible items in a source.
    
    Returns the Azure OpenAI batch job ID.
    """
    from helper.azure_openai_batch_client import AzureOpenAIBatchClient, AzureOpenAIBatchConfig

    cosmos_source = SOURCE_MAP.get(source_key)
    if not cosmos_source:
        logger.error("Unknown source: %s", source_key)
        sys.exit(1)

    # Get config and clients
    container_name = os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", "digital-items")
    container = get_container(container_name)
    prompt = _load_ocr_prompt()

    # Query eligible items
    items = get_eligible_items(container, cosmos_source)
    logger.info("Found %d items with assets for source '%s' (cosmos: %s)",
                len(items), source_key, cosmos_source)

    if not items:
        logger.info("No items to process.")
        return None

    # Collect all batch requests and metadata
    all_requests = []
    item_metadata = {}  # item_id -> metadata

    for i, item in enumerate(items, start=1):
        logger.info("[%d/%d] Preparing: %s", i, len(items), item.get("title", item["id"]))
        requests, metadata = build_item_batch_requests(item, prompt, dry_run=dry_run)

        if dry_run and metadata.get("status") == "dry_run":
            logger.info("  [DRY RUN] Item %s: %d pages", metadata["item_id"], metadata["page_count"])
            continue

        if metadata.get("status") == "skipped":
            logger.info("  Skipped: %s (%s)", metadata["item_id"], metadata.get("reason"))
            continue

        if metadata.get("status") == "failed":
            logger.warning("  Failed to prepare: %s (%s)", metadata["item_id"], metadata.get("error"))
            continue

        all_requests.extend(requests)
        item_metadata[metadata["item_id"]] = metadata

    if dry_run:
        logger.info("DRY RUN complete. Would submit %d batch requests for %d items.",
                    len(all_requests), len(item_metadata))
        return None

    if not all_requests:
        logger.warning("No batch requests to submit for source '%s'", source_key)
        return None

    # Submit batch job
    try:
        batch_config = AzureOpenAIBatchConfig.from_env()
        batch_client = AzureOpenAIBatchClient(batch_config)

        # Create JSONL from requests
        jsonl_file = batch_client.create_jsonl_from_requests(all_requests, output_path=None)
        logger.info("Created JSONL with %d requests: %s", len(all_requests), jsonl_file)

        # Submit batch job
        batch_id = batch_client.submit_batch_job(jsonl_file)
        logger.info("Submitted batch job: %s for source '%s' with %d requests",
                    batch_id, source_key, len(all_requests))

        # Store batch metadata for result processing
        batch_metadata = {
            "batch_id": batch_id,
            "source_key": source_key,
            "cosmos_source": cosmos_source,
            "item_count": len(item_metadata),
            "request_count": len(all_requests),
            "items": item_metadata,
            "created_at": time.time(),
        }

        # Store in Cosmos batch status container for tracking
        from helper.cosmos_client import get_container as get_cosmos_container
        batch_container = get_cosmos_container("batchstatus")
        batch_doc = {
            "id": f"digital-items-{batch_id}",
            "batch_id": batch_id,
            "batch_type": "digital-items-ocr",
            "source": source_key,
            "metadata": batch_metadata,
            "status": "submitted",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        batch_container.create_item(batch_doc)
        logger.info("Stored batch metadata in batchstatus container")

        return batch_id

    except Exception as e:
        logger.exception("Failed to submit batch job for source '%s': %s", source_key, e)
        sys.exit(1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point: run GPT-4 Vision OCR on digital items for a given source."""
    parser = argparse.ArgumentParser(description="Run GPT-4 Vision OCR on digital items")
    parser.add_argument("--source", required=True,
                        choices=["moore", "cyclopedia", "genealogy", "all"],
                        help="Which source to OCR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Query items and report counts without calling GPT-4")
    args = parser.parse_args()

    # Load env from local.settings.json if present (same pattern as ingest scripts)
    settings_file = Path(__file__).parent / "local.settings.json"
    if settings_file.exists():
        import json as _json
        settings = _json.loads(settings_file.read_text())
        for k, v in settings.get("Values", {}).items():
            if not k.startswith("_") and k not in os.environ:
                os.environ[k] = v

    if args.source == "all":
        for src in ["moore", "cyclopedia", "genealogy"]:
            run_ocr(src, dry_run=args.dry_run)
    else:
        run_ocr(args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
