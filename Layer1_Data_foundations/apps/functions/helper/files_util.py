"""
Utility functions for converting files (PDF, TIFF, PNG, JPEG) to JPEG data URLs.
Supports both local files and URLs with improved error handling.
"""

import os
import io
import base64
import logging
from typing import List
from urllib.parse import urlparse
from PIL import Image
import requests

from .storage_client import get_blob_client_from_url, uses_local_storage

# Use PyMuPDF for PDF conversion - pure Python, no system dependencies
try:
    import pymupdf
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logging.warning("PyMuPDF not available. PDF conversion will fail. Install with: pip install pymupdf")


class FileConversionError(Exception):
    """Raised when file conversion fails."""


def _get_file_extension(url: str) -> str:
    """
    Extract file extension from URL more robustly.

    Args:
        url: File URL (may contain query parameters, SAS tokens, etc.)

    Returns:
        File extension in lowercase (e.g., '.pdf', '.tiff')
    """
    # Parse URL to get path component only
    parsed = urlparse(url)
    path = parsed.path

    # Get extension from path
    ext = os.path.splitext(path.lower())[1]

    if not ext:
        logging.warning("No file extension found in URL: %s", url[:100])

    return ext


def compress_image_to_target(img: Image.Image, max_size_bytes: int, min_quality: int = 40) -> bytes:
    """
    Compress a PIL Image to fit within target size.

    This is the core compression function used by PDF/TIFF conversion
    and by ocr_batch_helper for URL/data URL compression.

    Args:
        img: PIL Image object
        max_size_bytes: Maximum size in bytes
        min_quality: Minimum JPEG quality to try

    Returns:
        JPEG bytes that fit within target size

    Raises:
        FileConversionError: If cannot achieve target size even at minimum quality and scale
    """
    # Convert to RGB if needed
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Try progressively lower quality
    for quality in [85, 70, 55, min_quality]:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        buf.seek(0)
        jpeg_bytes = buf.read()

        if len(jpeg_bytes) <= max_size_bytes:
            return jpeg_bytes

    # If still too large, resize progressively (more aggressive scales)
    width, height = img.size
    for scale in [0.75, 0.5, 0.35, 0.25, 0.15, 0.10]:
        new_width = max(int(width * scale), 100)  # Minimum 100px
        new_height = max(int(height * scale), 100)
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=min_quality, optimize=True)
        buf.seek(0)
        jpeg_bytes = buf.read()

        if len(jpeg_bytes) <= max_size_bytes:
            return jpeg_bytes

    # Could not achieve target size even at minimum
    raise FileConversionError(
        f"Could not compress image to target size ({max_size_bytes / (1024 * 1024):.2f}MB). "
        f"Best achieved: {len(jpeg_bytes) / (1024 * 1024):.2f}MB"
    )


def _convert_tiff_to_jpegs(file_bytes: io.BytesIO, max_size_mb: float = None, max_frames: int = None) -> List[str]:
    """
    Convert TIFF file (potentially multi-page) to JPEG data URLs.

    Args:
        file_bytes: BytesIO containing TIFF data
        max_size_mb: Maximum size per image in MB (default from MAX_IMAGE_SIZE_MB env var, or 15)
                     Note: This is the target for the final base64 data URL size.
        max_frames: Maximum frames to convert (default from TIFF_MAX_FRAMES env var, or 50)

    Returns:
        List of JPEG data URLs (one per page)
    """
    # Use environment variables for configuration if not specified
    if max_size_mb is None:
        max_size_mb = float(os.getenv("MAX_IMAGE_SIZE_MB", "15"))
    if max_frames is None:
        max_frames = int(os.getenv("TIFF_MAX_FRAMES", "50"))  # Increased for 32GB machines

    # Account for base64 overhead: target binary size is 75% of desired data URL size
    binary_max_size_mb = max_size_mb * 0.75
    binary_max_size_bytes = int(binary_max_size_mb * 1024 * 1024)

    data_urls = []

    with Image.open(file_bytes) as img:
        n_frames = getattr(img, 'n_frames', 1)
        frames_to_convert = min(n_frames, max_frames)

        if n_frames > max_frames:
            logging.warning("TIFF has %d frames, limiting to %d", n_frames, max_frames)

        for frame_idx in range(frames_to_convert):
            if n_frames > 1:
                img.seek(frame_idx)

            frame = img
            if frame.mode not in ("RGB", "L"):
                frame = frame.convert("RGB")

            jpeg_bytes = compress_image_to_target(frame, binary_max_size_bytes)
            base64_data = base64.b64encode(jpeg_bytes).decode("utf-8")
            data_urls.append(f"data:image/jpeg;base64,{base64_data}")

        logging.info("Converted TIFF: %d frames to JPEG", len(data_urls))

    return data_urls


