# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Full precomputed statistics rebuild after an unpublish batch activity (before publish-batch prune).

Called once per ``UnpublishDocumentBatchActivity`` after parallel per-document unpublishes complete,
so concurrent workers do not run overlapping full rebuilds.

Uses ``ARCHIVIST_API_BASE_URL`` when set (POST ``/api/v1/statistics/rebuild`` with a bearer token from
``DefaultAzureCredential``); otherwise rebuilds
directly from Cosmos. Logic must stay aligned with ``StatisticsService.rebuild_all_statistics``
in ``archivist-api/services/statistics_service.py``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

from helper.azure_bearer_token import get_bearer_token

from helper.config import CosmosDBConfig
from helper.cosmos_document_status import categorize_document_status
from helper.statistics_helper import (
    COLLECTION_STATS_PREFIX,
    DASHBOARD_STATS_ID,
    REPOSITORY_STATS_PREFIX,
    _make_collection_safe_id,
    _sanitize_id,
    empty_collection_status_bucket,
    empty_dashboard_status_totals,
    empty_record_status_counts,
)

logger = logging.getLogger(__name__)


def _accumulate_doc_ocr(bucket: Dict[str, Any], doc: Dict[str, Any]) -> None:
    raw = doc.get("docOcr")
    if raw is None:
        return
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return
    if math.isnan(val):
        return
    bucket["ocrConfidenceSum"] = bucket.get("ocrConfidenceSum", 0.0) + val
    bucket["ocrConfidenceCount"] = bucket.get("ocrConfidenceCount", 0) + 1


def _ocr_fields_for_saved_collection_doc(stats: Dict[str, Any]) -> Dict[str, Any]:
    cnt = max(0, int(stats.get("ocrConfidenceCount", 0)))
    s = float(stats.get("ocrConfidenceSum", 0.0))
    if cnt <= 0:
        return {"avgOcrConfidence": None, "ocrConfidenceSum": 0.0, "ocrConfidenceCount": 0}
    return {
        "avgOcrConfidence": round((s / cnt) * 100.0, 1),
        "ocrConfidenceSum": s,
        "ocrConfidenceCount": cnt,
    }


_STATUS_KEY_MAP = {
    "published": "published",
    "publishing": "publishing",
    "reviewed": "reviewed",
    "error": "errors",
    "pending": "pending",
}

_STATUS_KEY_MAP_TOTAL = {
    "published": "totalPublished",
    "publishing": "totalPublishing",
    "reviewed": "totalReviewed",
    "error": "totalErrors",
    "pending": "totalPending",
}


def _get_status_key(status: str, use_total_prefix: bool = False) -> str:
    key_map = _STATUS_KEY_MAP_TOTAL if use_total_prefix else _STATUS_KEY_MAP
    return key_map.get(status, key_map["pending"])


def _adjust_status_counter(
    stats: Dict[str, Any], status: str, delta: int, *, use_total_prefix: bool = False
) -> None:
    key = _get_status_key(status, use_total_prefix)
    stats[key] = max(0, stats.get(key, 0) + delta)


_DOC_OCR_SELECT = """
                    "docOcr": IIF(IS_DEFINED(c.asset_avg_confidence) AND IS_NUMBER(c.asset_avg_confidence), c.asset_avg_confidence, null)
"""


def _full_projection_query() -> str:
    return (
        """
                SELECT VALUE {
                    "repository": IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository), c.metadata.Repository.label, 'Unknown')),
                        'Unknown'),
                    "collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection), c.metadata.Collection.label, 'Unknown')),
                        'Unknown'),
                    "archivist_status": IIF(IS_DEFINED(c.archivist_status) AND c.archivist_status != null
                        AND c.archivist_status != '' AND IS_STRING(c.archivist_status),
                        LOWER(c.archivist_status), 'pending'),
                    "related_assets_status": c.related_assets_status,
                    "asset_details_status": c.asset_details_status,
                    "original_file_status": c.original_file_status,
                    "ocr_batch_status": c.ocr_batch_status,
                    "ocr_processing_status": c.ocr_processing_status,
                    "metadata_extraction_status": c.metadata_extraction_status,
                    "resource_type_batch_status": c.resource_type_batch_status,
                    "resource_type_processing_status": c.resource_type_processing_status,
                    "metadata_batchs_status": c.metadata_batchs_status,
                """
        + _DOC_OCR_SELECT
        + """
                }
                FROM c
            """
    )


def _clear_precomputed_stats(stats_container) -> None:
    query = f"""
                SELECT c.id FROM c
                WHERE c.id = '{DASHBOARD_STATS_ID}'
                   OR STARTSWITH(c.id, '{REPOSITORY_STATS_PREFIX}')
                   OR STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}')
            """
    items = list(
        stats_container.query_items(query=query, enable_cross_partition_query=True)
    )
    for item in items:
        doc_id = item["id"]
        try:
            stats_container.delete_item(item=doc_id, partition_key=doc_id)
        except exceptions.CosmosResourceNotFoundError:
            pass


