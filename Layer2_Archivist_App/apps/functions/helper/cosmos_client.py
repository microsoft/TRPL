# pylint: disable=too-many-arguments, too-many-positional-arguments, too-many-instance-attributes, invalid-name

"""
Azure CosmosDB client for OCR extractor document management.
"""

import functools
import logging
import os
import random
import time
from typing import Callable, Dict, List, Optional, Any, TypeVar
from dataclasses import dataclass, asdict, fields
from datetime import datetime
import requests

from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.cosmos.database import DatabaseProxy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential

from helper.config import CosmosDBConfig

# Type variable for generic retry decorator
T = TypeVar('T')

# Retry configuration from environment variables
MAX_RETRIES = int(os.environ.get("COSMOS_MAX_RETRIES", "5"))
BASE_DELAY_SECONDS = float(os.environ.get("COSMOS_BASE_DELAY_SECONDS", "1.0"))
MAX_DELAY_SECONDS = float(os.environ.get("COSMOS_MAX_DELAY_SECONDS", "60.0"))


_RETRYABLE_STATUS_CODES = {429, 503}


def retry_on_429(
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY_SECONDS,
    max_delay: float = MAX_DELAY_SECONDS
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to retry CosmosDB operations on transient errors (429, 503).
    
    Uses exponential backoff with jitter and respects the Retry-After header
    if provided by CosmosDB.
    
    Args:
        max_retries: Maximum number of retry attempts (default from env: COSMOS_MAX_RETRIES=5)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
    
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions.CosmosHttpResponseError as e:
                    # Retry on transient errors (429 Too Many Requests, 503 Service Unavailable)
                    if e.status_code not in _RETRYABLE_STATUS_CODES:
                        raise
                    
                    last_exception = e
                    
                    if attempt == max_retries:
                        logging.error(
                            "CosmosDB %d error: Max retries (%d) exceeded for %s",
                            e.status_code, max_retries, func.__name__
                        )
                        raise
                    
                    # Calculate delay with exponential backoff and jitter
                    # Check if Retry-After header is available
                    retry_after = None
                    if hasattr(e, 'headers') and e.headers:
                        retry_after_ms = e.headers.get('x-ms-retry-after-ms')
                        if retry_after_ms:
                            retry_after = float(retry_after_ms) / 1000.0
                    
                    if retry_after:
                        # Use Retry-After header value
                        delay = min(retry_after, max_delay)
                    else:
                        # Exponential backoff: base_delay * 2^attempt + jitter
                        exponential_delay = base_delay * (2 ** attempt)
                        jitter = random.uniform(0, base_delay)
                        delay = min(exponential_delay + jitter, max_delay)
                    
                    logging.warning(
                        "CosmosDB %d error in %s (attempt %d/%d). Retrying in %.2f seconds...",
                        e.status_code, func.__name__, attempt + 1, max_retries, delay
                    )
                    time.sleep(delay)
            
            # This should not be reached, but raise last exception if it is
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")
        
        return wrapper
    return decorator


def execute_with_retry(
    operation: Callable[..., T],
    *args,
    operation_name: str = "CosmosDB operation",
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY_SECONDS,
    max_delay: float = MAX_DELAY_SECONDS,
    **kwargs
) -> T:
    """
    Execute a CosmosDB operation with retry logic for 429 errors.
    
    This is a standalone function for use with direct container operations
    that can't be decorated (e.g., cosmos_client._container.upsert_item).
    
    Args:
        operation: The callable to execute
        operation_name: Name for logging purposes
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for exponential backoff
        max_delay: Maximum delay in seconds
        *args, **kwargs: Arguments to pass to the operation
    
    Returns:
        Result of the operation
    
    Example:
        result = execute_with_retry(
            container.upsert_item,
            operation_name="upsert_item",
            body=document
        )
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return operation(*args, **kwargs)
        except exceptions.CosmosHttpResponseError as e:
            # Retry on transient errors (429 Too Many Requests, 503 Service Unavailable)
            if e.status_code not in _RETRYABLE_STATUS_CODES:
                raise
            
            last_exception = e
            
            if attempt == max_retries:
                logging.error(
                    "CosmosDB %d error: Max retries (%d) exceeded for %s",
                    e.status_code, max_retries, operation_name
                )
                raise
            
            # Calculate delay with exponential backoff and jitter
            retry_after = None
            if hasattr(e, 'headers') and e.headers:
                retry_after_ms = e.headers.get('x-ms-retry-after-ms')
                if retry_after_ms:
                    retry_after = float(retry_after_ms) / 1000.0
            
            if retry_after:
                delay = min(retry_after, max_delay)
            else:
                exponential_delay = base_delay * (2 ** attempt)
                jitter = random.uniform(0, base_delay)
                delay = min(exponential_delay + jitter, max_delay)
            
            logging.warning(
                "CosmosDB %d error in %s (attempt %d/%d). Retrying in %.2f seconds...",
                e.status_code, operation_name, attempt + 1, max_retries, delay
            )
            time.sleep(delay)
    
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry loop exit")


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Singleton CosmosClient instance
_cosmos_client: Optional[CosmosClient] = None
_requests_session: Optional[requests.Session] = None
_cosmos_client_config: Optional[CosmosDBConfig] = None


def _resolve_cosmos_credential(config: CosmosDBConfig):
    key = config.key.strip() if config.key else ""
    if key:
        if os.getenv("ENVIRONMENT", "production").strip().lower() != "local":
            raise ValueError(
                "COSMOS_DB_KEY is allowed only when ENVIRONMENT=local; "
                "non-local deployments must use managed identity"
            )
        return key
    return DefaultAzureCredential()


def _get_cosmos_client(config: CosmosDBConfig) -> CosmosClient:
    """
    Get or create singleton CosmosClient instance with connection pooling.

    Args:
        config: CosmosDB configuration

    Returns:
        Singleton CosmosClient instance
    """
    # pylint: disable=global-statement
    global _cosmos_client, _requests_session, _cosmos_client_config

    # Check if we need to create a new client (different config or first time)
    # Compare config values to determine if we need a new client
    config_changed = (
        _cosmos_client_config is None or
        _cosmos_client_config.endpoint != config.endpoint or
        _cosmos_client_config.key != config.key or
        _cosmos_client_config.connection_mode != config.connection_mode or
        _cosmos_client_config.database_name != config.database_name or
        _cosmos_client_config.container_name != config.container_name
    )

    if _cosmos_client is None or config_changed:
        # Create requests session with connection pooling
        _requests_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
        _requests_session.mount('https://', adapter)

        credential = _resolve_cosmos_credential(config)

        # Create CosmosClient with custom transport
        _cosmos_client = CosmosClient(
            url=config.endpoint,
            credential=credential,
            connection_mode=config.connection_mode,
            transport=RequestsTransport(session=_requests_session, session_owner=False)
        )

        _cosmos_client_config = config
        logger.info("Created singleton CosmosClient with connection pooling")

    return _cosmos_client

@dataclass
class DocumentMetadata:
    """Metadata for a document to be processed by OCR."""

    id: str
    image_url: str
    storage_path: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    created_at: Optional[str] = None
    status: str = "pending"  # pending, processing, completed, failed
    ocr_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    processed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for CosmosDB storage."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DocumentMetadata':
        """Create DocumentMetadata from dictionary."""
        field_names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in field_names})


class CosmosDBClient:
    """Client for Azure CosmosDB operations."""

    def __init__(self, config: CosmosDBConfig):
        """
        Initialize CosmosDB client.

        Args:
            config: CosmosDB configuration
        """
        self.config = config
        self._client: Optional[CosmosClient] = None
        self._database: Optional[DatabaseProxy] = None
        self._container: Optional[ContainerProxy] = None

        # Initialize connection
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB."""
        try:
            logger.info("Connecting to CosmosDB at %s", self.config.endpoint)

            # Get singleton CosmosDB client with connection pooling
            self._client = _get_cosmos_client(self.config)

            # Get database reference
            self._database = self._client.get_database_client(self.config.database_name)

            # Get container reference
            self._container = self._database.get_container_client(self.config.container_name)

            # Assign to locals to keep the log line under the max length
            db_name = self.config.database_name
            container_name = self.config.container_name
            logger.info(
                "Successfully connected to database '%s', container '%s'",
                db_name,
                container_name,
            )

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to connect to CosmosDB: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error connecting to CosmosDB: %s", str(e))
            raise

    @retry_on_429()
    def create_document(self, document: DocumentMetadata) -> DocumentMetadata:
        """
        Create a new document in CosmosDB.

        Args:
            document: Document metadata to create

        Returns:
            Created document metadata

        Raises:
            exceptions.CosmosResourceExistsError: If document already exists
        """
        try:
            doc_dict = document.to_dict()

            # Add timestamp if not present
            if 'created_at' not in doc_dict:
                doc_dict['created_at'] = datetime.utcnow().isoformat()

            result = self._container.create_item(body=doc_dict)
            logger.info("Created document with id: %s", result['id'])

            return DocumentMetadata.from_dict(result)

        except exceptions.CosmosResourceExistsError:
            logger.warning("Document with id '%s' already exists", document.id)
            raise
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to create document: %s", e.message)
            raise

    @retry_on_429()
    def get_document(self, document_id: str,
                     partition_key: Optional[str] = None
                     ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a document by ID.

        Args:
            document_id: Document ID
            partition_key: Partition key value (if different from ID)

        Returns:
            Document dictionary or None if not found
        """
        try:
            # Use document_id as partition key if not specified
            pk = partition_key if partition_key is not None else document_id

            result = self._container.read_item(
                item=document_id,
                partition_key=pk
            )
            return result

        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to retrieve document: %s", e.message)
            raise

    @retry_on_429()
    def update_document(self, document: DocumentMetadata) -> DocumentMetadata:
        """
        Update an existing document.

        Args:
            document: Document metadata to update

        Returns:
            Updated document metadata
        """
        try:
            doc_dict = document.to_dict()

            result = self._container.upsert_item(body=doc_dict)
            logger.info("Updated document with id: %s", result['id'])

            return DocumentMetadata.from_dict(result)

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update document: %s", e.message)
            raise

    @retry_on_429()
    def delete_document(self, document_id: str, partition_key: Optional[str] = None) -> bool:
        """
        Delete a document by ID.

        Args:
            document_id: Document ID
            partition_key: Partition key value (if different from ID)

        Returns:
            True if deleted successfully
        """
        try:
            pk = partition_key if partition_key is not None else document_id

            self._container.delete_item(
                item=document_id,
                partition_key=pk
            )

            logger.info("Deleted document with id: %s", document_id)
            return True

        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Document with id '%s' not found", document_id)
            return False
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to delete document: %s", e.message)
            raise

    @retry_on_429()
    def query_document_count(
        self,
        where_clause: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        Get count of documents matching a WHERE clause.
        
        This is more efficient than querying all IDs when you only need the count.

        Args:
            where_clause: WHERE clause without "WHERE" keyword (e.g., "c.archivist_status = @status")
            parameters: Query parameters

        Returns:
            Count of matching documents

        Example:
            count = client.query_document_count(
                where_clause="c.archivist_status = @status",
                parameters=[{"name": "@status", "value": "published"}]
            )
        """
        try:
            # Build COUNT query
            count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_clause}"
            
            items = list(self._container.query_items(
                query=count_query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True
            ))
            
            count = items[0] if items else 0
            logger.info("Count query returned %d", count)
            return count

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get document count: %s", e.message)
            raise

    @retry_on_429()
    def query_document_ids(
        self,
        query: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """
        Query documents and return only their IDs.

        Args:
            query: SQL query string (can return full documents or just c.id)
            parameters: Query parameters

        Returns:
            List of document IDs

        Example:
            query = "SELECT * FROM c WHERE c.archivist_status = @status"
            parameters = [{"name": "@status", "value": "pending"}]
            OR
            query = "SELECT c.id FROM c WHERE c.archivist_status = @status"
            parameters = [{"name": "@status", "value": "pending"}]
        """
        try:
            items = self._container.query_items(
                query=query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True
            )

            # Extract document IDs from query results
            doc_ids = []
            for item in items:
                doc_id = item.get("id") or item.get("record_id")
                if doc_id:
                    doc_ids.append(doc_id)

            logger.info("Query returned %d document IDs", len(doc_ids))
            return doc_ids

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to query document IDs: %s", e.message)
            raise

    @retry_on_429()
    def query_document_ids_paginated(
        self,
        query: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[str]:
        """
        Query documents with pagination (OFFSET/LIMIT) and return only their IDs.
        
        More memory-efficient than loading all IDs at once for large datasets.

        Args:
            query: Base SQL query string (should select c.id, ORDER BY will be added if missing)
            parameters: Query parameters
            offset: Number of records to skip
            limit: Maximum records to return

        Returns:
            List of document IDs for this page

        Example:
            # Get first 100 IDs
            ids = client.query_document_ids_paginated(
                query="SELECT c.id FROM c WHERE c.archivist_status = @status",
                parameters=[{"name": "@status", "value": "published"}],
                offset=0,
                limit=100
            )
            # Get next 100 IDs
            ids = client.query_document_ids_paginated(..., offset=100, limit=100)
        """
        try:
            # Add ORDER BY if not present (required for consistent pagination)
            paginated_query = query
            if "ORDER BY" not in query.upper():
                paginated_query = f"{query} ORDER BY c.id"
            
            # Add OFFSET and LIMIT
            paginated_query = f"{paginated_query} OFFSET {offset} LIMIT {limit}"
            
            items = self._container.query_items(
                query=paginated_query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True
            )

            # Extract document IDs from query results
            doc_ids = []
            for item in items:
                doc_id = item.get("id") or item.get("record_id")
                if doc_id:
                    doc_ids.append(doc_id)

            logger.info(
                "Paginated query returned %d document IDs (offset=%d, limit=%d)",
                len(doc_ids), offset, limit
            )
            return doc_ids

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to query document IDs (paginated): %s", e.message)
            raise

    def close(self) -> None:
        """Close the CosmosDB client connection."""
        if self._client:
            # CosmosDB client doesn't require explicit closing
            # but we set references to None for cleanup
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
