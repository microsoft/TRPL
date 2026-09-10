"""
Cosmos DB client for the ``digital-items`` container in ``contentdb``.

Stores ingested content from the three TRPL Digital Resources:
    - Theodore Roosevelt Cyclopedia  (source = "tr-cyclopedia")
  - Moore Chronology               (source = "moore-chronology")
  - Digitized Genealogy & Papers   (source = "genealogy-papers")

Partition key: ``/source``
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos import exceptions
from azure.cosmos.container import ContainerProxy

from .cosmos_client import get_container

logger = logging.getLogger(__name__)

DIGITAL_ITEMS_CONTAINER = "digital-items"


def _container(container_name: Optional[str] = None) -> ContainerProxy:
    name = container_name or os.getenv(
        "COSMOS_DIGITAL_ITEMS_CONTAINER_NAME", DIGITAL_ITEMS_CONTAINER
    )
    db = os.getenv("COSMOS_DATABASE_NAME", "contentdb")
    return get_container(name, db)


def upsert_digital_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert a single digital item document.

    Every document must have ``id`` and ``source`` (the partition key).
    ``ingested_at`` is added automatically on first insert.
    """
    if "id" not in item or "source" not in item:
        raise ValueError("Digital item must have 'id' and 'source' fields")

    now = datetime.now(timezone.utc).isoformat()
    item.setdefault("ingested_at", now)
    item["updated_at"] = now

    container = _container()
    for attempt in range(8):
        try:
            result = container.upsert_item(body=item)
            logger.info("Upserted digital item %s (source=%s)", item["id"], item["source"])
            return result
        except exceptions.CosmosHttpResponseError as exc:
            if exc.status_code == 429:
                header_wait = float((exc.headers or {}).get("x-ms-retry-after-ms", 0)) / 1000
                backoff = min(2 ** attempt, 30)
                wait = max(header_wait, backoff)
                logger.warning("429 on %s (attempt %d/8), retrying in %.1fs", item["id"], attempt + 1, wait)
                time.sleep(wait)
            else:
                raise
    raise exceptions.CosmosHttpResponseError(message=f"Cosmos 429 persisted after 8 attempts for item {item['id']}")


def upsert_digital_items_batch(items: List[Dict[str, Any]]) -> int:
    """Upsert a batch of digital items. Returns count of successful upserts.
    
    Logs detailed diagnostics for each failure including item ID, source, 
    error code, and error message to aid debugging.
    """
    count = 0
    failed_items = []

    for item in items:
        item_id = item.get("id", "<no-id>")
        source = item.get("source", "<no-source>")

        try:
            upsert_digital_item(item)
            count += 1
        except exceptions.CosmosHttpResponseError as e:
            status_code = getattr(e, "status_code", 0)
            message = getattr(e, "message", str(e))

            error_detail = {
                "item_id": item_id,
                "source": source,
                "status_code": status_code,
                "error": message,
                "retriable": status_code in (429, 503, 408, 500),
            }
            failed_items.append(error_detail)

            if status_code == 403:
                logger.error(
                    "[PERMISSION_ERROR] Failed to upsert item %s (source=%s): HTTP 403 Forbidden. "
                    "Check Managed Identity RBAC permissions on Cosmos container. Error: %s",
                    item_id, source, message
                )
            elif status_code == 400:
                logger.error(
                    "[VALIDATION_ERROR] Failed to upsert item %s (source=%s): HTTP 400 Bad Request. "
                    "Item may have invalid schema or missing required fields. Error: %s. Item: %s",
                    item_id, source, message, item
                )
            elif status_code == 429:
                logger.warning(
                    "[THROTTLE] Failed to upsert item %s (source=%s): HTTP 429 Too Many Requests (throttle). "
                    "Cosmos RU limit exceeded. Error: %s",
                    item_id, source, message
                )
            elif status_code == 404:
                logger.error(
                    "[NOT_FOUND] Failed to upsert item %s (source=%s): HTTP 404 Not Found. "
                    "Container, database, or account may not exist. Error: %s",
                    item_id, source, message
                )
            elif status_code in (500, 503, 408):
                logger.warning(
                    "[RETRIABLE_ERROR] Failed to upsert item %s (source=%s): HTTP %d. "
                    "Service temporarily unavailable, may retry. Error: %s",
                    item_id, source, status_code, message
                )
            else:
                logger.error(
                    "[HTTP_ERROR] Failed to upsert item %s (source=%s): HTTP %d. Error: %s",
                    item_id, source, status_code, message
                )
        except ValueError as e:
            logger.error(
                "[SCHEMA_ERROR] Failed to upsert item %s (source=%s): Missing required fields. "
                "Item must have 'id' and 'source'. Error: %s",
                item_id, source, str(e)
            )
            failed_items.append({
                "item_id": item_id,
                "source": source,
                "error_type": "SchemaValidation",
                "error": str(e),
                "retriable": False,
            })
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "[UNEXPECTED_ERROR] Failed to upsert item %s (source=%s): %s: %s",
                item_id, source, type(e).__name__, str(e)
            )
            failed_items.append({
                "item_id": item_id,
                "source": source,
                "error_type": type(e).__name__,
                "error": str(e),
                "retriable": False,
            })

        time.sleep(0.05)  # brief pause between upserts to stay within Cosmos RU budget

    if failed_items:
        logger.warning(
            "Batch upsert completed with failures: %d/%d items succeeded, %d failed",
            count, len(items), len(failed_items)
        )
        retriable_count = sum(1 for f in failed_items if f.get("retriable"))
        if retriable_count > 0:
            logger.warning("  %d failures are retriable (throttle, timeout, service errors)", retriable_count)

        # Log summary of failures by error type
        error_types = {}
        for f in failed_items:
            error_key = f.get("status_code") or f.get("error_type", "Unknown")
            if error_key not in error_types:
                error_types[error_key] = []
            error_types[error_key].append(f.get("item_id"))

        for error_key, item_ids in error_types.items():
            logger.warning("  %s: %d items failed (%s)", error_key, len(item_ids), ", ".join(item_ids[:5]))

    return count


def query_digital_items(
    source: str,
    item_type: Optional[str] = None,
    max_items: int = 100,
) -> List[Dict[str, Any]]:
    """Query digital items by source and optional type."""
    container = _container()
    if item_type:
        query = "SELECT * FROM c WHERE c.source = @source AND c.item_type = @item_type ORDER BY c.title"
        params = [
            {"name": "@source", "value": source},
            {"name": "@item_type", "value": item_type},
        ]
    else:
        query = "SELECT * FROM c WHERE c.source = @source ORDER BY c.title"
        params = [{"name": "@source", "value": source}]

    return list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=False,
            max_item_count=max_items,
            partition_key=source,
        )
    )


def get_digital_item(item_id: str, source: str) -> Optional[Dict[str, Any]]:
    """Read a single digital item by id and source (partition key)."""
    container = _container()
    try:
        return container.read_item(item=item_id, partition_key=source)
    except exceptions.CosmosResourceNotFoundError:
        return None


def count_digital_items(source: str) -> int:
    """Count items for a given source."""
    container = _container()
    query = "SELECT VALUE COUNT(1) FROM c WHERE c.source = @source"
    params = [{"name": "@source", "value": source}]
    result = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=False,
            partition_key=source,
        )
    )
    return result[0] if result else 0
