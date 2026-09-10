"""Cosmos DB queries for recordsmetadata."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from fastapi import HTTPException

from core.config import settings
from services.azure_credential import get_azure_credential

logger = logging.getLogger(__name__)

_container: Optional[ContainerProxy] = None


def get_records_container() -> ContainerProxy:
    global _container
    if _container is not None:
        return _container

    if not settings.cosmos_db_endpoint:
        logger.error("Cosmos DB endpoint is not configured")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "COSMOS_CONFIG",
                "message": "COSMOS_DB_ENDPOINT or COSMOS_DB_ACCOUNT_NAME must be configured.",
            },
        )

    try:
        if settings.cosmos_db_connection_string:
            if settings.environment != "local":
                raise RuntimeError(
                    "COSMOS_DB_CONNECTION_STRING is only supported in local mode"
                )
            client = CosmosClient.from_connection_string(
                settings.cosmos_db_connection_string
            )
        else:
            client = CosmosClient(
                settings.cosmos_db_endpoint,
                credential=get_azure_credential(),
            )
        database = client.get_database_client(settings.cosmos_db_database_name)
        _container = database.get_container_client(settings.cosmos_db_container_name)
    except exceptions.CosmosHttpResponseError as exc:
        _raise_cosmos_http_error(exc)
    except Exception as exc:
        logger.exception("Failed to initialize Cosmos container client: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "COSMOS_ERROR", "message": "Cosmos DB client initialization failed."},
        ) from exc
    return _container


def check_cosmos_readiness() -> tuple[bool, str | None]:
    """Lightweight readiness probe for health checks."""
    try:
        container = get_records_container()
        list(
            container.query_items(
                query="SELECT VALUE COUNT(1) FROM c OFFSET 0 LIMIT 1",
                enable_cross_partition_query=True,
            )
        )
        return True, None
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        message = detail.get("message", "Cosmos DB unavailable")
        logger.warning("Cosmos readiness check failed: %s", message)
        return False, message
    except Exception as exc:
        logger.warning("Cosmos readiness check failed: %s", exc)
        return False, str(exc)


def _raise_cosmos_http_error(exc: exceptions.CosmosHttpResponseError) -> None:
    message = str(exc.message or exc)
    status_code = exc.status_code or 0
    if status_code in (401, 403):
        logger.warning("Cosmos request rejected (HTTP %s): %s", status_code, message)
    elif status_code == 429:
        logger.warning("Cosmos request throttled (HTTP 429): %s", message)
    else:
        logger.error("Cosmos request failed (HTTP %s): %s", status_code, message)
    lowered = message.lower()
    if exc.status_code == 401 or "not trusted" in lowered:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "COSMOS_AUTH",
                "message": (
                    "Cosmos rejected the Azure AD token. Log in to the TRPL tenant: "
                    "az login --tenant <ENTRA_TENANT_ID> and set ENTRA_TENANT_ID in .env."
                ),
            },
        ) from exc
    if exc.status_code == 403 or "required rbac permissions" in lowered or "readmetadata" in lowered:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COSMOS_RBAC",
                "message": (
                    "This identity is not granted Cosmos DB SQL RBAC on the account. "
                    "Local development requires Cosmos DB Built-in Data Reader "
                    "(00000000-0000-0000-0000-000000000001) at account scope — the same "
                    "role assigned to the content source outbound App Service managed identity. "
                    "Ask a Cosmos administrator to assign that role to your user, or test "
                    "against the deployed App Service instead of localhost."
                ),
            },
        ) from exc
    raise HTTPException(
        status_code=503,
        detail={"code": "COSMOS_ERROR", "message": "Cosmos DB request failed."},
    ) from exc


def query_offset(
    where_clause: str,
    *,
    offset: int,
    limit: int,
    order_by: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    projection: str = "*",
) -> Tuple[List[Dict[str, Any]], int]:
    """OFFSET/LIMIT query. Returns (items for this window, total matching count)."""
    offset = max(0, offset)
    limit = max(1, limit)

    order_clause = f" ORDER BY {order_by}" if order_by else ""
    container = get_records_container()
    items_sql = (
        f"SELECT {projection} FROM c WHERE {where_clause}"
        f"{order_clause} OFFSET {offset} LIMIT {limit}"
    )
    count_sql = f"SELECT VALUE COUNT(1) FROM c WHERE {where_clause}"

    try:
        query_kwargs: Dict[str, Any] = {"enable_cross_partition_query": True}
        if parameters:
            query_kwargs["parameters"] = parameters

        items = list(container.query_items(query=items_sql, **query_kwargs))
        count_rows = list(container.query_items(query=count_sql, **query_kwargs))
        total = int(count_rows[0]) if count_rows else 0
        logger.info(
            "Cosmos page query offset=%d limit=%d returned %d items (total=%d)",
            offset,
            limit,
            len(items),
            total,
        )
        return items, total
    except exceptions.CosmosHttpResponseError as exc:
        _raise_cosmos_http_error(exc)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during Cosmos page query: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "COSMOS_ERROR", "message": "Cosmos DB query failed."},
        ) from exc


def get_record_by_id(record_id: str) -> Optional[Dict[str, Any]]:
    """Return a single record by its source-system id, or None if not found."""
    container = get_records_container()
    sql = (
        "SELECT * FROM c WHERE c.id = @id OR c.record_id = @id "
        "OR c.source_record_id = @id"
    )
    parameters = [{"name": "@id", "value": record_id}]
    try:
        rows = list(
            container.query_items(
                query=sql,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )
    except exceptions.CosmosHttpResponseError as exc:
        _raise_cosmos_http_error(exc)
    return rows[0] if rows else None