def _convert_pdf_to_jpegs(
    file_bytes: bytes,
    max_pages: int = None,
    dpi: int = None,
    max_size_mb: float = None
) -> List[str]:
    """
    Convert PDF to JPEG data URLs using PyMuPDF (fitz).

    PyMuPDF is a pure Python library that doesn't require external system
    dependencies like Poppler, making it ideal for Azure Functions.

    Args:
        file_bytes: PDF file bytes
        max_pages: Maximum number of pages to convert (default from PDF_MAX_PAGES env var, or 0 = unlimited)
        dpi: Resolution for rendering (default from PDF_CONVERSION_DPI env var, or 150)
        max_size_mb: Maximum size per image in MB (default from MAX_IMAGE_SIZE_MB env var, or 15)
                     Note: This is the target for the final base64 data URL size.
                     Binary compression target will be 75% of this to account for base64 overhead.

    Returns:
        List of JPEG data URLs (one per page)
    """
    if not PYMUPDF_AVAILABLE:
        raise FileConversionError(
            "PyMuPDF is not installed. Install with: pip install pymupdf"
        )

    # Use environment variables for configuration if not specified
    # PDF_MAX_PAGES=0 means unlimited (process all pages)
    if max_pages is None:
        max_pages = int(os.getenv("PDF_MAX_PAGES", "0"))  # 0 = unlimited (prevent data loss)
    if dpi is None:
        dpi = int(os.getenv("PDF_CONVERSION_DPI", "150"))
    if max_size_mb is None:
        max_size_mb = float(os.getenv("MAX_IMAGE_SIZE_MB", "15"))

    # Account for base64 overhead: target binary size is 75% of desired data URL size
    binary_max_size_mb = max_size_mb * 0.75

    # Validate input bytes
    if not file_bytes:
        raise FileConversionError("PDF conversion failed: Empty file bytes received")

    if len(file_bytes) < 100:
        raise FileConversionError(
            f"PDF conversion failed: File too small ({len(file_bytes)} bytes), likely corrupted or incomplete download"
        )

    # Check PDF magic bytes (should contain %PDF, sometimes with leading whitespace/BOM)
    # Look for %PDF in first 1024 bytes (allows for BOM, whitespace, or other headers)
    pdf_header_pos = file_bytes[:1024].find(b'%PDF')
    if pdf_header_pos == -1:
        # Check if it might be HTML error page
        if file_bytes[:5] == b'<!DOC' or file_bytes[:5] == b'<html' or file_bytes[:6] == b'<HTML>':
            raise FileConversionError(
                "PDF conversion failed: Received HTML instead of PDF (possibly access denied or file not found)"
            )
        raise FileConversionError(
            f"PDF conversion failed: Invalid PDF format (header: {file_bytes[:50]!r})"
        )
    if pdf_header_pos > 0:
        logging.info("PDF header found at offset %d (stripping leading bytes)", pdf_header_pos)
        file_bytes = file_bytes[pdf_header_pos:]

    pdf_document = None
    try:
        # Try to open PDF with repair mode if normal open fails
        try:
            pdf_document = pymupdf.open(stream=file_bytes, filetype="pdf")
        except Exception as open_err:
            logging.warning("PDF open failed, trying with repair: %s", open_err)
            # Wrap in BytesIO and try again - some PDFs need this
            pdf_stream = io.BytesIO(file_bytes)
            pdf_document = pymupdf.open(stream=pdf_stream, filetype="pdf")

        if pdf_document.is_encrypted:
            if not pdf_document.authenticate(""):
                raise FileConversionError("PDF conversion failed: PDF is password-protected")

        total_pages = len(pdf_document)
        if total_pages == 0:
            raise FileConversionError("PDF conversion failed: PDF has no pages")

        # max_pages=0 means unlimited (process all pages to prevent data loss)
        if 0 < max_pages < total_pages:
            logging.warning("PDF has %d pages, limiting to %d", total_pages, max_pages)
            pages_to_convert = max_pages
        else:
            pages_to_convert = total_pages
        data_urls = []
        binary_max_size_bytes = int(binary_max_size_mb * 1024 * 1024)
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        failed_pages = []

        for page_num in range(pages_to_convert):
            try:
                page = pdf_document[page_num]
                pixmap = page.get_pixmap(matrix=matrix)
                img = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                jpeg_bytes = compress_image_to_target(img, binary_max_size_bytes)
                base64_data = base64.b64encode(jpeg_bytes).decode("utf-8")
                data_urls.append(f"data:image/jpeg;base64,{base64_data}")
            except Exception as page_err:
                # Log and skip bad pages instead of failing entire PDF
                logging.warning("PDF page %d/%d failed: %s", page_num + 1, pages_to_convert, page_err)
                failed_pages.append(page_num + 1)
                continue

        if failed_pages:
            logging.warning("PDF conversion: %d pages failed: %s", len(failed_pages), failed_pages)

        if not data_urls:
            raise FileConversionError(
                f"PDF conversion failed: All {pages_to_convert} pages failed to convert. "
                f"PDF may be corrupted or use unsupported features."
            )

        logging.info("Converted PDF: %d/%d pages to JPEG", len(data_urls), pages_to_convert)
        return data_urls

    except FileConversionError:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        # Provide more context for common errors
        if "password" in error_msg:
            raise FileConversionError("PDF conversion failed: PDF is password-protected") from e
        if "encrypted" in error_msg:
            raise FileConversionError("PDF conversion failed: PDF is encrypted") from e
        if "memory" in error_msg or "alloc" in error_msg:
            raise FileConversionError(
                f"PDF conversion failed: Out of memory (PDF may be too large, size: {len(file_bytes) / (1024*1024):.1f}MB)"
            ) from e
        if "stream" in error_msg or "cannot open" in error_msg or "cannot parse" in error_msg:
            raise FileConversionError(
                f"PDF conversion failed: Corrupted or invalid PDF file (size: {len(file_bytes) / (1024*1024):.1f}MB). "
                f"PDF needs to be repaired."
            ) from e
        raise FileConversionError(f"PDF conversion failed: {e}") from e
    finally:
        if pdf_document:
            try:
                pdf_document.close()
            except Exception:
                pass


