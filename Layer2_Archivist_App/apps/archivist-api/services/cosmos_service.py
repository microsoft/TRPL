# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CosmosDB service for the Archivist API.

This service handles both transactional queries (CRUD operations) via the Cosmos DB SDK
and can delegate analytical queries (aggregations, GROUP BY) to Azure Synapse Link
when configured for faster retrieval.
"""
import logging
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Set, Tuple
import requests

from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.cosmos.database import DatabaseProxy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from models.schemas import AssetDetail, DocumentMetadata
from services.status_helpers import increment_status_counters

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DocumentVersionConflictError(ValueError):
    """Cosmos If-Match / ETag precondition failed (document changed elsewhere)."""

    DEFAULT_MESSAGE = (
        "Document was modified by another user. "
        "Please refresh and try again."
    )

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.DEFAULT_MESSAGE)


def _normalize_etag_for_compare(etag: Optional[str]) -> Optional[str]:
    """Compare etags from client headers and Cosmos without quote/format drift."""
    if etag is None:
        return None
    text = str(etag).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text


def _assert_client_etag_matches(client_etag: Optional[str], server_etag: Optional[str]) -> None:
    """Raise when the client If-Match no longer matches the live Cosmos document."""
    if client_etag is None or server_etag is None:
        return
    if _normalize_etag_for_compare(client_etag) != _normalize_etag_for_compare(server_etag):
        raise DocumentVersionConflictError()


def _find_asset_index(asset_details: Any, asset_id: str) -> Optional[int]:
    """Return the index of an asset in asset_details by asset_id or legacy id field."""
    if not isinstance(asset_details, list):
        return None
    for idx, asset in enumerate(asset_details):
        if not isinstance(asset, dict):
            continue
        if asset.get("asset_id") == asset_id or asset.get("id") == asset_id:
            return idx
    return None


# Cosmos SQL fragment for client-side collection summary (asset_avg_confidence only; matches statistics rebuild)
_CLIENT_AGG_DOC_OCR = """
                    "docOcr": IIF(IS_DEFINED(c.asset_avg_confidence) AND IS_NUMBER(c.asset_avg_confidence), c.asset_avg_confidence, null)
