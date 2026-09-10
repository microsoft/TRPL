"""
Audit service for tracking document changes in CosmosDB.
"""

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from azure.cosmos import exceptions
from azure.cosmos.container import ContainerProxy

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.blob_service import (
    download_blob_text,
    BlobDownloadError,
    BlobNotFoundError,
    BlobAuthenticationError
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuditService:
    """Service for managing audit trail in CosmosDB."""

    def __init__(self, audit_container: ContainerProxy):
        """
        Initialize audit service.

        Args:
            audit_container: CosmosDB container for audit logs
        """
        self._audit_container = audit_container

    def create_audit_entry(
        self,
        entity_id: str,
        user_id: str,
        user_display: str,
        operation: str,
        version: int,
        changed_fields: List[Dict[str, Any]],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an audit entry for a document change.
        Args:
            entity_id: ID of the document that was changed (partition key)
            user_id: User ID who made the change
            user_display: Display name of the user
            operation: Type of operation (create, update, delete)
            version: New version number after the change
            changed_fields: List of field changes with path, from, and to values
            correlation_id: Optional correlation ID for request tracing
        Returns:
            Created audit document
        """
        audit_id = f"audit_{uuid.uuid4()}"
        timestamp = datetime.now(timezone.utc).isoformat()
        audit_doc = {
            "id": audit_id,
            "entity_id": entity_id,   # partition key path (/entity_id) on digital-items-audit container
            "record_id": entity_id,   # kept for backward-compat query filter
            "ts": timestamp,
            "by": {
                "userId": user_id,
                "display": user_display
            },
            "op": operation,
            "version": version,
            "fields": changed_fields
        }

        if correlation_id:
            audit_doc["correlationId"] = correlation_id

        try:
            created = self._audit_container.create_item(
                body=audit_doc,
                enable_automatic_id_generation=False
            )
            logger.info("Created audit entry %s for record %s", audit_id, entity_id)
            return created
        except exceptions.CosmosHttpResponseError:
            logger.exception("Failed to create audit entry for record %s", entity_id)
            raise

    def get_audit_history(
        self,
        entity_id: str,
        max_items: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get audit history for a specific document.

        Args:
            entity_id: ID of the document (record_id)
            max_items: Maximum number of entries to return

        Returns:
            List of audit entries, sorted by timestamp descending
        """
        try:
            # Query by both entity_id (new docs, matches /entity_id partition key) and
            # record_id (legacy docs written before entity_id was added to the document).
            # Cross-partition is required to reach legacy docs stored in the null partition.
            query = """
                SELECT * FROM c
                WHERE c.entity_id = @entityId OR c.record_id = @entityId
                ORDER BY c.ts DESC
            """
            parameters = [{"name": "@entityId", "value": entity_id}]

            items = self._audit_container.query_items(
                query=query,
                parameters=parameters,
                max_item_count=max_items,
                enable_cross_partition_query=True
            )

            audit_entries = list(items)
            logger.info(
                "Retrieved %s audit entries for record %s",
                len(audit_entries), entity_id
            )

            return audit_entries

        except exceptions.CosmosHttpResponseError:
            logger.exception("Failed to query audit history for record %s", entity_id)
            raise

    def get_latest_version(self, entity_id: str) -> int:
        """
        Get the latest version number for an entity.

        Args:
            entity_id: ID of the document (record_id)

        Returns:
            Latest version number, or 0 if no audit entries exist
        """
        try:
            query = """
                SELECT TOP 1 c.version FROM c
                WHERE c.record_id = @recordId
                ORDER BY c.version DESC
            """
            parameters = [{"name": "@recordId", "value": entity_id}]

            items = list(self._audit_container.query_items(
                query=query,
                parameters=parameters,
                partition_key=entity_id,
                max_item_count=1,
                enable_cross_partition_query=False
            ))

            if items and len(items) > 0:
                return items[0].get('version', 0)
            return 0

        except exceptions.CosmosHttpResponseError:
            logger.exception("Failed to get latest version for record %s", entity_id)
            return 0


def create_field_changes(
    old_doc: Dict[str, Any],
    new_fields: Dict[str, Any],
    allowlist: List[str]
) -> List[Dict[str, Any]]:
    """
    Create a list of field changes between old and new document.
    Only includes fields that are in the allowlist and have actually changed.
    Args:
        old_doc: Original document from CosmosDB
        new_fields: New field values from the update request
        allowlist: List of allowed field names (e.g., ['title', 'creator'])
    Returns:
        List of field change objects with path, from, and to values
    """
    changes = []
    old_metadata = old_doc.get('metadata', {})
    # Field name mapping from camelCase (UI) to display names (CosmosDB)
    field_mapping = {
        'title': 'Title',
        'description': 'Description',
        'creationDate': 'Creation Date',
        'creator': 'Creator',
        'recipient': 'Recipient',
        'citation': 'Citation',
        'resourceType': 'Resource Type',
        'period': 'Period',
        'rights': 'Copyright Status',
        'productionMethod': 'Production Method',
        'language': 'Language'
    }
    for field_key, field_value in new_fields.items():
        # Skip if not in allowlist
        if field_key not in allowlist:
            continue

        # Get the CosmosDB field name
        cosmos_field = field_mapping.get(field_key, field_key)
        path = f"/metadata/{cosmos_field}"

        # Get old value
        old_value = old_metadata.get(cosmos_field, "")

        # Check if value actually changed
        if str(old_value).strip() != str(field_value).strip():
            changes.append({
                "path": path,
                "from": old_value if old_value else "",
                "to": field_value if field_value else ""
            })
    return changes


async def _get_ocr_text_from_asset(asset: Dict[str, Any]) -> str:
    """
    Extract OCR text from asset, downloading from blob if necessary.
    
    Args:
        asset: Asset dictionary that contains ocr_text or ocr_text_original_blob_url in ocr_result
        
    Returns:
        OCR text string (empty string if not available or download fails)
    """
    ocr_result = asset.get('ocr_result', {})

    # First check if ocr_text is already populated (hydrated or edited)
    ocr_text = ocr_result.get('ocr_text', '')
    if ocr_text and isinstance(ocr_text, str) and ocr_text.strip():
        return ocr_text.strip()

    # If not available, try to download from blob URL
    ocr_text_blob_url = ocr_result.get('ocr_text_original_blob_url', '')
    ocr_text_blob_url = ocr_text_blob_url.strip() if isinstance(ocr_text_blob_url, str) else ''

    # If it's a blob URL, download the content
    if ocr_text_blob_url and ocr_text_blob_url.startswith('https://'):
        try:
            downloaded_text = await download_blob_text(ocr_text_blob_url)
            return downloaded_text
        except (BlobDownloadError, BlobNotFoundError, BlobAuthenticationError, ValueError) as e:
            logger.warning(
                "Failed to download OCR text from blob for comparison: %s",
                str(e)
            )
            return ""

    # If not a blob URL or empty, return empty string
    return ""


def create_ocr_field_changes(
    old_doc: Dict[str, Any],
    asset_index: int,
    new_ocr_text: str
) -> List[Dict[str, Any]]:
    """
    Create field change entry for OCR text modification.

    Note: This is a synchronous function that wraps async blob downloading.
    If the old OCR text is stored as a blob URL, it will be downloaded
    for comparison.

    Args:
        old_doc: Original document from CosmosDB
        asset_index: Index of the asset in asset_details array
        new_ocr_text: New OCR text value
    Returns:
        List with single field change object for OCR text
    """
    changes = []
    asset_details = old_doc.get('asset_details', [])

    if asset_index < len(asset_details):
        asset = asset_details[asset_index]

        # Get old OCR text - may need to download from blob
        try:
            # Run async download in sync context
            old_ocr = asyncio.run(_get_ocr_text_from_asset(asset))
        except RuntimeError:
            # If we're already in an async context, use get_event_loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't use asyncio.run in a running loop
                    # Log warning and use empty string as fallback
                    logger.warning(
                        "Cannot download blob in running event loop. "
                        "Using empty string for comparison."
                    )
                    old_ocr = ""
                else:
                    old_ocr = loop.run_until_complete(_get_ocr_text_from_asset(asset))
            except Exception as e:
                logger.warning(
                    "Failed to download old OCR text for comparison: %s",
                    str(e)
                )
                old_ocr = ""

        if old_ocr != new_ocr_text:
            path = f"/asset_details/{asset_index}/ocr_result/ocr_text_original_blob_url"
            changes.append({
                "path": path,
                "from": old_ocr if old_ocr else "",
                "to": new_ocr_text if new_ocr_text else ""
            })

    return changes


async def create_ocr_field_changes_async(
    old_doc: Dict[str, Any],
    asset_index: int,
    new_ocr_text: str
) -> List[Dict[str, Any]]:
    """
    Async version of create_ocr_field_changes for use in async contexts.

    Create field change entry for OCR text modification, downloading
    old OCR text from blob storage if necessary.

    Args:
        old_doc: Original document from CosmosDB
        asset_index: Index of the asset in asset_details array
        new_ocr_text: New OCR text value
    Returns:
        List with single field change object for OCR text, or empty list if no changes
    Raises:
        ValueError: If asset_index is invalid or OCR result is missing
        BlobDownloadError: If old OCR text cannot be retrieved from blob storage
    """
    changes = []

    try:
        asset_details = old_doc.get('asset_details', [])
        # Validate asset_details structure
        if not isinstance(asset_details, list):
            logger.exception("asset_details is not a list: %s", type(asset_details))
            raise ValueError("asset_details must be a list")

        # Validate asset index
        if asset_index < 0 or asset_index >= len(asset_details):
            logger.warning(
                "Invalid asset_index %d for document with %d assets",
                asset_index,
                len(asset_details)
            )
            raise ValueError(
                f"asset_index {asset_index} is out of range (0-{len(asset_details)-1})"
            )

        asset = asset_details[asset_index]
        if not isinstance(asset, dict):
            logger.warning("Asset at index %d is not a dict: %s", asset_index, type(asset))
            raise ValueError(f"Asset at index {asset_index} is not a valid dictionary")

        # Get OCR result
        ocr_result = asset.get("ocr_result", {})
        if not ocr_result:
            logger.warning(
                "OCR result is empty for asset_index %d in document %s",
                asset_index,
                old_doc.get("id", "unknown")
            )
            raise ValueError(f"OCR result is empty for asset at index {asset_index}")

        # Get old OCR text - download from blob if needed
        old_ocr_url = ocr_result.get("ocr_text_flexible_blob_url", "")
        old_ocr = ""
        if old_ocr_url:
            try:
                old_ocr = await _get_ocr_text_from_asset(asset)
            except (BlobDownloadError, ValueError, OSError) as e:
                logger.warning(
                    "Failed to download old OCR text for comparison: %s",
                    str(e)
                )
                old_ocr = ""

        # Compare old and new OCR text
        if old_ocr == new_ocr_text:
            logger.info(
                "No OCR text changes detected for asset_index %d in document %s",
                asset_index,
                old_doc.get("id", "unknown")
            )
            return changes

        # Calculate change statistics for logging
        old_len = len(old_ocr) if old_ocr else 0
        new_len = len(new_ocr_text) if new_ocr_text else 0
        char_diff = new_len - old_len

        logger.info(
            "OCR text change detected for asset_index %d: "
            "old=%d chars, new=%d chars, diff=%+d chars",
            asset_index,
            old_len,
            new_len,
            char_diff
        )

        # Create change entry for flexible blob URL
        path = f"/asset_details/{asset_index}/ocr_result/ocr_text_flexible_blob_url"
        changes.append({
            "path": path,
            "from": old_ocr_url if old_ocr_url else "",
            "to": ""  # Will be set by caller with new URL
        })

        logger.debug(
            "Created OCR field change entry: path=%s, from=%s",
            path,
            old_ocr_url[:50] + "..." if len(old_ocr_url) > 50 else old_ocr_url
        )

        return changes

    except (ValueError, BlobDownloadError):
        # Re-raise expected exceptions
        raise
    except Exception as e:
        # Log and wrap unexpected exceptions
        logger.exception(
            "Unexpected error in create_ocr_field_changes_async for asset_index %d: %s",
            asset_index,
            str(e)
        )
        raise ValueError(
            f"Failed to create OCR field changes for asset at index {asset_index}: {str(e)}"
        ) from e
