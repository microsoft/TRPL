"""
Field Mapping Service for managing identifier field configurations.

This service handles CRUD operations for field mappings that define
which metadata field should be used as the identifier for each
repository or collection.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos import ContainerProxy, exceptions

from core.config import settings
from services.cosmos_service import get_cosmos_client

logger = logging.getLogger(__name__)


class FieldMappingService:
    """Service for managing field mapping configurations."""

    def __init__(self):
        """Initialize Field Mapping service."""
        self._container: Optional[ContainerProxy] = None
        self._data_container: Optional[ContainerProxy] = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB statistics container."""
        try:
            client = get_cosmos_client()
            database = client.get_database_client(settings.cosmos_db_database_name)
            
            # Get statistics container for field mappings
            stats_container_name = getattr(settings, 'cosmos_db_stats_container_name', 'statistics')
            try:
                self._container = database.get_container_client(stats_container_name)
                # Test connection
                self._container.read()
                logger.info("Field mapping service connected to container: %s", stats_container_name)
            except exceptions.CosmosResourceNotFoundError:
                # Create container if it doesn't exist
                database.create_container(
                    id=stats_container_name,
                    partition_key={"paths": ["/id"], "kind": "Hash"}
                )
                self._container = database.get_container_client(stats_container_name)
                logger.info("Created statistics container: %s", stats_container_name)
            
            # Also connect to data container for metadata field discovery
            if settings.cosmos_db_container_name:
                self._data_container = database.get_container_client(settings.cosmos_db_container_name)
                
            logger.info("Field mapping service connected")
            
        except Exception as e:
            logger.exception("Failed to connect field mapping service: %s", str(e))
            raise

    def _generate_mapping_id(self, repository: str, collection: Optional[str] = None) -> str:
        """Generate a unique ID for a field mapping."""
        # Normalize repository and collection names for ID
        repo_normalized = re.sub(r'[^a-zA-Z0-9]', '_', repository.lower())
        if collection:
            col_normalized = re.sub(r'[^a-zA-Z0-9]', '_', collection.lower())
            return f"field_mapping_{repo_normalized}_{col_normalized}"
        return f"field_mapping_{repo_normalized}"

    def get_mappings(
        self,
        repository: Optional[str] = None,
        collection: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get field mappings, optionally filtered by repository and/or collection.
        
        Args:
            repository: Optional repository name to filter by
            collection: Optional collection name to filter by
            
        Returns:
            List of field mapping documents
        """
        try:
            # Build query based on filters
            query = "SELECT * FROM c WHERE c.type = 'field_mapping'"
            params = []
            
            if repository:
                query += " AND c.repository = @repository"
                params.append({"name": "@repository", "value": repository})
            
            if collection:
                query += " AND c.collection = @collection"
                params.append({"name": "@collection", "value": collection})
            
            results = list(self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            # Sort results in Python to avoid needing a composite index
            results.sort(key=lambda x: (x.get('repository', ''), x.get('collection') or ''))
            
            logger.info("Retrieved %d field mappings", len(results))
            return results
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get field mappings: %s", e.message)
            raise

    def get_mapping(
        self,
        repository: str,
        collection: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific field mapping for a repository/collection.
        Falls back to repository-level mapping if collection-specific one doesn't exist.
        
        Args:
            repository: Repository name
            collection: Optional collection name
            
        Returns:
            Field mapping document or None if not found
        """
        try:
            # First try to find collection-specific mapping
            if collection:
                mapping_id = self._generate_mapping_id(repository, collection)
                query = "SELECT * FROM c WHERE c.type = 'field_mapping' AND c.id = @id"
                params = [{"name": "@id", "value": mapping_id}]
                results = list(self._container.query_items(
                    query=query,
                    parameters=params,
                    enable_cross_partition_query=True
                ))
                if results:
                    return results[0]
            
            # Try repository-level mapping
            mapping_id = self._generate_mapping_id(repository)
            query = "SELECT * FROM c WHERE c.type = 'field_mapping' AND c.id = @id"
            params = [{"name": "@id", "value": mapping_id}]
            results = list(self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            if results:
                return results[0]
            
            return None
                
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get field mapping: %s", e.message)
            raise

    def create_mapping(
        self,
        repository: str,
        identifier_field: str,
        collection: Optional[str] = None,
        display_name: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new field mapping.
        
        Args:
            repository: Repository name
            identifier_field: The metadata field to use for identifier
            collection: Optional collection name
            display_name: Optional custom display name for the column
            created_by: User who created the mapping
            
        Returns:
            Created field mapping document
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            mapping_id = self._generate_mapping_id(repository, collection)
            
            # Check if mapping already exists
            try:
                self._container.read_item(item=mapping_id, partition_key=mapping_id)
                raise ValueError(
                    f"Field mapping already exists for repository '{repository}'"
                    + (f" and collection '{collection}'" if collection else "")
                )
            except exceptions.CosmosResourceNotFoundError:
                pass  # Good, mapping doesn't exist
            
            mapping = {
                "id": mapping_id,
                "type": "field_mapping",
                "repository": repository,
                "collection": collection,
                "identifier_field": identifier_field,
                "display_name": display_name,
                "created_at": timestamp,
                "updated_at": timestamp,
                "created_by": created_by
            }
            
            result = self._container.create_item(body=mapping)
            logger.info("Created field mapping: %s", mapping_id)
            return result
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to create field mapping: %s", e.message)
            raise

    def update_mapping(
        self,
        mapping_id: str,
        identifier_field: Optional[str] = None,
        display_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing field mapping.
        
        Args:
            mapping_id: The ID of the mapping to update
            identifier_field: New identifier field value (optional)
            display_name: New display name value (optional)
            
        Returns:
            Updated field mapping document
        """
        try:
            # Read existing mapping
            try:
                existing = self._container.read_item(item=mapping_id, partition_key=mapping_id)
            except exceptions.CosmosResourceNotFoundError as exc:
                raise ValueError(f"Field mapping '{mapping_id}' not found") from exc
            
            # Update fields
            timestamp = datetime.now(timezone.utc).isoformat()
            existing["updated_at"] = timestamp
            
            if identifier_field is not None:
                existing["identifier_field"] = identifier_field
            if display_name is not None:
                existing["display_name"] = display_name
            
            result = self._container.replace_item(item=mapping_id, body=existing)
            logger.info("Updated field mapping: %s", mapping_id)
            return result
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update field mapping: %s", e.message)
            raise

    def delete_mapping(self, mapping_id: str) -> bool:
        """
        Delete a field mapping.
        
        Args:
            mapping_id: The ID of the mapping to delete
            
        Returns:
            True if deleted successfully
        """
        try:
            self._container.delete_item(item=mapping_id, partition_key=mapping_id)
            logger.info("Deleted field mapping: %s", mapping_id)
            return True
            
        except exceptions.CosmosResourceNotFoundError as exc:
            logger.warning("Field mapping not found for deletion: %s", mapping_id)
            raise ValueError(f"Field mapping '{mapping_id}' not found") from exc
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to delete field mapping: %s", e.message)
            raise

    def get_available_metadata_fields(self) -> List[str]:
        """
        Get a list of all unique metadata field names from documents.
        
        Returns:
            List of metadata field names
        """
        try:
            if not self._data_container:
                logger.warning("Data container not configured, returning default fields only")
                return sorted(list(self._get_default_fields()))
            
            # Query to get sample documents with metadata
            query = """
                SELECT TOP 100 c.metadata
                FROM c
                WHERE c.metadata != null AND c.type != 'field_mapping'
            """
            
            results = list(self._data_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            # Collect all unique field names
            all_fields = set()
            for doc in results:
                metadata = doc.get("metadata", {})
                if metadata:
                    all_fields.update(metadata.keys())
            
            # Add common default fields that might not be in the sample
            all_fields.update(self._get_default_fields())
            
            sorted_fields = sorted(list(all_fields))
            logger.info("Retrieved %d available metadata fields", len(sorted_fields))
            return sorted_fields
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get metadata fields: %s", e.message)
            raise

    def _get_default_fields(self) -> set:
        """Get default metadata field names."""
        return {
            "Source Record ID", "Identifier", "Title", "Description", "Creator", "Collection",
            "Repository", "Resource Type", "Creation Date", "Period",
            "Language", "record_id", "source_record_id"
        }


# Singleton instance
_field_mapping_service: Optional[FieldMappingService] = None


def get_field_mapping_service() -> FieldMappingService:
    """Get or create Field Mapping service instance."""
    global _field_mapping_service

    if _field_mapping_service is None:
        _field_mapping_service = FieldMappingService()

    return _field_mapping_service
