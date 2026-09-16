# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# pylint: disable=too-many-branches,broad-exception-caught,invalid-name

"""
Statistics Helper for Azure Functions.

Provides incremental statistics updates when documents are processed.
Connects to the same statistics container used by the API.
"""
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

from helper.archivist_status import normalize_archivist_status

logger = logging.getLogger(__name__)

# Statistics document IDs (must match API statistics_service.py)
DASHBOARD_STATS_ID = "dashboard_stats"
REPOSITORY_STATS_PREFIX = "repo_stats_"
COLLECTION_STATS_PREFIX = "collection_stats_"


def _sanitize_id(value: str) -> str:
    """
    Sanitize a string to be safe for use in Cosmos DB document IDs.
    Must match the API's _sanitize_id function exactly.
    """
    if not value:
        return "Unknown"

    result = str(value)
    for char in ['/', '\\', '?', '#', '|', ' ', '\t', '\n', '\r', '"', "'", '<', '>', '*', ':']:
        result = result.replace(char, '_')

    result = ''.join(c if c.isprintable() and ord(c) < 128 else '_' for c in result)

    while '__' in result:
        result = result.replace('__', '_')

    result = result.strip('_')
    return result or "Unknown"


def _make_collection_safe_id(repo: str, collection: str) -> str:
    """Generate a consistent safe ID for collection statistics documents."""
    safe_repo = _sanitize_id(repo)
    safe_collection = _sanitize_id(collection)
    return f"{safe_repo}_{safe_collection}"


def empty_dashboard_status_totals() -> Dict[str, int]:
    """Zero counters for dashboard aggregate and full statistics rebuild (Cosmos schema)."""
    return {
        "totalItems": 0,
        "totalPending": 0,
        "totalPublishing": 0,
        "totalPublished": 0,
        "totalReviewed": 0,
        "totalErrors": 0,
    }


def empty_record_status_counts() -> Dict[str, int]:
    """Per-status document counts at zero (repository/collection stats, non-total* keys)."""
    return {
        "pending": 0,
        "publishing": 0,
        "published": 0,
        "reviewed": 0,
        "errors": 0,
    }


def empty_collection_status_bucket(repo: str, collection: str) -> Dict[str, Any]:
    """Repository + collection + per-status counts at zero (incremental updates + full rebuild)."""
    return {
        "repository": repo,
        "collection": collection,
        "totalItems": 0,
        **empty_record_status_counts(),
    }


def _resolve_cosmos_credential():
    """Use the emulator key only in local mode; otherwise require managed identity."""
    key = os.getenv("COSMOS_DB_KEY", "").strip()
    if key:
        if os.getenv("ENVIRONMENT", "production").strip().lower() != "local":
            raise ValueError(
                "COSMOS_DB_KEY is allowed only when ENVIRONMENT=local; "
                "non-local deployments must use managed identity"
            )
        return key
    return DefaultAzureCredential()


def _ocr_value_from_full_document(doc: Optional[Dict[str, Any]]) -> Optional[float]:
    """Document-level OCR 0..1 from asset_avg_confidence only (must match archivist-api statistics_service)."""
    if not doc:
        return None
    avg = doc.get("asset_avg_confidence")
    if avg is not None and isinstance(avg, (int, float)):
        val = float(avg)
        if not math.isnan(val):
            return val
    return None


