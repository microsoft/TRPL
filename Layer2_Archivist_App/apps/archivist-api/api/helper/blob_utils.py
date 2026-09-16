# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared blob-upload and content-type utilities for ingestion scripts."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings


def guess_content_type(url: str, response_content_type: str | None) -> str | None:
    """Return a clean MIME type from the response header or by guessing from the URL."""
    if response_content_type:
        return response_content_type.split(";")[0].strip()
    guessed, _ = mimetypes.guess_type(url)
    return guessed


def filename_from_url(url: str, fallback: str) -> str:
    """Extract a filename from a URL path, falling back to *fallback*."""
    path = url.split("?")[0]
    name = Path(unquote(urlparse(path).path)).name.strip()
    return name or fallback


def is_external_url(url: str | None) -> bool:
    """Return True if *url* is an http(s) URL not hosted on Azure Blob Storage."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.netloc.endswith(".blob.core.windows.net")
    )


def upload_blob_no_overwrite(
    container_client,
    blob_name: str,
    content: bytes,
    content_type: str | None,
) -> str:
    """Upload *content* to *blob_name*; skip silently if blob already exists.

    Returns the blob URL.
    """
    blob_client = container_client.get_blob_client(blob_name)
    upload_kwargs: dict[str, Any] = {"overwrite": False}
    if content_type:
        upload_kwargs["content_settings"] = ContentSettings(content_type=content_type)
    try:
        blob_client.upload_blob(content, **upload_kwargs)
    except ResourceExistsError:
        pass
    return blob_client.url
