# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared constants for digital-items ingestion and OCR across the Functions app."""

from __future__ import annotations

from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Source registry
# Maps the URL/route source_key → Cosmos partition key.
# Keep in sync with INGESTION_SOURCES in digital_items_routes.py.
# ---------------------------------------------------------------------------
KNOWN_SOURCES = {
    "cyclopedia": "tr-cyclopedia",
    "tr-cyclopedia": "tr-cyclopedia",
    "moore": "moore-chronology",
    "moore-chronology": "moore-chronology",
    "genealogy": "genealogy-papers",
    "genealogy-papers": "genealogy-papers",
}

# Sources where text is extracted during fetch (no OCR needed)
NO_OCR_SOURCES = {"cyclopedia", "tr-cyclopedia", "genealogy", "genealogy-papers"}

# ---------------------------------------------------------------------------
# Referer map for external asset downloads
# S3 bucket policies and CDN configurations check the Referer header.
# ---------------------------------------------------------------------------
REFERER_MAP = {
    "theodorerooseveltcenter.s3.amazonaws.com": "https://www.theodorerooseveltcenter.org/",
    "theodorerooseveltcenter.s3.us-west-2.amazonaws.com": "https://www.theodorerooseveltcenter.org/",
    "theodorerooseveltcenter.s3.dualstack.us-west-2.amazonaws.com": "https://www.theodorerooseveltcenter.org/",
    "s3-us-west-2.amazonaws.com": "https://www.theodorerooseveltcenter.org/",
    "www.theodorerooseveltcenter.org": "https://www.theodorerooseveltcenter.org/",
    "www.theodoreroosevelt.org": "https://www.theodoreroosevelt.org/",
    "images.clubexpress.com": "https://www.theodoreroosevelt.org/",
    "cdn.clubexpress.com": "https://www.theodoreroosevelt.org/",
    "files.clubexpress.com": "https://www.theodoreroosevelt.org/",
}


def get_referer(url: str) -> str:
    """Return the appropriate Referer header value for the given URL."""
    parsed = urlparse(url)
    return REFERER_MAP.get(
        parsed.netloc.lower(),
        f"{parsed.scheme}://{parsed.netloc}/",
    )
