"""
Azure CosmosDB client for OCR extractor document management.

This module provides both a CosmosDBClient class for document operations
and a singleton CosmosClientManager for optimal connection management.
"""

import os
import logging
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, fields
from datetime import datetime

from azure.core.pipeline.transport import RequestsTransport
from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.cosmos.database import DatabaseProxy

from .config import CosmosDBConfig
from .credential import get_credential


# Use module logger; don't call basicConfig in library modules
logger = logging.getLogger(__name__)


class CosmosClientManager:
    """
    Singleton manager for shared CosmosClient instance.

    This class ensures only one CosmosClient instance is created
    for the entire application lifetime, which is the recommended pattern
    for optimal performance and connection management.
    """

    _instance: Optional["CosmosClientManager"] = None
    _cosmos_client: Optional[CosmosClient] = None
    _database: Optional[DatabaseProxy] = None
    _database_name: Optional[str] = None

    def __new__(cls) -> "CosmosClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _create_client(cls) -> CosmosClient:
        endpoint = os.getenv("COSMOS_ENDPOINT") or os.getenv("COSMOS_END_POINT")
        if not endpoint:
            raise ValueError("Missing required environment variable: COSMOS_ENDPOINT")

        connection_mode = os.getenv("COSMOS_CONNECTION_MODE", "Gateway")
        connection_string = os.getenv("COSMOS_CONNECTION_STRING", "").strip()
        environment = os.getenv("ENVIRONMENT", "production").strip().lower()
        if connection_string and environment != "local":
            raise RuntimeError(
                "COSMOS_CONNECTION_STRING is only supported when ENVIRONMENT=local"
            )

        logger.info(
            "Initializing shared CosmosClient instance (endpoint=%s, connection_mode=%s)",
            endpoint,
            connection_mode,
        )

        sess = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        client_options = {
            "connection_mode": connection_mode,
            "transport": RequestsTransport(session=sess, session_owner=False),
        }

        if connection_string:
            return CosmosClient.from_connection_string(
                connection_string,
                **client_options,
            )
        return CosmosClient(
            url=endpoint,
            credential=get_credential(),
            **client_options,
        )

    def get_client(self) -> CosmosClient:
        """
        Get or create the shared CosmosClient instance.

        Cloud environments authenticate through Entra ID; local development
        may use the Cosmos emulator connection string.
        """
        if self._cosmos_client is None:
            CosmosClientManager._cosmos_client = self._create_client()
            logger.info("Shared CosmosClient instance initialized successfully")
        return self._cosmos_client

    def get_database(self, database_name: Optional[str] = None) -> DatabaseProxy:
        """
        Get or create the shared DatabaseProxy instance.

        Args:
            database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

        Returns:
            DatabaseProxy: The shared DatabaseProxy instance
        """
        if database_name is None:
            database_name = os.getenv("COSMOS_DATABASE_NAME")
            if not database_name:
                raise ValueError(
                    "Missing required environment variable: COSMOS_DATABASE_NAME"
                )

        # Return cached database if it matches the requested name
        if self._database is not None and self._database_name == database_name:
            return self._database

        client = self.get_client()
        CosmosClientManager._database = client.get_database_client(database_name)
        CosmosClientManager._database_name = database_name
        logger.info("Shared DatabaseProxy instance initialized for database: %s", database_name)

        return self._database

    def get_container(self, container_name: str, database_name: Optional[str] = None) -> ContainerProxy:
        """
        Get a container client from the shared CosmosClient instance.

        Args:
            container_name: Container name
            database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

        Returns:
            ContainerProxy: Container client instance
        """
        database = self.get_database(database_name)
        return database.get_container_client(container_name)


# Module-level singleton instance
_manager = CosmosClientManager()


def get_cosmos_client() -> CosmosClient:
    """
    Get the shared CosmosClient instance.

    Returns:
        CosmosClient: The shared CosmosClient instance
    """
    return _manager.get_client()


def get_database(database_name: Optional[str] = None) -> DatabaseProxy:
    """
    Get the shared DatabaseProxy instance.

    Args:
        database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

    Returns:
        DatabaseProxy: The shared DatabaseProxy instance
    """
    return _manager.get_database(database_name)


def get_container(container_name: str, database_name: Optional[str] = None) -> ContainerProxy:
    """
    Get a container client from the shared CosmosClient instance.

    Args:
        container_name: Container name
        database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

    Returns:
        ContainerProxy: Container client instance
    """
    return _manager.get_container(container_name, database_name)