class StatisticsHelper:
    """Helper for updating pre-computed statistics from Azure Functions."""

    def __init__(self):
        """Initialize statistics helper with Cosmos DB connection."""
        self._client = None
        self._database = None
        self._stats_container = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB statistics container."""
        try:
            endpoint = os.getenv("COSMOS_DB_ENDPOINT")
            database_name = os.getenv("COSMOS_DB_DATABASE_NAME", "archivist")
            stats_container_name = os.getenv("COSMOS_DB_STATS_CONTAINER_NAME", "statistics")

            if not endpoint:
                logger.warning("Cosmos DB credentials not configured for statistics")
                return

            self._client = CosmosClient(
                endpoint,
                credential=_resolve_cosmos_credential(),
            )
            self._database = self._client.get_database_client(database_name)

            try:
                self._stats_container = self._database.get_container_client(stats_container_name)
                self._stats_container.read()
                logger.info("Statistics helper connected to container: %s", stats_container_name)
            except exceptions.CosmosResourceNotFoundError:
                logger.warning(
                    "Statistics container '%s' not found. Statistics updates will be skipped.",
                    stats_container_name
                )
                self._stats_container = None

        except Exception as e:
            logger.error("Failed to connect statistics helper: %s", str(e))
            self._stats_container = None

    def is_connected(self) -> bool:
        """Check if statistics container is available."""
        return self._stats_container is not None

    def update_on_document_change(
        self,
        old_doc: Optional[Dict[str, Any]],
        new_doc: Dict[str, Any]
    ) -> None:
        """
        Incrementally update statistics when a document changes.

        Args:
            old_doc: Previous document state (None for new documents)
            new_doc: New document state
        """
        if not self.is_connected():
            logger.debug("Statistics container not connected, skipping update")
            return

        try:
            # Extract old values
            old_repo = None
            old_collection = None
            old_status = None

            if old_doc:
                old_metadata = old_doc.get("metadata", {})
                old_repo_val = old_metadata.get("Repository")
                if isinstance(old_repo_val, dict):
                    old_repo = old_repo_val.get("label", "Unknown")
                else:
                    old_repo = old_repo_val or "Unknown"

                old_coll_val = old_metadata.get("Collection")
                if isinstance(old_coll_val, dict):
                    old_collection = old_coll_val.get("label", "Unknown")
                else:
                    old_collection = old_coll_val or "Unknown"

                old_status = normalize_archivist_status(old_doc.get("archivist_status"))

            # Extract new values
            new_metadata = new_doc.get("metadata", {})
            new_repo_val = new_metadata.get("Repository")
            if isinstance(new_repo_val, dict):
                new_repo = new_repo_val.get("label", "Unknown")
            else:
                new_repo = new_repo_val or "Unknown"

            new_coll_val = new_metadata.get("Collection")
            if isinstance(new_coll_val, dict):
                new_collection = new_coll_val.get("label", "Unknown")
            else:
                new_collection = new_coll_val or "Unknown"

            new_status = normalize_archivist_status(new_doc.get("archivist_status"))

            old_ocr = _ocr_value_from_full_document(old_doc)
            new_ocr = _ocr_value_from_full_document(new_doc)

            # Update dashboard stats
            self._update_dashboard_stats(old_status, new_status, old_doc is None)

            # Update repository stats
            if old_repo != new_repo:
                if old_repo:
                    self._update_repo_stats(old_repo, old_status, increment=False)
                self._update_repo_stats(new_repo, new_status, increment=True)
            elif old_status != new_status:
                self._update_repo_stats(new_repo, new_status, increment=True,
                                        old_status=old_status)

            # Update collection stats
            old_coll_key = f"{old_repo}|{old_collection}" if old_repo else None
            new_coll_key = f"{new_repo}|{new_collection}"

            if old_coll_key != new_coll_key:
                if old_coll_key:
                    self._update_collection_stats(
                        old_repo,
                        old_collection,
                        old_status,
                        increment=False,
                        ocr_remove=old_ocr,
                    )
                self._update_collection_stats(
                    new_repo,
                    new_collection,
                    new_status,
                    increment=True,
                    ocr_add=new_ocr,
                )
            else:
                if old_status != new_status:
                    self._update_collection_stats(
                        new_repo,
                        new_collection,
                        new_status,
                        increment=True,
                        old_status=old_status,
                    )
                if old_ocr != new_ocr:
                    self._apply_collection_ocr_delta(
                        new_repo, new_collection, old_ocr, new_ocr
                    )

            logger.info(
                "Updated statistics: repo=%s, collection=%s, status=%s->%s",
                new_repo, new_collection, old_status, new_status
            )

        except Exception as e:
            logger.error("Failed to update statistics: %s", str(e))
            # Don't raise - stats update failure shouldn't break document processing

    def _update_dashboard_stats(
        self,
        old_status: Optional[str],
        new_status: str,
        is_new_doc: bool,
        max_retries: int = 12,
    ) -> None:
        """Update dashboard statistics incrementally (ETag + retry for parallel unpublish)."""
        for attempt in range(max_retries):
            try:
                try:
                    stats = self._stats_container.read_item(
                        item=DASHBOARD_STATS_ID,
                        partition_key=DASHBOARD_STATS_ID,
                    )
                    etag = stats.get("_etag")
                    stats["totalPending"] = max(0, stats.get("totalPending", 0))
                    stats["totalPublishing"] = max(0, stats.get("totalPublishing", 0))
                    stats["totalPublished"] = max(0, stats.get("totalPublished", 0))
                    stats["totalReviewed"] = max(0, stats.get("totalReviewed", 0))
                except exceptions.CosmosResourceNotFoundError:
                    stats = {
                        "id": DASHBOARD_STATS_ID,
                        **empty_dashboard_status_totals(),
                        "totalRepositories": 0,
                        "totalCollections": 0,
                        "overallCompletionRate": 0.0,
                    }
                    etag = None

                if old_status and old_status.lower() == new_status.lower():
                    return

                if is_new_doc:
                    stats["totalItems"] = stats.get("totalItems", 0) + 1

                if old_status:
                    old_status_lower = old_status.lower() if old_status else ""
                    if old_status_lower == "published":
                        stats["totalPublished"] = max(0, stats.get("totalPublished", 0) - 1)
                    elif old_status_lower == "publishing":
                        stats["totalPublishing"] = max(0, stats.get("totalPublishing", 0) - 1)
                    elif old_status_lower == "reviewed":
                        stats["totalReviewed"] = max(0, stats.get("totalReviewed", 0) - 1)
                    else:
                        stats["totalPending"] = max(0, stats.get("totalPending", 0) - 1)

                new_status_lower = new_status.lower() if new_status else ""
                if new_status_lower == "published":
                    stats["totalPublished"] = stats.get("totalPublished", 0) + 1
                elif new_status_lower == "publishing":
                    stats["totalPublishing"] = stats.get("totalPublishing", 0) + 1
                elif new_status_lower == "reviewed":
                    stats["totalReviewed"] = stats.get("totalReviewed", 0) + 1
                else:
                    stats["totalPending"] = stats.get("totalPending", 0) + 1

                total = stats.get("totalItems", 0)
                if total > 0:
                    stats["overallCompletionRate"] = round(
                        (stats.get("totalPublished", 0) / total) * 100, 1
                    )

                stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                if etag:
                    self._stats_container.replace_item(
                        item=DASHBOARD_STATS_ID,
                        body=stats,
                        if_match=etag,
                    )
                else:
                    self._stats_container.upsert_item(stats)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    logger.debug(
                        "Dashboard stats ETag conflict (attempt %d/%d), retrying",
                        attempt + 1,
                        max_retries,
                    )
                    continue
                logger.warning(
                    "Dashboard stats update failed after %d retries due to ETag conflicts",
                    max_retries,
                )
                raise

    def _update_repo_stats(
        self,
        repo: str,
        status: str,
        increment: bool,
        old_status: Optional[str] = None,
        max_retries: int = 12,
    ) -> None:
        """Update repository statistics incrementally (ETag + retry for parallel unpublish)."""
        safe_repo_id = _sanitize_id(repo)
        doc_id = f"{REPOSITORY_STATS_PREFIX}{safe_repo_id}"

        status_lower = status.lower() if status else ""
        old_status_lower = old_status.lower() if old_status else ""
        if old_status is not None and old_status_lower == status_lower:
            return

        delta = 1 if increment else -1

        for attempt in range(max_retries):
            try:
                try:
                    stats = self._stats_container.read_item(item=doc_id, partition_key=doc_id)
                    etag = stats.get("_etag")
                    stats["pending"] = max(0, stats.get("pending", 0))
                    stats["publishing"] = max(0, stats.get("publishing", 0))
                    stats["published"] = max(0, stats.get("published", 0))
                    stats["reviewed"] = max(0, stats.get("reviewed", 0))
                except exceptions.CosmosResourceNotFoundError:
                    stats = {
                        "id": doc_id,
                        "repository": repo,
                        "totalItems": 0,
                        "collections": 0,
                        **empty_record_status_counts(),
                        "completionRate": 0.0,
                    }
                    etag = None

                if old_status is None:
                    stats["totalItems"] = max(0, stats.get("totalItems", 0) + delta)

                    if status_lower == "published":
                        stats["published"] = max(0, stats.get("published", 0) + delta)
                    elif status_lower == "publishing":
                        stats["publishing"] = max(0, stats.get("publishing", 0) + delta)
                    elif status_lower == "reviewed":
                        stats["reviewed"] = max(0, stats.get("reviewed", 0) + delta)
                    else:
                        stats["pending"] = max(0, stats.get("pending", 0) + delta)
                else:
                    if old_status_lower == "published":
                        stats["published"] = max(0, stats.get("published", 0) - 1)
                    elif old_status_lower == "publishing":
                        stats["publishing"] = max(0, stats.get("publishing", 0) - 1)
                    elif old_status_lower == "reviewed":
                        stats["reviewed"] = max(0, stats.get("reviewed", 0) - 1)
                    else:
                        stats["pending"] = max(0, stats.get("pending", 0) - 1)

                    if status_lower == "published":
                        stats["published"] = stats.get("published", 0) + 1
                    elif status_lower == "publishing":
                        stats["publishing"] = stats.get("publishing", 0) + 1
                    elif status_lower == "reviewed":
                        stats["reviewed"] = stats.get("reviewed", 0) + 1
                    else:
                        stats["pending"] = stats.get("pending", 0) + 1

                total = stats.get("totalItems", 0)
                if total > 0:
                    stats["completionRate"] = round(
                        (stats.get("published", 0) / total) * 100, 1
                    )

                stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                if etag:
                    self._stats_container.replace_item(
                        item=doc_id,
                        body=stats,
                        if_match=etag,
                    )
                else:
                    self._stats_container.upsert_item(stats)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    logger.debug(
                        "Repo stats ETag conflict for '%s' (attempt %d/%d), retrying",
                        repo,
                        attempt + 1,
                        max_retries,
                    )
                    continue
                logger.warning(
                    "Repo stats update failed for '%s' after %d retries due to ETag conflicts",
                    repo,
                    max_retries,
                )
                raise

    def _apply_collection_ocr_delta(
        self,
        repo: str,
        collection: str,
        old_ocr: Optional[float],
        new_ocr: Optional[float],
        max_retries: int = 12,
    ) -> None:
        """Adjust running OCR sum/count when only document confidence changes (ETag + retry)."""
        if old_ocr is None and new_ocr is None:
            return
        if old_ocr == new_ocr:
            return

        safe_id = _make_collection_safe_id(repo, collection)
        doc_id = f"{COLLECTION_STATS_PREFIX}{safe_id}"

        for attempt in range(max_retries):
            try:
                try:
                    stats = self._stats_container.read_item(item=doc_id, partition_key=doc_id)
                    etag = stats.get("_etag")
                except exceptions.CosmosResourceNotFoundError:
                    logger.debug(
                        "No collection stats for %s/%s; skip OCR delta",
                        repo,
                        collection,
                    )
                    return

                cnt = max(0, int(stats.get("ocrConfidenceCount", 0)))
                s = float(stats.get("ocrConfidenceSum", 0.0))

                if old_ocr is None and new_ocr is not None:
                    cnt += 1
                    s += float(new_ocr)
                elif old_ocr is not None and new_ocr is None:
                    cnt = max(0, cnt - 1)
                    s -= float(old_ocr)
                else:
                    s += float(new_ocr) - float(old_ocr)

                stats["ocrConfidenceCount"] = cnt
                stats["ocrConfidenceSum"] = s
                if cnt > 0:
                    stats["avgOcrConfidence"] = round((s / cnt) * 100.0, 1)
                else:
                    stats["avgOcrConfidence"] = None
                    stats["ocrConfidenceSum"] = 0.0
                    stats["ocrConfidenceCount"] = 0

                stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                if etag:
                    self._stats_container.replace_item(
                        item=doc_id,
                        body=stats,
                        if_match=etag,
                    )
                else:
                    self._stats_container.upsert_item(stats)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    continue
                raise

    def _update_collection_stats(
        self,
        repo: str,
        collection: str,
        status: str,
        increment: bool,
        old_status: Optional[str] = None,
        ocr_remove: Optional[float] = None,
        ocr_add: Optional[float] = None,
        max_retries: int = 12,
    ) -> None:
        """Update collection statistics incrementally (ETag + retry for parallel unpublish)."""
        safe_id = _make_collection_safe_id(repo, collection)
        doc_id = f"{COLLECTION_STATS_PREFIX}{safe_id}"

        status_lower = status.lower() if status else ""
        old_status_lower = old_status.lower() if old_status else ""
        if old_status is not None and old_status_lower == status_lower:
            if ocr_remove is None and ocr_add is None:
                return

        delta = 1 if increment else -1

        for attempt in range(max_retries):
            try:
                try:
                    stats = self._stats_container.read_item(item=doc_id, partition_key=doc_id)
                    etag = stats.get("_etag")
                    stats["pending"] = max(0, stats.get("pending", 0))
                    stats["publishing"] = max(0, stats.get("publishing", 0))
                    stats["published"] = max(0, stats.get("published", 0))
                    stats["reviewed"] = max(0, stats.get("reviewed", 0))
                except exceptions.CosmosResourceNotFoundError:
                    stats = {
                        "id": doc_id,
                        **empty_collection_status_bucket(repo, collection),
                        "completionRate": 0.0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                        "avgOcrConfidence": None,
                    }
                    etag = None

                if old_status is None:
                    stats["totalItems"] = max(0, stats.get("totalItems", 0) + delta)

                    if status_lower == "published":
                        stats["published"] = max(0, stats.get("published", 0) + delta)
                    elif status_lower == "publishing":
                        stats["publishing"] = max(0, stats.get("publishing", 0) + delta)
                    elif status_lower == "reviewed":
                        stats["reviewed"] = max(0, stats.get("reviewed", 0) + delta)
                    else:
                        stats["pending"] = max(0, stats.get("pending", 0) + delta)
                else:
                    if old_status_lower == "published":
                        stats["published"] = max(0, stats.get("published", 0) - 1)
                    elif old_status_lower == "publishing":
                        stats["publishing"] = max(0, stats.get("publishing", 0) - 1)
                    elif old_status_lower == "reviewed":
                        stats["reviewed"] = max(0, stats.get("reviewed", 0) - 1)
                    else:
                        stats["pending"] = max(0, stats.get("pending", 0) - 1)

                    if status_lower == "published":
                        stats["published"] = stats.get("published", 0) + 1
                    elif status_lower == "publishing":
                        stats["publishing"] = stats.get("publishing", 0) + 1
                    elif status_lower == "reviewed":
                        stats["reviewed"] = stats.get("reviewed", 0) + 1
                    else:
                        stats["pending"] = stats.get("pending", 0) + 1

                total = stats.get("totalItems", 0)
                if total > 0:
                    stats["completionRate"] = round(
                        (stats.get("published", 0) / total) * 100, 1
                    )

                if ocr_remove is not None or ocr_add is not None:
                    cnt = max(0, int(stats.get("ocrConfidenceCount", 0)))
                    s = float(stats.get("ocrConfidenceSum", 0.0))
                    if ocr_remove is not None:
                        cnt = max(0, cnt - 1)
                        s -= float(ocr_remove)
                    if ocr_add is not None:
                        cnt += 1
                        s += float(ocr_add)
                    stats["ocrConfidenceCount"] = cnt
                    stats["ocrConfidenceSum"] = s
                    if cnt > 0:
                        stats["avgOcrConfidence"] = round((s / cnt) * 100.0, 1)
                    else:
                        stats["avgOcrConfidence"] = None
                        stats["ocrConfidenceSum"] = 0.0
                        stats["ocrConfidenceCount"] = 0

                stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()
                if etag:
                    self._stats_container.replace_item(
                        item=doc_id,
                        body=stats,
                        if_match=etag,
                    )
                else:
                    self._stats_container.upsert_item(stats)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    logger.debug(
                        "Collection stats ETag conflict for '%s/%s' (attempt %d/%d), retrying",
                        repo,
                        collection,
                        attempt + 1,
                        max_retries,
                    )
                    continue
                logger.warning(
                    "Collection stats update failed for '%s/%s' after %d retries due to ETag conflicts",
                    repo,
                    collection,
                    max_retries,
                )
                raise


# Singleton instance
_stats_helper: Optional[StatisticsHelper] = None


def get_statistics_helper() -> StatisticsHelper:
    """Get or create statistics helper instance."""
    global _stats_helper  # pylint: disable=global-statement
    if _stats_helper is None:
        _stats_helper = StatisticsHelper()
    return _stats_helper