def rebuild_all_statistics_inline() -> None:
    """Rebuild dashboard / repository / collection statistics from the main documents container."""
    cfg = CosmosDBConfig.from_env()
    stats_name = os.environ.get("COSMOS_DB_STATS_CONTAINER_NAME", "statistics")
    client = CosmosClient(cfg.endpoint, credential=DefaultAzureCredential())
    db = client.get_database_client(cfg.database_name)
    data_container = db.get_container_client(cfg.container_name)
    stats_container = db.get_container_client(stats_name)
    stats_container.read()
    _clear_precomputed_stats(stats_container)
    query = _full_projection_query()
    docs = list(data_container.query_items(query=query, enable_cross_partition_query=True))
    repo_stats: Dict[str, Any] = {}
    collection_stats: Dict[str, Any] = {}
    total_stats: Dict[str, int] = empty_dashboard_status_totals()
    for doc in docs:
        repo = doc.get("repository", "Unknown")
        collection = doc.get("collection", "Unknown")
        category = categorize_document_status(doc)
        status_key = _STATUS_KEY_MAP.get(category, "pending")
        total_stats["totalItems"] += 1
        _adjust_status_counter(total_stats, category, +1, use_total_prefix=True)
        if repo not in repo_stats:
            repo_stats[repo] = {
                "repository": repo,
                "totalItems": 0,
                "collections": set(),
                **empty_record_status_counts(),
            }
        repo_stats[repo]["totalItems"] += 1
        repo_stats[repo]["collections"].add(collection)
        repo_stats[repo][status_key] = repo_stats[repo].get(status_key, 0) + 1
        coll_key = f"{repo}|{collection}"
        if coll_key not in collection_stats:
            collection_stats[coll_key] = {
                **empty_collection_status_bucket(repo, collection),
                "ocrConfidenceSum": 0.0,
                "ocrConfidenceCount": 0,
            }
        collection_stats[coll_key]["totalItems"] += 1
        collection_stats[coll_key][status_key] = collection_stats[coll_key].get(status_key, 0) + 1
        _accumulate_doc_ocr(collection_stats[coll_key], doc)
    completion_rate = 0.0
    if total_stats["totalItems"] > 0:
        completion_rate = round(
            (total_stats["totalPublished"] / total_stats["totalItems"]) * 100, 1
        )
    dashboard_doc = {
        "id": DASHBOARD_STATS_ID,
        "totalItems": max(0, total_stats.get("totalItems", 0)),
        "totalPending": max(0, total_stats.get("totalPending", 0)),
        "totalPublishing": max(0, total_stats.get("totalPublishing", 0)),
        "totalPublished": max(0, total_stats.get("totalPublished", 0)),
        "totalReviewed": max(0, total_stats.get("totalReviewed", 0)),
        "totalErrors": max(0, total_stats.get("totalErrors", 0)),
        "totalRepositories": len(repo_stats),
        "totalCollections": len(collection_stats),
        "overallCompletionRate": completion_rate,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
    }
    stats_container.upsert_item(dashboard_doc)
    for stats in repo_stats.values():
        total = stats["totalItems"]
        published = stats["published"]
        comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0
        safe_repo_id = _sanitize_id(stats["repository"])
        repo_doc = {
            "id": f"{REPOSITORY_STATS_PREFIX}{safe_repo_id}",
            "repository": stats["repository"],
            "totalItems": max(0, total),
            "collections": len(stats["collections"]),
            "pending": max(0, stats.get("pending", 0)),
            "publishing": max(0, stats.get("publishing", 0)),
            "published": max(0, published),
            "reviewed": max(0, stats.get("reviewed", 0)),
            "errors": max(0, stats.get("errors", 0)),
            "completionRate": comp_rate,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }
        stats_container.upsert_item(repo_doc)
    for stats in collection_stats.values():
        total = stats["totalItems"]
        published = stats["published"]
        comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0
        safe_id = _make_collection_safe_id(stats["repository"], stats["collection"])
        coll_doc = {
            "id": f"{COLLECTION_STATS_PREFIX}{safe_id}",
            "repository": stats["repository"],
            "collection": stats["collection"],
            "totalItems": max(0, total),
            "pending": max(0, stats.get("pending", 0)),
            "publishing": max(0, stats.get("publishing", 0)),
            "published": max(0, published),
            "reviewed": max(0, stats.get("reviewed", 0)),
            "errors": max(0, stats.get("errors", 0)),
            "completionRate": comp_rate,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }
        coll_doc.update(_ocr_fields_for_saved_collection_doc(stats))
        stats_container.upsert_item(coll_doc)
    logger.info(
        "Post-unpublish inline statistics rebuild complete (%d documents)", len(docs)
    )


def _try_http_statistics_rebuild() -> bool:
    """POST archivist-api ``/api/v1/statistics/rebuild`` when ``ARCHIVIST_API_BASE_URL`` is set."""
    base = (os.environ.get("ARCHIVIST_API_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return False
    url = f"{base}/api/v1/statistics/rebuild"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({}).encode("utf-8"),
    )
    token = get_bearer_token(base)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            resp.read(1024)
        logger.info("Post-unpublish statistics rebuild completed via HTTP: %s", url)
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning(
            "HTTP statistics rebuild failed %s: %s %s; falling back to inline rebuild",
            url,
            e.code,
            body,
        )
        return False
    except Exception as exc:
        logger.warning(
            "HTTP statistics rebuild failed (%s): %s; falling back to inline rebuild",
            url,
            exc,
        )
        return False


def trigger_statistics_rebuild_after_unpublish() -> None:
    """
    Rebuild dashboard/repository/collection statistics after unpublish work (call before
    publish-batch prune so aggregates match current Cosmos state).

    Intended to run once per unpublish **batch** activity, not per document in parallel.

    Never raises: failures are logged so unpublish can continue.
    """
    if _try_http_statistics_rebuild():
        return
    try:
        rebuild_all_statistics_inline()
    except Exception as exc:
        logger.exception(
            "Post-unpublish statistics rebuild failed (unpublish will continue): %s", exc
        )
