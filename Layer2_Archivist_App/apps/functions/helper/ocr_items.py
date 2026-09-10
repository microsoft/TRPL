"""
OCR processing for digital items with PDF/image assets.

Uses GPT-4 Vision to extract text from document pages and stores results
in Azure Blob Storage + updates Cosmos DB items with blob URLs.

Designed to run as a Durable Functions activity within the ingestion orchestrator.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

import requests as http_requests

from helper.constants import get_referer

logger = logging.getLogger(__name__)

# ─── Prompt ───────────────────────────────────────────────────────────────────

_OCR_PROMPT_FILE = Path(__file__).parent / "prompts" / "04_ocr_plus_entity_extraction.md"

# Fallback prompt if file not found
_DEFAULT_OCR_PROMPT = """You are an expert OCR system. Extract all text from this document image.
Return a JSON object with:
- "ocr_text": the full extracted text
- "confidence_scores": {"overall_ocr_confidence": 0.0-1.0}
- "entities": list of {"text": "...", "type": "person|date|place|organization"}
- "document_metadata": {"document_type": "...", "language": "en"}
"""


def _load_ocr_prompt() -> str:
    if _OCR_PROMPT_FILE.exists():
        return _OCR_PROMPT_FILE.read_text(encoding="utf-8")
    return _DEFAULT_OCR_PROMPT


# ─── Azure OpenAI Client ─────────────────────────────────────────────────────

def _get_openai_client():
    """Create Azure OpenAI client using environment config."""
    from openai import AzureOpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if not endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT not set")

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"),
        azure_ad_token_provider=token_provider,
    )


# ─── Image Conversion ────────────────────────────────────────────────────────

def _download_pdf(url: str) -> bytes:
    """Download a PDF from external URL or Azure Blob."""
    if ".blob.core.windows.net" in url:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        blob_client = BlobClient.from_blob_url(url, credential=DefaultAzureCredential())
        return blob_client.download_blob().readall()
    else:
        referer = get_referer(url)
        resp = http_requests.get(url, timeout=120, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": referer,
        })
        resp.raise_for_status()
        return resp.content


def _pdf_bytes_to_data_urls(pdf_bytes: bytes) -> List[str]:
    """Convert PDF bytes to JPEG data URLs (one per page)."""
    import pymupdf

    if len(pdf_bytes) < 100:
        raise ValueError(f"PDF too small ({len(pdf_bytes)} bytes)")

    data_urls = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            mat = pymupdf.Matrix(150 / 72, 150 / 72)  # 150 DPI
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            data_urls.append(f"data:image/jpeg;base64,{b64}")
    finally:
        doc.close()
    return data_urls


def _image_url_to_data_url(url: str) -> str:
    """Download image and convert to JPEG data URL."""
    from PIL import Image

    if ".blob.core.windows.net" in url:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        blob_client = BlobClient.from_blob_url(url, credential=DefaultAzureCredential())
        content = blob_client.download_blob().readall()
    else:
        referer = get_referer(url)
        resp = http_requests.get(url, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": referer,
            "Origin": referer.rstrip("/"),
        })
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

def _call_gpt4_vision(client, data_url: str, prompt: str) -> Dict[str, Any]:
    """Call GPT-4 Vision with a single image, return parsed JSON."""
    model = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")
    max_tokens = int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "8192"))

    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                {"type": "text", "text": "Please perform OCR and entity extraction on this document image."},
            ],
        },
    ]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.1,
        timeout=120,
    )

    content = response.choices[0].message.content or ""
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


# ─── Blob Upload ─────────────────────────────────────────────────────────────

def _upload_text_to_blob(blob_path: str, content: str) -> str | None:
    """Upload text to blob storage, return full URL or None on failure."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "digital-items-ocr")
    if not account_name:
        logger.error("AZURE_STORAGE_ACCOUNT_NAME not set, cannot upload OCR text")
        return None

    try:
        account_url = f"https://{account_name}.blob.core.windows.net"
        blob_service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        container_client = blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_path)
        blob_client.upload_blob(content, overwrite=True)
        return f"{account_url}/{container_name}/{blob_path}"
    except Exception as exc:
        logger.error("Blob upload failed for %s: %s", blob_path, exc)
        return None


# ─── Main OCR Processing ─────────────────────────────────────────────────────

