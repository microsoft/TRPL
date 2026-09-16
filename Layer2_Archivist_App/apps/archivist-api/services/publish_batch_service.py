# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Query and update bulk / retry publish batch records (Cosmos container)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from azure.cosmos import exceptions

from core.config import settings
from services.cosmos_service import get_cosmos_client


class PublishBatchService:
    """Read/write publish batch audit documents."""

    def __init__(self) -> None:
        name = settings.cosmos_db_bulk_publish_batches_container_name
        if not name or not settings.cosmos_db_database_name:
            raise ValueError("Bulk publish batches container is not configured")
        db = get_cosmos_client().get_database_client(settings.cosmos_db_database_name)
        self._container = db.get_container_client(name)

    def list_recent_batches(self, limit: int = 100) -> List[Dict[str, Any]]:
        q = "SELECT * FROM c"
        items = list(
            self._container.query_items(
                query=q,
                enable_cross_partition_query=True,
            )
        )
        items.sort(key=lambda d: d.get("created_at") or "", reverse=True)
        return items[: max(1, min(limit, 500))]

    def find_batches_with_record_id(self, record_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        rid = (record_id or "").strip()
        if not rid:
            return []
        q = "SELECT * FROM c WHERE ARRAY_CONTAINS(c.document_ids, @rid)"
        params = [{"name": "@rid", "value": rid}]
        items = list(
            self._container.query_items(
                query=q,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        items.sort(key=lambda d: d.get("created_at") or "", reverse=True)
        return items[: max(1, min(limit, 100))]

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        bid = (batch_id or "").strip()
        if not bid:
            return None
        try:
            return dict(self._container.read_item(item=bid, partition_key=bid))
        except exceptions.CosmosResourceNotFoundError:
            return None


_publish_batch_service: Optional[PublishBatchService] = None


def get_publish_batch_service() -> PublishBatchService:
    global _publish_batch_service
    if _publish_batch_service is None:
        _publish_batch_service = PublishBatchService()
    return _publish_batch_service