def query_records_by_ids(
    record_ids: List[str],
    cosmos_container: Optional[ContainerProxy] = None,
    container_name: Optional[str] = None,
    database_name: Optional[str] = None
) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Query multiple records by their IDs in a single batch query.

    This is much more efficient than individual read_item() calls.

    Args:
        record_ids: List of record IDs to query
        cosmos_container: Optional Cosmos DB container proxy
        container_name: Container name (used if cosmos_container not provided)
        database_name: Database name (used if cosmos_container not provided)

    Returns:
        Tuple of (records_map, missing_ids) where:
        - records_map: Dict mapping record_id -> record data
        - missing_ids: List of record IDs that weren't found
    """
    if cosmos_container is None:
        # Use default container from environment
        _container_name = container_name or os.getenv("COSMOS_CONTAINER_NAME")
        if not _container_name:
            raise ValueError("Container name must be provided or set via COSMOS_CONTAINER_NAME")
        cosmos_container = get_container(_container_name, database_name)

    if not record_ids:
        return {}, []

    # Build IN clause - escape single quotes in record IDs
    escaped_ids = [rid.replace("'", "''") for rid in record_ids]
    in_clause = "', '".join(escaped_ids)
    query = f"SELECT * FROM c WHERE c.record_id IN ('{in_clause}')"

    try:
        items = list(
            cosmos_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            )
        )

        # Create mapping from record_id to record data
        records_map = {item.get("record_id"): item for item in items if item.get("record_id")}

        # Identify missing records
        found_ids = set(records_map.keys())
        missing_ids = [rid for rid in record_ids if rid not in found_ids]

        logger.debug("Queried %d records: found %d, missing %d",
                    len(record_ids), len(records_map), len(missing_ids))

        return records_map, missing_ids

    except exceptions.CosmosHttpResponseError as e:
        logger.exception("Failed to query records from Cosmos DB: %s", e)
        raise


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
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentMetadata":
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
        """Establish connection to CosmosDB using shared client instance."""
        try:
            logger.info("Connecting to CosmosDB at %s", self.config.endpoint)

            # Use shared CosmosClient instance for optimal connection management
            # This ensures a single client instance across the application
            self._client = get_cosmos_client()

            # Get database reference
            self._database = self._client.get_database_client(self.config.database_name)

            # Get container reference
            self._container = self._database.get_container_client(
                self.config.container_name
            )

            logger.info(
                "Successfully connected to database '%s', container '%s' using shared CosmosClient",
                self.config.database_name,
                self.config.container_name,
            )

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to connect to CosmosDB: %s", e.message)
            raise
        except Exception as e:
            logger.exception("Unexpected error connecting to CosmosDB: %s", str(e))
            raise

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
            if "created_at" not in doc_dict:
                doc_dict["created_at"] = datetime.utcnow().isoformat()

            result = self._container.create_item(body=doc_dict)
            logger.info("Created document with id: %s", result["id"])

            return DocumentMetadata.from_dict(result)

        except exceptions.CosmosResourceExistsError:
            logger.exception("Document with id '%s' already exists", document.id)
            raise
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to create document: %s", e.message)
            raise

    def get_document(
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
            # Use document_id as partition key if not specified
            pk = partition_key if partition_key is not None else document_id

            result = self._container.read_item(item=document_id, partition_key=pk)

            logger.info("Retrieved document with id: %s", document_id)
            return DocumentMetadata.from_dict(result)

        except exceptions.CosmosResourceNotFoundError:
            logger.exception("Document with id '%s' not found", document_id)
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to retrieve document: %s", e.message)
            raise

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
            logger.info("Updated document with id: %s", result["id"])

            return DocumentMetadata.from_dict(result)

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update document: %s", e.message)
            raise

    def delete_document(
        self, document_id: str, partition_key: Optional[str] = None
    ) -> bool:
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

            self._container.delete_item(item=document_id, partition_key=pk)

            logger.info("Deleted document with id: %s", document_id)
            return True

        except exceptions.CosmosResourceNotFoundError:
            logger.exception("Document with id '%s' not found", document_id)
            return False
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to delete document: %s", e.message)
            raise

    def query_documents(
        self,
        query: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
        max_items: Optional[int] = None,
    ) -> List[DocumentMetadata]:
        """
        Query documents using SQL-like syntax.

        Args:
            query: SQL query string
            parameters: Query parameters
            max_items: Maximum number of items to return

        Returns:
            List of document metadata

        Example:
            query = "SELECT * FROM c WHERE c.status = @status"
            parameters = [{"name": "@status", "value": "pending"}]
        """
        try:
            items = self._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
                max_item_count=max_items,
            )

            documents = [DocumentMetadata.from_dict(item) for item in items]
            logger.info("Query returned %d documents", len(documents))

            return documents

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to query documents: %s", e.message)
            raise

    def get_pending_documents(
        self, max_items: Optional[int] = 100
    ) -> List[DocumentMetadata]:
        """
        Get all documents with status 'pending'.

        Args:
            max_items: Maximum number of items to return

        Returns:
            List of pending document metadata
        """
        query = "SELECT * FROM c WHERE c.status = @status ORDER BY c.created_at"
        parameters = [{"name": "@status", "value": "pending"}]

        return self.query_documents(query, parameters, max_items)

    def get_documents_by_status(
        self, status: str, max_items: Optional[int] = 100
    ) -> List[DocumentMetadata]:
        """
        Get all documents with a specific status.

        Args:
            status: Status to filter by (pending, processing, completed, failed)
            max_items: Maximum number of items to return

        Returns:
            List of document metadata
        """
        query = "SELECT * FROM c WHERE c.status = @status ORDER BY c.created_at DESC"
        parameters = [{"name": "@status", "value": status}]

        return self.query_documents(query, parameters, max_items)

    def update_document_status(
        self,
        document_id: str,
        status: str,
        error_message: Optional[str] = None,
        ocr_result: Optional[Dict[str, Any]] = None,
        partition_key: Optional[str] = None,
    ) -> Optional[DocumentMetadata]:
        """
        Update document status and optionally set OCR result or error message.

        Args:
            document_id: Document ID
            status: New status
            error_message: Error message if status is 'failed'
            ocr_result: OCR result if status is 'completed'
            partition_key: Partition key value (if different from ID)

        Returns:
            Updated document metadata or None if not found
        """
        document = self.get_document(document_id, partition_key)

        if document is None:
            logger.warning("Cannot update status: document '%s' not found", document_id)
            return None

        document.status = status
        document.processed_at = datetime.utcnow().isoformat()

        if error_message:
            document.error_message = error_message

        if ocr_result:
            document.ocr_result = ocr_result

        return self.update_document(document)

    def get_images_for_processing(self, batch_size: int = 10) -> List[DocumentMetadata]:
        """
        Get a batch of pending documents for OCR processing.

        Args:
            batch_size: Number of documents to retrieve

        Returns:
            List of document metadata ready for processing
        """
        return self.get_pending_documents(max_items=batch_size)

    def mark_as_processing(
        self, document_id: str, partition_key: Optional[str] = None
    ) -> Optional[DocumentMetadata]:
        """
        Mark a document as currently being processed.

        Args:
            document_id: Document ID
            partition_key: Partition key value (if different from ID)

        Returns:
            Updated document metadata or None if not found
        """
        return self.update_document_status(
            document_id, "processing", partition_key=partition_key
        )

    def mark_as_completed(
        self,
        document_id: str,
        ocr_result: Dict[str, Any],
        partition_key: Optional[str] = None,
    ) -> Optional[DocumentMetadata]:
        """
        Mark a document as completed with OCR results.

        Args:
            document_id: Document ID
            ocr_result: OCR extraction result
            partition_key: Partition key value (if different from ID)

        Returns:
            Updated document metadata or None if not found
        """
        return self.update_document_status(
            document_id, "completed", ocr_result=ocr_result, partition_key=partition_key
        )

    def mark_as_failed(
        self, document_id: str, error_message: str, partition_key: Optional[str] = None
    ) -> Optional[DocumentMetadata]:
        """
        Mark a document as failed with error message.

        Args:
            document_id: Document ID
            error_message: Error description
            partition_key: Partition key value (if different from ID)

        Returns:
            Updated document metadata or None if not found
        """
        return self.update_document_status(
            document_id,
            "failed",
            error_message=error_message,
            partition_key=partition_key,
        )

    def get_statistics(self) -> Dict[str, int]:
        """
        Get statistics about documents in the database.

        Returns:
            Dictionary with counts by status
        """
        stats = {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}

        try:
            # Count total documents
            query = "SELECT VALUE COUNT(1) FROM c"
            result = list(
                self._container.query_items(
                    query=query, enable_cross_partition_query=True
                )
            )
            stats["total"] = result[0] if result else 0

            # Count by status
            for status in ["pending", "processing", "completed", "failed"]:
                query = "SELECT VALUE COUNT(1) FROM c WHERE c.status = @status"
                parameters = [{"name": "@status", "value": status}]
                result = list(
                    self._container.query_items(
                        query=query,
                        parameters=parameters,
                        enable_cross_partition_query=True,
                    )
                )
                stats[status] = result[0] if result else 0

            logger.info("Database statistics: %s", stats)
            return stats

        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get statistics: %s", e.message)
            raise

    def close(self) -> None:
        """Close the CosmosDB client connection."""
        # Note: We use a shared CosmosClient instance, so we don't close it here
        # Only clear local references
        self._container = None
        self._database = None
        # Don't set _client to None as it's shared across the application
        logger.info("CosmosDB client references cleared (shared client remains active)")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