def convert_file_url_to_jpeg_data_urls(
    file_url: str,
    timeout: int = None,
    max_size_mb: float = None
) -> List[str]:
    """
    Convert a remote file (PDF, TIFF, JPEG, PNG) from URL to JPEG data URLs.

    Uses environment variables for configuration:
    - MAX_IMAGE_SIZE_MB: Target size per image (default: 15MB)
    - PDF_MAX_PAGES: Max pages to convert from PDF (default: 0 = unlimited)
    - PDF_CONVERSION_DPI: DPI for PDF rendering (default: 150)
    - TIFF_MAX_FRAMES: Max frames to convert from TIFF (default: 50)
    - FILE_DOWNLOAD_TIMEOUT: Download timeout in seconds (default: 3600)
    - MAX_FILE_SIZE_MB: Max file size to download (default: 4000MB)

    Args:
        file_url: Publicly accessible URL to the file
        timeout: Download timeout in seconds (default from FILE_DOWNLOAD_TIMEOUT env var, or 3600)
        max_size_mb: Maximum size per image in MB (default from MAX_IMAGE_SIZE_MB env var, or 15)
                     This is the target for the final base64 data URL size.

    Returns:
        List of JPEG data URLs (one per page for multi-page documents)

    Raises:
        FileConversionError: If conversion fails
    """
    # Use environment variables if not specified
    if timeout is None:
        timeout = int(os.getenv("FILE_DOWNLOAD_TIMEOUT", "3600"))  # 60 min for large files
    if max_size_mb is None:
        max_size_mb = float(os.getenv("MAX_IMAGE_SIZE_MB", "15"))
    file_ext = _get_file_extension(file_url)

    if not file_ext:
        raise FileConversionError(
            f"Cannot determine file type from URL: {file_url[:100]}"
        )

    # Check if file format is supported before downloading
    supported_formats = [".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png"]
    if file_ext not in supported_formats:
        raise FileConversionError(
            f"Unsupported file format: {file_ext}. "
            f"Supported formats: PDF, TIFF, JPEG, PNG"
        )

    # Check file size before downloading (prevent OOM for very large files)
    max_file_size_mb = float(os.getenv("MAX_FILE_SIZE_MB", "4000"))  # 4GB for 32GB machines
    local_blob_client = None
    if uses_local_storage():
        try:
            local_blob_client = get_blob_client_from_url(file_url)
        except ValueError:
            # Public source URLs still use HTTP while running locally.
            pass

    if local_blob_client is not None:
        file_size_mb = (
            local_blob_client.get_blob_properties().size / (1024 * 1024)
        )
        if file_size_mb > max_file_size_mb:
            raise FileConversionError(
                f"File too large: {file_size_mb:.1f}MB exceeds limit of "
                f"{max_file_size_mb}MB. Large files may cause memory issues."
            )
    else:
        try:
            head_response = requests.head(
                file_url, timeout=30, allow_redirects=True
            )
            content_length = head_response.headers.get("Content-Length")
            if content_length:
                file_size_mb = int(content_length) / (1024 * 1024)
                if file_size_mb > max_file_size_mb:
                    raise FileConversionError(
                        f"File too large: {file_size_mb:.1f}MB exceeds limit "
                        f"of {max_file_size_mb}MB. Large files may cause "
                        "memory issues."
                    )
        except requests.exceptions.RequestException:
            pass  # If HEAD fails, proceed with download and check after

    try:
        if local_blob_client is not None:
            content = local_blob_client.download_blob().readall()
            expected_size = None
        else:
            response = requests.get(file_url, timeout=timeout, stream=True)
            response.raise_for_status()
            content = response.content
            expected_size = response.headers.get("Content-Length")

        # Check size after download
        actual_size_mb = len(content) / (1024 * 1024)
        if actual_size_mb > max_file_size_mb:
            raise FileConversionError(
                f"File too large: {actual_size_mb:.1f}MB exceeds limit of {max_file_size_mb}MB."
            )

        # Validate download completed
        if expected_size and len(content) < int(expected_size):
            raise FileConversionError(
                f"Download incomplete: received {len(content)} bytes, expected {expected_size} bytes"
            )

        if len(content) == 0:
            raise FileConversionError("Download failed: received empty response")

        file_bytes = io.BytesIO(content)

        if file_ext in [".jpg", ".jpeg", ".png"]:
            # Embed as data URLs so Azure OpenAI Batch does not need to fetch
            # customer blob storage (often blocked by network rules or SAS expiry).
            img = Image.open(file_bytes)
            binary_max_size_bytes = int(max_size_mb * 0.75 * 1024 * 1024)
            jpeg_bytes = compress_image_to_target(img, binary_max_size_bytes)
            base64_data = base64.b64encode(jpeg_bytes).decode("utf-8")
            return [f"data:image/jpeg;base64,{base64_data}"]

        if file_ext == ".pdf":
            return _convert_pdf_to_jpegs(file_bytes.read(), max_size_mb=max_size_mb)

        if file_ext in [".tif", ".tiff"]:
            return _convert_tiff_to_jpegs(file_bytes, max_size_mb=max_size_mb)

        raise FileConversionError(f"Unexpected file type: {file_ext}")

    except requests.exceptions.Timeout as exc:
        raise FileConversionError(
            f"Download timeout after {timeout}s for URL: {file_url[:100]}"
        ) from exc

    except requests.exceptions.RequestException as exc:
        raise FileConversionError(
            f"Download failed for URL: {file_url[:100]} - {exc}"
        ) from exc

    except FileConversionError:
        raise

    except Exception as exc:
        raise FileConversionError(f"{exc}") from exc