"""


def _accumulate_client_side_doc_ocr(bucket: Dict[str, Any], doc: Dict[str, Any]) -> None:
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


# Singleton CosmosClient instance
_cosmos_client: Optional[CosmosClient] = None
_requests_session: Optional[requests.Session] = None


def _resolve_cosmos_credential(key: Optional[str]):
    """Prefer account key when provided, otherwise use AAD for local-auth-disabled accounts."""
    if key and str(key).strip():
        if settings.environment != "local":
            raise ValueError(
                "COSMOS_DB_KEY authentication is allowed only when ENVIRONMENT=local"
            )
        return key.strip()
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def get_cosmos_client() -> CosmosClient:
    """
    Get or create singleton CosmosClient instance with connection pooling.
    This is a shared singleton used across all archivist-api modules.
    
    Returns:
        Singleton CosmosClient instance
    """
    global _cosmos_client, _requests_session
    
    if _cosmos_client is None:
        if not settings.cosmos_db_endpoint:
            raise ValueError("CosmosDB endpoint must be configured")

        # Create requests session with connection pooling
        _requests_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
        _requests_session.mount('https://', adapter)

        # Create CosmosClient with custom transport
        _cosmos_client = CosmosClient(
            url=settings.cosmos_db_endpoint,
            credential=_resolve_cosmos_credential(settings.cosmos_db_key),
            connection_mode=settings.cosmos_db_connection_mode,
            transport=RequestsTransport(session=_requests_session, session_owner=False)
        )

        logger.info("Created singleton CosmosClient with connection pooling")

    return _cosmos_client


class CosmosDBService:  # pylint: disable=too-many-public-methods
    """Service for Azure CosmosDB operations."""

    def __init__(self):
        """Initialize CosmosDB service."""
        self._client: Optional[CosmosClient] = None
        self._database: Optional[DatabaseProxy] = None
        self._container: Optional[ContainerProxy] = None
        self._audit_container: Optional[ContainerProxy] = None

        # Initialize connection
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB."""
        try:
            if not settings.cosmos_db_endpoint:
                raise ValueError("CosmosDB endpoint must be configured")

            if not settings.cosmos_db_database_name:
                raise ValueError("CosmosDB database name must be configured")

            logger.info("Connecting to CosmosDB at %s", settings.cosmos_db_endpoint)

            # Get singleton CosmosDB client with connection pooling
            self._client = get_cosmos_client()

            # Get database reference
            self._database = self._client.get_database_client(
                settings.cosmos_db_database_name
            )

            # Get container (OCR)
            if not settings.cosmos_db_container_name:
                raise ValueError("Container name must be configured")

            self._container = self._database.get_container_client(
                settings.cosmos_db_container_name
            )
            logger.info("Connected to container: %s", settings.cosmos_db_container_name)
            # Get audit container
            if settings.cosmos_db_audit_container_name:
                self._audit_container = self._database.get_container_client(
                    settings.cosmos_db_audit_container_name
                )
                logger.info(
                    "Connected to audit container: %s",
                    settings.cosmos_db_audit_container_name,
                )
            else:
                logger.warning(
                    "Audit container not configured - audit features will be disabled"
                )

            logger.info(
                "Successfully connected to CosmosDB database '%s'",
                settings.cosmos_db_database_name,
            )

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to connect to CosmosDB: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error connecting to CosmosDB: %s", str(e))
            raise

    def get_container(self) -> ContainerProxy:
        """Get container reference."""
        if not self._container:
            raise ValueError("Container not configured")
        return self._container

    def _normalize_value(self, val: Any) -> Optional[str]:
        """Normalize a Cosmos query result to a string or None.

        Cosmos may return scalar values or small objects depending on the
        query form. This helper returns a consistent string for sorting and
        filtering, or None for empty/invalid values.
        """
        if val is None:
            return None
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            # Try to extract the first non-empty string value
            for v in val.values():
                if isinstance(v, str) and v:
                    return v
            # Fallback to string representation
            try:
                return str(val)
            except Exception:
                return None
        # For other scalar types, stringify
        try:
            return str(val)
        except Exception:
            return None

    def get_audit_container(self) -> ContainerProxy:
        """Get audit container reference."""
        if not self._audit_container:
            raise ValueError("Audit container not configured")
        return self._audit_container

    def build_query_filters(
        self,
        status_filter: Optional[str] = None,
        repository_filter: Optional[str] = None,
        collection_filter: Optional[str] = None,
        resource_type_filter: Optional[str] = None,
        source_filter: Optional[str] = None,
        creator_filter: Optional[str] = None,
        recipient_filter: Optional[str] = None,
        min_confidence: Optional[float] = None,
        max_confidence: Optional[float] = None,
        title_contains: Optional[str] = None,
        exclude_errors: Optional[bool] = None,
        exclude_zero_assets: Optional[bool] = None,
    ) -> tuple[str, str, List[Dict[str, Any]]]:
        """
        Build WHERE clause and parameters for Cosmos DB query based on filters.

        Args:
            status_filter: Filter by status (pending, processing, completed, failed, published, publishing, reviewed)
            repository_filter: Filter by repository name
            collection_filter: Filter by collection name
            resource_type_filter: Filter by resource type name
            source_filter: Filter by Digital Item Publisher (source)
            creator_filter: Filter by Creator name
            recipient_filter: Filter by Recipient name
            min_confidence: Filter by minimum OCR confidence score (optional)
            max_confidence: Filter by maximum OCR confidence score (optional)
            title_contains: Filter documents where title contains this text
            exclude_errors: If True, exclude documents where any pipeline stage has 'error' or 'failed' status
            exclude_zero_assets: If True, exclude documents with no assets (asset_details array is empty or missing)

        Returns:
            Tuple of (WHERE clause string, list of query parameters)
        """
        where_clauses = []
        parameters = []

        status_lower = (
            str(status_filter).strip().lower() if status_filter else None
        )
        if status_lower:
            if status_lower == "published":
                where_clauses.append(
                    "(IS_DEFINED(c.archivist_status) AND c.archivist_status != null "
                    "AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'published')"
                )
            elif status_lower == "publishing":
                where_clauses.append(
                    "(IS_DEFINED(c.archivist_status) AND c.archivist_status != null "
                    "AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'publishing')"
                )
            elif status_lower == "reviewed":
                where_clauses.append(
                    "(IS_DEFINED(c.archivist_status) AND c.archivist_status != null "
                    "AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'reviewed')"
                )
            elif status_lower == "pending":
                where_clauses.append(
                    "(NOT IS_DEFINED(c.archivist_status) OR c.archivist_status = null OR c.archivist_status = '' "
                    "OR (IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'pending'))"
                )
            elif status_lower == "failed":
                where_clauses.append(
                    "(IS_DEFINED(c.archivist_status) AND c.archivist_status != null "
                    "AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')"
                )

        if repository_filter:
            repo_val = str(repository_filter).strip()
            # Treat 'Unknown' as missing/null repository values (case-insensitive for this special case)
            if repo_val.lower() == "unknown":
                where_clauses.append("(NOT IS_DEFINED(c.metadata.Repository) OR c.metadata.Repository = null)")
            else:
                # Case-insensitive (same as Collection): avoids URL/UI casing mismatches vs Cosmos.
                where_clauses.append(
                    "((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label) AND "
                    "LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )
                parameters.append({"name": "@repository", "value": repo_val})

        if collection_filter:
            col_val = str(collection_filter).strip()
            # Treat 'Unknown' as missing/null collection values (or object without label)
            if col_val.lower() == "unknown":
                where_clauses.append(
                    "(NOT IS_DEFINED(c.metadata.Collection) OR c.metadata.Collection = null "
                    "OR (IS_OBJECT(c.metadata.Collection) AND (NOT IS_DEFINED(c.metadata.Collection.label) OR c.metadata.Collection.label = null)))"
                )
            else:
                # Support collection stored as string or object with 'label'
                where_clauses.append(
                    "((IS_STRING(c.metadata.Collection) AND LOWER(c.metadata.Collection) = LOWER(@collection)) "
                    "OR (IS_OBJECT(c.metadata.Collection) AND LOWER(c.metadata.Collection.label) = LOWER(@collection)))"
                )
                parameters.append({"name": "@collection", "value": col_val})

        if resource_type_filter:
            rt_val = str(resource_type_filter).strip()
            if rt_val.lower() == "unknown":
                where_clauses.append(
                    '(NOT IS_DEFINED(c.metadata["Resource Type"]) '
                    'OR IS_NULL(c.metadata["Resource Type"]) '
                    'OR c.metadata["Resource Type"] = "")'
                )
            else:
                where_clauses.append(
                    '((IS_STRING(c.metadata["Resource Type"]) AND '
                    'LOWER(c.metadata["Resource Type"]) = LOWER(@resourceType)) OR '
                    '(IS_OBJECT(c.metadata["Resource Type"]) AND '
                    'IS_STRING(c.metadata["Resource Type"].label) AND '
                    'LOWER(c.metadata["Resource Type"].label) = LOWER(@resourceType)))'
                )
                parameters.append({"name": "@resourceType", "value": rt_val})

        if source_filter:
            src_val = str(source_filter).strip()
            if src_val.lower() == "unknown":
                where_clauses.append(
                    '(NOT IS_DEFINED(c.metadata["Digital Item Publisher"]) '
                    'OR IS_NULL(c.metadata["Digital Item Publisher"]) '
                    'OR c.metadata["Digital Item Publisher"] = "")'
                )
            else:
                where_clauses.append(
                    '(IS_STRING(c.metadata["Digital Item Publisher"]) AND '
                    'LOWER(c.metadata["Digital Item Publisher"]) = LOWER(@source))'
                )
                parameters.append({"name": "@source", "value": src_val})

        if creator_filter:
            crt_val = str(creator_filter).strip()
            if crt_val.lower() == "unknown":
                where_clauses.append(
                    '(NOT IS_DEFINED(c.metadata.Creator) '
                    'OR IS_NULL(c.metadata.Creator) '
                    'OR c.metadata.Creator = "")'
                )
            else:
                where_clauses.append(
                    '((IS_STRING(c.metadata.Creator) AND '
                    'LOWER(c.metadata.Creator) = LOWER(@creator)) OR '
                    '(IS_OBJECT(c.metadata.Creator) AND '
                    'IS_STRING(c.metadata.Creator.label) AND '
                    'LOWER(c.metadata.Creator.label) = LOWER(@creator)))'
                )
                parameters.append({"name": "@creator", "value": crt_val})

        if recipient_filter:
            rcp_val = str(recipient_filter).strip()
            if rcp_val.lower() == "unknown":
                where_clauses.append(
                    '(NOT IS_DEFINED(c.metadata.Recipient) '
                    'OR IS_NULL(c.metadata.Recipient) '
                    'OR c.metadata.Recipient = "")'
                )
            else:
                where_clauses.append(
                    '((IS_STRING(c.metadata.Recipient) AND '
                    'LOWER(c.metadata.Recipient) = LOWER(@recipient)) OR '
                    '(IS_OBJECT(c.metadata.Recipient) AND '
                    'IS_STRING(c.metadata.Recipient.label) AND '
                    'LOWER(c.metadata.Recipient.label) = LOWER(@recipient)))'
                )
                parameters.append({"name": "@recipient", "value": rcp_val})

        # Add confidence filters based on average OCR confidence across assets
        if min_confidence is not None:
            where_clauses.append(
                "(IS_DEFINED(c.asset_avg_confidence) AND IS_NUMBER(c.asset_avg_confidence) "
                "AND c.asset_avg_confidence >= @min_confidence)"
            )
            parameters.append({"name": "@min_confidence", "value": min_confidence})

        if max_confidence is not None:
            where_clauses.append(
                "(IS_DEFINED(c.asset_avg_confidence) AND IS_NUMBER(c.asset_avg_confidence) "
                "AND c.asset_avg_confidence <= @max_confidence)"
            )
            parameters.append({"name": "@max_confidence", "value": max_confidence})

        if title_contains:
            # Search in both Title and Creation Date fields (guard types: LOWER/CONTAINS on non-strings
            # causes Cosmos BadRequest "One of the input values is invalid").
            where_clauses.append(
                "((IS_DEFINED(c.metadata.Title) AND IS_STRING(c.metadata.Title) AND "
                "CONTAINS(LOWER(c.metadata.Title), LOWER(@title_contains))) OR "
                "(IS_DEFINED(c.metadata[\"Creation Date\"]) AND IS_STRING(c.metadata[\"Creation Date\"]) AND "
                "CONTAINS(LOWER(c.metadata[\"Creation Date\"]), LOWER(@title_contains))))"
            )
            parameters.append({"name": "@title_contains", "value": title_contains})

        # Exclude documents with error status in any pipeline stage
        if exclude_errors and status_lower != "failed":
            error_exclusion = """(
                (NOT IS_DEFINED(c.related_assets_status) OR (c.related_assets_status != 'error' AND c.related_assets_status != 'failed')) AND
                (NOT IS_DEFINED(c.asset_details_status) OR (c.asset_details_status != 'error' AND c.asset_details_status != 'failed')) AND
                (NOT IS_DEFINED(c.original_file_status) OR (c.original_file_status != 'error' AND c.original_file_status != 'failed')) AND
                (NOT IS_DEFINED(c.resource_type_batch_status) OR (c.resource_type_batch_status != 'error' AND c.resource_type_batch_status != 'failed')) AND
                (NOT IS_DEFINED(c.ocr_batch_status) OR (c.ocr_batch_status != 'error' AND c.ocr_batch_status != 'failed')) AND
                (NOT IS_DEFINED(c.ocr_processing_status) OR (c.ocr_processing_status != 'error' AND c.ocr_processing_status != 'failed')) AND
                (NOT IS_DEFINED(c.metadata_extraction_status) OR (c.metadata_extraction_status != 'error' AND c.metadata_extraction_status != 'failed'))
            )"""
            where_clauses.append(error_exclusion)

        # Exclude documents with no assets
        if exclude_zero_assets and status_lower != "failed":
            where_clauses.append("(IS_DEFINED(c.asset_details) AND ARRAY_LENGTH(c.asset_details) > 0)")

        where_clause = ""
        if where_clauses:
            where_clause = "WHERE " + " AND ".join(where_clauses)

        # Cosmos item id (c.id) — required for data-ingest / read_item. record_id is often a
        # business field and may be null; SELECT c.record_id caused empty doc_id lists and
        # "No documents found" while COUNT-based excluded-TRC totals were still non-zero.
        query = f"SELECT c.id FROM c {where_clause}"
        
        # Log the constructed WHERE clause for debugging
        logger.debug("Constructed WHERE clause: %s", where_clause)
        logger.debug("Query parameters: %s", parameters)

        return query, where_clause, parameters

    @staticmethod
    def portal_publish_date_sql_predicate() -> str:
        """
        Cosmos WHERE fragment: metadata has non-empty Date Published to Portal.
        Aligns with portal_publish_metadata_to_string / document_has_portal_publish_date (TRC gate).

        Uses bracket notation for sub-properties because "value" and "name" are
        reserved keywords in Cosmos SQL and cause BadRequest when used with dot notation.
        """
        m = 'c.metadata["Date Published to Portal"]'
        return (
            "(IS_DEFINED(" + m + ") AND " + m + " != null AND ("
            "(IS_STRING(" + m + ") AND " + m + " != \"\") OR "
            "IS_NUMBER(" + m + ") OR "
            "(IS_OBJECT(" + m + ") AND ("
            '(IS_DEFINED(' + m + '["label"]) AND IS_STRING(' + m + '["label"]) AND ' + m + '["label"] != "") OR '
            '(IS_DEFINED(' + m + '["value"]) AND IS_STRING(' + m + '["value"]) AND ' + m + '["value"] != "") OR '
            '(IS_DEFINED(' + m + '["name"]) AND IS_STRING(' + m + '["name"]) AND ' + m + '["name"] != "") OR '
            '(IS_DEFINED(' + m + '["text"]) AND IS_STRING(' + m + '["text"]) AND ' + m + '["text"] != "")'
            ")) OR "
            "(IS_ARRAY(" + m + ") AND ARRAY_LENGTH(" + m + ") > 0)"
            "))"
        )

    _INGEST_ID_SELECT_PREFIX = "SELECT c.id FROM c "

    def list_record_ids_matching_ingest_but_missing_trc(
        self,
        query: str,
        where_clause: str,
        exclusion_clause: str,
        portal_pred: str,
        parameters: Optional[List[Dict[str, Any]]],
        max_ids_returned: int = 300,
    ) -> Tuple[List[str], int, bool]:
        """
        Cosmos document ids (c.id) that match bulk-ingest filters + pipeline exclusions but NOT
        portal_publish_date_sql_predicate().

        Used to notify users which documents were not queued because Date Published to Portal is missing/empty.
        """
        if where_clause:
            miss_q = f"{query} AND {exclusion_clause} AND NOT ({portal_pred})"
        else:
            miss_q = f"{query} WHERE {exclusion_clause} AND NOT ({portal_pred})"

        if not miss_q.startswith(self._INGEST_ID_SELECT_PREFIX):
            logger.warning(
                "list_record_ids_matching_ingest_but_missing_trc: unexpected query prefix, skipping: %s",
                miss_q[:80],
            )
            return [], 0, False

        remainder = miss_q[len(self._INGEST_ID_SELECT_PREFIX) :]
        count_q = f"SELECT VALUE COUNT(1) FROM c {remainder}"
        container = self.get_container()
        params = parameters if parameters else None

        try:
            count_result = list(
                container.query_items(
                    query=count_q,
                    parameters=params,
                    enable_cross_partition_query=True,
                )
            )
            total = int(count_result[0]) if count_result else 0
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Missing-TRC count query failed: %s", e.message)
            return [], 0, False

        if total == 0:
            return [], 0, False

        ids_q = f"{miss_q} ORDER BY c.id OFFSET 0 LIMIT {max_ids_returned}"
        ids: List[str] = []
        try:
            for item in container.query_items(
                query=ids_q,
                parameters=params,
                enable_cross_partition_query=True,
            ):
                rid = item.get("id") or item.get("record_id")
                if rid:
                    ids.append(str(rid))
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Missing-TRC id list query failed: %s", e.message)
            return [], total, True

        truncated = total > len(ids)
        return ids, total, truncated

    def document_has_portal_publish_date(self, document: Dict[str, Any]) -> bool:
        """True if document metadata has non-empty Date Published to Portal (mirrors cosmos_ingest normalization)."""
        meta = document.get("metadata") or {}
        val = meta.get("Date Published to Portal")
        if val is None:
            return False
        if isinstance(val, str):
            return bool(val.strip())
        if isinstance(val, dict):
            for key in ("label", "value", "name", "text"):
                inner = val.get(key)
                if inner is not None and str(inner).strip():
                    return True
            return False
        if isinstance(val, (list, tuple)):
            joined = ", ".join(str(x) for x in val if x is not None and str(x).strip())
            return bool(joined.strip())
        if isinstance(val, int) or (isinstance(val, float) and not math.isnan(val)):
            return True
        return bool(str(val).strip())

    def get_all_documents(
        self,
        order_by: str = "created_at",
        descending: bool = True,
        page_number: Optional[int] = 1,
        page_size: Optional[int] = 20,
        status_filter: Optional[str] = None,
        repository_filter: Optional[str] = None,
        collection_filter: Optional[str] = None,
        resource_type_filter: Optional[str] = None,
        source_filter: Optional[str] = None,
        creator_filter: Optional[str] = None,
        recipient_filter: Optional[str] = None,
        min_confidence: Optional[float] = None,
        max_confidence: Optional[float] = None,
        title_contains: Optional[str] = None,
        exclude_errors: Optional[bool] = None,
        exclude_zero_assets: Optional[bool] = None,
    ) -> tuple[List[DocumentMetadata], Optional[str], int]:
        """
        Get documents from the container with pagination and filtering support.

        Args:
            order_by: Field to order by (default: created_at, currently not used)
            descending: Sort in descending order (default: True)
            page_number: Page number for pagination (default: 1)
            page_size: Number of items per page (default: 20)
            status_filter: Filter by status (pending, processing, completed, failed)
            repository_filter: Filter by repository name
            collection_filter: Filter by collection name
            resource_type_filter: Filter by resource type name
            min_confidence: Filter by minimum OCR confidence score (optional)
            max_confidence: Filter by maximum OCR confidence score (optional)
            title_contains: Filter documents where title contains this text
            exclude_errors: If True, exclude documents where any pipeline stage has 'error' or 'failed' status

        Returns:
            Tuple of (list of document metadata, next continuation token or None, total matching records)

        Note:
            order_by parameter is accepted for API compatibility but not currently
            used due to query complexity. All queries order by created_at.
        """
        try:
            container = self.get_container()

            # Build query with only essential fields for list view to reduce data transfer
            # NOTE: Removed ORDER BY for performance - cross-partition ORDER BY is very slow
            # Documents will be returned in natural partition order (fast)

            # Build WHERE clause based on filters using shared method
            # pylint: disable=duplicate-code
            _, where_clause, parameters = self.build_query_filters(
                status_filter=status_filter,
                repository_filter=repository_filter,
                collection_filter=collection_filter,
                resource_type_filter=resource_type_filter,
                source_filter=source_filter,
                creator_filter=creator_filter,
                recipient_filter=recipient_filter,
                min_confidence=min_confidence,
                max_confidence=max_confidence,
                title_contains=title_contains,
                exclude_errors=exclude_errors,
                exclude_zero_assets=exclude_zero_assets,
            )
            # pylint: enable=duplicate-code

            order_expr = None
            if order_by:
                order_map = {
                    "created_at": "c.metadata['Creation Date']",
                    "title": "c.metadata.Title",
                    "confidence": "c.asset_avg_confidence",
                }
                order_expr = (
                    order_map.get(order_by, order_map.get(order_by.lower()))
                    if isinstance(order_by, str)
                    else None
                )

            order_clause = ""
            if order_expr:
                order_clause = (
                    f"ORDER BY {order_expr} {'DESC' if descending else 'ASC'}"
                )

            query = f"""
            SELECT
                c.id,
                c.record_id,
                c.created_at,
                c.asset_count,
                c.archivist_modified_ts,
                c.archivist_status,
                c.ocr_processing_status,
                c.related_assets_status,
                c.asset_details_status,
                c.original_file_status,
                c.ocr_batch_status,
                c.metadata_extraction_status,
                c.metadata as metadata,
                c.asset_avg_confidence as confidence,
                c.metadata_extraction_confidence as metadata_extraction_confidence,
                c.asset_details,
                c.published_by,
                c.published_at,
                c.validated_by,
                c.validated_at,
                c.visual_description_possible,
                c.visual_detailed_description_original_blob_url,
                c.visual_detailed_description_flexible_blob_url

            FROM c
            {where_clause}
            {order_clause}
            """
            logger.info(
                "Querying documents with filters: status=%s, repository=%s, collection=%s, resource_type=%s, confidence=%.2f to %.2f",
                status_filter,
                repository_filter,
                collection_filter,
                resource_type_filter,
                min_confidence if min_confidence is not None else 0.0,
                max_confidence if max_confidence is not None else 1.0,
            )
            logger.info("Constructed WHERE clause: %s", where_clause)
            logger.info("Query parameters: %s", parameters)

            # Compute total matching records with the same filters (fast COUNT)
            count_query = f"SELECT VALUE COUNT(1) FROM c {where_clause}"
            count_result = list(
                container.query_items(
                    query=count_query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True,
                )
            )
            total_matching = int(count_result[0]) if count_result else 0

            # Create query with continuation token support
            # No ORDER BY = much faster cross-partition queries
            start_time = time.time()

            paginated_query = f"{query} OFFSET {(page_number - 1) * page_size} LIMIT {page_size}"
            items = container.query_items(
                query=paginated_query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True,
                max_item_count=page_size,
            )


            # Map items to DocumentMetadata with full metadata object
            documents = [
                DocumentMetadata(
                    **{
                        "id": item.get("id"),
                        "record_id": item.get("record_id"),
                        "created_at": item.get("created_at"),
                        "asset_count": item.get("asset_count"),
                        "archivist_modified_ts": item.get("archivist_modified_ts"),
                        "archivist_status": item.get("archivist_status"),
                        "ocr_processing_status": item.get("ocr_processing_status"),
                        "related_assets_status": item.get("related_assets_status"),
                        "asset_details_status": item.get("asset_details_status"),
                        "original_file_status": item.get("original_file_status"),
                        "ocr_batch_status": item.get("ocr_batch_status"),
                        "metadata_extraction_status": item.get("metadata_extraction_status"),
                        "visual_description_possible": item.get("visual_description_possible"),
                        "visual_detailed_description_original_blob_url": item.get("visual_detailed_description_original_blob_url"),
                        "visual_detailed_description_flexible_blob_url": item.get("visual_detailed_description_flexible_blob_url"),
                        "metadata": {
                            **item.get("metadata", {}),
                            "Creator": item.get("metadata", {}).get("Creator") or "Unknown",
                            "Recipient": item.get("metadata", {}).get("Recipient") or "Unknown",
                        },
                        "ocr_result": (
                            {"confidence": item.get("confidence")}
                            if item.get("confidence") is not None
                            else None
                        ),
                        "metadata_extraction_confidence": item.get("metadata_extraction_confidence"),
                        "asset_details": item.get("asset_details", []),
                        "published_by": item.get("published_by"),
                        "published_at": item.get("published_at"),
                        "validated_by": item.get("validated_by"),
                        "validated_at": item.get("validated_at"),
                    }
                )
                for item in items
            ]

            query_time = time.time() - start_time
            logger.info(
                "Retrieved %d documents in %.2f seconds",
                len(documents),
                query_time
            )

            return documents, total_matching

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to query documents: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error querying documents: %s", str(e))
            raise

    def get_document_by_id(
        self, document_id: str, partition_key: Optional[str] = None
    ) -> Optional[DocumentMetadata]:
        """
        Retrieve a document by ID.

        Args:
            document_id: Document ID
            partition_key: Partition key value (if different from ID)

        Returns:
            Document metadata or None if not found
        """
        try:
            container = self.get_container()

            # Use document_id as partition key if not specified
            pk = partition_key if partition_key is not None else document_id

            result = container.read_item(item=document_id, partition_key=pk)

            logger.info("Retrieved document with id: %s", document_id)
            return DocumentMetadata(**result)

        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to retrieve document: %s", e.message)
            raise

    def get_document_raw(
        self, document_id: str, partition_key: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a document by ID as raw dict (no Pydantic conversion).

        Use this when you need the exact Cosmos DB document structure,
        e.g., for statistics updates where metadata format matters.

        Args:
            document_id: Document ID
            partition_key: Partition key value (if different from ID)

        Returns:
            Raw document dict or None if not found
        """
        try:
            container = self.get_container()
            pk = partition_key if partition_key is not None else document_id
            result = container.read_item(item=document_id, partition_key=pk)
            return dict(result)
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to retrieve raw document: %s", e.message)
            raise

    def get_existing_document_ids(self, record_ids: List[str]) -> Set[str]:
        """
        Return which of the given identifiers exist in the main document container,
        matching either document id or c.record_id.
        """
        unique = [rid for rid in dict.fromkeys((r or "").strip() for r in record_ids) if rid]
        if not unique:
            return set()
        container = self.get_container()
        params = [{"name": f"@r{i}", "value": rid} for i, rid in enumerate(unique)]
        placeholders = ", ".join(f"@r{i}" for i in range(len(unique)))
        query = (
            f"SELECT c.id, c.record_id FROM c WHERE c.id IN ({placeholders}) "
            f"OR c.record_id IN ({placeholders})"
        )
        rows = list(
            container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        matched: Set[str] = set()
        for row in rows:
            doc_id = row.get("id")
            if doc_id is not None and doc_id != "":
                matched.add(str(doc_id).strip())
            rk = row.get("record_id")
            if rk is not None and rk != "":
                matched.add(str(rk).strip())
        return set(unique) & matched

    def get_record_catalog_fields_batch(
        self, record_ids: List[str]
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """
        Return a map of requested id -> {title, repository, collection} for documents
        whose c.id or c.record_id is in record_ids. One query for the whole batch.
        """
        unique: List[str] = [
            rid for rid in dict.fromkeys((r or "").strip() for r in record_ids) if rid
        ]
        if not unique:
            return {}

        repo_sql = """IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
            IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                    c.metadata.Repository.label, null)), null)"""

        col_sql = """IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
            IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                    c.metadata.Collection.label, null)), null)"""

        title_sql = """IIF(IS_DEFINED(c.title) AND IS_STRING(c.title) AND c.title != "", c.title,
            IIF(IS_DEFINED(c.metadata.Title) AND c.metadata.Title != null,
                IIF(IS_STRING(c.metadata.Title), c.metadata.Title,
                    IIF(IS_OBJECT(c.metadata.Title) AND IS_DEFINED(c.metadata.Title.label),
                        c.metadata.Title.label, null)), null))"""

        placeholders = ", ".join(f"@r{i}" for i in range(len(unique)))
        params: List[Dict[str, Any]] = [{"name": f"@r{i}", "value": rid} for i, rid in enumerate(unique)]
        query = f"""
            SELECT c.id, c.record_id, {title_sql} AS title, {repo_sql} AS repository, {col_sql} AS collection
            FROM c
            WHERE c.id IN ({placeholders}) OR c.record_id IN ({placeholders})
        """

        container = self.get_container()
        rows = list(
            container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        def _norm(val: Any) -> Optional[str]:
            if val is None:
                return None
            s = str(val).strip()
            return s if s else None

        out: Dict[str, Dict[str, Optional[str]]] = {}
        for row in rows:
            pack = {
                "title": _norm(row.get("title")),
                "repository": _norm(row.get("repository")),
                "collection": _norm(row.get("collection")),
            }
            did = _norm(row.get("id"))
            rid = _norm(row.get("record_id"))
            if did:
                out[did] = pack
            if rid:
                out[rid] = pack
        return out

    def get_document_asset_by_id(
        self, document_id: str, asset_id: str, partition_key: Optional[str] = None
    ) -> Optional[AssetDetail]:
        """
        Retrieve a specific asset from a document by document ID and asset ID.

        Uses a Cosmos DB query to directly extract the matching asset from the document.

        Args:
            document_id: Document ID
            asset_id: Asset ID to find within the document
            partition_key: Partition key value (if different from document_id)

        Returns:
            Asset dictionary if found, None if document or asset not found
        """
        try:
            container = self.get_container()

            # Use document_id as partition key if not specified
            pk = partition_key if partition_key is not None else document_id

            # Query to extract the specific asset from the document's asset_details array
            # asset_id may be stored as 'asset_id' or 'id' depending on ingestion
            query = """
                SELECT VALUE asset
                FROM c
                JOIN asset IN c.asset_details
                WHERE c.id = @document_id
                  AND (asset.asset_id = @asset_id OR asset.id = @asset_id)
            """
            parameters = [
                {"name": "@document_id", "value": document_id},
                {"name": "@asset_id", "value": asset_id},
            ]

            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    partition_key=pk,
                )
            )

            if items:
                logger.info(
                    "Retrieved asset '%s' from document '%s'", asset_id, document_id
                )
                return AssetDetail(**items[0])

            logger.warning(
                "Asset '%s' not found in document '%s'", asset_id, document_id
            )
            return None

        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to retrieve asset: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error retrieving asset: %s", str(e))
            raise

    def get_distinct_repositories(self) -> List[str]:
        """
        Return a sorted list of distinct repository names present in the container.

        Returns:
            List of repository names (strings). Empty list if none found.
        """
        try:
            container = self.get_container()
            query = "SELECT DISTINCT IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null, IIF(IS_STRING(c.metadata.Repository),  c.metadata.Repository, IIF(IS_OBJECT(c.metadata.Repository), c.metadata.Repository.label, 'Unknown')), 'Unknown') FROM c"
            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            # Normalize results to strings. Cosmos may return scalar values or
            # small objects depending on query form; defensively handle both.
            repos = [r for r in (self._normalize_value(r) for r in results) if r]
            repos = sorted(repos, key=lambda s: s.lower())
            logger.info("Found %d distinct repositories", len(repos))
            return repos
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct repositories: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching repositories: %s", str(e))
            raise

    def get_distinct_collections(self, repository: Optional[str] = None) -> List[str]:
        """
        Return a sorted list of distinct collection names. If `repository` is provided,
        only collections within that repository are returned.

        Args:
            repository: Optional repository name to filter collections by.

        Returns:
            List of collection names (strings). Empty list if none found.
        """
        try:
            container = self.get_container()
            if repository:
                query = (
                    "SELECT DISTINCT VALUE IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null, IIF(IS_STRING(c.metadata.Collection),  c.metadata.Collection, IIF(IS_OBJECT(c.metadata.Collection), c.metadata.Collection.label, 'Unknown')), 'Unknown')  FROM c "
                    "WHERE ((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label) AND "
                    "LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )
                parameters = [{"name": "@repository", "value": repository}]
            else:
                query = "SELECT DISTINCT VALUE IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null, IIF(IS_STRING(c.metadata.Collection),  c.metadata.Collection, IIF(IS_OBJECT(c.metadata.Collection), c.metadata.Collection.label, 'Unknown')), 'Unknown') FROM c"
                parameters = None

            results = list(
                container.query_items(
                    query=query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True,
                )
            )

            collections = [c for c in (self._normalize_value(c) for c in results) if c]
            collections = sorted(collections, key=lambda s: s.lower())
            logger.info(
                "Found %d distinct collections (repository=%s)",
                len(collections),
                repository,
            )
            return collections
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct collections: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching collections: %s", str(e))
            raise

    def get_distinct_resource_types(self) -> List[str]:
        """
        Return a sorted list of distinct resource type names present in the container.
        Includes "Unknown" when records with missing, null, or empty Resource Type exist.

        Returns:
            List of resource type names (strings). Empty list if none found.
        """
        try:
            container = self.get_container()
            query = 'SELECT DISTINCT VALUE c.metadata["Resource Type"] FROM c WHERE c.metadata["Resource Type"] != null'
            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            # Normalize results to strings
            resource_types = [r for r in (self._normalize_value(r) for r in results) if r]
            resource_types = sorted(resource_types, key=lambda s: s.lower())

            unknown_query = (
                'SELECT VALUE COUNT(1) FROM c '
                'WHERE NOT IS_DEFINED(c.metadata["Resource Type"]) '
                'OR IS_NULL(c.metadata["Resource Type"]) '
                'OR c.metadata["Resource Type"] = ""'
            )
            unknown_count_results = list(
                container.query_items(query=unknown_query, enable_cross_partition_query=True)
            )
            if unknown_count_results and unknown_count_results[0] > 0:
                resource_types.append("Unknown")

            logger.info("Found %d distinct resource types", len(resource_types))
            return resource_types
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct resource types: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching resource types: %s", str(e))
            raise

    def get_distinct_sources(self) -> List[str]:
        """
        Return a sorted list of distinct Digital Item Publisher values.
        Includes "Unknown" when records with missing, null, or empty values exist.
        """
        try:
            container = self.get_container()
            query = (
                'SELECT DISTINCT VALUE c.metadata["Digital Item Publisher"] '
                'FROM c WHERE c.metadata["Digital Item Publisher"] != null'
            )
            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            sources = [r for r in (self._normalize_value(r) for r in results) if r]
            sources = sorted(sources, key=lambda s: s.lower())

            unknown_query = (
                'SELECT VALUE COUNT(1) FROM c '
                'WHERE NOT IS_DEFINED(c.metadata["Digital Item Publisher"]) '
                'OR IS_NULL(c.metadata["Digital Item Publisher"]) '
                'OR c.metadata["Digital Item Publisher"] = ""'
            )
            unknown_count_results = list(
                container.query_items(query=unknown_query, enable_cross_partition_query=True)
            )
            if unknown_count_results and unknown_count_results[0] > 0:
                sources.append("Unknown")

            logger.info("Found %d distinct sources", len(sources))
            return sources
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct sources: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching sources: %s", str(e))
            raise

    def get_distinct_creators(self, repository: Optional[str] = None, collection: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return distinct Creator values grouped by repository and collection.
        Includes "Unknown" when records with missing, null, or empty Creator exist.

        Args:
            repository: Optional repository name to filter by.
            collection: Optional collection name to filter by.

        Returns:
            List of {"creator": str, "repository": str, "collection": str} dicts, sorted by creator name.
        """
        try:
            container = self.get_container()

            where_clauses = []
            parameters: List[Dict[str, Any]] = []

            if repository:
                where_clauses.append(
                    "((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label) AND "
                    "LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )
                parameters.append({"name": "@repository", "value": repository})

            if collection:
                where_clauses.append(
                    "((IS_STRING(c.metadata.Collection) AND LOWER(c.metadata.Collection) = LOWER(@collection)) "
                    "OR (IS_OBJECT(c.metadata.Collection) AND LOWER(c.metadata.Collection.label) = LOWER(@collection)))"
                )
                parameters.append({"name": "@collection", "value": collection})

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            query = f"""
                SELECT DISTINCT VALUE {{
                    "creator": IIF(
                        IS_DEFINED(c.metadata.Creator) AND c.metadata.Creator != null AND c.metadata.Creator != "",
                        IIF(IS_STRING(c.metadata.Creator), c.metadata.Creator,
                            IIF(IS_OBJECT(c.metadata.Creator) AND IS_DEFINED(c.metadata.Creator.label),
                                c.metadata.Creator.label, "Unknown")),
                        "Unknown"),
                    "repository": IIF(
                        IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                                c.metadata.Repository.label, "Unknown")),
                        "Unknown"),
                    "collection": IIF(
                        IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown")
                }}
                FROM c
                {where_sql}
            """

            results = list(
                container.query_items(
                    query=query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True,
                )
            )

            results = sorted(results, key=lambda r: (r.get("repository", "").lower(), r.get("collection", "").lower(), r.get("creator", "").lower()))
            logger.info("Found %d distinct creator entries", len(results))
            return results
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct creators: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching creators: %s", str(e))
            raise

    def get_distinct_recipients(self, repository: Optional[str] = None, collection: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return distinct Recipient values for the given repository/collection scope.
        Includes "Unknown" when records with missing, null, or empty Recipient exist.

        Returns:
            List of {"recipient": str, "repository": str, "collection": str} dicts.
        """
        try:
            container = self.get_container()

            where_clauses = []
            parameters: List[Dict[str, Any]] = []

            if repository:
                where_clauses.append(
                    "((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label) AND "
                    "LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )
                parameters.append({"name": "@repository", "value": repository})

            if collection:
                where_clauses.append(
                    "((IS_STRING(c.metadata.Collection) AND LOWER(c.metadata.Collection) = LOWER(@collection)) "
                    "OR (IS_OBJECT(c.metadata.Collection) AND LOWER(c.metadata.Collection.label) = LOWER(@collection)))"
                )
                parameters.append({"name": "@collection", "value": collection})

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            query = f"""
                SELECT DISTINCT VALUE {{
                    "recipient": IIF(
                        IS_DEFINED(c.metadata.Recipient) AND c.metadata.Recipient != null AND c.metadata.Recipient != "",
                        IIF(IS_STRING(c.metadata.Recipient), c.metadata.Recipient,
                            IIF(IS_OBJECT(c.metadata.Recipient) AND IS_DEFINED(c.metadata.Recipient.label),
                                c.metadata.Recipient.label, "Unknown")),
                        "Unknown"),
                    "repository": IIF(
                        IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository), c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                                c.metadata.Repository.label, "Unknown")),
                        "Unknown"),
                    "collection": IIF(
                        IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection), c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown")
                }}
                FROM c
                {where_sql}
            """

            results = list(
                container.query_items(
                    query=query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True,
                )
            )

            results = sorted(
                results,
                key=lambda r: (
                    r.get("repository", "").lower(),
                    r.get("collection", "").lower(),
                    r.get("recipient", "").lower(),
                ),
            )
            logger.info("Found %d distinct recipient entries", len(results))
            return results
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to fetch distinct recipients: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching recipients: %s", str(e))
            raise

    def _get_statistics_service(self):
        """Get Statistics service for pre-computed aggregates."""
        if getattr(settings, 'use_precomputed_stats', True):
            try:
                from services.statistics_service import get_statistics_service
                return get_statistics_service()
            except Exception as e:
                logger.debug("Statistics service not available: %s", str(e))
        return None

    def get_collection_summaries(self) -> List[Dict[str, any]]:
        """
        Get aggregated statistics grouped by collection and repository.

        Uses pre-computed statistics for instant response.
        Falls back to client-side aggregation if stats not available.

        Returns:
            List of collection summaries with counts and statistics
        """
        # Try pre-computed stats first (instant read)
        stats_service = self._get_statistics_service()
        if stats_service:
            try:
                logger.info("Using pre-computed statistics for collection summaries")
                return stats_service.get_collection_summaries()
            except Exception as e:
                logger.warning("Pre-computed stats failed, falling back to client-side: %s", str(e))

        try:
            container = self.get_container()

            # Fetch minimal data needed for aggregation (client-side aggregation)
            # Include pipeline status fields for error detection
            query = (
                """
                SELECT VALUE {
                    "Repository": IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository),
                            c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                                c.metadata.Repository.label, "Unknown")),
                        "Unknown"),
                    "Collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection),
                            c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown"),
                    "ArchivistStatus": IIF(IS_DEFINED(c.archivist_status) AND c.archivist_status != null AND c.archivist_status != "" AND IS_STRING(c.archivist_status),
                        LOWER(c.archivist_status), "pending"),
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
                + _CLIENT_AGG_DOC_OCR
                + """
                }
                FROM c
            """
            )

            logger.info("Fetching minimal document data for collection summaries aggregation")

            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            logger.info(
                "Retrieved %d documents, aggregating by collection/repository",
                len(results),
            )

            # Aggregate by repository and collection (client-side)
            summaries_dict = {}

            for doc in results:
                repo = doc.get("Repository", "Unknown")
                collection = doc.get("Collection", "Unknown")
                
                key = f"{repo}|{collection}"

                if key not in summaries_dict:
                    summaries_dict[key] = {
                        "repository": repo,
                        "collection": collection,
                        "totalItems": 0,
                        "pending": 0,
                        "reviewed": 0,
                        "published": 0,
                        "error": 0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                    }

                summary = summaries_dict[key]
                summary["totalItems"] += 1
                increment_status_counters(summary, doc)
                _accumulate_client_side_doc_ocr(summary, doc)

            # Convert to list and add completion rate
            summaries_list = []
            for summary in summaries_dict.values():
                total = summary["totalItems"]
                published = summary["published"]

                # Calculate completion rate
                if total > 0:
                    completion_rate = round(
                        (published / total) * 100, 1
                    )
                    
                else:
                    completion_rate = 0.0

                summary["completionRate"] = completion_rate
                ocr_cnt = max(0, int(summary.get("ocrConfidenceCount", 0)))
                ocr_sum = float(summary.get("ocrConfidenceSum", 0.0))
                summary["avgOcrConfidence"] = (
                    round((ocr_sum / ocr_cnt) * 100.0, 1) if ocr_cnt > 0 else None
                )
                summary.pop("ocrConfidenceSum", None)
                summary.pop("ocrConfidenceCount", None)
                summaries_list.append(summary)

            logger.info("Aggregated into %d collection summaries", len(summaries_list))
            return summaries_list

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get collection summaries: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error getting collection summaries: %s", str(e))
            raise

    def get_repository_statistics(
        self,
        title_contains: Optional[str] = None,
        page_number: Optional[int] = 1,
        page_size: Optional[int] = 20,
    ) -> tuple[List[Dict[str, any]], int]:
        """
        Get aggregated statistics grouped by repository.
        
        Uses pre-computed statistics for instant response.
        Falls back to client-side aggregation if stats not available.

        Args:
            title_contains: Optional filter to search repositories by name (case-insensitive)
            page_number: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Tuple of (paginated list of repository statistics, total count)
        """
        # Try pre-computed stats first (instant read)
        stats_service = self._get_statistics_service()
        if stats_service:
            try:
                logger.info("Using pre-computed statistics for repositories")
                all_stats = stats_service.get_repository_statistics()
                
                # Apply search filter
                if title_contains:
                    search_term = title_contains.lower()
                    all_stats = [r for r in all_stats if search_term in r["repository"].lower()]
                
                # Apply pagination
                total_count = len(all_stats)
                page_num = page_number or 1
                page_sz = page_size or 20
                start_idx = (page_num - 1) * page_sz
                end_idx = start_idx + page_sz
                
                return all_stats[start_idx:end_idx], total_count
            except Exception as e:
                logger.warning("Pre-computed stats failed, falling back to client-side: %s", str(e))

        try:
            container = self.get_container()

            # Fetch minimal data needed for aggregation (client-side aggregation)
            # Include pipeline status fields for error detection
            query = """
                SELECT VALUE {
                    "Repository": IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository),
                            c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                                c.metadata.Repository.label, "Unknown")),
                        "Unknown"),
                    "Collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection),
                            c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown"),
                    "ArchivistStatus": IIF(IS_DEFINED(c.archivist_status) AND c.archivist_status != null AND c.archivist_status != "" AND IS_STRING(c.archivist_status),
                        LOWER(c.archivist_status), "pending"),
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

            logger.info("Fetching minimal document data for repository statistics aggregation")

            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            logger.info(
                "Retrieved %d documents, aggregating by repository",
                len(results),
            )

            # Aggregate by repository (client-side)
            repo_stats_dict = {}
            collection_counts = {}  # Track unique collections per repo

            for doc in results:
                repo = doc.get("Repository", "Unknown")
                collection = doc.get("Collection", "Unknown")
                
                if repo not in repo_stats_dict:
                    repo_stats_dict[repo] = {
                        "repository": repo,
                        "totalItems": 0,
                        "pending": 0,
                        "published": 0,
                        "reviewed": 0,
                        "publishing": 0,
                        "error": 0,
                    }
                    collection_counts[repo] = set()

                stats = repo_stats_dict[repo]
                stats["totalItems"] += 1
                collection_counts[repo].add(collection)
                increment_status_counters(stats, doc)

            # Convert to list and add completion rate and collection count
            repo_stats_list = []
            for repo, stats in repo_stats_dict.items():
                total = stats["totalItems"]
                pending = stats["pending"]
                published = stats["published"]
                reviewed = stats.get("reviewed", 0)
                publishing = stats.get("publishing", 0)
                errors = stats.get("error", 0)
                collections_count = len(collection_counts[repo])

                # Calculate completion rate based on published items
                completion_rate = 0.0
                if total > 0:
                    completion_rate = round((published / total) * 100, 1)

                repo_stats_list.append({
                    "repository": repo,
                    "totalItems": total,
                    "collections": collections_count,
                    "pending": pending,
                    "reviewed": reviewed,
                    "publishing": publishing,
                    "published": published,
                    "errors": errors,
                    "completionRate": completion_rate
                })

            # Apply search filter if provided
            if title_contains:
                search_term = title_contains.lower()
                repo_stats_list = [
                    repo for repo in repo_stats_list
                    if search_term in repo["repository"].lower()
                ]

            # Sort by repository name for consistent pagination
            repo_stats_list.sort(key=lambda x: x["repository"].lower())

            # Calculate total count before pagination
            total_count = len(repo_stats_list)

            # Apply pagination
            page_num = page_number or 1
            page_sz = page_size or 20
            start_idx = (page_num - 1) * page_sz
            end_idx = start_idx + page_sz
            paginated_list = repo_stats_list[start_idx:end_idx]

            logger.info(
                "Aggregated into %d repository statistics, returning page %d (%d items)",
                total_count,
                page_num,
                len(paginated_list),
            )
            return paginated_list, total_count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get repository statistics: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error getting repository statistics: %s", str(e))
            raise

    def get_repository_statistics_summary(self) -> Dict[str, any]:
        """
        Get overall aggregated statistics summary for repositories page.
        
        This method calculates overall statistics across all repositories
        for dashboard cards and summary metrics.

        Returns:
            Dictionary with overall repository statistics
        """
        try:
            # Get all repository statistics (no pagination)
            all_stats, total_count = self.get_repository_statistics(
                title_contains=None,
                page_number=1,
                page_size=10000  # Large number to get all
            )

            if not all_stats:
                return {
                    "totalRepositories": 0,
                    "totalItems": 0,
                    "totalCollections": 0,
                    "totalPending": 0,
                    "totalReviewed": 0,
                    "totalPublishing": 0,
                    "totalPublished": 0,
                    "totalErrors": 0,
                    "overallCompletionRate": 0.0
                }

            # Aggregate across all repositories
            total_items = sum(repo["totalItems"] for repo in all_stats)
            total_collections = sum(repo["collections"] for repo in all_stats)
            total_pending = sum(repo["pending"] for repo in all_stats)
            total_reviewed = sum(repo.get("reviewed", 0) for repo in all_stats)
            total_publishing = sum(repo.get("publishing", 0) for repo in all_stats)
            total_published = sum(repo["published"] for repo in all_stats)
            total_errors = sum(repo.get("errors", 0) for repo in all_stats)

            overall_completion_rate = 0.0
            if total_items > 0:
                overall_completion_rate = round((total_published / total_items) * 100, 1)

            return {
                "totalRepositories": total_count,
                "totalItems": total_items,
                "totalCollections": total_collections,
                "totalPending": total_pending,
                "totalReviewed": total_reviewed,
                "totalPublishing": total_publishing,
                "totalPublished": total_published,
                "totalErrors": total_errors,
                "overallCompletionRate": overall_completion_rate
            }

        except Exception as e:
            logger.exception("Unexpected error getting repository statistics summary: %s", str(e))
            raise

    def get_collections_by_repository(
        self,
        repository: str,
        title_contains: Optional[str] = None,
        page_number: Optional[int] = 1,
        page_size: Optional[int] = 20,
    ) -> tuple[List[Dict[str, any]], int]:
        """
        Get collections for a specific repository with optional search and pagination.

        Uses pre-computed statistics for instant response.
        Falls back to client-side aggregation if stats not available.

        Args:
            repository: Repository name to filter by
            title_contains: Optional search term to filter collection names
            page_number: Page number for pagination
            page_size: Number of items per page

        Returns:
            Tuple of (list of collection details, total count)
        """
        # Try pre-computed stats first (instant read)
        stats_service = self._get_statistics_service()
        if stats_service:
            try:
                logger.info("Using pre-computed statistics for collections by repository")
                all_stats = stats_service.get_collections_by_repository(repository)
                
                # Apply search filter
                if title_contains:
                    search_term = title_contains.lower()
                    all_stats = [c for c in all_stats if search_term in c["collection"].lower()]
                
                # Apply pagination
                total_count = len(all_stats)
                page_num = page_number or 1
                page_sz = page_size or 20
                start_idx = (page_num - 1) * page_sz
                end_idx = start_idx + page_sz
                
                return all_stats[start_idx:end_idx], total_count
            except Exception as e:
                logger.warning("Pre-computed stats failed, falling back to client-side: %s", str(e))

        try:
            container = self.get_container()

            # Build WHERE clause
            where_clauses = []
            parameters = []

            # Repository filter
            repo_val = str(repository).strip()
            if repo_val.lower() == "unknown":
                where_clauses.append("(NOT IS_DEFINED(c.metadata.Repository) OR c.metadata.Repository = null)")
            else:
                where_clauses.append(
                    "((IS_STRING(c.metadata.Repository) AND LOWER(c.metadata.Repository) = LOWER(@repository)) "
                    "OR (IS_OBJECT(c.metadata.Repository) AND IS_STRING(c.metadata.Repository.label) AND "
                    "LOWER(c.metadata.Repository.label) = LOWER(@repository)))"
                )
                parameters.append({"name": "@repository", "value": repo_val})

            where_clause = ""
            if where_clauses:
                where_clause = "WHERE " + " AND ".join(where_clauses)

            # Fetch minimal data needed for aggregation (client-side aggregation)
            # Include pipeline status fields for error detection
            query = (
                """
                SELECT VALUE {
                    "Collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection),
                            c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown"),
                    "ArchivistStatus": IIF(IS_DEFINED(c.archivist_status) AND c.archivist_status != null AND c.archivist_status != "" AND IS_STRING(c.archivist_status),
                        LOWER(c.archivist_status), "pending"),
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
                + _CLIENT_AGG_DOC_OCR
                + f"""
                }}
                FROM c
                {where_clause}
            """
            )

            logger.info("Fetching minimal document data for collections by repository '%s'", repository)

            results = list(
                container.query_items(
                    query=query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True
                )
            )

            logger.info(
                "Retrieved %d documents for repository '%s', aggregating by collection",
                len(results),
                repository
            )

            # Aggregate by collection (client-side)
            collections_dict = {}

            for doc in results:
                collection = doc.get("Collection", "Unknown")
                key = collection

                if key not in collections_dict:
                    collections_dict[key] = {
                        "collection": collection,
                        "repository": repo_val,
                        "totalItems": 0,
                        "pending": 0,
                        "reviewed": 0,
                        "published": 0,
                        "error": 0,
                        "ocrConfidenceSum": 0.0,
                        "ocrConfidenceCount": 0,
                    }

                summary = collections_dict[key]
                summary["totalItems"] += 1
                increment_status_counters(summary, doc)
                _accumulate_client_side_doc_ocr(summary, doc)

            # Convert to list and add completion rate
            collections_list = []
            for collection_data in collections_dict.values():
                total = collection_data["totalItems"]
                pending = collection_data["pending"]

                completion_rate = 0.0
                if total > 0:
                    published = collection_data["published"]
                    completion_rate = round((published / total) * 100, 1)

                collection_data["completionRate"] = completion_rate
                ocr_cnt = max(0, int(collection_data.get("ocrConfidenceCount", 0)))
                ocr_sum = float(collection_data.get("ocrConfidenceSum", 0.0))
                collection_data["avgOcrConfidence"] = (
                    round((ocr_sum / ocr_cnt) * 100.0, 1) if ocr_cnt > 0 else None
                )
                collection_data.pop("ocrConfidenceSum", None)
                collection_data.pop("ocrConfidenceCount", None)
                collections_list.append(collection_data)

            # Apply search filter if provided
            if title_contains:
                search_term = title_contains.lower()
                collections_list = [
                    c for c in collections_list
                    if search_term in c["collection"].lower()
                ]

            # Sort by collection name
            collections_list.sort(key=lambda x: x["collection"].lower())

            # Apply pagination
            total_count = len(collections_list)
            start_index = (page_number - 1) * page_size
            end_index = start_index + page_size
            paginated_collections = collections_list[start_index:end_index]

            logger.info(
                "Retrieved %d collections for repository '%s' (page %d, size %d)",
                len(paginated_collections),
                repository,
                page_number,
                page_size
            )

            return paginated_collections, total_count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get collections by repository: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error getting collections by repository: %s", str(e))
            raise

    def get_repository_collection_statistics_summary(self, repository: str) -> Dict[str, any]:
        """
        Get overall aggregated statistics summary for a repository's collections.
        
        This method calculates overall statistics across all collections in a repository
        for dashboard cards and summary metrics.

        Args:
            repository: Repository name to filter by

        Returns:
            Dictionary with overall repository collection statistics
        """
        try:
            # Get all collections for the repository (no pagination)
            all_collections, total_count = self.get_collections_by_repository(
                repository=repository,
                title_contains=None,
                page_number=1,
                page_size=10000  # Large number to get all
            )

            if not all_collections:
                return {
                    "totalCollections": 0,
                    "totalItems": 0,
                    "totalPending": 0,
                    "totalReviewed": 0,
                    "totalPublishing": 0,
                    "totalPublished": 0,
                    "totalErrors": 0,
                    "overallCompletionRate": 0.0
                }

            # Aggregate across all collections
            total_items = sum(coll["totalItems"] for coll in all_collections)
            total_pending = sum(coll["pending"] for coll in all_collections)
            total_reviewed = sum(coll.get("reviewed", 0) for coll in all_collections)
            total_publishing = sum(coll.get("publishing", 0) for coll in all_collections)
            total_published = sum(coll["published"] for coll in all_collections)
            total_errors = sum(coll.get("errors", 0) for coll in all_collections)
            
            overall_completion_rate = 0.0
            if total_items > 0:
                overall_completion_rate = round((total_published / total_items) * 100, 1)

            return {
                "totalCollections": total_count,
                "totalItems": total_items,
                "totalPending": total_pending,
                "totalReviewed": total_reviewed,
                "totalPublishing": total_publishing,
                "totalPublished": total_published,
                "totalErrors": total_errors,
                "overallCompletionRate": overall_completion_rate
            }

        except Exception as e:
            logger.exception("Unexpected error getting repository collection statistics summary: %s", str(e))
            raise

    def get_record_ids_by_collection(
        self,
        repository: Optional[str] = None,
        collection: Optional[str] = None,
        status_filter: Optional[str] = None,
        resource_type_filter: Optional[str] = None,
        min_confidence: Optional[float] = None,
        max_confidence: Optional[float] = None,
        title_contains: Optional[str] = None,
        order_by: str = "created_at",
        descending: bool = False,
    ) -> List[str]:
        """
        Get only record IDs for a collection (for navigation purposes).
        This is optimized to return only IDs, not full documents.

        Args:
            repository: Repository name to filter by
            collection: Collection name to filter by
            status_filter: Filter by status
            resource_type_filter: Filter by resource type
            min_confidence: Minimum OCR confidence
            max_confidence: Maximum OCR confidence
            title_contains: Filter by title containing text
            order_by: Field to order by
            descending: Sort in descending order

        Returns:
            List of document IDs in order
        """
        try:
            container = self.get_container()

            # Build WHERE clause using shared method
            _, where_clause, parameters = self.build_query_filters(
                status_filter=status_filter,
                repository_filter=repository,
                collection_filter=collection,
                resource_type_filter=resource_type_filter,
                min_confidence=min_confidence,
                max_confidence=max_confidence,
                title_contains=title_contains,
            )

            # Build ORDER BY clause
            order_expr = None
            if order_by:
                order_map = {
                    "created_at": "c.metadata['Creation Date']",
                    "title": "c.metadata.Title",
                    "confidence": "c.asset_avg_confidence",
                }
                order_expr = (
                    order_map.get(order_by, order_map.get(order_by.lower()))
                    if isinstance(order_by, str)
                    else None
                )

            order_clause = ""
            if order_expr:
                order_clause = (
                    f"ORDER BY {order_expr} {'DESC' if descending else 'ASC'}"
                )

            # Query only IDs
            query = f"""
                SELECT VALUE c.record_id
                FROM c
                {where_clause}
                {order_clause}
            """

            logger.info(
                "Fetching record IDs for collection '%s' in repository '%s'",
                collection,
                repository
            )

            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters if parameters else None,
                    enable_cross_partition_query=True,
                )
            )

            record_ids = [item for item in items if item]

            logger.info("Retrieved %d record IDs", len(record_ids))
            return record_ids

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get record IDs: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error getting record IDs: %s", str(e))
            raise

    def get_dashboard_statistics(self) -> Dict[str, any]:
        """
        Get aggregated dashboard statistics.
        
        Uses pre-computed statistics for instant response (~50ms).
        Falls back to client-side aggregation if stats not available.

        Returns:
            Dictionary with overall statistics
        """
        # Try pre-computed stats first (instant read)
        stats_service = self._get_statistics_service()
        if stats_service:
            try:
                logger.info("Using pre-computed statistics for dashboard")
                return stats_service.get_dashboard_statistics()
            except Exception as e:
                logger.warning("Pre-computed stats failed, falling back to client-side: %s", str(e))

        try:
            container = self.get_container()

            # Fetch minimal data needed for aggregation (client-side aggregation)
            # Include pipeline status fields for error detection
            query = """
                SELECT VALUE {
                    "Repository": IIF(IS_DEFINED(c.metadata.Repository) AND c.metadata.Repository != null,
                        IIF(IS_STRING(c.metadata.Repository),
                            c.metadata.Repository,
                            IIF(IS_OBJECT(c.metadata.Repository) AND IS_DEFINED(c.metadata.Repository.label),
                                c.metadata.Repository.label, "Unknown")),
                        "Unknown"),
                    "Collection": IIF(IS_DEFINED(c.metadata.Collection) AND c.metadata.Collection != null,
                        IIF(IS_STRING(c.metadata.Collection),
                            c.metadata.Collection,
                            IIF(IS_OBJECT(c.metadata.Collection) AND IS_DEFINED(c.metadata.Collection.label),
                                c.metadata.Collection.label, "Unknown")),
                        "Unknown"),
                    "ArchivistStatus": IIF(IS_DEFINED(c.archivist_status) AND c.archivist_status != null AND c.archivist_status != "" AND IS_STRING(c.archivist_status),
                        LOWER(c.archivist_status), "pending"),
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

            logger.info("Fetching minimal document data for dashboard statistics aggregation")

            results = list(
                container.query_items(query=query, enable_cross_partition_query=True)
            )

            # Aggregate statistics (client-side)
            # Counters for status totals (error = pending with incomplete pipeline)
            status_counters = {"pending": 0, "reviewed": 0, "publishing": 0, "published": 0, "error": 0}
            repositories = set()
            collections = set()

            for doc in results:
                repo = doc.get("Repository", "Unknown")
                collection = doc.get("Collection", "Unknown")

                repositories.add(repo)
                collections.add(f"{repo}|{collection}")
                increment_status_counters(status_counters, doc)

            total_items = sum(status_counters.values())
            total_repositories = len(repositories)
            total_collections = len(collections)

            overall_completion_rate = 0.0
            if total_items > 0:
                overall_completion_rate = round(
                    (status_counters["published"] / total_items) * 100, 1
                )

            stats = {
                "totalItems": total_items,
                "totalCollections": total_collections,
                "totalPending": status_counters["pending"],
                "totalPublishing": status_counters["publishing"],
                "totalPublished": status_counters["published"],
                "totalRepositories": total_repositories,
                "totalReviewed": status_counters["reviewed"],
                "totalErrors": status_counters["error"],
                "overallCompletionRate": overall_completion_rate,
            }

            logger.info("Calculated dashboard statistics: %s", stats)
            return stats

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get dashboard statistics: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error getting dashboard statistics: %s", str(e))
            raise

    def patch_document(
        self,
        document_id: str,
        patch_operations: List[Dict[str, any]],
        partition_key: Optional[str] = None,
        etag: Optional[str] = None,
    ) -> DocumentMetadata:
        """
        Patch a document using CosmosDB patch operations.
        Args:
            document_id: Document ID
            patch_operations: List of patch operations (add, replace, remove, etc.)
            partition_key: Partition key value (defaults to document_id)
            etag: ETag for optimistic concurrency control
        Returns:
            Updated document metadata
        """
        try:
            container = self.get_container()
            pk = partition_key if partition_key is not None else document_id
            # Build options for patch
            options = {}
            if etag:
                options["if_match"] = etag
            # Execute patch
            result = container.patch_item(
                item=document_id,
                partition_key=pk,
                patch_operations=patch_operations,
                **options,
            )

            logger.info("Patched document with id: %s", document_id)
            return DocumentMetadata(**result)

        except exceptions.CosmosAccessConditionFailedError:
            logger.warning(
                "Etag mismatch for document %s - modified elsewhere",
                document_id,
            )
            raise DocumentVersionConflictError() from None
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            raise ValueError(f"Document '{document_id}' not found") from None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to patch document: %s", e.message)
            raise

    def patch_ocr_flexible_blob_url(
        self,
        document_id: str,
        asset_id: str,
        flexible_blob_url: str,
        partition_key: Optional[str] = None,
        client_etag: Optional[str] = None,
    ) -> Tuple[DocumentMetadata, int]:
        """
        Patch only the flexible OCR blob URL for one asset after blob upload.

        Re-reads the live document so concurrent pipeline/metadata edits are not
        overwritten by a stale asset_details snapshot from before blob upload.
        """
        try:
            container = self.get_container()
            pk = partition_key if partition_key is not None else document_id
            current_doc = container.read_item(item=document_id, partition_key=pk)

            asset_details = current_doc.get("asset_details", [])
            asset_index = _find_asset_index(asset_details, asset_id)
            if asset_index is None:
                raise ValueError(
                    f"Asset '{asset_id}' not found in document '{document_id}'"
                )

            current_version = current_doc.get("version", 0)
            new_version = current_version + 1
            timestamp = datetime.now(timezone.utc).isoformat()

            asset_entry = asset_details[asset_index]
            if not isinstance(asset_entry.get("ocr_result"), dict):
                asset_entry["ocr_result"] = {}

            patch_operations = [
                {
                    "op": "set",
                    "path": f"/asset_details/{asset_index}/ocr_result",
                    "value": {
                        **asset_entry["ocr_result"],
                        "ocr_text_flexible_blob_url": flexible_blob_url,
                    },
                },
                {"op": "replace", "path": "/version", "value": new_version},
                {"op": "set", "path": "/updated_at", "value": timestamp},
                {"op": "set", "path": "/archivist_modified_ts", "value": timestamp},
            ]

            options: Dict[str, Any] = {}
            if client_etag:
                options["if_match"] = client_etag

            result = container.patch_item(
                item=document_id,
                partition_key=pk,
                patch_operations=patch_operations,
                **options,
            )

            logger.info(
                "Patched OCR blob URL for document %s asset %s (version %s)",
                document_id,
                asset_id,
                new_version,
            )
            return DocumentMetadata(**result), asset_index

        except exceptions.CosmosAccessConditionFailedError:
            logger.warning(
                "Etag mismatch patching OCR for document %s asset %s",
                document_id,
                asset_id,
            )
            raise DocumentVersionConflictError() from None
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            raise ValueError(f"Document '{document_id}' not found") from None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to patch OCR blob URL: %s", e.message)
            raise

    def update_document_with_version(
        self,
        document_id: str,
        updates: Dict[str, any],
        partition_key: Optional[str] = None,
        etag: Optional[str] = None,
    ) -> DocumentMetadata:
        """
        Update a document and increment its version number.
        Args:
            document_id: Document ID
            updates: Dictionary of fields to update
            partition_key: Partition key value (defaults to document_id)
            etag: ETag for optimistic concurrency control
        Returns:
            Updated document metadata
        """
        try:
            container = self.get_container()
            pk = partition_key if partition_key is not None else document_id
            current_doc = container.read_item(item=document_id, partition_key=pk)

            # Increment version
            current_version = current_doc.get("version", 0)
            new_version = current_version + 1

            # Update fields
            current_doc["version"] = new_version
            timestamp = datetime.now(timezone.utc).isoformat()
            current_doc["updated_at"] = timestamp

            # Set archivist_modified_ts for archivist edits
            # This tracks when an archivist made changes (vs system updates)
            current_doc["archivist_modified_ts"] = timestamp

            # Update metadata fields
            if "metadata" not in current_doc:
                current_doc["metadata"] = {}

            for key, value in updates.items():
                if key == "metadata":
                    current_doc["metadata"].update(value)
                else:
                    current_doc[key] = value
            # Client If-Match from the browser; Cosmos validates at replace time.
            options = {}
            if etag:
                options["if_match"] = etag
            result = container.replace_item(
                item=document_id, body=current_doc, **options
            )

            logger.info("Updated document %s to version %s", document_id, new_version)
            return DocumentMetadata(**result)

        except exceptions.CosmosAccessConditionFailedError:
            logger.warning(
                "Etag mismatch for document %s - modified elsewhere",
                document_id,
            )
            raise DocumentVersionConflictError() from None
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            raise ValueError(f"Document '{document_id}' not found") from None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update document: %s", e.message)
            raise

    def get_publishers_list(self) -> List[Dict[str, Any]]:
        """
        Get list of users who have published documents with their publication dates.
        
        Returns a list of unique publishers with the dates they published documents.
        Used for the unpublish UI to populate dropdowns.
        
        Returns:
            List of dicts with 'user' and 'dates' keys
        """
        try:
            container = self.get_container()
            
            # Query for unique published_by values with their dates
            query = """
                SELECT DISTINCT c.published_by, SUBSTRING(c.published_at, 0, 10) as published_date
                FROM c
                WHERE (IS_DEFINED(c.archivist_status) AND c.archivist_status != null
                AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'published')
                AND IS_DEFINED(c.published_by) AND c.published_by != null
                AND IS_DEFINED(c.published_at) AND c.published_at != null
            """
            
            results = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            # Group by publisher
            publishers: Dict[str, set] = {}
            for item in results:
                user = item.get("published_by")
                date = item.get("published_date")
                if user and date:
                    if user not in publishers:
                        publishers[user] = set()
                    publishers[user].add(date)
            
            # Convert to list format
            publisher_list = [
                {"user": user, "dates": sorted(list(dates), reverse=True)}
                for user, dates in publishers.items()
            ]
            
            # Sort by user name
            publisher_list.sort(key=lambda x: x["user"])
            
            return publisher_list
            
        except Exception as e:
            logger.exception("Failed to get publishers list: %s", str(e))
            raise

    def list_stuck_publishing_documents(self, threshold_minutes: int) -> List[Dict[str, Any]]:
        """
        Documents stuck in archivist_status=publishing longer than threshold_minutes.

        Uses archivist_publish_started_at when set (preferred); otherwise falls back to updated_at.
        """
        if threshold_minutes < 1:
            raise ValueError("threshold_minutes must be at least 1")
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)).isoformat()
        container = self.get_container()
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
        return list(
            container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )

    def reconcile_stuck_publishing_documents(
        self,
        threshold_minutes: int,
        action: str,
        reason_suffix: str = "",
    ) -> Dict[str, Any]:
        """
        Move stuck publishing documents to 'reviewed' (default) or 'failed'.

        action: 'failed' | 'reviewed'
        reason_suffix: only used when action is 'failed' (appended to error message)
        """
        if action not in ("failed", "reviewed"):
            raise ValueError("action must be 'failed' or 'reviewed'")
        stuck = self.list_stuck_publishing_documents(threshold_minutes)
        container = self.get_container()
        updated_ids: List[str] = []
        errors: List[Dict[str, str]] = []
        stats = self._get_statistics_service()
        ts = datetime.now(timezone.utc).isoformat()
        base_msg = None
        if action == "failed":
            base_msg = (
                f"Publishing exceeded {threshold_minutes} minute(s) without completing"
                + (f" ({reason_suffix})" if reason_suffix else "")
            )

        for row in stuck:
            doc_id = row.get("id") or row.get("record_id")
            if not doc_id:
                continue
            try:
                doc = container.read_item(item=doc_id, partition_key=doc_id)
            except exceptions.CosmosResourceNotFoundError:
                errors.append({"id": str(doc_id), "error": "not_found"})
                continue
            etag = doc.get("_etag")
            old = dict(doc)
            status = (doc.get("archivist_status") or "").strip().lower()
            if status != "publishing":
                continue
            doc["archivist_status"] = "failed" if action == "failed" else "reviewed"
            doc["archivist_publish_started_at"] = None
            doc["updated_at"] = ts
            if action == "failed":
                doc["archivist_error_message"] = (base_msg or "")[:500]
            else:
                doc["archivist_error_message"] = None
                doc["archivist_approval_method"] = None
            try:
                options = {}
                if etag:
                    options["if_match"] = etag
                container.replace_item(item=doc_id, body=doc, **options)
                try:
                    stats.update_on_document_change(old_doc=old, new_doc=doc)
                except (KeyError, TypeError, ValueError) as se:
                    logger.warning("Statistics update failed for reconcile %s: %s", doc_id, se)
                updated_ids.append(doc_id)
            except exceptions.CosmosAccessConditionFailedError:
                logger.info("Skipping %s: document changed since read (ETag conflict)", doc_id)
            except exceptions.CosmosHttpResponseError as he:
                logger.exception("Cosmos replace failed for %s: %s", doc_id, he.message)
                errors.append({"id": doc_id, "error": he.message or str(he)})

        return {
            "threshold_minutes": threshold_minutes,
            "action": action,
            "matched": len(stuck),
            "updated": len(updated_ids),
            "document_ids": updated_ids,
            "errors": errors,
        }

    def close(self) -> None:
        """Close the CosmosDB client connection."""
        if self._client:
            self._audit_container = None
            self._container = None
            self._database = None
            self._client = None
            logger.info("CosmosDB client connection closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


# Singleton instance
_cosmos_service: Optional[CosmosDBService] = None


def get_cosmos_service() -> CosmosDBService:
    """Get or create CosmosDB service instance."""
    global _cosmos_service

    if _cosmos_service is None:
        _cosmos_service = CosmosDBService()

    return _cosmos_service
