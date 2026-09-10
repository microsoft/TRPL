"""
Store metadata for bulk/retry publish jobs (Cosmos audit).

One document per durable orchestration: each ProcessDocumentBatchActivity upsert merges
its chunk into the same row (id = orchestration_instance_id). Processing still uses
batch_size-sized chunks in the orchestrator; history shows one logical ingestion batch.

Cosmos container: COSMOS_DB_BULK_PUBLISH_BATCHES_CONTAINER_NAME (default: bulk-publish-batches).
Partition key path: /id (document id equals batch_id / orchestration instance id).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos import exceptions

from helper.config import CosmosDBConfig
from helper.cosmos_client import CosmosDBClient, execute_with_retry

CONTAINER_ENV = "COSMOS_DB_BULK_PUBLISH_BATCHES_CONTAINER_NAME"
DEFAULT_CONTAINER = "bulk-publish-batches"

logger = logging.getLogger(__name__)


def _get_container():
    config = CosmosDBConfig.from_env()
    client = CosmosDBClient(config)
    name = os.environ.get(CONTAINER_ENV, DEFAULT_CONTAINER)
    return client._database.get_container_client(name)


def _merge_ordered_id_lists(prev: Optional[List[Any]], new: Optional[List[Any]]) -> List[str]:
    """Concatenate then dedupe, preserving first-seen order."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in (prev or []) + (new or []):
        s = str(raw).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_failed_records(
    prev: Optional[List[Dict[str, Any]]],
    new: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Union by record_id; later entries replace earlier for the same id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for lst in (prev or []), (new or []):
        for row in lst:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("record_id") or "").strip()
            if rid:
                by_id[rid] = row
    return list(by_id.values())


def _merge_successful_details(
    prev: Optional[List[Dict[str, Any]]],
    new: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Union by record_id; later entries replace earlier for the same id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for lst in (prev or []), (new or []):
        for row in lst:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("record_id") or "").strip()
            if rid:
                by_id[rid] = row
    return list(by_id.values())


