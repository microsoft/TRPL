# pylint: disable=broad-exception-caught,too-few-public-methods,invalid-name

"""
Audit helper for tracking document status changes in Azure Functions.

This module provides functionality to create audit entries when documents
transition between statuses (e.g., Pending -> Publishing -> Published).
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


class AuditHelper:
    """Helper class for creating audit entries in CosmosDB."""

    # System user for automated operations
    SYSTEM_USER_ID = "system"
    SYSTEM_USER_DISPLAY = "Archivist System"

    def __init__(self):
        """Initialize audit helper."""
        self._client: Optional[CosmosClient] = None
        self._audit_container: Optional[ContainerProxy] = None
        self._connected = False

    def _connect(self) -> bool:
        """
        Establish connection to the audit container in CosmosDB.
        
        Returns:
            True if connection successful, False otherwise
        """
        if self._connected and self._audit_container:
            return True

        try:
            endpoint = os.getenv('COSMOS_DB_ENDPOINT')
            database_name = os.getenv('COSMOS_DB_DATABASE_NAME')
            audit_container_name = os.getenv('COSMOS_DB_AUDIT_CONTAINER_NAME', 'audit')

            if not endpoint or not database_name:
                logger.warning(
                    "Audit helper: Missing CosmosDB configuration. "
                    "Audit logging disabled."
                )
                return False

            if not audit_container_name:
                logger.warning(
                    "Audit helper: Audit container not configured. "
                    "Audit logging disabled."
                )
                return False

            self._client = CosmosClient(endpoint, credential=DefaultAzureCredential())
            database = self._client.get_database_client(database_name)
            self._audit_container = database.get_container_client(audit_container_name)
            self._connected = True

            logger.info("Audit helper connected to container: %s", audit_container_name)
            return True

        except exceptions.CosmosHttpResponseError as e:
            logger.error("Audit helper: Failed to connect to CosmosDB: %s", str(e))
            return False
        except Exception as e:
            logger.error("Audit helper: Unexpected error during connection: %s", str(e))
            return False

    def create_status_change_audit(
        self,
        document_id: str,
        old_status: str,
        new_status: str,
        version: int = 1,
        user_id: Optional[str] = None,
        user_display: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create an audit entry for a document status change.
        
        Args:
            document_id: ID of the document that changed
            old_status: Previous status value
            new_status: New status value
            version: Document version number
            user_id: User who made the change (defaults to system)
            user_display: Display name of the user (defaults to system)
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Created audit document, or None if audit logging failed/disabled
        """
        if not self._connect():
            return None

        try:
            audit_id = f"audit_{uuid.uuid4()}"
            timestamp = datetime.now(timezone.utc).isoformat()

            # Use system user if no user provided
            actual_user_id = user_id or self.SYSTEM_USER_ID
            actual_user_display = user_display or self.SYSTEM_USER_DISPLAY

            # Create field change entry for status
            field_changes: List[Dict[str, Any]] = [
                {
                    "path": "/archivist_status",
                    "from": old_status or "",
                    "to": new_status or ""
                }
            ]

            audit_doc = {
                "id": audit_id,
                "record_id": document_id,  # partition key
                "ts": timestamp,
                "by": {
                    "userId": actual_user_id,
                    "display": actual_user_display
                },
                "op": "status_change",
                "version": version,
                "fields": field_changes
            }

            if correlation_id:
                audit_doc["correlationId"] = correlation_id

            # Add metadata about the operation
            audit_doc["metadata"] = {
                "source": "azure_functions",
                "operation_type": "publish_workflow"
            }

            created = self._audit_container.create_item(
                body=audit_doc,
                enable_automatic_id_generation=False
            )

            logger.info(
                "Created audit entry %s for document %s: %s -> %s",
                audit_id, document_id, old_status, new_status
            )
            return created

        except exceptions.CosmosHttpResponseError as e:
            logger.error(
                "Failed to create audit entry for document %s: %s",
                document_id, str(e)
            )
            return None
        except Exception as e:
            logger.error(
                "Unexpected error creating audit entry for document %s: %s",
                document_id, str(e)
            )
            return None


# Singleton instance
_audit_helper: Optional[AuditHelper] = None


def get_audit_helper() -> AuditHelper:
    """
    Get or create the singleton AuditHelper instance.
    
    Returns:
        AuditHelper instance
    """
    global _audit_helper  # pylint: disable=global-statement
    if _audit_helper is None:
        _audit_helper = AuditHelper()
    return _audit_helper