def ocr_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Run OCR on a single digital item. Returns OCR result metadata.

    Reads the files[] array to find PDFs and images, downloads them,
    runs GPT-4 Vision OCR, uploads text to blob, returns metadata to
    be merged into the Cosmos item.
    """
    item_id = item.get("id", "unknown")

    files = item.get("files") or []
    file_urls = [f["url"] for f in files if isinstance(f, dict) and "url" in f]
    pdf_urls = [u for u in file_urls if u.lower().endswith(".pdf")]
    image_urls = [u for u in file_urls if not u.lower().endswith(".pdf")]

    if not pdf_urls and not image_urls:
        return {"item_id": item_id, "ocr_status": "skipped", "reason": "no_assets"}

    prompt = _load_ocr_prompt()
    client = _get_openai_client()
    page_workers = int(os.getenv("OCR_PAGE_WORKERS", "25"))

    results: List[Dict[str, Any]] = []

    # Priority: first PDF wins; otherwise process all images
    data_urls: List[str] = []
    try:
        if pdf_urls:
            logger.info("  OCR: downloading PDF for %s: %s", item_id, pdf_urls[0][:80])
            pdf_bytes = _download_pdf(pdf_urls[0])
            data_urls = _pdf_bytes_to_data_urls(pdf_bytes)
            logger.info("  OCR: PDF has %d pages", len(data_urls))
        else:
            for img_url in image_urls:
                logger.info("  OCR: downloading image for %s: %s", item_id, img_url[:80])
                data_urls.append(_image_url_to_data_url(img_url))
            logger.info("  OCR: %d images to process for %s", len(data_urls), item_id)
    except Exception as e:
        logger.error("  OCR: file download/conversion failed for %s: %s", item_id, e)
        return {"item_id": item_id, "ocr_status": "failed", "error": str(e)}

    if not data_urls:
        return {"item_id": item_id, "ocr_status": "skipped", "reason": "no_pages"}

    def _ocr_page(args):
        page_num, data_url = args
        try:
            result = _call_gpt4_vision(client, data_url, prompt)
            return {
                "page_number": page_num,
                "ocr_text": result.get("ocr_text", ""),
                "confidence": result.get("confidence_scores", {}).get("overall_ocr_confidence", 0.0),
                "entities": result.get("entities", []),
            }
        except Exception as e:
            logger.warning("  OCR page %d failed for %s: %s", page_num, item_id, e)
            return {"page_number": page_num, "ocr_text": "", "confidence": 0.0, "error": str(e)}

    page_args = [(i + 1, du) for i, du in enumerate(data_urls)]
    with ThreadPoolExecutor(max_workers=page_workers) as pool:
        results = list(pool.map(_ocr_page, page_args))
    results.sort(key=lambda r: r["page_number"])

    # Calculate accuracy
    confidences = [r["confidence"] for r in results if r.get("confidence", 0) > 0]
    ocr_accuracy = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

    # Combine text
    combined_text = "\n\n--- Page Break ---\n\n".join(
        r["ocr_text"] for r in results if r.get("ocr_text")
    )

    if not combined_text.strip():
        return {"item_id": item_id, "ocr_status": "completed", "ocr_accuracy": 0.0, "ocr_page_count": len(results)}

    # Upload to blob
    blob_original = f"{item_id}/ocr/original/{item_id}.txt"
    blob_flexible = f"{item_id}/ocr/flexible/{item_id}/v1.txt"

    original_url = _upload_text_to_blob(blob_original, combined_text)
    flexible_url = _upload_text_to_blob(blob_flexible, combined_text)

    logger.info("  OCR complete for %s: accuracy=%.3f, pages=%d", item_id, ocr_accuracy, len(results))

    return {
        "item_id": item_id,
        "ocr_status": "completed",
        "ocr_accuracy": ocr_accuracy,
        "ocr_page_count": len(results),
        "ocr_text_original_blob_url": original_url,
        "ocr_text_flexible_blob_url": flexible_url,
        "ocr_version": 1,
        "publish_status": "pending",
    }


def ocr_items_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run OCR on a batch of items that have PDF/image URLs.

    Filters items to only those with assets, processes sequentially
    (GPT-4 Vision is the bottleneck, not I/O).
    """
    results = []
    eligible = [
        item for item in items
        if any(
            isinstance(f, dict) and "url" in f
            for f in (item.get("files") or [])
        )
    ]

    if not eligible:
        logger.info("OCR: no items with PDF/image assets to process")
        return results

    logger.info("OCR: processing %d items with assets (out of %d total)", len(eligible), len(items))

    for item in eligible:
        result = ocr_item(item)
        results.append(result)

    completed = sum(1 for r in results if r.get("ocr_status") == "completed")
    logger.info("OCR batch done: %d completed, %d total", completed, len(results))
    return results
