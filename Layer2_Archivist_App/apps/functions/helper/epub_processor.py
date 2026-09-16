# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
EPUB Processing Helper

Handles parsing EPUB files and ingesting selected sections into the search index.
"""

import asyncio
import copy
import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from urllib.parse import unquote, urlparse

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.storage.blob import BlobServiceClient, ContentSettings

from helper.epub_parser import EpubParser
from helper.config import CosmosDBConfig, get_epub_config
from helper.cosmos_client import CosmosDBClient

logger = logging.getLogger(__name__)

# Get EPUB configuration from environment variables
_epub_config = get_epub_config()

# Singleton CosmosDB client for EPUB operations
_epub_cosmos_client: Optional[CosmosDBClient] = None
_epub_chunks_cosmos_client: Optional[CosmosDBClient] = None


def get_epub_container():
    """Get EPUB CosmosDB container using the existing CosmosDBClient."""
    global _epub_cosmos_client

    if _epub_cosmos_client is None:
        config = CosmosDBConfig.from_env()
        # Override container name for EPUB
        config.container_name = _epub_config.epub_cosmos_container
        _epub_cosmos_client = CosmosDBClient(config)

    return _epub_cosmos_client._container


def get_epub_chunks_container():
    """Get EPUB chunks CosmosDB container for storing text chunks."""
    global _epub_chunks_cosmos_client

    if _epub_chunks_cosmos_client is None:
        config = CosmosDBConfig.from_env()
        # Override container name for EPUB chunks
        config.container_name = _epub_config.epub_chunks_container
        _epub_chunks_cosmos_client = CosmosDBClient(config)

    return _epub_chunks_cosmos_client._container


def get_blob_service_client() -> BlobServiceClient:
    """Blob client for local Azurite or Archivist-owned cloud storage."""
    from helper.config import get_storage_config

    config = get_storage_config()
    if config.connection_string:
        if os.getenv("ENVIRONMENT", "production").lower() != "local":
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is allowed only when "
                "ENVIRONMENT=local"
            )
        return BlobServiceClient.from_connection_string(config.connection_string)
    account = (config.archivist_storage_account_name or "").strip()
    if not account:
        raise ValueError(
            "ARCHIVIST_STORAGE_ACCOUNT_NAME (or AZURE_STORAGE_ACCOUNT_NAME) "
            "is required for EPUB blob storage."
        )
    return BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )


def _blob_location(blob_url: str) -> tuple[str, str]:
    parsed = urlparse(blob_url)
    path = parsed.path.lstrip("/")
    if parsed.hostname in {"azurite", "127.0.0.1", "localhost"}:
        path_parts = path.split("/", 1)
        if len(path_parts) != 2:
            raise ValueError(f"Invalid Azurite blob URL: {blob_url}")
        path = path_parts[1]
    if "/" not in path:
        raise ValueError(f"Invalid blob URL: {blob_url}")
    container_name, blob_name = path.split("/", 1)
    return container_name, unquote(blob_name)


def download_epub_blob(blob_url: str) -> bytes:
    """Download EPUB file from blob storage."""
    blob_service = get_blob_service_client()

    container_name, blob_name = _blob_location(blob_url)

    logger.info("Downloading blob: container=%s, blob=%s", container_name, blob_name)

    container_client = blob_service.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)

    return blob_client.download_blob().readall()


def ensure_storage_container():
    """Ensure the EPUB storage container exists."""
    blob_service = get_blob_service_client()
    container_client = blob_service.get_container_client(_epub_config.storage_container_name)
    try:
        container_client.create_container()
        logger.info("Created EPUB storage container: %s", _epub_config.storage_container_name)
    except Exception:
        pass  # Container already exists
    return container_client


def upload_json_to_storage(document_id: str, filename: str, data: Any) -> str:
    """
    Upload JSON data to blob storage under document folder.
    
    Args:
        document_id: The EPUB document ID
        filename: Name of the JSON file (e.g., 'sections.json')
        data: Data to serialize as JSON
        
    Returns:
        URL of the uploaded JSON blob
    """
    container_client = ensure_storage_container()
    
    json_content = json.dumps(data, ensure_ascii=False, indent=2)
    blob_name = f"{document_id}/{filename}"
    
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(
        json_content,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json")
    )
    
    logger.info("Uploaded %s (%d bytes)", blob_name, len(json_content))
    return blob_client.url


def download_json_from_storage(blob_url: str) -> Any:
    """
    Download JSON from blob storage.
    
    Args:
        blob_url: URL of the JSON blob
        
    Returns:
        Parsed JSON data
    """
    blob_service = get_blob_service_client()
    
    container_name, blob_name = _blob_location(blob_url)
    
    logger.info("Downloading JSON: container=%s, blob=%s", container_name, blob_name)
    
    container_client = blob_service.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)
    
    json_content = blob_client.download_blob().readall().decode('utf-8')
    return json.loads(json_content)


def upload_sections_json(document_id: str, sections: List[Dict[str, Any]]) -> str:
    """Upload sections data as JSON to blob storage."""
    return upload_json_to_storage(document_id, "sections.json", sections)


def download_sections_json(sections_url: str) -> List[Dict[str, Any]]:
    """Download sections JSON from blob storage."""
    return download_json_from_storage(sections_url)


def update_epub_status(
    container,
    doc_id: str,
    status: str,
    error_message: Optional[str] = None,
    additional_fields: Optional[Dict[str, Any]] = None
):
    """Update EPUB document status in CosmosDB."""
    try:
        doc = container.read_item(item=doc_id, partition_key=doc_id)
        doc["status"] = status
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        if error_message:
            doc["error_message"] = error_message
        
        if additional_fields:
            doc.update(additional_fields)
        
        container.upsert_item(doc)
        logger.info("Updated EPUB document %s status to %s", doc_id, status)
    except Exception as e:
        logger.error("Failed to update EPUB status for %s: %s", doc_id, str(e))
        raise


def process_epub_parse(document_id: str) -> None:
    """
    Parse an EPUB file and save results to CosmosDB.
    
    Args:
        document_id: The EPUB document ID in CosmosDB
    """
    logger.info("Starting EPUB parse for document: %s", document_id)
    
    container = get_epub_container()
    
    try:
        # Get document from CosmosDB
        doc = container.read_item(item=document_id, partition_key=document_id)
    except Exception as e:
        logger.error("EPUB document not found: %s", document_id)
        raise ValueError(f"EPUB document not found: {document_id}") from e
    
    # Update status to parsing
    update_epub_status(container, document_id, "parsing")
    
    try:
        # Download EPUB file
        blob_url = doc.get("blob_url")
        if not blob_url:
            raise ValueError("EPUB document missing blob_url")
        
        logger.info("Downloading EPUB from: %s", blob_url)
        epub_content = download_epub_blob(blob_url)
        
        # Check for external OPF file
        opf_url = doc.get("opf_url")
        logger.info("Document opf_url field: %s", opf_url)
        external_opf_content = None
        if opf_url:
            logger.info("Downloading external OPF from: %s", opf_url)
            try:
                external_opf_content = download_epub_blob(opf_url)
                logger.info("Downloaded external OPF (%d bytes)", len(external_opf_content))
                # Log first 500 chars of OPF for debugging
                if external_opf_content:
                    opf_preview = external_opf_content.decode('utf-8', errors='ignore')[:500]
                    logger.info("OPF content preview: %s", opf_preview)
            except Exception as opf_err:
                logger.warning("Failed to download OPF file, will use embedded: %s", opf_err)
        else:
            logger.info("No external OPF file provided - using embedded EPUB metadata")
        
        # Parse EPUB
        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp_file:
            tmp_file.write(epub_content)
            tmp_path = tmp_file.name
        
        # Save external OPF if provided
        external_opf_path = None
        if external_opf_content:
            with tempfile.NamedTemporaryFile(suffix='.opf', delete=False, mode='wb') as opf_tmp:
                opf_tmp.write(external_opf_content)
                external_opf_path = opf_tmp.name
            logger.info("Saved external OPF to temp file: %s (%d bytes)", 
                       external_opf_path, len(external_opf_content))
            
            # Verify the file was written correctly
            with open(external_opf_path, 'r', encoding='utf-8') as verify_f:
                verify_content = verify_f.read()
                logger.info("Verified OPF file is readable, %d chars", len(verify_content))
        else:
            logger.info("No external OPF content to save")
        
        try:
            parser = EpubParser()
            logger.info("Calling parser.parse with external_opf_path=%s", external_opf_path)
            book_structure = parser.parse(tmp_path, external_opf_path=external_opf_path)
        finally:
            os.unlink(tmp_path)
            if external_opf_path:
                os.unlink(external_opf_path)
        
        # Convert to dict - this gives us TOC structure WITHOUT content
        book_dict = book_structure.to_dict(include_html=False, content_as_html=False)
        
        sections = []
        order = 0
        
        def build_section_dict(section_data: dict, section_type: str,
                               selected: bool = True, level: int = 1,
                               parent_order: Optional[int] = None) -> dict:
            """Build a section dictionary with nested children support (no content yet)."""
            nonlocal order
            current_order = order
            order += 1
            
            children = []
            if "children" in section_data and section_data["children"]:
                for child in section_data["children"]:
                    child_dict = build_section_dict(
                        child,
                        "subsection",
                        selected=selected,
                        level=child.get("level", level + 1),
                        parent_order=current_order
                    )
                    children.append(child_dict)
            
            return {
                "type": section_type,
                "title": section_data.get("title", ""),
                "href": section_data.get("href", ""),  # Store href for later extraction
                "content": "",  # No content yet - extracted during ingestion
                "word_count": 0,
                "order": current_order,
                "selected": selected,
                "html_content": None,
                "level": section_data.get("level", level),
                "parent_order": parent_order,
                "children": children
            }
        
        # All sections from TOC go to chapters list
        for chapter in book_dict.get("chapters", []):
            sections.append(build_section_dict(chapter, "chapter", selected=True))
        
        logger.info("=" * 60)
        logger.info("SECTIONS BUILT FOR UPLOAD")
        logger.info("Top-level sections: %d", len(sections))
        
        # Log structure
        def log_section_tree(secs, indent=0):
            for s in secs:
                child_count = len(s.get("children", []))
                logger.info("%s[%d] %s (%s) - %d children",
                           "  " * indent, s["order"], 
                           s["title"][:40] if s.get("title") else "(no title)",
                           s["type"], child_count)
                if s.get("children"):
                    log_section_tree(s["children"], indent + 1)
        
        log_section_tree(sections)
        logger.info("=" * 60)
        
        # Count all sections including nested children
        def count_all_sections_recursive(section_list, selected_only=False):
            """Count all sections including nested children."""
            count = 0
            for s in section_list:
                if not selected_only or s.get("selected", False):
                    count += 1
                if s.get("children"):
                    count += count_all_sections_recursive(s["children"], selected_only)
            return count
        
        # Build comprehensive metadata from parsed OPF
        metadata = book_dict.get("metadata", {})
        epub_metadata = {
            # Core metadata
            "title": metadata.get("title", ""),
            "titleSort": metadata.get("titleSort", ""),
            "author": metadata.get("author", ""),
            "creator": metadata.get("creator", ""),
            "creatorInfo": metadata.get("creatorInfo"),  # {name, fileAs, role}
            "contributors": metadata.get("contributors", []),  # [{name, fileAs, role}, ...]
            "publisher": metadata.get("publisher", ""),
            "language": metadata.get("language", ""),
            "description": metadata.get("description", ""),
            "subjects": metadata.get("subjects", []),
            "rights": metadata.get("rights", ""),
            "date": metadata.get("date", ""),
            
            # Identifiers
            "identifier": metadata.get("identifier", ""),
            "isbn": metadata.get("isbn", ""),
            "uuid": metadata.get("uuid", ""),
            "asin": metadata.get("asin", ""),
            "googleId": metadata.get("googleId", ""),
            "calibreId": metadata.get("calibreId", ""),
            "identifiers": metadata.get("identifiers", {}),  # All identifiers by scheme
            
            # Series info
            "series": metadata.get("series", ""),
            "seriesIndex": metadata.get("seriesIndex", ""),
            
            # Calibre metadata
            "calibreTimestamp": metadata.get("calibreTimestamp", ""),
            
            # Additional Dublin Core
            "source": metadata.get("source", ""),
            "relation": metadata.get("relation", ""),
            "coverage": metadata.get("coverage", ""),
            "type": metadata.get("type", ""),
            "format": metadata.get("format", ""),
            
            # Custom metadata (from calibre or other sources)
            "custom": metadata.get("custom", {}),
            
            # Computed fields
            "total_word_count": metadata.get("totalWordCount", 0),
            "chapter_count": metadata.get("chapterCount", 0)
        }
        
        # Log stored metadata
        logger.info("=" * 60)
        logger.info("METADATA TO BE STORED IN COSMOSDB")
        logger.info("=" * 60)
        logger.info("Title: %s", epub_metadata.get("title"))
        logger.info("Author: %s", epub_metadata.get("author"))
        logger.info("Publisher: %s", epub_metadata.get("publisher"))
        logger.info("Date: %s", epub_metadata.get("date"))
        logger.info("ISBN: %s", epub_metadata.get("isbn"))
        logger.info("Series: %s (#%s)", epub_metadata.get("series"), epub_metadata.get("seriesIndex"))
        logger.info("Subjects: %s", epub_metadata.get("subjects"))
        logger.info("Identifiers: %s", epub_metadata.get("identifiers"))
        if epub_metadata.get("custom"):
            logger.info("Custom fields: %d", len(epub_metadata["custom"]))
        logger.info("=" * 60)
        
        # Calculate counts including nested children
        total_section_count = count_all_sections_recursive(sections, selected_only=False)
        selected_section_count = count_all_sections_recursive(sections, selected_only=True)
        
        # Calculate word count for selected sections (including children)
        def sum_word_count(section_list):
            total = 0
            for s in section_list:
                if s.get("selected", False):
                    total += s.get("word_count", 0)
                if s.get("children"):
                    total += sum_word_count(s["children"])
            return total
        
        selected_word_count = sum_word_count(sections)
        
        # Upload sections to blob storage (not CosmosDB to avoid size limits)
        sections_url = upload_sections_json(document_id, sections)
        logger.info("Sections uploaded to: %s", sections_url)
        
        # Update document with parsed data (sections stored in blob, not here)
        additional_fields = {
            "metadata": epub_metadata,
            "sections_url": sections_url,  # Reference to blob storage
            "total_sections": total_section_count,
            "selected_sections": selected_section_count,
            "selected_word_count": selected_word_count,
            "parsed_at": datetime.now(timezone.utc).isoformat()
        }
        
        update_epub_status(container, document_id, "parsed", additional_fields=additional_fields)
        logger.info(
            "Successfully parsed EPUB %s: %d total sections (top-level: %d), %d selected, %d words",
            document_id, total_section_count, len(sections), 
            selected_section_count, epub_metadata.get("total_word_count", 0)
        )
        
    except Exception as e:
        logger.exception("Failed to parse EPUB %s: %s", document_id, str(e))
        update_epub_status(container, document_id, "error", error_message=str(e))
        raise


def upload_extracted_text(
    document_id: str,
    sections: List[Dict[str, Any]],
    text_type: str
) -> str:
    """Upload extracted text (original or filtered) to blob storage."""
    return upload_json_to_storage(document_id, f"{text_type}_text.json", sections)


def download_extracted_text(text_url: str) -> List[Dict[str, Any]]:
    """Download extracted text JSON from blob storage."""
    return download_json_from_storage(text_url)


def _extract_images_from_epub(
    epub_path: str,
    image_names_filter: Optional[Set[str]] = None
) -> Dict[str, bytes]:
    """
    Extract images from an EPUB file.
    
    Args:
        epub_path: Path to the EPUB file
        image_names_filter: Optional set of image name patterns to extract.
                           If provided, only images matching these patterns are extracted.
                           If None, extracts all images.
        
    Returns:
        Dictionary mapping image paths to image bytes
    """
    from ebooklib import epub, ITEM_IMAGE  # pylint: disable=import-error
    
    images = {}
    try:
        book = epub.read_epub(epub_path)
        for item in book.get_items():
            if item.get_type() == ITEM_IMAGE:
                image_name = item.get_name()
                
                # If filter provided, only extract matching images
                if image_names_filter is not None:
                    # Check if this image matches any of the filter patterns
                    should_extract = False
                    for filter_name in image_names_filter:
                        if filter_name in image_name or image_name.endswith(filter_name):
                            should_extract = True
                            break
                    if not should_extract:
                        continue
                
                images[image_name] = item.get_content()
        
        if image_names_filter:
            logger.info("Extracted %d images from EPUB (filtered from %d requested)",
                       len(images), len(image_names_filter))
        else:
            logger.info("Extracted %d images from EPUB", len(images))
    except Exception as e:
        logger.error("Failed to extract images from EPUB: %s", str(e))
    return images


def _perform_ocr_on_image(image_bytes: bytes, image_name: str) -> str:
    """
    Perform OCR on an image using Azure OpenAI GPT-4 Vision.
    
    Args:
        image_bytes: The image content as bytes
        image_name: Name of the image for logging
        
    Returns:
        Extracted text from the image
    """
    import base64
    from openai import AzureOpenAI
    from helper.config import get_indexing_config
    
    config = get_indexing_config()
    
    if not config.azure_openai_endpoint:
        logger.warning("Azure OpenAI not configured - skipping OCR for %s", image_name)
        return ""
    
    try:
        # Encode image to base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Determine image mime type
        image_lower = image_name.lower()
        if image_lower.endswith('.png'):
            mime_type = "image/png"
        elif image_lower.endswith('.gif'):
            mime_type = "image/gif"
        elif image_lower.endswith('.webp'):
            mime_type = "image/webp"
        else:
            mime_type = "image/jpeg"
        
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        client = AzureOpenAI(
            azure_ad_token_provider=token_provider,
            api_version="2024-02-15-preview",
            azure_endpoint=config.azure_openai_endpoint
        )
        
        # Use Vision model and max tokens from config for OCR
        vision_model = config.azure_openai_vision_deployment_name
        max_tokens = config.azure_openai_max_tokens
        logger.debug("Using vision model '%s' for OCR on %s (max_tokens: %d)", vision_model, image_name, max_tokens)
        
        response = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an OCR assistant. Extract all readable text from the image exactly as it appears. Preserve the original formatting, paragraphs, and line breaks. If the image contains a book page, extract all the text content. Only output the extracted text, nothing else."
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_base64}",
                                "detail": "high"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this image:"
                        }
                    ]
                }
            ],
            max_tokens=max_tokens
        )
        
        extracted_text = response.choices[0].message.content or ""
        logger.info("OCR extracted %d characters from %s", len(extracted_text), image_name)
        return extracted_text.strip()
        
    except Exception as e:
        logger.error("OCR failed for image %s: %s", image_name, str(e))
        return ""


def _perform_ocr_on_sections(
    epub_path: str,
    sections: List[Dict[str, Any]],
    images: Optional[Dict[str, bytes]] = None
) -> List[Dict[str, Any]]:
    """
    Perform OCR on images referenced in EPUB sections.
    
    Uses parallel processing with asyncio and semaphore (limit: 20) for efficiency.
    Each unique image is processed only once, then results are distributed to
    all sections that reference it.
    
    For each section, checks if the HTML contains image references and if
    the section has little/no text content. If so, extracts text from
    the images using OCR.
    
    Args:
        epub_path: Path to the EPUB file
        sections: List of section dictionaries with content
        images: Pre-extracted images dict (optional, will extract only needed images)
        
    Returns:
        Updated sections with OCR-extracted text appended
    """
    # Note: re and ThreadPoolExecutor are imported at module level
    img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
    
    # First pass: scan sections to find which images are referenced
    # This happens BEFORE extracting any images from the EPUB
    # referenced_image_paths: {normalized_path}
    # section_image_map: {section_order: [normalized_path, ...]}
    referenced_image_paths: Set[str] = set()
    section_image_map: Dict[Any, List[str]] = {}
    
    # Track per-section image counts for logging
    section_image_counts: Dict[str, int] = {}  # section_title -> image_count
    
    def scan_section_for_images(section: Dict[str, Any]):
        """Scan section for image references without loading images."""
        # Only process selected sections
        if not section.get("selected", False):
            # Still need to check children even if parent not selected
            if section.get("children"):
                for child in section["children"]:
                    scan_section_for_images(child)
            return
        
        html_content = section.get("html_content", "")
        text_content = section.get("content", "")
        section_order = section.get("order")
        section_title = section.get("title", f"Section {section_order}")[:50]
        
        # Only consider OCR if text content is sparse (less than 100 chars)
        images_in_section = 0
        if html_content and len(text_content.strip()) < 100:
            img_matches = img_pattern.findall(html_content)
            
            for img_src in img_matches:
                # Normalize image path (get filename)
                img_path = img_src.split('/')[-1] if '/' in img_src else img_src
                referenced_image_paths.add(img_path)
                images_in_section += 1
                
                # Track which sections reference this image
                if section_order not in section_image_map:
                    section_image_map[section_order] = []
                if img_path not in section_image_map[section_order]:
                    section_image_map[section_order].append(img_path)
        
        # Log section details
        if images_in_section > 0:
            section_image_counts[section_title] = images_in_section
            logger.info("Section '%s' (order=%s): %d images, %d chars text",
                       section_title, section_order, images_in_section, len(text_content.strip()))
        
        # Process children (only if they are selected, handled by recursion)
        if section.get("children"):
            for child in section["children"]:
                scan_section_for_images(child)
    
    # Scan all sections to find referenced images
    for section in sections:
        scan_section_for_images(section)
    
    if not referenced_image_paths:
        logger.info("No images referenced in sections needing OCR")
        return sections
    
    # Log summary of images per section
    logger.info("=== OCR Section Summary ===")
    for section_title, img_count in section_image_counts.items():
        logger.info("  - %s: %d images", section_title, img_count)
    logger.info("Found %d unique images referenced in %d sections (total refs: %d)",
                len(referenced_image_paths), len(section_image_map),
                sum(len(imgs) for imgs in section_image_map.values()))
    
    # Extract only the needed images from EPUB (not all images)
    if images is None:
        images = _extract_images_from_epub(epub_path, image_names_filter=referenced_image_paths)
    else:
        # Filter pre-extracted images to only those we need
        filtered_images = {}
        for image_name, image_bytes in images.items():
            for ref_path in referenced_image_paths:
                if ref_path in image_name or image_name.endswith(ref_path):
                    filtered_images[image_name] = image_bytes
                    break
        images = filtered_images
        logger.info("Filtered to %d images from pre-extracted set", len(images))
    
    if not images:
        logger.info("No matching images found in EPUB - skipping OCR")
        return sections
    
    # Build unique_images dict mapping normalized paths to actual image data
    # Also update section_image_map to use actual image names
    unique_images: Dict[str, bytes] = {}
    updated_section_image_map: Dict[Any, List[str]] = {}
    
    for section_order, ref_paths in section_image_map.items():
        updated_section_image_map[section_order] = []
        for ref_path in ref_paths:
            # Find the actual image that matches this reference
            for image_name, image_bytes in images.items():
                if ref_path in image_name or image_name.endswith(ref_path):
                    if image_name not in unique_images:
                        unique_images[image_name] = image_bytes
                    if image_name not in updated_section_image_map[section_order]:
                        updated_section_image_map[section_order].append(image_name)
                    break
    
    section_image_map = updated_section_image_map
    
    if not unique_images:
        logger.info("No images need OCR processing after matching")
        return sections
    
    total_references = sum(len(imgs) for imgs in section_image_map.values())
    logger.info("Processing %d unique images for OCR (referenced %d times across %d sections)",
                len(unique_images), total_references, len(section_image_map))
    
    # Process unique images in parallel with asyncio and semaphore
    image_ocr_results: Dict[str, str] = {}  # image_name -> ocr_text
    processed_count = 0
    total_images = len(unique_images)
    
    async def process_image_async(semaphore, image_name, image_bytes):
        """Process a single image with semaphore control."""
        nonlocal processed_count
        async with semaphore:
            # Run OCR in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                ocr_text = await loop.run_in_executor(
                    executor,
                    _perform_ocr_on_image,
                    image_bytes,
                    image_name
                )
            
            processed_count += 1
            if processed_count % 10 == 0:
                logger.info("OCR progress: %d/%d unique images processed", processed_count, total_images)
            
            return image_name, ocr_text
    
    async def process_all_images():
        """Process all unique images in parallel with semaphore limit."""
        semaphore = asyncio.Semaphore(20)  # Limit concurrent OCR calls
        
        tasks = [
            process_image_async(semaphore, image_name, image_bytes)
            for image_name, image_bytes in unique_images.items()
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Store results by image name
        for image_name, ocr_text in results:
            if ocr_text:
                image_ocr_results[image_name] = ocr_text
    
    # Run async processing
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(process_all_images())
    
    logger.info("OCR completed: %d unique images processed, %d had extractable text",
                len(unique_images), len(image_ocr_results))
    
    # Build section_order -> [ocr_text, ...] mapping from image results
    ocr_results: Dict[Any, List[str]] = {}
    for section_order, image_names in section_image_map.items():
        for image_name in image_names:
            if image_name in image_ocr_results:
                if section_order not in ocr_results:
                    ocr_results[section_order] = []
                ocr_results[section_order].append(image_ocr_results[image_name])
    
    # Second pass: apply OCR results to sections
    def apply_ocr_to_section(section: Dict[str, Any]) -> Dict[str, Any]:
        """Apply OCR results to a section."""
        result = section.copy()
        section_order = section.get("order")
        
        if section_order in ocr_results:
            ocr_texts = ocr_results[section_order]
            combined_ocr = "\n\n".join(ocr_texts)
            text_content = section.get("content", "")
            
            if text_content:
                result["content"] = text_content + "\n\n[OCR Extracted Text]\n" + combined_ocr
            else:
                result["content"] = combined_ocr
            
            result["word_count"] = len(result["content"].split())
            result["ocr_applied"] = True
            logger.info("Added OCR text to section '%s' (order=%s): %d words",
                       section.get("title", "")[:30], section_order, result["word_count"])
        
        # Process children recursively
        if section.get("children"):
            result["children"] = [apply_ocr_to_section(child) for child in section["children"]]
        
        return result
    
    return [apply_ocr_to_section(s) for s in sections]


def process_epub_extract(document_id: str, enable_ocr: bool = False) -> None:
    """
    Extract text from selected EPUB sections (both original and filtered).
    
    This is the step after user selects sections. It:
    1. Downloads sections from blob storage
    2. Downloads the EPUB file
    3. Extracts ORIGINAL text (without filtering) for validation
    4. Extracts FILTERED text (using epub_filter_config) for ingestion
    5. If enable_ocr=True, also extracts text from images using OCR
    6. Stores both versions in blob storage
    7. Sets status to 'validate' for user review
    
    Args:
        document_id: The EPUB document ID in CosmosDB
        enable_ocr: If True, use OCR to extract text from images
    """
    logger.info("Starting EPUB text extraction for document: %s (OCR: %s)", document_id, enable_ocr)
    
    container = get_epub_container()
    
    try:
        # Get document from CosmosDB
        doc = container.read_item(item=document_id, partition_key=document_id)
    except Exception as e:
        logger.error("EPUB document not found: %s", document_id)
        raise ValueError(f"EPUB document not found: {document_id}") from e
    
    # Update status to extracting
    update_epub_status(container, document_id, "extracting")
    
    try:
        # Download sections from blob storage
        sections_url = doc.get("sections_url")
        if not sections_url:
            raise ValueError("No sections_url found - document not parsed yet")
        
        sections = download_sections_json(sections_url)
        logger.info("Downloaded %d sections from blob", len(sections))
        
        # Count selected sections
        def count_selected(secs):
            count = sum(1 for s in secs if s.get("selected", False))
            for s in secs:
                if s.get("children"):
                    count += count_selected(s["children"])
            return count
        
        selected_count = count_selected(sections)
        if selected_count == 0:
            raise ValueError("No sections selected for extraction")
        
        logger.info("Found %d selected sections for extraction", selected_count)
        
        # Download EPUB file
        blob_url = doc.get("blob_url")
        if not blob_url:
            raise ValueError("EPUB document missing blob_url")
        
        epub_content = download_epub_blob(blob_url)
        
        # Get custom filter config from document (if any)
        custom_filter_config = doc.get("filter_config")
        if custom_filter_config:
            logger.info("Using custom filter config for document %s", document_id)
        else:
            logger.info("Using default filter config for document %s", document_id)
        
        # Extract both original and filtered content
        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp_file:
            tmp_file.write(epub_content)
            tmp_path = tmp_file.name
        
        try:
            parser = EpubParser()
            original_sections, filtered_sections = parser.extract_content_dual(
                tmp_path, sections, custom_filter_config=custom_filter_config
            )
            
            # If OCR is enabled, extract text from images
            # OCR produces plain text (no HTML), so original = filtered for OCR content
            if enable_ocr:
                logger.info("OCR enabled - processing images from selected sections...")
                # Let _perform_ocr_on_sections extract only needed images (not all)
                original_sections = _perform_ocr_on_sections(tmp_path, original_sections)
                # For OCR, filtered = original (plain text has nothing to filter)
                filtered_sections = copy.deepcopy(original_sections)
                logger.info("OCR extraction completed")
        finally:
            os.unlink(tmp_path)
        
        # Upload both versions to blob storage
        original_text_url = upload_extracted_text(
            document_id, original_sections, "original"
        )
        filtered_text_url = upload_extracted_text(
            document_id, filtered_sections, "filtered"
        )
        
        logger.info("Uploaded original text to: %s", original_text_url)
        logger.info("Uploaded filtered text to: %s", filtered_text_url)
        
        # Calculate word counts
        def total_word_count(secs):
            total = sum(s.get("word_count", 0) for s in secs if s.get("selected"))
            for s in secs:
                if s.get("children"):
                    total += total_word_count(s["children"])
            return total
        
        original_word_count = total_word_count(original_sections)
        filtered_word_count = total_word_count(filtered_sections)
        
        # Update document with extracted text URLs and set status to 'validate'
        additional_fields = {
            "original_text_url": original_text_url,
            "filtered_text_url": filtered_text_url,
            "original_word_count": original_word_count,
            "filtered_word_count": filtered_word_count,
            "ocr_enabled": enable_ocr,
            "extracted_at": datetime.now(timezone.utc).isoformat()
        }
        
        update_epub_status(container, document_id, "validate",
                          additional_fields=additional_fields)
        
        logger.info(
            "Successfully extracted EPUB %s: %d sections, "
            "original: %d words, filtered: %d words",
            document_id, selected_count, original_word_count, filtered_word_count
        )
        
    except Exception as e:
        logger.exception("Failed to extract EPUB %s: %s", document_id, str(e))
        update_epub_status(container, document_id, "error", error_message=str(e))
        raise


# ============================================================
# EPUB Ingestion Helper Functions
# ============================================================

def _count_selected_sections(sections: List[Dict[str, Any]]) -> int:
    """Count total selected sections including nested children."""
    count = sum(1 for s in sections if s.get("selected", False))
    for s in sections:
        if s.get("children"):
            count += _count_selected_sections(s["children"])
    return count


def _collect_chunks_from_sections(
    sections: List[Dict[str, Any]],
    document_id: str,
    book_metadata: Dict[str, Any],
    blob_url: str
) -> List[Dict[str, Any]]:
    """
    Process sections into consolidated chunks with search documents.
    
    Args:
        sections: List of sections with content
        document_id: Document identifier
        book_metadata: Book metadata dictionary
        blob_url: URL to the source file
        
    Returns:
        List of chunk data dictionaries ready for embedding generation
    """
    from helper import epub_text_utils
    
    chunks_to_process = []
    chunk_order_within_book = 0
    
    def collect_chunks(section, parent_titles=None):
        """Recursively collect chunks from sections."""
        nonlocal chunk_order_within_book
        
        if parent_titles is None:
            parent_titles = []
        
        section_title = section.get("title", "Untitled")
        current_path = parent_titles + [section_title]
        full_title = " > ".join(current_path)
        
        if section.get("selected", False) and section.get("content"):
            content = section.get("content", "")
            section_order = section.get("order", 0)
            
            consolidated_chunks = epub_text_utils.process_section_content(
                content=content,
                section_order=section_order,
                section_title=full_title,
                document_id=document_id,
                max_chunk_tokens=_epub_config.chunk_size_tokens,
                target_consolidation_tokens=_epub_config.chunk_size_tokens
            )
            
            if consolidated_chunks:
                logger.info(
                    "Section '%s' processed into %d chunks",
                    section_title[:30], len(consolidated_chunks)
                )
                
                for chunk in consolidated_chunks:
                    search_doc = epub_text_utils.create_search_document(
                        chunk=chunk,
                        document_id=document_id,
                        book_title=book_metadata.get("title", ""),
                        book_author=book_metadata.get("author", ""),
                        book_publisher=book_metadata.get("publisher", ""),
                        book_published_date=book_metadata.get("date", ""),
                        book_isbn=book_metadata.get("isbn", ""),
                        book_series=book_metadata.get("series", ""),
                        book_subjects=book_metadata.get("subjects", []),
                        book_description=book_metadata.get("description", ""),
                        book_language=book_metadata.get("language", ""),
                        blob_url=blob_url,
                        chunk_order_within_book=chunk_order_within_book
                    )
                    
                    chunk_id = sanitize_id(search_doc["id"])
                    
                    chunks_to_process.append({
                        "chunk_id": chunk_id,
                        "enriched_content": search_doc["text"],
                        "search_doc": search_doc,
                        "section_title": section_title,
                        "token_count": search_doc.get("token_count", 0)
                    })
                    chunk_order_within_book += 1
        
        if section.get("children"):
            for child in section["children"]:
                collect_chunks(child, current_path)
    
    for section in sections:
        collect_chunks(section)
    
    return chunks_to_process


def _generate_embeddings_for_chunks(
    chunks_to_process: List[Dict[str, Any]],
    config_index: Any
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Generate embeddings for chunks in parallel.
    
    Args:
        chunks_to_process: List of chunk data dictionaries
        config_index: Indexing configuration
        
    Returns:
        Tuple of (indexed_docs with embeddings, errors)
    """
    from helper import embedding_utils
    
    indexed_docs = []
    errors = []
    processed_count = 0
    
    def generate_single_embedding(chunk_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate embedding for a single chunk."""
        try:
            embedding = embedding_utils.generate_embedding(
                text=chunk_data["enriched_content"],
                deployment_name=config_index.azure_openai_embedding_deployment_name,
                azure_endpoint=config_index.azure_openai_endpoint
            )
            search_doc = chunk_data["search_doc"].copy()
            search_doc["text_vector"] = embedding
            return {"search_doc": search_doc, "error": None}
        except Exception as e:
            return {
                "id": chunk_data["chunk_id"],
                "error": str(e),
                "section_title": chunk_data["section_title"]
            }
    
    async def generate_embeddings_parallel():
        """Generate embeddings in parallel with semaphore limit."""
        nonlocal processed_count
        semaphore = asyncio.Semaphore(_epub_config.embedding_concurrency)
        loop = asyncio.get_event_loop()
        
        async def process_with_semaphore(chunk_data):
            nonlocal processed_count
            async with semaphore:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    result = await loop.run_in_executor(
                        executor,
                        generate_single_embedding,
                        chunk_data
                    )
                processed_count += 1
                if processed_count % 10 == 0:
                    logger.info("Generated embeddings: %d/%d",
                               processed_count, len(chunks_to_process))
                return result
        
        tasks = [process_with_semaphore(chunk) for chunk in chunks_to_process]
        return await asyncio.gather(*tasks)
    
    logger.info("Starting parallel embedding generation (concurrency: %d)...",
               _epub_config.embedding_concurrency)
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    results = loop.run_until_complete(generate_embeddings_parallel())
    
    for result in results:
        if result.get("error"):
            errors.append(result)
            logger.error("Failed to generate embedding for chunk %s: %s",
                        result.get("id", "unknown"), result["error"])
        else:
            indexed_docs.append(result["search_doc"])
    
    return indexed_docs, errors


def _delete_existing_document_chunks(
    document_id: str,
    chunks_container: Any,
    config_index: Any
) -> tuple[int, int]:
    """
    Delete existing chunks for a document from CosmosDB and Search Index.
    
    Args:
        document_id: Document identifier
        chunks_container: CosmosDB chunks container
        config_index: Indexing configuration
        
    Returns:
        Tuple of (cosmos_deleted_count, search_deleted_count)
    """
    logger.info("=" * 60)
    logger.info("DELETING EXISTING CHUNKS")
    logger.info("=" * 60)
    logger.info("Document ID: %s", document_id)
    
    # Delete from CosmosDB
    deleted_cosmos_count = 0
    try:
        query = "SELECT c.id FROM c WHERE c.document_id = @document_id"
        parameters = [{"name": "@document_id", "value": document_id}]
        existing_chunks = list(chunks_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        for chunk in existing_chunks:
            try:
                chunks_container.delete_item(item=chunk["id"], partition_key=chunk["id"])
                deleted_cosmos_count += 1
            except Exception as del_err:
                logger.warning("Failed to delete chunk %s from CosmosDB: %s", chunk["id"], del_err)
        
        logger.info("Deleted %d existing chunks from CosmosDB", deleted_cosmos_count)
    except Exception as e:
        logger.warning("Error querying existing chunks from CosmosDB: %s", e)
    
    # Delete from Search Index
    epub_index_name = _epub_config.search_index_name
    deleted_search_count = 0
    try:
        if config_index.search_endpoint:
            from azure.search.documents import SearchClient
            from helper.credential import get_search_credential

            search_cred = get_search_credential()

            search_client = SearchClient(
                endpoint=config_index.search_endpoint,
                index_name=epub_index_name,
                credential=search_cred
            )
            
            results = search_client.search(
                search_text="*",
                filter=f"record_id eq '{document_id}'",
                select=["id"],
                top=10000
            )
            
            chunk_ids_to_delete = [doc["id"] for doc in results]
            
            if chunk_ids_to_delete:
                batch_size = 500
                for i in range(0, len(chunk_ids_to_delete), batch_size):
                    batch = [{"id": cid} for cid in chunk_ids_to_delete[i:i + batch_size]]
                    search_client.delete_documents(documents=batch)
                    deleted_search_count += len(batch)
                
            logger.info("Deleted %d existing chunks from search index '%s'",
                       deleted_search_count, epub_index_name)
    except Exception as e:
        logger.warning("Error deleting existing chunks from search index: %s", e)
    
    logger.info("Cleanup complete: %d CosmosDB, %d search index",
               deleted_cosmos_count, deleted_search_count)
    logger.info("=" * 60)
    
    return deleted_cosmos_count, deleted_search_count


def _write_chunks_to_cosmosdb(
    indexed_docs: List[Dict[str, Any]],
    document_id: str,
    chunks_container: Any
) -> int:
    """
    Write chunks to CosmosDB.
    
    Args:
        indexed_docs: List of documents with embeddings
        document_id: Document identifier
        chunks_container: CosmosDB chunks container
        
    Returns:
        Number of chunks successfully written
    """
    logger.info("=" * 60)
    logger.info("WRITING TO COSMOS DB (SOURCE OF TRUTH)")
    logger.info("=" * 60)
    logger.info("Container: epub-chunks")
    logger.info("Document ID: %s", document_id)
    logger.info("Chunks to write: %d", len(indexed_docs))
    
    chunks_written = 0
    
    for idx, search_doc in enumerate(indexed_docs):
        chunk_id = sanitize_id(search_doc.get("id", f"{document_id}_chunk_{idx}"))
        chunk_doc = {
            # Primary key
            "id": chunk_id,
            "chunk_name": search_doc.get("chunk_name", chunk_id),
            
            # Book metadata
            "book_title": search_doc.get("book_title", ""),
            "book_authors": search_doc.get("book_authors", ""),
            "book_publisher": search_doc.get("book_publisher", ""),
            "book_description": search_doc.get("book_description", ""),
            "book_language": search_doc.get("book_language", ""),
            "book_subjects": search_doc.get("book_subjects", []),
            "book_published_date": search_doc.get("book_published_date", ""),
            "book_isbn": search_doc.get("book_isbn", ""),
            
            # Chapter/section metadata
            "chapter_id": search_doc.get("chapter_id", ""),
            "chapter_title": search_doc.get("chapter_title", ""),
            "paragraph_ids": search_doc.get("paragraph_ids", []),
            
            # Content
            "text": search_doc.get("text", ""),
            
            # Chunk metadata
            "token_count": search_doc.get("token_count", 0),
            "overlap_previous": search_doc.get("overlap_previous", False),
            "overlap_next": search_doc.get("overlap_next", False),
            "chunk_order_within_chapter": search_doc.get("chunk_order_within_chapter", 0),
            "chunk_order_within_book": search_doc.get("chunk_order_within_book", idx),
            
            # Vector embedding
            "text_vector": search_doc.get("text_vector", []),
            
            # Additional metadata
            "document_id": document_id,
            "blob_url": search_doc.get("blob_url", ""),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            chunks_container.upsert_item(chunk_doc)
            chunks_written += 1
        except Exception as chunk_err:
            logger.error("Failed to write chunk %s: %s", chunk_id, str(chunk_err))
    
    logger.info("Chunks written: %d/%d", chunks_written, len(indexed_docs))
    logger.info("=" * 60)
    
    return chunks_written


def _shape_for_search(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform Cosmos DB document to search index schema.
    
    Includes all fields defined in create_epub_index.py.
    Reference: book_processing/09_update_book_index.py
    """
    book_authors = item.get("book_authors", "")
    if isinstance(book_authors, list):
        book_authors = ", ".join(book_authors)
    
    book_subjects = item.get("book_subjects", [])
    subjects_str = ", ".join(book_subjects) if isinstance(book_subjects, list) else str(book_subjects)
    
    return {
        # Primary key
        "id": item.get("id"),
        
        # Content for full-text + semantic search
        "text": item.get("text", ""),
        
        # Vector field for vector search
        "text_vector": item.get("text_vector", []),
        
        # Metadata fields (searchable) - matches book-idx schema
        "book_title": item.get("book_title", ""),
        "book_authors": book_authors,
        "chapter_title": item.get("chapter_title", ""),
        
        # Metadata fields (not searchable)
        "book_publisher": item.get("book_publisher", ""),
        "book_published_date": item.get("book_published_date", ""),
        "book_isbn": item.get("book_isbn", ""),
        "book_subjects": subjects_str,
        
        # Debugging fields
        "chunk_name": item.get("chunk_name", ""),
        "paragraph_ids": item.get("paragraph_ids", []),
        
        # Additional fields from book_processing reference (06_upload_to_cosmos.py)
        "book_description": item.get("book_description", ""),
        "book_language": item.get("book_language", ""),
        "chapter_id": item.get("chapter_id", ""),
        "token_count": item.get("token_count", 0),
        "overlap_previous": item.get("overlap_previous", False),
        "overlap_next": item.get("overlap_next", False),
        "chunk_order_within_chapter": item.get("chunk_order_within_chapter", 0),
        "chunk_order_within_book": item.get("chunk_order_within_book", 0),
        
        # EPUB-specific fields
        "record_id": item.get("document_id", ""),  # Note: stored as document_id in CosmosDB
        "blob_url": item.get("blob_url", ""),
    }


def _build_search_index_from_cosmosdb(
    document_id: str,
    chunks_container: Any,
    config_index: Any
) -> int:
    """
    Build search index from CosmosDB chunks.
    
    Args:
        document_id: Document identifier
        chunks_container: CosmosDB chunks container
        config_index: Indexing configuration
        
    Returns:
        Number of documents indexed
    """
    from helper import search_utils
    
    epub_index_name = _epub_config.search_index_name
    
    logger.info("=" * 60)
    logger.info("BUILDING SEARCH INDEX FROM COSMOS DB")
    logger.info("=" * 60)
    logger.info("Source: CosmosDB epub-chunks container")
    logger.info("Target: Search index '%s'", epub_index_name)
    
    query = "SELECT * FROM c WHERE c.document_id = @document_id AND IS_DEFINED(c.text_vector)"
    parameters = [{"name": "@document_id", "value": document_id}]
    
    cosmos_chunks = list(chunks_container.query_items(
        query=query,
        parameters=parameters,
        enable_cross_partition_query=True
    ))
    
    logger.info("Retrieved %d chunks from CosmosDB", len(cosmos_chunks))
    
    search_docs = [_shape_for_search(chunk) for chunk in cosmos_chunks]
    
    search_utils.index_documents(
        document_id=document_id,
        endpoint=config_index.search_endpoint,
        index_name=epub_index_name,
        documents=search_docs,
    )
    
    logger.info("Indexed %d documents to '%s'", len(search_docs), epub_index_name)
    
    for doc_item in search_docs[:3]:
        title = doc_item.get("book_title", "") + " - " + doc_item.get("chapter_title", "")
        logger.info("  - %s (chunk: %s)", title[:50], doc_item.get("id", ""))
    if len(search_docs) > 3:
        logger.info("  ... and %d more", len(search_docs) - 3)
    
    logger.info("=" * 60)
    
    return len(search_docs)


# ============================================================
# Main Ingestion Function
# ============================================================

def process_epub_ingest(document_id: str) -> None:
    """
    Ingest pre-extracted filtered text from an EPUB document into the search index.
    
    This function uses ALREADY EXTRACTED filtered text (from the validate step):
    1. Downloads pre-extracted filtered text from blob storage
    2. Creates chunks with book metadata using sentence-aware splitting
    3. Consolidates chunks with sentence overlaps
    4. Generates embeddings in parallel (semaphore limit: 20)
    5. Indexes documents in Azure AI Search 
    
    The document must have status 'processing' (approved after validation).
    
    Args:
        document_id: The EPUB document ID in CosmosDB
    """
    from helper.config import IndexingConfig
    
    logger.info("Starting EPUB ingestion for document: %s", document_id)
    logger.info(
        "Chunking config: chunk_size=%d tokens, overlap=%d tokens, concurrency=%d",
        _epub_config.chunk_size_tokens,
        _epub_config.chunk_overlap_tokens,
        _epub_config.embedding_concurrency
    )
    
    container = get_epub_container()
    config_index = IndexingConfig.from_env()
    
    try:
        doc = container.read_item(item=document_id, partition_key=document_id)
    except Exception as e:
        logger.error("EPUB document not found: %s", document_id)
        raise ValueError(f"EPUB document not found: {document_id}") from e
    
    if doc.get("status") != "processing":
        logger.warning("EPUB document %s not in processing status (current: %s)",
                      document_id, doc.get("status"))
    
    try:
        # Download pre-extracted filtered text
        filtered_text_url = doc.get("filtered_text_url")
        if not filtered_text_url:
            raise ValueError("No filtered_text_url found - document must go through extraction step first")
        
        logger.info("Using pre-extracted filtered text from: %s", filtered_text_url)
        sections_with_content = download_extracted_text(filtered_text_url)
        selected_count = _count_selected_sections(sections_with_content)
        logger.info("Downloaded %d sections with content", len(sections_with_content))
        
        # Get book metadata
        metadata = doc.get("metadata", {})
        blob_url = doc.get("blob_url", "")
        
        # Step 1: Process sections into chunks
        logger.info("Phase 1: Processing sections into chunks...")
        chunks_to_process = _collect_chunks_from_sections(
            sections=sections_with_content,
            document_id=document_id,
            book_metadata=metadata,
            blob_url=blob_url
        )
        logger.info("Collected %d chunks for embedding generation", len(chunks_to_process))
        
        if not chunks_to_process:
            raise ValueError("No content extracted from selected sections")
        
        # Step 2: Generate embeddings
        logger.info("Phase 2: Generating embeddings...")
        indexed_docs, errors = _generate_embeddings_for_chunks(chunks_to_process, config_index)
        
        if errors:
            logger.warning("Failed to generate %d embeddings out of %d",
                          len(errors), len(chunks_to_process))
        
        if not indexed_docs:
            raise ValueError(f"All embedding generations failed: {len(errors)} errors")
        
        logger.info("Successfully generated %d embeddings (%d failed)",
                   len(indexed_docs), len(errors))
        
        total_tokens = sum(d.get("token_count", 0) for d in indexed_docs)
        
        # Step 3: Delete existing chunks
        chunks_container = get_epub_chunks_container()
        _delete_existing_document_chunks(document_id, chunks_container, config_index)
        
        # Step 4: Write chunks to CosmosDB
        chunks_written = _write_chunks_to_cosmosdb(indexed_docs, document_id, chunks_container)
        
        # Step 5: Build search index from CosmosDB
        search_docs_count = _build_search_index_from_cosmosdb(
            document_id, chunks_container, config_index
        )
        
        # Final verification and status update
        logger.info("=" * 60)
        logger.info("INGESTION COMPLETE")
        logger.info("=" * 60)
        logger.info("CosmosDB chunks: %d", chunks_written)
        logger.info("Search index docs: %d", search_docs_count)
        logger.info("=" * 60)
        
        additional_fields = {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "ingested_sections": selected_count,
            "ingested_chunks": chunks_written,
            "ingested_word_count": total_tokens
        }
        
        update_epub_status(container, document_id, "completed", additional_fields=additional_fields)
        logger.info(
            "Successfully ingested EPUB %s: %d sections, %d chunks, ~%d tokens",
            document_id, selected_count, len(indexed_docs), total_tokens
        )
        
    except Exception as e:
        logger.exception("Failed to ingest EPUB %s: %s", document_id, str(e))
        update_epub_status(container, document_id, "error", error_message=str(e))
        raise


def sanitize_id(doc_id: str) -> str:
    """Ensure the document ID has only valid characters for Azure Search."""
    return re.sub(r"[^a-zA-Z0-9_\-=]", "_", doc_id)


def process_epub_delete(document_id: str) -> None:
    """
    Delete an EPUB document completely - from search index, CosmosDB, and blob storage.
    
    This is called via the queue when a user deletes a document.
    
    Args:
        document_id: The EPUB document ID to delete
    """
    from helper.config import get_indexing_config
    
    logger.info("Starting deletion process for EPUB document: %s", document_id)
    
    container = get_epub_container()
    config_index = get_indexing_config()
    
    # Get document info before deletion (for logging)
    doc = None
    try:
        doc = container.read_item(item=document_id, partition_key=document_id)
        logger.info("Found document to delete: %s (status: %s)", 
                   doc.get("filename", "unknown"), doc.get("status", "unknown"))
    except Exception as e:
        logger.warning("Document %s not found in CosmosDB (may already be deleted): %s", 
                      document_id, str(e))
    
    # Step 1: Delete chunks from CosmosDB (epub-chunks container)
    deleted_cosmos_chunks = 0
    try:
        chunks_container = get_epub_chunks_container()
        
        # Query all chunks for this document
        query = "SELECT c.id FROM c WHERE c.document_id = @document_id"
        parameters = [{"name": "@document_id", "value": document_id}]
        
        chunk_items = list(chunks_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        logger.info("Found %d chunks in CosmosDB for document %s", len(chunk_items), document_id)
        
        # Delete each chunk
        for chunk in chunk_items:
            try:
                chunk_id = chunk["id"]
                chunks_container.delete_item(item=chunk_id, partition_key=chunk_id)
                deleted_cosmos_chunks += 1
            except Exception as chunk_err:
                logger.error("Failed to delete chunk %s: %s", chunk.get("id"), str(chunk_err))
        
        logger.info("Deleted %d chunks from CosmosDB for document %s", deleted_cosmos_chunks, document_id)
        
    except Exception as e:
        logger.error("Failed to delete chunks from CosmosDB: %s", str(e))
        # Continue with other deletions even if this fails
    
    # Step 2: Delete from search index
    deleted_search_chunks = 0
    try:
        epub_index_name = _epub_config.search_index_name
        
        if config_index.search_endpoint:
            from azure.search.documents import SearchClient
            from helper.search_document_util import SearchDocumentReader
            from helper.credential import get_search_credential

            search_cred = get_search_credential()

            # Use SearchDocumentReader to get all chunk IDs
            doc_reader = SearchDocumentReader(
                endpoint=config_index.search_endpoint,
                index_name=epub_index_name,
            )
            chunk_ids = doc_reader.get_all_chunks_for_document(document_id)
            
            if chunk_ids:
                # Delete in batches using SearchClient
                client = SearchClient(
                    endpoint=config_index.search_endpoint,
                    index_name=epub_index_name,
                    credential=search_cred
                )
                batch_size = 500
                for i in range(0, len(chunk_ids), batch_size):
                    batch = [{"id": cid} for cid in chunk_ids[i:i + batch_size]]
                    client.delete_documents(documents=batch)
                    logger.info("Deleted batch of %d chunks from search index", len(batch))
                
                deleted_search_chunks = len(chunk_ids)
            
            logger.info("Deleted %d chunks from search index '%s' for document %s", 
                       deleted_search_chunks, epub_index_name, document_id)
        else:
            logger.warning("Search endpoint not configured, skipping search index cleanup")
            
    except Exception as e:
        logger.error("Failed to delete from search index: %s", str(e))
        # Continue with blob and document deletion even if search fails
    
    # Step 3: Delete from blob storage
    blobs_deleted = 0
    try:
        blob_service = get_blob_service_client()
        container_client = blob_service.get_container_client(_epub_config.storage_container_name)
        
        # List and delete all blobs with the document_id prefix
        blob_list = container_client.list_blobs(name_starts_with=f"{document_id}/")
        for blob in blob_list:
            container_client.delete_blob(blob.name)
            blobs_deleted += 1
            logger.debug("Deleted blob: %s", blob.name)
        
        logger.info("Deleted %d blobs from storage for document %s", blobs_deleted, document_id)
    except Exception as e:
        logger.error("Failed to delete blobs for document %s: %s", document_id, str(e))
        # Continue with document deletion even if blob deletion fails
    
    # Step 4: Delete document from CosmosDB
    try:
        container.delete_item(item=document_id, partition_key=document_id)
        logger.info("Deleted document %s from CosmosDB", document_id)
    except Exception as e:
        logger.error("Failed to delete document %s from CosmosDB: %s", document_id, str(e))
        raise
    
    logger.info(
        "Successfully deleted EPUB document %s: %d cosmos chunks, %d search chunks, %d blobs removed",
        document_id, deleted_cosmos_chunks, deleted_search_chunks, blobs_deleted
    )
