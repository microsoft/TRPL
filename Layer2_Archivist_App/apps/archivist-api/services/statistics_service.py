# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Statistics Service for Archivist API.

Provides fast dashboard statistics using pre-computed aggregates stored in a
dedicated statistics container. Updates are handled incrementally via Change Feed.
"""
# pylint: disable=duplicate-code,import-error

import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add parent directory to path for imports (must be before local imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Local imports after path setup
from azure.cosmos import exceptions
from core.config import settings
from services.cosmos_service import get_cosmos_client
from services.status_helpers import categorize_document_status

logger = logging.getLogger(__name__)

# Statistics document IDs
DASHBOARD_STATS_ID = "dashboard_stats"
REPOSITORY_STATS_PREFIX = "repo_stats_"
COLLECTION_STATS_PREFIX = "collection_stats_"
OCR_REPORT_PREFIX = "ocr_report_"


def _sanitize_id(value: str) -> str:
    """
    Sanitize a string to be safe for use in Cosmos DB document IDs.
    
    Cosmos DB prohibits: / \\ ? # and control characters in IDs.
    We also replace spaces and pipes for consistency.
    Case is preserved to distinguish "Collection A" from "Collection a".
    
    Args:
        value: String to sanitize
        
    Returns:
        Sanitized string safe for use in document IDs (case preserved)
    """
    if not value:
        return "Unknown"

    # Characters not allowed in Cosmos DB IDs: / \ ? #
    # Also replace: spaces, pipes, and other problematic chars
    result = str(value)  # Preserve original case
    for char in ['/', '\\', '?', '#', '|', ' ', '\t', '\n', '\r', '"', "'", '<', '>', '*', ':']:
        result = result.replace(char, '_')

    # Remove any remaining control characters
    result = ''.join(c if c.isprintable() and ord(c) < 128 else '_' for c in result)

    # Collapse multiple underscores
    while '__' in result:
        result = result.replace('__', '_')

    # Strip leading/trailing underscores
    result = result.strip('_')

    return result or "Unknown"



def _make_collection_safe_id(repo: str, collection: str) -> str:
    """
    Generate a consistent safe ID for collection statistics documents.
    
    Args:
        repo: Repository name
        collection: Collection name
        
    Returns:
        Safe ID string for the collection statistics document
    """
    safe_repo = _sanitize_id(repo)
    safe_collection = _sanitize_id(collection)
    return f"{safe_repo}_{safe_collection}"


# Cosmos SQL: per-document OCR 0..1 from asset_avg_confidence only (derived from per-asset OCR)
_DOC_OCR_SELECT = """
                    "docOcr": IIF(IS_DEFINED(c.asset_avg_confidence) AND IS_NUMBER(c.asset_avg_confidence), c.asset_avg_confidence, null)
"""


def _ocr_value_from_full_document(doc: Optional[Dict[str, Any]]) -> Optional[float]:
    """Document-level OCR 0..1 from asset_avg_confidence only (incremental stats; matches rebuild)."""
    if not doc:
        return None
    avg = doc.get("asset_avg_confidence")
    if avg is not None and isinstance(avg, (int, float)):
        val = float(avg)
        if not math.isnan(val):
            return val
    return None


def _meta_confidence_from_document(doc: Optional[Dict[str, Any]]) -> Optional[float]:
    """Document-level metadata extraction confidence 0..1."""
    if not doc:
        return None
    val = doc.get("metadata_extraction_confidence")
    if val is not None and isinstance(val, (int, float)):
        v = float(val)
        if not math.isnan(v):
            return v
    return None


def _resource_type_from_document(doc: Optional[Dict[str, Any]]) -> str:
    """Extract Resource Type string from a full document."""
    if not doc:
        return "Unknown"
    metadata = doc.get("metadata", {})
    rt = metadata.get("Resource Type")
    if rt is None:
        return "Unknown"
    if isinstance(rt, str):
        return rt if rt else "Unknown"
    if isinstance(rt, dict):
        return rt.get("label", "Unknown") or "Unknown"
    return "Unknown"


def _accumulate_doc_ocr(bucket: Dict[str, Any], doc: Dict[str, Any]) -> None:
    """Add one document's projected docOcr into collection aggregation buckets."""
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
    """Persisted OCR aggregates on collection_stats_* documents (0..100 avg + running sum/count)."""
    cnt = max(0, int(stats.get("ocrConfidenceCount", 0)))
    s = float(stats.get("ocrConfidenceSum", 0.0))
    if cnt <= 0:
        return {
            "avgOcrConfidence": None,
            "ocrConfidenceSum": 0.0,
            "ocrConfidenceCount": 0,
        }
    return {
        "avgOcrConfidence": round((s / cnt) * 100.0, 1),
        "ocrConfidenceSum": s,
        "ocrConfidenceCount": cnt,
    }