def record_publish_batch(
    *,
    batch_doc_id: str,
    orchestration_instance_id: str,
    batch_num: int,
    operation_type: str,
    started_by: Optional[str],
    document_ids: List[str],
    successful_record_ids: List[str],
    failed_records: List[Dict[str, Any]],
    successful_record_details: Optional[List[Dict[str, Any]]] = None,
    repository: Optional[str] = None,
    collection: Optional[str] = None,
    correlation_id: Optional[str] = None,
    skipped: bool = False,
) -> None:
    """
    Upsert the publish-batch audit document for this orchestration.

    ``batch_doc_id`` must equal ``orchestration_instance_id`` (one Cosmos row per ingestion run).
    Each activity call merges its document_ids / successes / failures into that row.
    ``batch_num`` is kept as 1 in storage for a single logical batch in history; activity index is only for logs.
    """
    batch_doc_id = (batch_doc_id or "").strip()
    orchestration_instance_id = (orchestration_instance_id or "").strip()
    if not batch_doc_id or not orchestration_instance_id:
        logger.warning("record_publish_batch: missing batch_doc_id or orchestration_instance_id; skip")
        return
    if batch_doc_id != orchestration_instance_id:
        logger.warning(
            "record_publish_batch: batch_doc_id must match orchestration_instance_id for aggregate storage; got %r vs %r",
            batch_doc_id,
            orchestration_instance_id,
        )
        return

    now = datetime.now(timezone.utc).isoformat()
    details_in = successful_record_details or []

    try:
        container = _get_container()
        try:
            existing = container.read_item(item=batch_doc_id, partition_key=batch_doc_id)
        except exceptions.CosmosResourceNotFoundError:
            existing = None

        if isinstance(existing, dict):
            body: Dict[str, Any] = {
                "id": batch_doc_id,
                "batch_id": batch_doc_id,
                "orchestration_instance_id": orchestration_instance_id,
                "batch_num": 1,
                "operation_type": str(existing.get("operation_type") or operation_type),
                "started_by": (existing.get("started_by") or started_by),
                "correlation_id": (existing.get("correlation_id") or correlation_id),
                "created_at": existing.get("created_at") or now,
                "document_ids": _merge_ordered_id_lists(existing.get("document_ids"), document_ids),
                "successful_record_ids": _merge_ordered_id_lists(
                    existing.get("successful_record_ids"), successful_record_ids
                ),
                "successful_record_details": _merge_successful_details(
                    existing.get("successful_record_details"), details_in
                ),
                "failed_records": _merge_failed_records(existing.get("failed_records"), failed_records),
                "skipped": bool(existing.get("skipped")) or bool(skipped),
                "repository": (repository or "").strip() or None,
                "collection": (collection or "").strip() or None,
            }
            if not body.get("repository") and existing.get("repository"):
                body["repository"] = existing.get("repository")
            if not body.get("collection") and existing.get("collection"):
                body["collection"] = existing.get("collection")
            for key in ("unpublish_requested_at", "unpublish_requested_by", "unpublish_completed_at"):
                if existing.get(key) is not None:
                    body[key] = existing[key]
        else:
            body = {
                "id": batch_doc_id,
                "batch_id": batch_doc_id,
                "orchestration_instance_id": orchestration_instance_id,
                "batch_num": 1,
                "operation_type": operation_type,
                "started_by": started_by,
                "correlation_id": correlation_id,
                "created_at": now,
                "document_ids": _merge_ordered_id_lists(None, document_ids),
                "successful_record_ids": _merge_ordered_id_lists(None, successful_record_ids),
                "successful_record_details": _merge_successful_details(None, details_in),
                "failed_records": list(failed_records or []),
                "skipped": bool(skipped),
                "repository": (repository or "").strip() or None,
                "collection": (collection or "").strip() or None,
            }

        execute_with_retry(
            container.upsert_item,
            operation_name=f"bulk_publish_batch_upsert({batch_doc_id})",
            body=body,
        )
        logger.info(
            "Recorded publish batch aggregate %s (%d docs total, %d ok)",
            batch_doc_id,
            len(body.get("document_ids") or []),
            len(body.get("successful_record_ids") or []),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to record publish batch %s: %s", batch_doc_id, exc)


def find_publish_batch_ids_with_successful_record(record_id: str) -> List[str]:
    """
    Cosmos ids of publish-batch audit documents whose ``successful_record_ids`` contain ``record_id``.
    """
    rid = (record_id or "").strip()
    if not rid:
        return []
    try:
        container = _get_container()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("find_publish_batch_ids_with_successful_record: no container: %s", exc)
        return []
    q = "SELECT VALUE c.id FROM c WHERE ARRAY_CONTAINS(c.successful_record_ids, @rid)"
    params = [{"name": "@rid", "value": rid}]
    try:
        rows = list(
            container.query_items(
                query=q,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("find_publish_batch_ids_with_successful_record query failed: %s", exc)
        return []
    out: List[str] = []
    seen: set[str] = set()
    for x in rows:
        s = str(x).strip() if x is not None else ""
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _main_document_is_published(cosmos_client: CosmosDBClient, doc_id: str) -> bool:
    doc = cosmos_client.get_document(doc_id)
    if not doc:
        return False
    st = (doc.get("archivist_status") or "").strip().lower()
    return st == "published"


def maybe_delete_publish_batch_if_all_unpublished(batch_id: str) -> None:
    """
    If every id in the batch's ``successful_record_ids`` is no longer *published* in the main
    container (missing document counts as unpublished), delete the audit row.
    """
    bid = (batch_id or "").strip()
    if not bid:
        return
    try:
        container = _get_container()
        batch = container.read_item(item=bid, partition_key=bid)
    except exceptions.CosmosResourceNotFoundError:
        return
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("maybe_delete_publish_batch_if_all_unpublished: read batch %s: %s", bid, exc)
        return
    succ = batch.get("successful_record_ids")
    if not isinstance(succ, list) or not succ:
        return
    try:
        config = CosmosDBConfig.from_env()
        cosmos_client = CosmosDBClient(config)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("maybe_delete_publish_batch_if_all_unpublished: cosmos client: %s", exc)
        return
    for raw in succ:
        rid = str(raw).strip()
        if not rid:
            continue
        if _main_document_is_published(cosmos_client, rid):
            return
    delete_publish_batch(bid)


def delete_publish_batch(batch_doc_id: str) -> None:
    """
    Remove a publish-batch audit document (e.g. after a successful unpublish-from-batch job).
    Failures are logged only — never raises to callers.
    """
    bid = (batch_doc_id or "").strip()
    if not bid:
        return
    try:
        container = _get_container()
        execute_with_retry(
            container.delete_item,
            operation_name=f"bulk_publish_batch_delete({bid})",
            item=bid,
            partition_key=bid,
        )
        logger.info("Deleted publish batch audit document %s", bid)
    except exceptions.CosmosResourceNotFoundError:
        logger.info("Publish batch audit %s already deleted or missing", bid)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to delete publish batch audit %s: %s", bid, exc)


def should_record_batch(operation_type: Optional[str]) -> bool:
    """Only bulk and retry_failed runs are tracked as publish batches."""
    if not operation_type:
        return False
    return str(operation_type).lower() in ("bulk", "retry_failed")
