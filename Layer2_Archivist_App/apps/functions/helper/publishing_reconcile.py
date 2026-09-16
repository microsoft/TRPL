# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Reconcile documents stuck in archivist_status=publishing (host crash, lost queue, etc.).

Logic mirrors archivist-api CosmosDBService.list/reconcile_stuck_publishing_documents.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from azure.cosmos import exceptions

from helper.config import CosmosDBConfig
from helper.cosmos_client import CosmosDBClient, execute_with_retry
from helper.statistics_helper import get_statistics_helper

logger = logging.getLogger(__name__)


def _cutoff_iso(threshold_minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)).isoformat()


def list_stuck_publishing_doc_ids(
    cosmos_client: CosmosDBClient, threshold_minutes: int
) -> List[Dict[str, Any]]:
    cutoff = _cutoff_iso(threshold_minutes)
    query = """
        SELECT c.id, c.record_id, c.title, c.archivist_publish_started_at, c.updated_at,
               c.archivist_error_message
        FROM c
        WHERE (IS_DEFINED(c.archivist_status) AND c.archivist_status != null
               AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'publishing')
          AND (
            (IS_DEFINED(c.archivist_publish_started_at) AND c.archivist_publish_started_at != null
             AND IS_STRING(c.archivist_publish_started_at)
             AND c.archivist_publish_started_at < @cutoff)
            OR (
              (NOT IS_DEFINED(c.archivist_publish_started_at) OR c.archivist_publish_started_at = null
               OR c.archivist_publish_started_at = '')
              AND IS_DEFINED(c.updated_at) AND c.updated_at != null AND IS_STRING(c.updated_at)
              AND c.updated_at < @cutoff
            )
          )
    """
    parameters = [{"name": "@cutoff", "value": cutoff}]
    items = list(
        cosmos_client._container.query_items(  # pylint: disable=protected-access
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )
    return items


def reconcile_stuck_publishing(
    threshold_minutes: int,
    action: str = "reviewed",
    reason_suffix: str = "",
) -> Dict[str, Any]:
    """
    action: 'failed' (sets error message) | 'reviewed' (default: silent reset for users)
    """
    if action not in ("failed", "reviewed"):
        raise ValueError("action must be 'failed' or 'reviewed'")
    if threshold_minutes < 1:
        raise ValueError("threshold_minutes must be at least 1")

    enabled = os.environ.get("STUCK_PUBLISHING_RECONCILE_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled:
        logger.info("Stuck publishing reconcile skipped (STUCK_PUBLISHING_RECONCILE_ENABLED=false)")
        return {"skipped": True, "reason": "disabled"}

    config = CosmosDBConfig.from_env()
    cosmos_client = CosmosDBClient(config)
    stats = get_statistics_helper()
    stuck = list_stuck_publishing_doc_ids(cosmos_client, threshold_minutes)
    ts = datetime.now(timezone.utc).isoformat()
    base_msg = None
    if action == "failed":
        base_msg = (
            f"Publishing exceeded {threshold_minutes} minute(s) without completing"
            + (f" ({reason_suffix})" if reason_suffix else "")
        )[:500]

    updated_ids: List[str] = []
    for row in stuck:
        doc_id = row.get("id") or row.get("record_id")
        if not doc_id:
            continue
        try:
            doc = cosmos_client.get_document(doc_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Could not read document %s for reconcile", doc_id)
            continue
        if not doc:
            continue
        status = (doc.get("archivist_status") or "").strip().lower()
        if status != "publishing":
            continue
        old = dict(doc)
        doc["archivist_status"] = "failed" if action == "failed" else "reviewed"
        doc["archivist_publish_started_at"] = None
        doc["updated_at"] = ts
        if action == "failed":
            doc["archivist_error_message"] = base_msg
        else:
            doc["archivist_error_message"] = None
            doc["archivist_approval_method"] = None
        try:
            execute_with_retry(
                cosmos_client._container.replace_item,  # pylint: disable=protected-access
                operation_name=f"reconcile_stuck_publishing({doc_id})",
                item=doc_id,
                body=doc,
            )
            try:
                stats.update_on_document_change(old_doc=old, new_doc=doc)
            except Exception as se:  # pylint: disable=broad-exception-caught
                logger.warning("Statistics update failed for reconcile %s: %s", doc_id, se)
            updated_ids.append(doc_id)
            logger.info(
                "Reconciled stuck publishing: %s -> %s (%s)",
                doc_id,
                doc["archivist_status"],
                action,
            )
        except exceptions.CosmosHttpResponseError as he:
            logger.warning("Cosmos replace failed for %s: %s", doc_id, he.message)

    return {
        "threshold_minutes": threshold_minutes,
        "action": action,
        "matched": len(stuck),
        "updated": len(updated_ids),
        "document_ids": updated_ids,
    }