def _build_dashboard_stats_response(stats_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a standardized dashboard statistics response from a stats document.
    
    Args:
        stats_doc: Statistics document from Cosmos DB
        
    Returns:
        Formatted dashboard statistics dictionary
    """
    return {
        "totalItems": stats_doc.get("totalItems", 0),
        "totalCollections": stats_doc.get("totalCollections", 0),
        "totalPending": stats_doc.get("totalPending", 0),
        "totalPublishing": stats_doc.get("totalPublishing", 0),
        "totalPublished": stats_doc.get("totalPublished", 0),
        "totalRepositories": stats_doc.get("totalRepositories", 0),
        "totalReviewed": stats_doc.get("totalReviewed", 0),
        "totalErrors": stats_doc.get("totalErrors", 0),
        "overallCompletionRate": stats_doc.get("overallCompletionRate", 0.0),
        "lastUpdated": stats_doc.get("lastUpdated"),
    }


# Status to counter key mapping
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
    """
    Get the counter key for a given status.
    
    Args:
        status: Categorized status (published/publishing/reviewed/error/pending)
        use_total_prefix: If True, return keys like 'totalPublished', else 'published'
        
    Returns:
        Counter key string
    """
    key_map = _STATUS_KEY_MAP_TOTAL if use_total_prefix else _STATUS_KEY_MAP
    return key_map.get(status, key_map["pending"])


def _adjust_status_counter(
    stats: Dict[str, Any], 
    status: str, 
    delta: int, 
    use_total_prefix: bool = False
) -> None:
    """
    Adjust a status counter by delta (increment or decrement).
    
    Args:
        stats: Statistics dictionary to modify
        status: Categorized status (published/publishing/reviewed/error/pending)
        delta: Amount to adjust (+1 for increment, -1 for decrement)
        use_total_prefix: If True, use keys like 'totalPublished', else 'published'
    """
    key = _get_status_key(status, use_total_prefix)
    stats[key] = max(0, stats.get(key, 0) + delta)


class StatisticsService:
    """
    Service for managing pre-computed statistics.

    Statistics are stored in a dedicated container and updated incrementally
    when documents change, providing instant dashboard load times.
    """

    def __init__(self):
        """Initialize Statistics service."""
        self._client = None
        self._database = None
        self._stats_container = None
        self._ocr_container = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB."""
        try:
            self._client = get_cosmos_client()
            self._database = self._client.get_database_client(
                settings.cosmos_db_database_name
            )

            # Statistics container (create if not exists)
            stats_container_name = getattr(settings, 'cosmos_db_stats_container_name', 'statistics')
            try:
                self._stats_container = self._database.get_container_client(stats_container_name)
                # Test connection
                self._stats_container.read()
            except exceptions.CosmosResourceNotFoundError:
                # Create container if it doesn't exist
                self._database.create_container(
                    id=stats_container_name,
                    partition_key={"paths": ["/id"], "kind": "Hash"}
                )
                self._stats_container = self._database.get_container_client(stats_container_name)
                logger.info("Created statistics container: %s", stats_container_name)

            # OCR container reference
            self._ocr_container = self._database.get_container_client(
                settings.cosmos_db_container_name
            )

            logger.info("Statistics service connected")

        except Exception as e:
            logger.exception("Failed to connect statistics service: %s", str(e))
            raise

    def get_dashboard_statistics(self) -> Dict[str, Any]:
        """
        Get dashboard statistics from pre-computed aggregate.

        Returns instantly from statistics container.
        Falls back to computing if stats don't exist.

        Returns:
            Dictionary with dashboard statistics
        """
        try:
            # Try to read pre-computed stats (single point read - instant)
            stats_doc = self._stats_container.read_item(
                item=DASHBOARD_STATS_ID,
                partition_key=DASHBOARD_STATS_ID
            )

            logger.debug("Returning pre-computed dashboard statistics")
            return _build_dashboard_stats_response(stats_doc)

        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Dashboard statistics not found, rebuilding dashboard statistics...")
            self.rebuild_dashboard_statistics()
            # Read the newly created stats directly (avoid recursion)
            try:
                stats_doc = self._stats_container.read_item(
                    item=DASHBOARD_STATS_ID,
                    partition_key=DASHBOARD_STATS_ID
                )
                return _build_dashboard_stats_response(stats_doc)
            except exceptions.CosmosResourceNotFoundError:
                # If still not found after rebuild, return zeros
                return _build_dashboard_stats_response({})
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get dashboard statistics: %s", str(e))
            raise

    def get_repository_statistics(self) -> List[Dict[str, Any]]:
        """
        Get all repository statistics from pre-computed aggregates.

        Returns:
            List of all repository statistics
        """
        try:
            query = f"SELECT * FROM c WHERE STARTSWITH(c.id, '{REPOSITORY_STATS_PREFIX}')"

            items = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            # If no stats found, trigger rebuild
            if not items:
                logger.warning("No repository statistics found, rebuilding repository statistics...")
                self.rebuild_repository_statistics()
                items = list(self._stats_container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))

            # Transform to expected format
            repo_list = []
            for item in items:
                repo_list.append({
                    "repository": item.get("repository", "Unknown"),
                    "totalItems": item.get("totalItems", 0),
                    "collections": item.get("collections", 0),
                    "pending": item.get("pending", 0),
                    "reviewed": item.get("reviewed", 0),
                    "publishing": item.get("publishing", 0),
                    "published": item.get("published", 0),
                    "errors": item.get("errors", 0),
                    "completionRate": item.get("completionRate", 0.0),
                })

            # Sort by repository name
            repo_list.sort(key=lambda x: x["repository"].lower())

            return repo_list

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get repository statistics: %s", str(e))
            raise

    def get_collection_summaries(self) -> List[Dict[str, Any]]:
        """
        Get collection summaries from pre-computed aggregates.

        Returns:
            List of collection summaries
        """
        try:
            query = f"SELECT * FROM c WHERE STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}')"

            items = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            # If no stats found, trigger rebuild
            if not items:
                logger.warning("No collection statistics found, rebuilding collection statistics...")
                self.rebuild_collection_statistics()
                # Re-query after rebuild
                items = list(self._stats_container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))

            summaries = []
            for item in items:
                summaries.append({
                    "repository": item.get("repository", "Unknown"),
                    "collection": item.get("collection", "Unknown"),
                    "totalItems": item.get("totalItems", 0),
                    "pending": item.get("pending", 0),
                    "reviewed": item.get("reviewed", 0),
                    "publishing": item.get("publishing", 0),
                    "published": item.get("published", 0),
                    "errors": item.get("errors", 0),
                    "completionRate": item.get("completionRate", 0.0),
                    "avgOcrConfidence": item.get("avgOcrConfidence"),
                })

            return summaries

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get collection summaries: %s", str(e))
            raise

    def get_collections_by_repository(self, repository: str) -> List[Dict[str, Any]]:
        """
        Get all collection statistics for a specific repository.

        Args:
            repository: Repository name to filter by

        Returns:
            List of collection statistics for the repository
        """
        try:
            query = (
                f"SELECT * FROM c WHERE STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}') "
                "AND c.repository = @repository"
            )
            parameters = [{"name": "@repository", "value": repository}]

            items = list(self._stats_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))

            # If no stats found, trigger rebuild
            if not items:
                logger.warning(
                    "No collection statistics found for repository '%s', rebuilding...",
                    repository
                )
                self.rebuild_collection_statistics_by_repository(repository)
                items = list(self._stats_container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True
                ))

            # Transform to expected format
            collections_list = []
            for item in items:
                collections_list.append({
                    "collection": item.get("collection", "Unknown"),
                    "repository": item.get("repository", repository),
                    "totalItems": item.get("totalItems", 0),
                    "pending": item.get("pending", 0),
                    "reviewed": item.get("reviewed", 0),
                    "publishing": item.get("publishing", 0),
                    "published": item.get("published", 0),
                    "errors": item.get("errors", 0),
                    "completionRate": item.get("completionRate", 0.0),
                    "avgOcrConfidence": item.get("avgOcrConfidence"),
                })

            # Sort by collection name
            collections_list.sort(key=lambda x: x["collection"].lower())

            return collections_list

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get collections by repository: %s", str(e))
            raise

    def get_collection_statistics(
        self, repository: str, collection: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get statistics for a specific collection using direct point read.

        This is faster than get_collections_by_repository when you only need
        one collection's stats.

        Args:
            repository: Repository name
            collection: Collection name

        Returns:
            Collection statistics dictionary or None if not found
        """
        try:
            # Build the document ID using the same logic as rebuild methods
            safe_id = _make_collection_safe_id(repository, collection)
            doc_id = f"{COLLECTION_STATS_PREFIX}{safe_id}"

            # Try direct point read (fastest)
            try:
                stats_doc = self._stats_container.read_item(
                    item=doc_id,
                    partition_key=doc_id
                )
                return {
                    "collection": stats_doc.get("collection", collection),
                    "repository": stats_doc.get("repository", repository),
                    "totalItems": stats_doc.get("totalItems", 0),
                    "pending": stats_doc.get("pending", 0),
                    "reviewed": stats_doc.get("reviewed", 0),
                    "publishing": stats_doc.get("publishing", 0),
                    "published": stats_doc.get("published", 0),
                    "errors": stats_doc.get("errors", 0),
                    "completionRate": stats_doc.get("completionRate", 0.0),
                    "avgOcrConfidence": stats_doc.get("avgOcrConfidence"),
                }
            except exceptions.CosmosResourceNotFoundError:
                # Not found with exact ID, try query with case-sensitive match
                # Use case-sensitive matching for repository to distinguish different cases
                query = (
                    f"SELECT * FROM c WHERE STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}') "
                    "AND c.repository = @repository "
                    "AND c.collection = @collection"
                )
                parameters = [
                    {"name": "@repository", "value": repository},
                    {"name": "@collection", "value": collection}
                ]

                items = list(self._stats_container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True
                ))

                if items:
                    item = items[0]
                    return {
                        "collection": item.get("collection", collection),
                        "repository": item.get("repository", repository),
                        "totalItems": item.get("totalItems", 0),
                        "pending": item.get("pending", 0),
                        "reviewed": item.get("reviewed", 0),
                        "publishing": item.get("publishing", 0),
                        "published": item.get("published", 0),
                        "errors": item.get("errors", 0),
                        "completionRate": item.get("completionRate", 0.0),
                        "avgOcrConfidence": item.get("avgOcrConfidence"),
                    }

                # Still not found - return None
                logger.warning(
                    "Collection statistics not found for %s/%s",
                    repository, collection
                )
                return None

        except exceptions.CosmosHttpResponseError as e:
            logger.exception(
                "Failed to get collection statistics for %s/%s: %s",
                repository, collection, str(e)
            )
            raise

    def get_statistics_status(self) -> Dict[str, Any]:
        """
        Get the status of pre-computed statistics.

        Returns:
            Dictionary with exists flag and lastUpdated timestamp
        """
        try:
            stats_doc = self._stats_container.read_item(
                item=DASHBOARD_STATS_ID,
                partition_key=DASHBOARD_STATS_ID
            )
            return {
                "exists": True,
                "lastUpdated": stats_doc.get("lastUpdated")
            }
        except exceptions.CosmosResourceNotFoundError:
            return {
                "exists": False,
                "lastUpdated": None
            }

    # ------------------------------------------------------------------
    # Collection OCR Report (by resource type)
    # ------------------------------------------------------------------

    def get_collection_ocr_report(
        self, repository: str, collection: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the pre-computed OCR accuracy report for a collection,
        broken down by resource type.

        Args:
            repository: Repository name
            collection: Collection name

        Returns:
            OCR report dict or None if not found
        """
        safe_id = _make_collection_safe_id(repository, collection)
        doc_id = f"{OCR_REPORT_PREFIX}{safe_id}"

        try:
            doc = self._stats_container.read_item(
                item=doc_id, partition_key=doc_id
            )
            # Strip Cosmos metadata
            for key in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                doc.pop(key, None)
            return doc
        except exceptions.CosmosResourceNotFoundError:
            logger.info(
                "OCR report not found for '%s/%s', rebuilding...",
                repository, collection,
            )
            self.rebuild_collection_ocr_report(repository, collection)
            try:
                doc = self._stats_container.read_item(
                    item=doc_id, partition_key=doc_id
                )
                for key in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                    doc.pop(key, None)
                return doc
            except exceptions.CosmosResourceNotFoundError:
                return None
        except Exception as e:
            logger.error(
                "Failed to get OCR report for '%s/%s': %s",
                repository, collection, str(e),
            )
            raise

    def rebuild_collection_ocr_report(
        self,
        repository: str,
        collection: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Rebuild the OCR accuracy report for a single collection.

        Queries the OCR container, groups by resource type, and computes
        average asset_avg_confidence and metadata_extraction_confidence.
        Stores the result in the statistics container.

        Args:
            repository: Repository name
            collection: Collection name

        Returns:
            The saved report document, or None on failure
        """
        logger.info(
            "Rebuilding OCR report for '%s/%s'...", repository, collection
        )

        try:
            # Build WHERE clause for repository
            repo_val = str(repository).strip()
            if repo_val.lower() == "unknown":
                repo_where = (
                    "(NOT IS_DEFINED(c.metadata.Repository) "
                    "OR c.metadata.Repository = null)"
                )
            else:
                repo_where = (
                    "((IS_STRING(c.metadata.Repository) "
                    "AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) "
                    "AND IS_STRING(c.metadata.Repository.label) "
                    "AND LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )

            # Build WHERE clause for collection
            coll_val = str(collection).strip()
            if coll_val.lower() == "unknown":
                coll_where = (
                    "(NOT IS_DEFINED(c.metadata.Collection) "
                    "OR c.metadata.Collection = null)"
                )
            else:
                coll_where = (
                    "((IS_STRING(c.metadata.Collection) "
                    "AND LOWER(c.metadata.Collection) = LOWER(@collection)) "
                    "OR (IS_OBJECT(c.metadata.Collection) "
                    "AND IS_STRING(c.metadata.Collection.label) "
                    "AND LOWER(c.metadata.Collection.label) = LOWER(@collection)))"
                )

            query = f"""
                SELECT VALUE {{
                    "resourceType": IIF(
                        IS_DEFINED(c.metadata["Resource Type"])
                            AND c.metadata["Resource Type"] != null
                            AND c.metadata["Resource Type"] != "",
                        IIF(IS_STRING(c.metadata["Resource Type"]),
                            c.metadata["Resource Type"],
                            IIF(IS_OBJECT(c.metadata["Resource Type"]),
                                c.metadata["Resource Type"].label, 'Unknown')),
                        'Unknown'),
                    "ocrConfidence": IIF(
                        IS_DEFINED(c.asset_avg_confidence)
                            AND IS_NUMBER(c.asset_avg_confidence),
                        c.asset_avg_confidence, null),
                    "metadataConfidence": IIF(
                        IS_DEFINED(c.metadata_extraction_confidence)
                            AND IS_NUMBER(c.metadata_extraction_confidence),
                        c.metadata_extraction_confidence, null),
                    "isError": (
                        (NOT IS_DEFINED(c.archivist_status) OR c.archivist_status = ''
                            OR c.archivist_status = null
                            OR (
                                IS_STRING(c.archivist_status)
                                AND LOWER(c.archivist_status) = 'pending'
                            )
                            OR (
                                IS_STRING(c.archivist_status)
                                AND LOWER(c.archivist_status) = 'failed'
                            ))
                        AND (
                            (
                                IS_DEFINED(c.related_assets_status)
                                AND IS_STRING(c.related_assets_status)
                                AND c.related_assets_status != ''
                                AND c.related_assets_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.asset_details_status)
                                AND IS_STRING(c.asset_details_status)
                                AND c.asset_details_status != ''
                                AND c.asset_details_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.original_file_status)
                                AND IS_STRING(c.original_file_status)
                                AND c.original_file_status != ''
                                AND c.original_file_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.ocr_batch_status)
                                AND IS_STRING(c.ocr_batch_status)
                                AND c.ocr_batch_status != ''
                                AND c.ocr_batch_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.ocr_processing_status)
                                AND IS_STRING(c.ocr_processing_status)
                                AND c.ocr_processing_status != ''
                                AND c.ocr_processing_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.metadata_extraction_status)
                                AND IS_STRING(c.metadata_extraction_status)
                                AND c.metadata_extraction_status != ''
                                AND c.metadata_extraction_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.resource_type_batch_status)
                                AND IS_STRING(c.resource_type_batch_status)
                                AND c.resource_type_batch_status != ''
                                AND c.resource_type_batch_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.resource_type_processing_status)
                                AND IS_STRING(c.resource_type_processing_status)
                                AND c.resource_type_processing_status != ''
                                AND c.resource_type_processing_status != 'completed'
                            ) OR
                            (
                                IS_DEFINED(c.metadata_batchs_status)
                                AND IS_STRING(c.metadata_batchs_status)
                                AND c.metadata_batchs_status != ''
                                AND c.metadata_batchs_status != 'completed'
                            )
                        )
                    )
                }}
                FROM c
                WHERE {repo_where} AND {coll_where}
            """

            params: List[Dict[str, Any]] = []
            if repo_val.lower() != "unknown":
                params.append({"name": "@repository", "value": repo_val})
            if coll_val.lower() != "unknown":
                params.append({"name": "@collection", "value": coll_val})

            docs = list(self._ocr_container.query_items(
                query=query,
                parameters=params or None,
                enable_cross_partition_query=True,
            ))

            # Aggregate by resource type
            type_buckets: Dict[str, Dict[str, Any]] = {}
            overall_ocr_sum = 0.0
            overall_ocr_count = 0
            overall_meta_sum = 0.0
            overall_meta_count = 0
            error_count = 0

            for doc in docs:
                is_error = bool(doc.get("isError"))
                if is_error:
                    error_count += 1

                rt = doc.get("resourceType", "Unknown")
                if rt not in type_buckets:
                    type_buckets[rt] = {
                        "resourceType": rt,
                        "documentCount": 0,
                        "errorCount": 0,
                        "ocrSum": 0.0,
                        "ocrCount": 0,
                        "metaSum": 0.0,
                        "metaCount": 0,
                    }
                bucket = type_buckets[rt]

                if is_error:
                    bucket["errorCount"] += 1
                    continue

                bucket["documentCount"] += 1

                ocr_val = doc.get("ocrConfidence")
                if ocr_val is not None:
                    try:
                        v = float(ocr_val)
                        if not math.isnan(v):
                            bucket["ocrSum"] += v
                            bucket["ocrCount"] += 1
                            overall_ocr_sum += v
                            overall_ocr_count += 1
                    except (TypeError, ValueError):
                        pass

                meta_val = doc.get("metadataConfidence")
                if meta_val is not None:
                    try:
                        v = float(meta_val)
                        if not math.isnan(v):
                            bucket["metaSum"] += v
                            bucket["metaCount"] += 1
                            overall_meta_sum += v
                            overall_meta_count += 1
                    except (TypeError, ValueError):
                        pass

            # Build per-resource-type array
            by_resource_type = []
            for bucket in sorted(
                type_buckets.values(),
                key=lambda b: b["documentCount"],
                reverse=True,
            ):
                avg_ocr = (
                    round((bucket["ocrSum"] / bucket["ocrCount"]) * 100.0, 1)
                    if bucket["ocrCount"] > 0
                    else None
                )
                avg_meta = (
                    round((bucket["metaSum"] / bucket["metaCount"]) * 100.0, 1)
                    if bucket["metaCount"] > 0
                    else None
                )
                by_resource_type.append({
                    "resourceType": bucket["resourceType"],
                    "documentCount": bucket["documentCount"],
                    "errorCount": bucket["errorCount"],
                    "avgOcrAccuracy": avg_ocr,
                    "avgMetadataAccuracy": avg_meta,
                    "ocrSum": bucket["ocrSum"],
                    "ocrCount": bucket["ocrCount"],
                    "metaSum": bucket["metaSum"],
                    "metaCount": bucket["metaCount"],
                })

            overall_ocr = (
                round((overall_ocr_sum / overall_ocr_count) * 100.0, 1)
                if overall_ocr_count > 0
                else None
            )
            overall_meta = (
                round((overall_meta_sum / overall_meta_count) * 100.0, 1)
                if overall_meta_count > 0
                else None
            )

            safe_id = _make_collection_safe_id(repository, collection)
            report_doc = {
                "id": f"{OCR_REPORT_PREFIX}{safe_id}",
                "type": "ocr_report",
                "repository": repository,
                "collection": collection,
                "totalDocuments": len(docs),
                "errorCount": error_count,
                "overallOcrAccuracy": overall_ocr,
                "overallMetadataAccuracy": overall_meta,
                "_overallOcrSum": overall_ocr_sum,
                "_overallOcrCount": overall_ocr_count,
                "_overallMetaSum": overall_meta_sum,
                "_overallMetaCount": overall_meta_count,
                "byResourceType": by_resource_type,
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

            self._stats_container.upsert_item(report_doc)
            logger.info(
                "Saved OCR report for '%s/%s' (%d docs, %d resource types)",
                repository, collection, len(docs), len(by_resource_type),
            )
            return report_doc

        except Exception as e:
            logger.exception(
                "Failed to rebuild OCR report for '%s/%s': %s",
                repository, collection, str(e),
            )
            raise

    def rebuild_all_ocr_reports(self) -> int:
        """
        Rebuild OCR reports for every distinct collection found in the
        source OCR container. Returns the number of reports rebuilt.
        """
        logger.info("Rebuilding all collection OCR reports...")

        # Clear old reports first
        self.clear_ocr_reports()

        # Query distinct (repo, collection) pairs directly from source data
        # so this works independently of collection_stats documents
        query = """
            SELECT DISTINCT VALUE {
                "repository": IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                    IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                        IIF(IS_OBJECT(c.metadata.Repository), c.metadata.Repository.label, 'Unknown')),
                    'Unknown'),
                "collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                    IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                        IIF(IS_OBJECT(c.metadata.Collection), c.metadata.Collection.label, 'Unknown')),
                    'Unknown')
            }
            FROM c
        """
        items = list(self._ocr_container.query_items(
            query=query, enable_cross_partition_query=True
        ))

        count = 0
        for item in items:
            repo = item.get("repository", "Unknown")
            coll = item.get("collection", "Unknown")
            try:
                self.rebuild_collection_ocr_report(repo, coll)
                count += 1
            except Exception as e:
                logger.error(
                    "Failed to rebuild OCR report for '%s/%s': %s",
                    repo, coll, str(e),
                )

        logger.info("Rebuilt %d OCR reports", count)
        return count

    def clear_ocr_reports(self) -> int:
        """Delete all OCR report documents from the statistics container."""
        logger.info("Clearing OCR reports...")
        deleted = 0

        try:
            query = f"SELECT c.id FROM c WHERE STARTSWITH(c.id, '{OCR_REPORT_PREFIX}')"
            items = list(self._stats_container.query_items(
                query=query, enable_cross_partition_query=True
            ))
            for item in items:
                doc_id = item["id"]
                try:
                    self._stats_container.delete_item(
                        item=doc_id, partition_key=doc_id
                    )
                    deleted += 1
                except exceptions.CosmosResourceNotFoundError:
                    pass
            logger.info("Cleared %d OCR report documents", deleted)
        except Exception as e:
            logger.exception("Failed to clear OCR reports: %s", str(e))

        return deleted

    def clear_all_statistics(self) -> int:
        """
        Delete all statistics documents from the container.

        Use before rebuild to ensure no stale/duplicate data remains.
        
        NOTE: This only deletes dashboard, repository, and collection statistics.
        It preserves:
        - Active pipeline jobs (type = 'active_job')
        - Pipeline statistics (id = 'pipeline_stats')

        Returns:
            Number of documents deleted
        """
        logger.info("Clearing all statistics documents...")
        deleted_count = 0

        try:
            # Query only statistics documents (exclude active jobs and pipeline stats)
            # Statistics documents have IDs starting with known prefixes or are the dashboard stats
            query = f"""
                SELECT c.id FROM c 
                WHERE c.id = '{DASHBOARD_STATS_ID}'
                   OR STARTSWITH(c.id, '{REPOSITORY_STATS_PREFIX}')
                   OR STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}')
                   OR STARTSWITH(c.id, '{OCR_REPORT_PREFIX}')
            """
            items = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            for item in items:
                doc_id = item["id"]
                try:
                    self._stats_container.delete_item(item=doc_id, partition_key=doc_id)
                    deleted_count += 1
                except exceptions.CosmosResourceNotFoundError:
                    pass  # Already deleted

            logger.info("Cleared %d statistics documents (preserved active jobs and pipeline stats)", deleted_count)
            return deleted_count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to clear statistics: %s", str(e))
            raise

    def clear_repository_statistics(self) -> int:
        """Delete all repository statistics documents."""
        logger.info("Clearing repository statistics...")
        deleted_count = 0

        try:
            query = f"SELECT c.id FROM c WHERE STARTSWITH(c.id, '{REPOSITORY_STATS_PREFIX}')"
            items = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            for item in items:
                doc_id = item["id"]
                try:
                    self._stats_container.delete_item(item=doc_id, partition_key=doc_id)
                    deleted_count += 1
                except exceptions.CosmosResourceNotFoundError:
                    pass

            logger.info("Cleared %d repository statistics documents", deleted_count)
            return deleted_count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to clear repository statistics: %s", str(e))
            raise

    def clear_collection_statistics(self) -> int:
        """Delete all collection statistics documents."""
        logger.info("Clearing collection statistics...")
        deleted_count = 0

        try:
            query = f"SELECT c.id FROM c WHERE STARTSWITH(c.id, '{COLLECTION_STATS_PREFIX}')"
            items = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            for item in items:
                doc_id = item["id"]
                try:
                    self._stats_container.delete_item(item=doc_id, partition_key=doc_id)
                    deleted_count += 1
                except exceptions.CosmosResourceNotFoundError:
                    pass

            logger.info("Cleared %d collection statistics documents", deleted_count)
            return deleted_count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to clear collection statistics: %s", str(e))
            raise

    def rebuild_dashboard_statistics(self) -> Dict[str, Any]:
        """
        Rebuild dashboard statistics using optimized COUNT queries.

        Uses parallel COUNT queries instead of fetching all documents.
        Much faster than client-side aggregation.

        Returns:
            Dictionary with dashboard statistics
        """
        logger.info("Rebuilding dashboard statistics with COUNT queries...")

        try:
            # Use COUNT queries - MUCH faster than fetching all docs
            # These queries are optimized by Cosmos DB

            # Total items
            total_query = "SELECT VALUE COUNT(1) FROM c"
            total_result = list(self._ocr_container.query_items(
                query=total_query, enable_cross_partition_query=True
            ))
            total_items = total_result[0] if total_result else 0

            # Count by status using indexed queries
            published_query = (
                "SELECT VALUE COUNT(1) FROM c WHERE (IS_DEFINED(c.archivist_status) AND "
                "c.archivist_status != null AND IS_STRING(c.archivist_status) AND "
                "LOWER(c.archivist_status) = 'published')"
            )
            published_result = list(self._ocr_container.query_items(
                query=published_query, enable_cross_partition_query=True
            ))
            total_published = published_result[0] if published_result else 0

            reviewed_query = (
                "SELECT VALUE COUNT(1) FROM c WHERE (IS_DEFINED(c.archivist_status) AND "
                "c.archivist_status != null AND IS_STRING(c.archivist_status) AND "
                "LOWER(c.archivist_status) = 'reviewed')"
            )
            reviewed_result = list(self._ocr_container.query_items(
                query=reviewed_query, enable_cross_partition_query=True
            ))
            total_reviewed = reviewed_result[0] if reviewed_result else 0

            publishing_query = (
                "SELECT VALUE COUNT(1) FROM c WHERE (IS_DEFINED(c.archivist_status) AND "
                "c.archivist_status != null AND IS_STRING(c.archivist_status) AND "
                "LOWER(c.archivist_status) = 'publishing')"
            )
            publishing_result = list(self._ocr_container.query_items(
                query=publishing_query, enable_cross_partition_query=True
            ))
            total_publishing = publishing_result[0] if publishing_result else 0

            # Count records with errors (pending status with incomplete pipeline)
            # Error = pending document where at least one pipeline stage is NOT 'completed'
            errors_query = """
                SELECT VALUE COUNT(1) FROM c WHERE 
                    (NOT IS_DEFINED(c.archivist_status) OR c.archivist_status = '' OR 
                     c.archivist_status = null OR 
                     (IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'pending'))
                    AND (
                        (IS_DEFINED(c.related_assets_status) AND c.related_assets_status != 'completed') OR
                        (IS_DEFINED(c.asset_details_status) AND c.asset_details_status != 'completed') OR
                        (IS_DEFINED(c.original_file_status) AND c.original_file_status != 'completed') OR
                        (IS_DEFINED(c.ocr_batch_status) AND c.ocr_batch_status != 'completed') OR
                        (IS_DEFINED(c.ocr_processing_status) AND c.ocr_processing_status != 'completed') OR
                        (IS_DEFINED(c.metadata_extraction_status) AND c.metadata_extraction_status != 'completed') OR
                        (IS_DEFINED(c.resource_type_batch_status) AND c.resource_type_batch_status != 'completed') OR
                        (IS_DEFINED(c.resource_type_processing_status) AND c.resource_type_processing_status != 'completed') OR
                        (IS_DEFINED(c.metadata_batchs_status) AND c.metadata_batchs_status != 'completed')
                    )
            """
            errors_result = list(self._ocr_container.query_items(
                query=errors_query, enable_cross_partition_query=True
            ))
            total_errors = errors_result[0] if errors_result else 0

            # Pending = total - published - reviewed - publishing - errors
            # (pending = documents with all pipeline stages completed and not yet reviewed/published)
            total_pending = total_items - total_published - total_reviewed - total_publishing - total_errors

            # Distinct repositories
            repo_query = """
                SELECT DISTINCT VALUE IIF(
                    IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                    IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                        IIF(IS_OBJECT(c.metadata.Repository), c.metadata.Repository.label, 'Unknown')),
                    'Unknown'
                ) FROM c
            """
            repos = list(self._ocr_container.query_items(
                query=repo_query, enable_cross_partition_query=True
            ))
            total_repositories = len(repos)

            # Distinct collections (repo|collection pairs)
            collection_query = """
                SELECT DISTINCT VALUE CONCAT(
                    IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository), c.metadata.Repository.label, 'Unknown')),
                        'Unknown'),
                    '|',
                    IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection), c.metadata.Collection.label, 'Unknown')),
                        'Unknown')
                ) FROM c
            """
            collections = list(self._ocr_container.query_items(
                query=collection_query, enable_cross_partition_query=True
            ))
            total_collections = len(collections)

            # Calculate completion rate
            completion_rate = 0.0
            if total_items > 0:
                completion_rate = round((total_published / total_items) * 100, 1)

            # Build stats document with clamped values to prevent negatives
            stats = {
                "id": DASHBOARD_STATS_ID,
                "totalItems": max(0, total_items),
                "totalCollections": max(0, total_collections),
                "totalPending": max(0, total_pending),
                "totalPublishing": max(0, total_publishing),
                "totalPublished": max(0, total_published),
                "totalRepositories": max(0, total_repositories),
                "totalReviewed": max(0, total_reviewed),
                "totalErrors": max(0, total_errors),
                "overallCompletionRate": completion_rate,
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

            # Upsert to statistics container
            # Note: Rebuild operations typically clear_first=True, so ETag protection not needed
            # If clear_first=False, there's a small risk of race with incremental updates
            # but rebuilds are rare admin operations, so this is acceptable
            self._stats_container.upsert_item(stats)

            logger.info("Dashboard statistics rebuilt: %s", stats)
            return stats

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to rebuild dashboard statistics: %s", str(e))
            raise

    def rebuild_all_statistics(self, clear_first: bool = True) -> None:
        """
        Rebuild all statistics (dashboard, repositories, collections).

        Called on initial setup or manual refresh.
        Uses optimized queries to minimize time.

        Args:
            clear_first: If True, delete all existing statistics before rebuild
                         to avoid stale/duplicate data. Default is True.
                         WARNING: If False, there's a risk of race conditions with
                         concurrent incremental updates. Always use True in production.
        """
        if not clear_first:
            logger.warning(
                "rebuild_all_statistics called with clear_first=False. "
                "This may cause race conditions with concurrent incremental updates. "
                "Consider using clear_first=True for safety."
            )
        logger.info("Starting full statistics rebuild...")

        try:
            # Clear existing statistics to avoid duplicates from case changes
            if clear_first:
                self.clear_all_statistics()
            # Step 1: Fetch minimal data for aggregation (single query)
            # Include pipeline status fields to detect errors
            query = (
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

            logger.info("Fetching document metadata for aggregation...")
            docs = list(self._ocr_container.query_items(
                query=query, enable_cross_partition_query=True
            ))

            logger.info("Processing %d documents...", len(docs))

            # Aggregate data
            repo_stats = {}
            collection_stats = {}
            total_stats = {
                "totalItems": 0,
                "totalPending": 0,
                "totalPublishing": 0,
                "totalPublished": 0,
                "totalReviewed": 0,
                "totalErrors": 0,
            }

            for doc in docs:
                repo = doc.get("repository", "Unknown")
                collection = doc.get("collection", "Unknown")
                
                # Categorize document: pending only if all pipeline stages completed, else error
                category = categorize_document_status(doc)
                # Map category to counter key (error -> errors)
                status_key = _get_status_key(category)

                # Dashboard totals
                total_stats["totalItems"] += 1
                _adjust_status_counter(total_stats, category, +1, use_total_prefix=True)

                # Repository stats (case-sensitive)
                if repo not in repo_stats:
                    repo_stats[repo] = {
                        "repository": repo,
                        "totalItems": 0,
                        "collections": set(),
                        "pending": 0,
                        "publishing": 0,
                        "published": 0,
                        "reviewed": 0,
                        "errors": 0,
                    }
                repo_stats[repo]["totalItems"] += 1
                repo_stats[repo]["collections"].add(collection)
                repo_stats[repo][status_key] = repo_stats[repo].get(status_key, 0) + 1

                # Collection stats (case-sensitive)
                coll_key = f"{repo}|{collection}"
                if coll_key not in collection_stats:
                    collection_stats[coll_key] = {
                        "repository": repo,
                        "collection": collection,
                        "totalItems": 0,
                        "pending": 0,
                        "publishing": 0,
                        "published": 0,
                        "reviewed": 0,
                        "errors": 0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                    }
                collection_stats[coll_key]["totalItems"] += 1
                collection_stats[coll_key][status_key] = collection_stats[coll_key].get(status_key, 0) + 1
                _accumulate_doc_ocr(collection_stats[coll_key], doc)

            # Save dashboard stats
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
            self._stats_container.upsert_item(dashboard_doc)
            logger.info("Saved dashboard statistics")

            # Save repository stats
            for repo, stats in repo_stats.items():
                total = stats["totalItems"]
                published = stats["published"]
                comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0

                safe_repo_id = _sanitize_id(repo)
                repo_doc = {
                    "id": f"{REPOSITORY_STATS_PREFIX}{safe_repo_id}",
                    "repository": repo,
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
                self._stats_container.upsert_item(repo_doc)
            logger.info("Saved %d repository statistics", len(repo_stats))

            # Save collection stats
            for coll_key, stats in collection_stats.items():
                total = stats["totalItems"]
                published = stats["published"]
                comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0

                # Create safe ID using helper function
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
                self._stats_container.upsert_item(coll_doc)
            logger.info("Saved %d collection statistics", len(collection_stats))

            # Rebuild OCR accuracy reports for all collections
            self.rebuild_all_ocr_reports()

            logger.info("Full statistics rebuild complete!")

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to rebuild all statistics: %s", str(e))
            raise

    def rebuild_repository_statistics(self, clear_first: bool = True) -> None:
        """
        Rebuild only repository statistics.

        Faster than full rebuild when only repository stats need updating.

        Args:
            clear_first: If True, delete existing repository statistics first.
                        WARNING: If False, there's a risk of race conditions with
                        concurrent incremental updates. Always use True in production.
        """
        if not clear_first:
            logger.warning(
                "rebuild_repository_statistics called with clear_first=False. "
                "This may cause race conditions with concurrent incremental updates. "
                "Consider using clear_first=True for safety."
            )
        logger.info("Rebuilding repository statistics...")

        try:
            if clear_first:
                self.clear_repository_statistics()
            # Fetch minimal data needed for repository aggregation
            # Include pipeline status fields to detect errors
            query = """
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
                    "metadata_batchs_status": c.metadata_batchs_status
                }
                FROM c
            """

            docs = list(self._ocr_container.query_items(
                query=query, enable_cross_partition_query=True
            ))

            logger.info("Processing %d documents for repository statistics...", len(docs))

            # Aggregate by repository (case-sensitive)
            repo_stats = {}

            for doc in docs:
                repo = doc.get("repository", "Unknown")
                collection = doc.get("collection", "Unknown")
                
                # Categorize document: pending only if all pipeline stages completed, else error
                category = categorize_document_status(doc)
                # Map category to counter key (error -> errors)
                status_key = _get_status_key(category)

                if repo not in repo_stats:
                    repo_stats[repo] = {
                        "repository": repo,
                        "totalItems": 0,
                        "collections": set(),
                        "pending": 0,
                        "publishing": 0,
                        "published": 0,
                        "reviewed": 0,
                        "errors": 0,
                    }
                repo_stats[repo]["totalItems"] += 1
                repo_stats[repo]["collections"].add(collection)
                repo_stats[repo][status_key] = repo_stats[repo].get(status_key, 0) + 1

            # Save repository stats
            for repo, stats in repo_stats.items():
                total = stats["totalItems"]
                published = stats["published"]
                comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0

                safe_repo_id = _sanitize_id(repo)
                repo_doc = {
                    "id": f"{REPOSITORY_STATS_PREFIX}{safe_repo_id}",
                    "repository": repo,
                    "totalItems": total,
                    "collections": len(stats["collections"]),
                    "pending": stats["pending"],
                    "publishing": stats["publishing"],
                    "published": published,
                    "reviewed": stats["reviewed"],
                    "errors": stats["errors"],
                    "completionRate": comp_rate,
                    "lastUpdated": datetime.now(timezone.utc).isoformat(),
                }
                self._stats_container.upsert_item(repo_doc)

            logger.info("Saved %d repository statistics", len(repo_stats))

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to rebuild repository statistics: %s", str(e))
            raise

    def rebuild_collection_statistics(self, clear_first: bool = True) -> None:
        """
        Rebuild only collection statistics (all collections across all repositories).

        Faster than full rebuild when only collection stats need updating.

        Args:
            clear_first: If True, delete existing collection statistics first.
                        WARNING: If False, there's a risk of race conditions with
                        concurrent incremental updates. Always use True in production.
        """
        if not clear_first:
            logger.warning(
                "rebuild_collection_statistics called with clear_first=False. "
                "This may cause race conditions with concurrent incremental updates. "
                "Consider using clear_first=True for safety."
            )
        logger.info("Rebuilding collection statistics...")

        try:
            if clear_first:
                self.clear_collection_statistics()
            # Fetch minimal data needed for collection aggregation
            # Include pipeline status fields to detect errors
            query = (
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

            docs = list(self._ocr_container.query_items(
                query=query, enable_cross_partition_query=True
            ))

            logger.info("Processing %d documents for collection statistics...", len(docs))

            # Aggregate by collection (case-sensitive)
            collection_stats = {}

            for doc in docs:
                repo = doc.get("repository", "Unknown")
                collection = doc.get("collection", "Unknown")
                
                # Categorize document: pending only if all pipeline stages completed, else error
                category = categorize_document_status(doc)
                # Map category to counter key (error -> errors)
                status_key = _get_status_key(category)

                coll_key = f"{repo}|{collection}"
                if coll_key not in collection_stats:
                    collection_stats[coll_key] = {
                        "repository": repo,
                        "collection": collection,
                        "totalItems": 0,
                        "pending": 0,
                        "publishing": 0,
                        "published": 0,
                        "reviewed": 0,
                        "errors": 0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                    }
                collection_stats[coll_key]["totalItems"] += 1
                collection_stats[coll_key][status_key] = collection_stats[coll_key].get(status_key, 0) + 1
                _accumulate_doc_ocr(collection_stats[coll_key], doc)

            # Save collection stats
            for coll_key, stats in collection_stats.items():
                total = stats["totalItems"]
                published = stats["published"]
                comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0

                safe_id = _make_collection_safe_id(stats["repository"], stats["collection"])

                coll_doc = {
                    "id": f"{COLLECTION_STATS_PREFIX}{safe_id}",
                    "repository": stats["repository"],
                    "collection": stats["collection"],
                    "totalItems": total,
                    "pending": stats["pending"],
                    "publishing": stats["publishing"],
                    "published": published,
                    "reviewed": stats["reviewed"],
                    "errors": stats["errors"],
                    "completionRate": comp_rate,
                    "lastUpdated": datetime.now(timezone.utc).isoformat(),
                }
                coll_doc.update(_ocr_fields_for_saved_collection_doc(stats))
                self._stats_container.upsert_item(coll_doc)

            logger.info("Saved %d collection statistics", len(collection_stats))

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to rebuild collection statistics: %s", str(e))
            raise

    def rebuild_collection_statistics_by_repository(self, repository: str) -> None:
        """
        Rebuild collection statistics for a specific repository only.

        Much faster than full rebuild when only one repository needs updating.

        Args:
            repository: Repository name to rebuild stats for
        """
        logger.info("Rebuilding collection statistics for repository '%s'...", repository)

        try:
            # Build WHERE clause for repository
            repo_val = str(repository).strip()
            if repo_val.lower() == "unknown":
                where_clause = "(NOT IS_DEFINED(c.metadata.Repository) OR c.metadata.Repository = null)"
            else:
                where_clause = """
                    ((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository))
                    OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label)
                    AND LOWER(c.metadata.Repository.label) = LOWER(@repository)))
                """

            # Include pipeline status fields to detect errors
            query = (
                """
                SELECT VALUE {
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
                + f"""
                }}
                FROM c
                WHERE {where_clause}
            """
            )

            if repo_val.lower() != "unknown":
                parameters = [{"name": "@repository", "value": repo_val}]
            else:
                parameters = None

            docs = list(self._ocr_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))

            logger.info("Processing %d documents for repository '%s'...", len(docs), repository)

            # Aggregate by collection (case-sensitive)
            collection_stats = {}

            for doc in docs:
                collection = doc.get("collection", "Unknown")
                
                # Categorize document: pending only if all pipeline stages completed, else error
                category = categorize_document_status(doc)
                # Map category to counter key (error -> errors)
                status_key = _get_status_key(category)

                if collection not in collection_stats:
                    collection_stats[collection] = {
                        "totalItems": 0,
                        "pending": 0,
                        "publishing": 0,
                        "published": 0,
                        "reviewed": 0,
                        "errors": 0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                    }
                collection_stats[collection]["totalItems"] += 1
                collection_stats[collection][status_key] = collection_stats[collection].get(status_key, 0) + 1
                _accumulate_doc_ocr(collection_stats[collection], doc)

            # Save collection stats
            for collection, stats in collection_stats.items():
                total = stats["totalItems"]
                published = stats["published"]
                comp_rate = round((published / total) * 100, 1) if total > 0 else 0.0

                safe_id = _make_collection_safe_id(repo_val, collection)

                coll_doc = {
                    "id": f"{COLLECTION_STATS_PREFIX}{safe_id}",
                    "repository": repo_val,
                    "collection": collection,
                    "totalItems": total,
                    "pending": stats["pending"],
                    "publishing": stats["publishing"],
                    "published": published,
                    "reviewed": stats["reviewed"],
                    "errors": stats["errors"],
                    "completionRate": comp_rate,
                    "lastUpdated": datetime.now(timezone.utc).isoformat(),
                }
                coll_doc.update(_ocr_fields_for_saved_collection_doc(stats))
                self._stats_container.upsert_item(coll_doc)

            logger.info(
                "Saved %d collection statistics for repository '%s'",
                len(collection_stats), repository
            )

        except exceptions.CosmosHttpResponseError as e:
            logger.exception(
                "Failed to rebuild collection statistics for repository '%s': %s",
                repository, str(e)
            )
            raise

    def update_on_document_change(
        self,
        old_doc: Optional[Dict[str, Any]],
        new_doc: Dict[str, Any]
    ) -> None:
        """
        Incrementally update statistics when a document changes.

        Called by Change Feed processor. Updates only the affected counters.

        Args:
            old_doc: Previous document state (None for new documents)
            new_doc: New document state
        """
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

                # Use categorize_document_status to properly handle pending vs error
                old_status = categorize_document_status(old_doc)

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

            # Use categorize_document_status to properly handle pending vs error
            new_status = categorize_document_status(new_doc)

            old_ocr = _ocr_value_from_full_document(old_doc)
            new_ocr = _ocr_value_from_full_document(new_doc)

            # Update dashboard stats
            self._update_dashboard_stats(old_status, new_status, old_doc is None)

            # Update repository stats
            if old_repo != new_repo:
                # Repository changed - update both old and new repositories
                if old_repo:
                    self._update_repo_stats(old_repo, old_status, increment=False)
                self._update_repo_stats(new_repo, new_status, increment=True)
            elif old_status != new_status:
                # Same repository, status changed - only update status counts
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

            # Update OCR report (per-resource-type breakdown)
            if old_coll_key != new_coll_key:
                # Document moved collections - update both reports
                if old_coll_key:
                    self._update_ocr_report_on_change(
                        old_repo, old_collection, old_doc, None
                    )
                self._update_ocr_report_on_change(
                    new_repo, new_collection, None, new_doc
                )
            else:
                # Same collection - update in place
                self._update_ocr_report_on_change(
                    new_repo, new_collection, old_doc, new_doc
                )

            logger.debug(
                "Updated statistics: repo=%s, collection=%s, status=%s->%s",
                new_repo, new_collection, old_status, new_status
            )

        except exceptions.CosmosHttpResponseError as e:
            logger.error(
                "Failed to update statistics on document change: %s",
                str(e),
                exc_info=True
            )
            # Don't raise - stats update failure shouldn't break document operations
        except (KeyError, TypeError, ValueError) as e:
            logger.error(
                "Unexpected error updating statistics on document change: %s",
                str(e),
                exc_info=True
            )

    def _update_dashboard_stats(
        self,
        old_status: Optional[str],
        new_status: str,
        is_new_doc: bool,
        max_retries: int = 3
    ) -> None:
        """Update dashboard statistics incrementally with optimistic concurrency control."""
        for attempt in range(max_retries):
            try:
                # Read with ETag
                stats = self._stats_container.read_item(
                    item=DASHBOARD_STATS_ID,
                    partition_key=DASHBOARD_STATS_ID
                )
                etag = stats.get("_etag")
                # Clamp any existing negative values (fixes data corruption from previous bugs)
                stats["totalPending"] = max(0, stats.get("totalPending", 0))
                stats["totalPublishing"] = max(0, stats.get("totalPublishing", 0))
                stats["totalPublished"] = max(0, stats.get("totalPublished", 0))
                stats["totalReviewed"] = max(0, stats.get("totalReviewed", 0))
                stats["totalErrors"] = max(0, stats.get("totalErrors", 0))
            except exceptions.CosmosResourceNotFoundError:
                # Initialize if not exists
                stats = {
                    "id": DASHBOARD_STATS_ID,
                    "totalItems": 0,
                    "totalPending": 0,
                    "totalPublishing": 0,
                    "totalPublished": 0,
                    "totalReviewed": 0,
                    "totalErrors": 0,
                    "totalRepositories": 0,
                    "totalCollections": 0,
                    "overallCompletionRate": 0.0,
                }
                etag = None

            # Modify stats
            if is_new_doc:
                stats["totalItems"] = stats.get("totalItems", 0) + 1

            # Early return if old_status == new_status (no-op, but prevents unnecessary decrement/increment)
            if old_status and old_status.lower() == new_status.lower():
                logger.debug("Status unchanged (status=%s), skipping dashboard stats update", new_status.lower())
                return

            # Decrement old status, increment new status
            if old_status:
                _adjust_status_counter(stats, old_status, -1, use_total_prefix=True)
            _adjust_status_counter(stats, new_status, +1, use_total_prefix=True)

            # Recalculate completion rate
            total = stats.get("totalItems", 0)
            if total > 0:
                stats["overallCompletionRate"] = round(
                    (stats.get("totalPublished", 0) / total) * 100, 1
                )

            stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()

            # Write with ETag check for optimistic concurrency
            try:
                if etag:
                    # Use replace_item with if_match for existing documents
                    self._stats_container.replace_item(
                        item=DASHBOARD_STATS_ID,
                        body=stats,
                        if_match=etag
                    )
                else:
                    # Use upsert_item for new documents
                    self._stats_container.upsert_item(stats)
                return  # Success!
            except exceptions.CosmosAccessConditionFailedError:
                # ETag mismatch - another process modified it, retry
                if attempt < max_retries - 1:
                    logger.debug(
                        "Dashboard stats ETag conflict (attempt %d/%d), retrying...",
                        attempt + 1, max_retries
                    )
                    continue
                logger.warning(
                    "Dashboard stats update failed after %d retries due to ETag conflicts",
                    max_retries
                )
                raise
            except Exception as e:
                logger.error("Failed to update dashboard stats: %s", str(e))
                raise

    def _update_repo_stats(
        self,
        repo: str,
        status: str,
        increment: bool,
        old_status: Optional[str] = None,
        max_retries: int = 3
    ) -> None:
        """Update repository statistics incrementally with optimistic concurrency control."""
        safe_repo_id = _sanitize_id(repo)
        doc_id = f"{REPOSITORY_STATS_PREFIX}{safe_repo_id}"

        for attempt in range(max_retries):
            try:
                # Read with ETag
                stats = self._stats_container.read_item(item=doc_id, partition_key=doc_id)
                etag = stats.get("_etag")
                # Clamp any existing negative values (fixes data corruption from previous bugs)
                stats["pending"] = max(0, stats.get("pending", 0))
                stats["publishing"] = max(0, stats.get("publishing", 0))
                stats["published"] = max(0, stats.get("published", 0))
                stats["reviewed"] = max(0, stats.get("reviewed", 0))
                stats["errors"] = max(0, stats.get("errors", 0))
            except exceptions.CosmosResourceNotFoundError:
                logger.info("Creating new repo stats for '%s' (id=%s)", repo, doc_id)
                stats = {
                    "id": doc_id,
                    "repository": repo,
                    "totalItems": 0,
                    "collections": 0,
                    "pending": 0,
                    "publishing": 0,
                    "published": 0,
                    "reviewed": 0,
                    "errors": 0,
                    "completionRate": 0.0,
                }
                etag = None

            # Modify stats
            delta = 1 if increment else -1

            # Early return if old_status == new_status (no-op, but prevents unnecessary decrement/increment)
            if old_status is not None and old_status.lower() == status.lower():
                logger.debug("Status unchanged for repo '%s' (status=%s), skipping update", repo, status.lower())
                return

            if old_status is None:
                # Full add/remove
                stats["totalItems"] = max(0, stats.get("totalItems", 0) + delta)
                _adjust_status_counter(stats, status, delta)
            else:
                # Status change only - decrement old, increment new
                _adjust_status_counter(stats, old_status, -1)
                _adjust_status_counter(stats, status, +1)

            # Recalculate completion rate
            total = stats.get("totalItems", 0)
            if total > 0:
                stats["completionRate"] = round(
                    (stats.get("published", 0) / total) * 100, 1
                )

            stats["lastUpdated"] = datetime.now(timezone.utc).isoformat()

            # Write with ETag check for optimistic concurrency
            try:
                if etag:
                    # Use replace_item with if_match for existing documents
                    self._stats_container.replace_item(
                        item=doc_id,
                        body=stats,
                        if_match=etag
                    )
                else:
                    # Use upsert_item for new documents
                    self._stats_container.upsert_item(stats)
                return  # Success!
            except exceptions.CosmosAccessConditionFailedError:
                # ETag mismatch - another process modified it, retry
                if attempt < max_retries - 1:
                    logger.debug(
                        "Repo stats ETag conflict for '%s' (attempt %d/%d), retrying...",
                        repo, attempt + 1, max_retries
                    )
                    continue
                logger.warning(
                    "Repo stats update failed for '%s' after %d retries due to ETag conflicts",
                    repo, max_retries
                )
                raise
            except Exception as e:
                logger.error("Failed to update repo stats for '%s': %s", repo, str(e))
                raise

    def _apply_collection_ocr_delta(
        self,
        repo: str,
        collection: str,
        old_ocr: Optional[float],
        new_ocr: Optional[float],
        max_retries: int = 3,
    ) -> None:
        """Adjust running OCR sum/count when only document confidence changes (same collection)."""
        if old_ocr is None and new_ocr is None:
            return
        if old_ocr == new_ocr:
            return

        safe_id = _make_collection_safe_id(repo, collection)
        doc_id = f"{COLLECTION_STATS_PREFIX}{safe_id}"

        for attempt in range(max_retries):
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

            try:
                if etag:
                    self._stats_container.replace_item(
                        item=doc_id, body=stats, if_match=etag
                    )
                else:
                    self._stats_container.upsert_item(stats)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    continue
                raise

    def _update_ocr_report_on_change(
        self,
        repo: str,
        collection: str,
        old_doc: Optional[Dict[str, Any]],
        new_doc: Optional[Dict[str, Any]],
        max_retries: int = 3,
    ) -> None:
        """
        Incrementally update the OCR report for a collection when a document changes.

        Adjusts running sums/counts per resource type and overall, then recalculates
        display averages. Uses ETag-based optimistic concurrency like other stats.

        Args:
            repo: Repository name
            collection: Collection name
            old_doc: Previous document state (None for new documents or when removing)
            new_doc: New document state (None when only removing from this collection)
        """
        old_rt = _resource_type_from_document(old_doc)
        new_rt = _resource_type_from_document(new_doc)
        old_ocr = _ocr_value_from_full_document(old_doc)
        new_ocr = _ocr_value_from_full_document(new_doc)
        old_meta = _meta_confidence_from_document(old_doc)
        new_meta = _meta_confidence_from_document(new_doc)

        # Skip if nothing relevant changed
        if (old_doc is not None and new_doc is not None
                and old_rt == new_rt and old_ocr == new_ocr and old_meta == new_meta):
            return

        safe_id = _make_collection_safe_id(repo, collection)
        doc_id = f"{OCR_REPORT_PREFIX}{safe_id}"

        for attempt in range(max_retries):
            try:
                report = self._stats_container.read_item(
                    item=doc_id, partition_key=doc_id
                )
                etag = report.get("_etag")
            except exceptions.CosmosResourceNotFoundError:
                # No report exists yet; it will be built on next access or rebuild
                return

            # Index resource-type buckets by name
            rt_buckets: Dict[str, Dict[str, Any]] = {}
            for entry in report.get("byResourceType", []):
                rt_buckets[entry["resourceType"]] = entry

            overall_ocr_sum = float(report.get("_overallOcrSum", 0.0))
            overall_ocr_count = int(report.get("_overallOcrCount", 0))
            overall_meta_sum = float(report.get("_overallMetaSum", 0.0))
            overall_meta_count = int(report.get("_overallMetaCount", 0))
            total_docs = int(report.get("totalDocuments", 0))

            # --- Remove old values ---
            if old_doc is not None:
                total_docs = max(0, total_docs - 1)
                if old_rt in rt_buckets:
                    bucket = rt_buckets[old_rt]
                    bucket["documentCount"] = max(0, bucket.get("documentCount", 0) - 1)
                    if old_ocr is not None:
                        bucket["ocrSum"] = bucket.get("ocrSum", 0.0) - old_ocr
                        bucket["ocrCount"] = max(0, bucket.get("ocrCount", 0) - 1)
                        overall_ocr_sum -= old_ocr
                        overall_ocr_count = max(0, overall_ocr_count - 1)
                    if old_meta is not None:
                        bucket["metaSum"] = bucket.get("metaSum", 0.0) - old_meta
                        bucket["metaCount"] = max(0, bucket.get("metaCount", 0) - 1)
                        overall_meta_sum -= old_meta
                        overall_meta_count = max(0, overall_meta_count - 1)

            # --- Add new values ---
            if new_doc is not None:
                total_docs += 1
                if new_rt not in rt_buckets:
                    rt_buckets[new_rt] = {
                        "resourceType": new_rt,
                        "documentCount": 0,
                        "avgOcrAccuracy": None,
                        "avgMetadataAccuracy": None,
                        "ocrSum": 0.0,
                        "ocrCount": 0,
                        "metaSum": 0.0,
                        "metaCount": 0,
                    }
                bucket = rt_buckets[new_rt]
                bucket["documentCount"] = bucket.get("documentCount", 0) + 1
                if new_ocr is not None:
                    bucket["ocrSum"] = bucket.get("ocrSum", 0.0) + new_ocr
                    bucket["ocrCount"] = bucket.get("ocrCount", 0) + 1
                    overall_ocr_sum += new_ocr
                    overall_ocr_count += 1
                if new_meta is not None:
                    bucket["metaSum"] = bucket.get("metaSum", 0.0) + new_meta
                    bucket["metaCount"] = bucket.get("metaCount", 0) + 1
                    overall_meta_sum += new_meta
                    overall_meta_count += 1

            # --- Recalculate averages and rebuild array ---
            by_resource_type = []
            for bucket in sorted(
                rt_buckets.values(),
                key=lambda b: b.get("documentCount", 0),
                reverse=True,
            ):
                if bucket.get("documentCount", 0) <= 0:
                    continue
                ocr_cnt = bucket.get("ocrCount", 0)
                bucket["avgOcrAccuracy"] = (
                    round((bucket["ocrSum"] / ocr_cnt) * 100.0, 1)
                    if ocr_cnt > 0 else None
                )
                meta_cnt = bucket.get("metaCount", 0)
                bucket["avgMetadataAccuracy"] = (
                    round((bucket["metaSum"] / meta_cnt) * 100.0, 1)
                    if meta_cnt > 0 else None
                )
                by_resource_type.append(bucket)

            report["byResourceType"] = by_resource_type
            report["totalDocuments"] = total_docs
            report["overallOcrAccuracy"] = (
                round((overall_ocr_sum / overall_ocr_count) * 100.0, 1)
                if overall_ocr_count > 0 else None
            )
            report["overallMetadataAccuracy"] = (
                round((overall_meta_sum / overall_meta_count) * 100.0, 1)
                if overall_meta_count > 0 else None
            )
            report["_overallOcrSum"] = overall_ocr_sum
            report["_overallOcrCount"] = overall_ocr_count
            report["_overallMetaSum"] = overall_meta_sum
            report["_overallMetaCount"] = overall_meta_count
            report["lastUpdated"] = datetime.now(timezone.utc).isoformat()

            try:
                if etag:
                    self._stats_container.replace_item(
                        item=doc_id, body=report, if_match=etag
                    )
                else:
                    self._stats_container.upsert_item(report)
                return
            except exceptions.CosmosAccessConditionFailedError:
                if attempt < max_retries - 1:
                    logger.debug(
                        "OCR report ETag conflict for '%s/%s' (attempt %d/%d), retrying...",
                        repo, collection, attempt + 1, max_retries,
                    )
                    continue
                logger.warning(
                    "OCR report update failed for '%s/%s' after %d retries",
                    repo, collection, max_retries,
                )
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
        max_retries: int = 3
    ) -> None:
        """Update collection statistics incrementally with optimistic concurrency control."""
        safe_id = _make_collection_safe_id(repo, collection)
        doc_id = f"{COLLECTION_STATS_PREFIX}{safe_id}"

        for attempt in range(max_retries):
            try:
                # Read with ETag
                stats = self._stats_container.read_item(item=doc_id, partition_key=doc_id)
                etag = stats.get("_etag")
                # Clamp any existing negative values (fixes data corruption from previous bugs)
                stats["pending"] = max(0, stats.get("pending", 0))
                stats["publishing"] = max(0, stats.get("publishing", 0))
                stats["published"] = max(0, stats.get("published", 0))
                stats["reviewed"] = max(0, stats.get("reviewed", 0))
                stats["errors"] = max(0, stats.get("errors", 0))
            except exceptions.CosmosResourceNotFoundError:
                logger.info("Creating new collection stats for '%s/%s' (id=%s)", repo, collection, doc_id)
                stats = {
                    "id": doc_id,
                    "repository": repo,
                    "collection": collection,
                    "totalItems": 0,
                    "pending": 0,
                    "publishing": 0,
                    "published": 0,
                    "reviewed": 0,
                    "errors": 0,
                    "completionRate": 0.0,
                    "ocrConfidenceSum": 0.0,
                    "ocrConfidenceCount": 0,
                    "avgOcrConfidence": None,
                }
                etag = None

            # Modify stats
            delta = 1 if increment else -1

            if old_status is not None and old_status.lower() == status.lower():
                if ocr_remove is None and ocr_add is None:
                    logger.debug(
                        "Status unchanged for collection '%s/%s' (status=%s), skipping update",
                        repo,
                        collection,
                        status.lower(),
                    )
                    return

            if old_status is None:
                # Full add/remove
                stats["totalItems"] = max(0, stats.get("totalItems", 0) + delta)
                _adjust_status_counter(stats, status, delta)
            else:
                # Status change only - decrement old, increment new
                _adjust_status_counter(stats, old_status, -1)
                _adjust_status_counter(stats, status, +1)

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

            # Write with ETag check for optimistic concurrency
            try:
                if etag:
                    # Use replace_item with if_match for existing documents
                    self._stats_container.replace_item(
                        item=doc_id,
                        body=stats,
                        if_match=etag
                    )
                else:
                    # Use upsert_item for new documents
                    self._stats_container.upsert_item(stats)
                return  # Success!
            except exceptions.CosmosAccessConditionFailedError:
                # ETag mismatch - another process modified it, retry
                if attempt < max_retries - 1:
                    logger.debug(
                        "Collection stats ETag conflict for '%s/%s' (attempt %d/%d), retrying...",
                        repo, collection, attempt + 1, max_retries
                    )
                    continue
                logger.warning(
                    "Collection stats update failed for '%s/%s' after %d retries due to ETag conflicts",
                    repo, collection, max_retries
                )
                raise
            except Exception as e:
                logger.error("Failed to update collection stats for '%s/%s': %s", repo, collection, str(e))
                raise


class _StatisticsServiceSingleton:
    """Singleton holder for StatisticsService instance."""
    instance: Optional[StatisticsService] = None


def get_statistics_service() -> StatisticsService:
    """Get or create Statistics service instance."""
    if _StatisticsServiceSingleton.instance is None:
        _StatisticsServiceSingleton.instance = StatisticsService()

    return _StatisticsServiceSingleton.instance
