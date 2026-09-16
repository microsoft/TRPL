# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
EPUB Document Service

Handles CosmosDB operations for EPUB documents.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from azure.cosmos.database import DatabaseProxy
from azure.cosmos.container import ContainerProxy
from azure.cosmos import exceptions

from core.config import settings
from services.cosmos_service import get_cosmos_client

logger = logging.getLogger(__name__)


# Singleton instance
_epub_document_service: Optional['EpubDocumentService'] = None


def get_epub_document_service() -> 'EpubDocumentService':
    """Get or create singleton EpubDocumentService instance."""
    global _epub_document_service
    if _epub_document_service is None:
        _epub_document_service = EpubDocumentService()
    return _epub_document_service


class EpubDocumentService:
    """Service for EPUB document CosmosDB operations."""

    def __init__(self):
        """Initialize EPUB document service."""
        self._database: Optional[DatabaseProxy] = None
        self._epub_container: Optional[ContainerProxy] = None
        self._chunks_container: Optional[ContainerProxy] = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB."""
        try:
            client = get_cosmos_client()
            self._database = client.get_database_client(settings.cosmos_db_database_name)
            logger.info("EpubDocumentService connected to database: %s", settings.cosmos_db_database_name)
        except Exception as e:
            logger.exception("Failed to connect to CosmosDB: %s", str(e))
            raise

    def _get_epub_container(self) -> Optional[ContainerProxy]:
        """Get EPUB container with lazy initialization."""
        if self._epub_container is None and self._database:
            try:
                self._epub_container = self._database.get_container_client(
                    settings.epub_cosmos_container_name
                )
                # Verify container exists
                self._epub_container.read()
            except exceptions.CosmosResourceNotFoundError:
                logger.warning("EPUB container not found: %s", settings.epub_cosmos_container_name)
                return None
            except Exception as e:
                logger.warning("Failed to get EPUB container: %s", str(e))
                return None
        return self._epub_container

    def _get_chunks_container(self) -> Optional[ContainerProxy]:
        """Get chunks container with lazy initialization."""
        if self._chunks_container is None and self._database:
            try:
                self._chunks_container = self._database.get_container_client(
                    settings.epub_chunks_container_name
                )
            except Exception as e:
                logger.warning("Failed to get chunks container: %s", str(e))
                return None
        return self._chunks_container

    def get_document_by_id(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        Get an EPUB document by ID.
        
        Args:
            document_id: The document ID
            
        Returns:
            Document dict or None if not found
        """
        container = self._get_epub_container()
        if not container:
            return None
            
        try:
            return container.read_item(item=document_id, partition_key=document_id)
        except exceptions.CosmosResourceNotFoundError:
            return None
        except Exception as e:
            logger.exception("Failed to get EPUB document %s: %s", document_id, str(e))
            return None

    def get_document_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """
        Get an EPUB document by filename.
        
        Args:
            filename: The EPUB filename
            
        Returns:
            Document dict or None if not found
        """
        container = self._get_epub_container()
        if not container:
            return None
            
        try:
            query = "SELECT * FROM c WHERE c.filename = @filename"
            params = [{"name": "@filename", "value": filename}]
            items = list(container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            return items[0] if items else None
        except Exception as e:
            logger.warning("Error checking for existing document: %s", str(e))
            return None

    def list_documents(
        self,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[List[Dict[str, Any]], int, int]:
        """
        List EPUB documents with pagination.
        
        Args:
            status: Optional status filter
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            Tuple of (documents, total_count, total_pages)
        """
        container = self._get_epub_container()
        if not container:
            return [], 0, 0
            
        try:
            # Build query
            query = "SELECT * FROM c"
            parameters = []

            if status:
                query += " WHERE c.status = @status"
                parameters.append({"name": "@status", "value": status})

            query += " ORDER BY c.created_at DESC"

            # Execute query
            items = list(container.query_items(
                query=query,
                parameters=parameters if parameters else None,
                enable_cross_partition_query=True
            ))

            total_count = len(items)
            total_pages = (total_count + page_size - 1) // page_size

            # Paginate
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_items = items[start_idx:end_idx]

            return page_items, total_count, total_pages
            
        except Exception as e:
            logger.exception("Failed to list EPUB documents: %s", str(e))
            return [], 0, 0

    def upsert_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upsert an EPUB document.
        
        Args:
            document: The document to upsert
            
        Returns:
            The upserted document
        """
        container = self._get_epub_container()
        if not container:
            raise ValueError("EPUB container not available")
            
        document["updated_at"] = datetime.now(timezone.utc).isoformat()
        return container.upsert_item(document)

    def delete_document(self, document_id: str) -> bool:
        """
        Delete an EPUB document.
        
        Args:
            document_id: The document ID to delete
            
        Returns:
            True if deleted, False otherwise
        """
        container = self._get_epub_container()
        if not container:
            return False
            
        try:
            container.delete_item(item=document_id, partition_key=document_id)
            return True
        except exceptions.CosmosResourceNotFoundError:
            return False
        except Exception as e:
            logger.exception("Failed to delete EPUB document %s: %s", document_id, str(e))
            return False

    def get_document_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        """
        Get all chunks for an EPUB document.
        
        Args:
            document_id: The document ID
            
        Returns:
            List of chunk documents
        """
        container = self._get_chunks_container()
        if not container:
            return []
            
        try:
            query = "SELECT * FROM c WHERE c.document_id = @document_id ORDER BY c.chunk_index"
            parameters = [{"name": "@document_id", "value": document_id}]
            
            return list(container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
        except Exception as e:
            logger.exception("Failed to get chunks for document %s: %s", document_id, str(e))
            return []

    def container_exists(self) -> bool:
        """Check if the EPUB container exists."""
        return self._get_epub_container() is not None
