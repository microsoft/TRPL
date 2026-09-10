"""
Helper functions for metadata extraction from OCR text.
"""

import logging
import asyncio

from helper.blob_utils import download_trusted_blob_bytes

logger = logging.getLogger(__name__)


async def download_text_from_blob(blob_url: str) -> str:
    """Download text content from Azure Blob Storage URL."""
    try:
        content = await asyncio.to_thread(download_trusted_blob_bytes, blob_url)
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return str(content)
    except Exception as e:
        logger.exception("Failed to download text from blob URL %s: %s", blob_url, e)
        raise
