"""Service for querying digital resource items from the digital-items Cosmos container."""

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.identity import DefaultAzureCredential

from core.config import settings
from services.keyvault_reference import is_keyvault_reference, resolve_keyvault_reference

logger = logging.getLogger(__name__)

_client: Optional[CosmosClient] = None
_container: Optional[ContainerProxy] = None

T = TypeVar("T")


def _retry(fn: Callable[[], T], max_retries: int = 2, delay: float = 1.0) -> T:
    """Retry a Cosmos operation on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except exceptions.CosmosResourceNotFoundError:
            # Not transient — resource doesn't exist, don't retry
            raise
        except exceptions.CosmosHttpResponseError as exc:
            # 4xx errors (except 429 Too Many Requests) are not transient — fail immediately
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
                raise
            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    "Cosmos operation failed (attempt %d/%d): %s. Retrying...",
                    attempt + 1, max_retries + 1, str(exc)[:200]
                )
                time.sleep(delay * (attempt + 1))
            else:
                raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    "Cosmos operation failed (attempt %d/%d): %s. Retrying...",
                    attempt + 1, max_retries + 1, str(exc)[:200]
                )
                time.sleep(delay * (attempt + 1))
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _get_container() -> ContainerProxy:
    global _client, _container
    if _container is not None:
        return _container

    endpoint = settings.cosmos_db_digital_items_endpoint
    if not endpoint:
        raise ValueError("Digital items Cosmos endpoint not configured")

    container_name = settings.cosmos_db_digital_items_container
    if is_keyvault_reference(container_name):
        try:
            container_name = resolve_keyvault_reference(container_name)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            alias = (os.getenv("COSMOS_DIGITAL_ITEMS_CONTAINER_NAME") or "").strip()
            fallback = alias or "digital-items"
            logger.warning(
                "Could not resolve COSMOS_DB_DIGITAL_ITEMS_CONTAINER Key Vault reference; "
                "falling back to '%s'. Resolution error: %s",
                fallback,
                exc,
            )
            container_name = fallback

    if not container_name:
        raise ValueError(
            f"COSMOS_DB_DIGITAL_ITEMS_CONTAINER is not set or is an unresolved Key Vault reference: {container_name!r}. "
            "Check that the App Service Key Vault access policy is in place and the secret exists."
        )

    credential = DefaultAzureCredential(
        exclude_interactive_browser_credential=True
    )

    _client = CosmosClient(
        url=endpoint,
        credential=credential,
        connection_mode=settings.cosmos_db_connection_mode,
    )
    db = _client.get_database_client(settings.cosmos_db_digital_items_database)
    _container = db.get_container_client(container_name)
    logger.info(
        "Digital items container initialized: %s/%s",
        settings.cosmos_db_digital_items_database,
        container_name,
    )
    return _container


def get_digital_items_container() -> ContainerProxy:
    """Public accessor for the digital-items Cosmos container proxy."""
    return _get_container()


def list_items_by_source(
    source: str,
    item_type: Optional[str] = None,
    max_items: int = 200,
) -> List[Dict[str, Any]]:
    container = _get_container()
    if item_type:
        query = (
            "SELECT c.id, c.source, c.item_type, c.title, c.hub_label, "
            "c.description, c.date_range, c.source_url, c.source_id, "
            "c.module_id, c.parent_module_id, c.parent_id, c.pattern, "
            "c.entry_count, c.section_count, c.toc_entry_count, c.status, "
            "c.primary_pdf_url, c.thumbnail_url, c.page_count, c.metadata, c.provenance, c.files, "
            "c.extraction_summary, c.ingested_at, "
            'c.audio_url, c.image_url, c.has_audio, c["order"], '
            "c.ocr_accuracy, c.ocr_status, c.ocr_page_count "
            "FROM c WHERE c.source = @source AND c.item_type = @item_type "
            "AND c.item_type != 'page' "
            "ORDER BY c.title"
        )
        params = [
            {"name": "@source", "value": source},
            {"name": "@item_type", "value": item_type},
        ]
    else:
        query = (
            "SELECT c.id, c.source, c.item_type, c.title, c.hub_label, "
            "c.description, c.date_range, c.source_url, c.source_id, "
            "c.module_id, c.parent_module_id, c.parent_id, c.pattern, "
            "c.entry_count, c.section_count, c.toc_entry_count, c.status, "
            "c.primary_pdf_url, c.thumbnail_url, c.page_count, c.metadata, c.provenance, c.files, "
            "c.extraction_summary, c.ingested_at, "
            'c.audio_url, c.audio_files, c.image_url, c.image_alt, c.images, c.has_audio, c["order"], '
            "c.plain_text, c.plain_text_blob_url, c.html_content, c.linked_topics, c.publish_status, "
            "c.document_sections, c.content_url, "
            "c.ocr_accuracy, c.ocr_status, c.ocr_page_count "
            "FROM c WHERE c.source = @source AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page') "
            "ORDER BY c.title"
        )
        params = [{"name": "@source", "value": source}]

    return list(
        _retry(lambda: list(
            container.query_items(
                query=query,
                parameters=params,
                partition_key=source,
                max_item_count=max_items,
            )
        ))
    )


def get_item(item_id: str, source: str) -> Optional[Dict[str, Any]]:
    container = _get_container()
    try:
        return _retry(lambda: container.read_item(item=item_id, partition_key=source))
    except exceptions.CosmosResourceNotFoundError:
        return None


def get_item_full(item_id: str, source: str) -> Optional[Dict[str, Any]]:
    """Read with all fields including document_sections, cyclopedia_entries, etc."""
    container = _get_container()
    try:
        return _retry(lambda: container.read_item(item=item_id, partition_key=source))
    except exceptions.CosmosResourceNotFoundError:
        return None


def count_items_by_source(source: str) -> int:
    container = _get_container()
    query = (
        "SELECT VALUE COUNT(1) FROM c WHERE c.source = @source "
        "AND (NOT IS_DEFINED(c.item_type) OR c.item_type != 'page')"
    )
    params = [{"name": "@source", "value": source}]

    def _do():
        result = list(
            container.query_items(
                query=query, parameters=params, partition_key=source,
            )
        )
        return result[0] if result else 0

    try:
        return _retry(_do)
    except exceptions.CosmosResourceNotFoundError:
        logger.warning("Container or partition not found for source '%s'", source)
        return 0
    except Exception as exc:
        logger.error("Failed to count items for source '%s': %s", source, exc)
        return 0


def get_page_item(
    parent_id: str,
    source: str,
    page_number: int,
) -> Optional[Dict[str, Any]]:
    """Read a single page document for a Moore chronology volume."""
    container = _get_container()
    page_id = f"{parent_id}-p{page_number:04d}"
    try:
        return container.read_item(item=page_id, partition_key=source)
    except exceptions.CosmosResourceNotFoundError:
        query = (
            "SELECT * FROM c WHERE c.source = @source AND c.parent_id = @parent_id "
            "AND c.page_number = @page_number"
        )
        params = [
            {"name": "@source", "value": source},
            {"name": "@parent_id", "value": parent_id},
            {"name": "@page_number", "value": page_number},
        ]
        results = list(
            container.query_items(
                query=query,
                parameters=params,
                partition_key=source,
                max_item_count=1,
            )
        )
        return results[0] if results else None


def get_all_sources_summary() -> Dict[str, int]:
    """Return item counts keyed by source, querying each partition individually."""
    sources = ["tr-cyclopedia", "moore-chronology", "genealogy-papers"]
    result: Dict[str, int] = {}
    for source in sources:
        count = count_items_by_source(source)
        if count > 0:
            result[source] = count
    return result
