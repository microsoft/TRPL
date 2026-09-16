# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Extract per-page OCR text from Moore Chronology PDFs and build Cosmos documents."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

try:
    import pymupdf
except ImportError as exc:
    raise ImportError("PyMuPDF is required. Install with: pip install pymupdf") from exc

TRC_REFERER = "https://www.theodorerooseveltcenter.org/"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": TRC_REFERER,
}


def _download_pdf(url: str, timeout: int = 120) -> bytes:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content


def _load_local_pdf(local_path: Path) -> bytes | None:
    if local_path.exists():
        return local_path.read_bytes()
    return None


def extract_pdf_page_text(pdf_bytes: bytes) -> list[str]:
    """Return OCR/source text for each PDF page (1-based index matches list position)."""
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [document[page_index].get_text("text") or "" for page_index in range(document.page_count)]
    finally:
        document.close()


def build_moore_page_items(
    *,
    oid: str,
    parent_id: str,
    pdf_url: str,
    pdf_bytes: bytes | None = None,
    local_pdf_path: Path | None = None,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[dict[str, Any]]:
    """Build page-level digital-item documents for a Moore chronology volume."""
    raw = pdf_bytes
    if raw is None and local_pdf_path is not None:
        raw = _load_local_pdf(local_pdf_path)
    if raw is None and pdf_url:
        raw = _download_pdf(pdf_url)
    if raw is None:
        raise ValueError("Could not load PDF from local path or URL")

    page_texts = extract_pdf_page_text(raw)
    total_pages = len(page_texts)

    if end_page is None:
        end_page = total_pages
    end_page = min(end_page, total_pages)
    start_page = max(1, start_page)

    items: list[dict[str, Any]] = []
    for page_number in range(start_page, end_page + 1):
        items.append(
            {
                "id": f"moore-{oid}-p{page_number:04d}",
                "source": "moore-chronology",
                "item_type": "page",
                "parent_id": parent_id,
                "parent_source_id": oid,
                "page_number": page_number,
                "page_count": total_pages,
                "pdf_url": pdf_url,
                "ocr_text_original": page_texts[page_number - 1],
            }
        )

    logger.info(
        "Built %d page documents for %s (pages %d-%d of %d)",
        len(items),
        parent_id,
        start_page,
        end_page,
        total_pages,
    )
    return items
