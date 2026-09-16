# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pipeline Service for Archivist API.

Provides pipeline monitoring and statistics for the data pipeline orchestration.
Uses pre-computed aggregates stored in the statistics container for fast retrieval.
"""
# pylint: disable=duplicate-code

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add parent directory to path for imports (must be before local imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from azure.cosmos import exceptions
from core.config import settings
from services.cosmos_service import get_cosmos_service, get_cosmos_client

logger = logging.getLogger(__name__)

# Container name for batch status
BATCH_STATUS_CONTAINER = "batchstatus"

# Statistics document ID for pipeline stats
PIPELINE_STATS_ID = "pipeline_stats"
PIPELINE_STEP_PREFIX = "pipeline_step_"

# Pipeline config document ID
PIPELINE_CONFIG_ID = "pipeline_config"

# Default pipeline configuration - can be overridden via API
# This serves as the template for new configurations
DEFAULT_PIPELINE_CONFIG = {
    # Runtime configuration (user-editable)
    "collection_ids": [],
    "batch_size": 100,
    "parallel_batches": 20,
    
    # Pipeline stages definition
    "stages": [
        {
            "id": "content_source_sync",
            "name": "Content Source Sync",
            "description": "Query records through the configured content-source adapter",
            "icon": "Database",
            "status_field": "content_source_sync",
            "has_config": True,
            "trigger_endpoint": "content-source-sync-client",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "related_assets",
            "name": "Fetch Related Assets",
            "description": "Get asset IDs for each record",
            "icon": "FileImage",
            "status_field": "related_assets_status",
            "has_config": False,
            "trigger_endpoint": "fetch-related-assets",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "asset_details",
            "name": "Process Asset Details",
            "description": "Read asset metadata and rights",
            "icon": "Settings2",
            "status_field": "asset_details_status",
            "has_config": False,
            "trigger_endpoint": "process-asset-details",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "original_file",
            "name": "Download Original Files",
            "description": "Copy adapter-provided content to Blob Storage",
            "icon": "Download",
            "status_field": "original_file_status",
            "has_config": False,
            "trigger_endpoint": "process-original-files",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "resource_type_batch",
            "name": "Create Resource Type Batches",
            "description": "Create Azure OpenAI batch jobs for Resource Types",
            "icon": "ScanText",
            "status_field": "resource_type_batch_status",
            "has_config": False,
            "trigger_endpoint": "create-resource-type-batch",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "resource_type_processing",
            "name": "Process Resource Type Results",
            "description": "Process Resource Type batch results and extract resource type (Timer triggered)",
            "icon": "FileImage",
            "status_field": "resource_type_processing_status",
            "has_config": False,
            "trigger_endpoint": "resource-type-batch-status-poller",
            "is_automated": True,
            "can_retry": True
        },
        {
            "id": "ocr_batch",
            "name": "Create OCR Batches",
            "description": "Create Azure OpenAI batch jobs for OCR",
            "icon": "ScanText",
            "status_field": "ocr_batch_status",
            "has_config": False,
            "trigger_endpoint": "create-ocr-batch",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "ocr_processing",
            "name": "Process OCR Results",
            "description": "Process OCR batch results and extract text (Timer triggered)",
            "icon": "FileImage",
            "status_field": "ocr_processing_status",
            "has_config": False,
            "trigger_endpoint": "ocr-batch-status-poller",
            "is_automated": True,
            "can_retry": True
        },
        {
            "id": "metadata_batch",
            "name": "Create Metadata Batches",
            "description": "Create Azure OpenAI batch jobs for metadata extraction",
            "icon": "Sparkles",
            "status_field": "metadata_batch_status",
            "has_config": False,
            "trigger_endpoint": "create-metadata-batch",
            "is_automated": False,
            "can_retry": True
        },
        {
            "id": "metadata_extraction",
            "name": "Extract Metadata",
            "description": "Process metadata batch results and extract structured data (Timer triggered)",
            "icon": "Brain",
            "status_field": "metadata_extraction_status",
            "has_config": False,
            "trigger_endpoint": "metadata-batch-status-poller",
            "is_automated": True,
            "can_retry": True
        }
    ],
    
    # Special action endpoints (not regular stages)
    "actions": {
        "full_pipeline": {
            "name": "Periodic Pipeline Sync",
            "description": "Periodic sync (content-source-periodic-sync): full pipeline from Content Source sync through metadata extraction",
            "trigger_endpoint": "content-source-periodic-sync",
            "button_label": "Run Periodic Sync Pipeline",
            "icon": "Zap"
        },
        "retry_failed": {
            "name": "Retry Failed Records",
            "description": "Retry records that failed in any stage",
            "trigger_endpoint": "retry-failed-records",
            "button_label": "Retry Failed",
            "icon": "RotateCcw"
        }
    }
}


class PipelineService:  # pylint: disable=too-many-public-methods
    """
    Service for managing pipeline monitoring.
    
    Provides methods for:
    - Getting pipeline statistics by stage (from pre-computed aggregates)
    - Rebuilding pipeline statistics
    - Querying active batch jobs
    """

    def __init__(self):
        """Initialize Pipeline service."""
        self._cosmos_service = None
        self._database = None
        self._stats_container = None
        self._connect()

    def _connect(self) -> None:
        """Establish connection to CosmosDB."""
        try:
            self._cosmos_service = get_cosmos_service()
            self._database = self._cosmos_service._database
            
            # Get statistics container
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
                
            logger.info("Pipeline service connected")
        except Exception as e:
            logger.exception("Failed to connect to CosmosDB: %s", str(e))
            raise

    def get_pipeline_stages(self) -> Dict[str, Any]:
        """
        Get pipeline configuration including stages and actions.
        
        Returns config from Cosmos DB if exists, otherwise returns defaults.
        
        Returns:
            Dictionary with stages, actions, and runtime config
        """
        try:
            config = self.get_pipeline_config()
            return {
                "stages": config.get("stages", DEFAULT_PIPELINE_CONFIG["stages"]),
                "actions": config.get("actions", DEFAULT_PIPELINE_CONFIG["actions"]),
                "runtime_config": {
                    "collection_ids": config.get("collection_ids", []),
                    "batch_size": config.get("batch_size", 100),
                    "parallel_batches": config.get("parallel_batches", 20),
                    "updated_at": config.get("updated_at")
                }
            }
        except Exception as e:
            logger.warning("Failed to load config, using defaults: %s", str(e))
            return {
                "stages": DEFAULT_PIPELINE_CONFIG["stages"],
                "actions": DEFAULT_PIPELINE_CONFIG["actions"],
                "runtime_config": {
                    "collection_ids": [],
                    "batch_size": 100,
                    "parallel_batches": 20,
                    "updated_at": None
                }
            }

    def get_pipeline_statistics(self) -> Dict[str, Any]:
        """
        Get pipeline statistics from pre-computed aggregates.
        
        Returns instantly from statistics container.
        Falls back to rebuilding if stats don't exist.
        
        Returns:
            Pipeline statistics with counts by stage and status
        """
        try:
            # Try to read pre-computed stats (single point read - instant)
            stats_doc = self._stats_container.read_item(
                item=PIPELINE_STATS_ID,
                partition_key=PIPELINE_STATS_ID
            )
            
            # Always get fresh active batch count (changes frequently)
            active_batches = self._get_active_batch_count()
            
            logger.debug("Returning pre-computed pipeline statistics")
            return {
                "total_records": stats_doc.get("total_records", 0),
                "by_stage": stats_doc.get("by_stage", {}),
                "overall": stats_doc.get("overall", {"pending": 0, "completed": 0, "error": 0}),
                "active_batches": active_batches,
                "last_updated": stats_doc.get("last_updated")
            }
            
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Pipeline statistics not found, rebuilding...")
            return self.rebuild_pipeline_statistics()
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get pipeline statistics: %s", str(e))
            raise

    def rebuild_pipeline_statistics(self) -> Dict[str, Any]:
        """
        Rebuild pipeline statistics by querying all records.
        
        Fetches all records and aggregates counts by pipeline stage status.
        Stores the result in the statistics container for fast retrieval.
        
        Returns:
            Pipeline statistics with counts by stage and status
        """
        logger.info("Rebuilding pipeline statistics...")
        
        try:
            container = self._cosmos_service.get_container()
            
            # Get stages from config (exclude content_source_sync as it doesn't have status tracking)
            config = self.get_pipeline_config()
            stages_config = [
                s for s in config.get("stages", DEFAULT_PIPELINE_CONFIG["stages"])
                if s.get("status_field") and s["id"] != "content_source_sync"
            ]
            
            # Build mapping from stage ID to status field name
            stage_to_field = {s["id"]: s["status_field"] for s in stages_config}
            stats_stages = list(stage_to_field.keys())
            
            # Build dynamic query based on config
            field_selections = ", ".join([
                f'"{field}": c.{field}' for field in stage_to_field.values()
            ])
            
            query = f"""
                SELECT VALUE {{
                    {field_selections}
                }}
                FROM c
            """
            
            logger.debug("Pipeline stats query: %s", query)
            
            results = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            logger.info("Processing %d records for pipeline statistics...", len(results))
            
            # Initialize stage counters
            by_stage = {}
            for stage in stats_stages:
                by_stage[stage] = {"pending": 0, "completed": 0, "error": 0}
            
            # Initialize overall counters (record-level status)
            # A record is "error" if ANY stage has error, "completed" if ALL stages completed, else "pending"
            overall = {"pending": 0, "completed": 0, "error": 0}
            
            # Count records by status for each stage
            # For a record to be "pending" at stage N:
            # - Stage N's status must not be completed/error
            # - All previous stages (1 to N-1) must be "completed"
            # This ensures errors in earlier stages don't inflate pending counts in later stages
            for record in results:
                # Track overall record status
                has_error = False
                all_completed = True
                
                # Track whether all previous stages are completed (for pending calculation)
                all_previous_completed = True
                
                for stage in stats_stages:
                    # Use actual status_field from config
                    status_field = stage_to_field[stage]
                    status = record.get(status_field)
                    # Normalize status to lowercase for comparison
                    status_lower = (status or "").lower()
                    
                    if status_lower == "completed":
                        by_stage[stage]["completed"] += 1
                    elif status_lower in ("error", "failed"):
                        by_stage[stage]["error"] += 1
                        has_error = True
                        # Once we hit an error, subsequent stages can't be "pending"
                        all_previous_completed = False
                    else:
                        # Only count as pending if all previous stages completed
                        if all_previous_completed:
                            by_stage[stage]["pending"] += 1
                        all_completed = False
                        # This stage not completed, so subsequent stages can't be pending
                        all_previous_completed = False
                
                # Determine overall record status
                if has_error:
                    overall["error"] += 1
                elif all_completed:
                    overall["completed"] += 1
                else:
                    overall["pending"] += 1
            
            # Get active batch count
            active_batches = self._get_active_batch_count()
            
            # Build stats document
            stats_doc = {
                "id": PIPELINE_STATS_ID,
                "total_records": len(results),
                "by_stage": by_stage,
                "overall": overall,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            
            # Upsert to statistics container
            self._stats_container.upsert_item(stats_doc)
            
            # Log detailed breakdown for debugging
            error_breakdown = {stage: by_stage[stage]["error"] for stage in stats_stages}
            sum_of_stage_errors = sum(error_breakdown.values())
            logger.info(
                "Pipeline statistics rebuilt: total_records=%d, overall=%s", 
                len(results), overall
            )
            logger.info(
                "Error breakdown by stage: %s (sum=%d, unique records with errors=%d)", 
                error_breakdown, sum_of_stage_errors, overall["error"]
            )
            
            return {
                "total_records": len(results),
                "by_stage": by_stage,
                "overall": overall,
                "active_batches": active_batches,
                "last_updated": stats_doc["last_updated"]
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to rebuild pipeline statistics: %s", str(e))
            raise

    def get_statistics_status(self) -> Dict[str, Any]:
        """
        Get the status of pre-computed pipeline statistics.
        
        Returns:
            Dictionary with exists flag and lastUpdated timestamp
        """
        try:
            stats_doc = self._stats_container.read_item(
                item=PIPELINE_STATS_ID,
                partition_key=PIPELINE_STATS_ID
            )
            return {
                "exists": True,
                "last_updated": stats_doc.get("last_updated")
            }
        except exceptions.CosmosResourceNotFoundError:
            return {
                "exists": False,
                "last_updated": None
            }

    def clear_pipeline_statistics(self) -> bool:
        """
        Delete the pipeline statistics document.
        
        Returns:
            True if deleted, False if not found
        """
        try:
            self._stats_container.delete_item(
                item=PIPELINE_STATS_ID,
                partition_key=PIPELINE_STATS_ID
            )
            logger.info("Cleared pipeline statistics")
            return True
        except exceptions.CosmosResourceNotFoundError:
            logger.info("Pipeline statistics document not found")
            return False

    def _get_active_batch_count(self) -> int:
        """Get count of active batch jobs."""
        try:
            batch_container = self._database.get_container_client(BATCH_STATUS_CONTAINER)
            # Count batches excluding terminal/inactive states
            batch_query = """
                SELECT VALUE COUNT(1) FROM c 
                WHERE c.status NOT IN ('failed', 'cancelled', 'cancelling', 'retried', 'expired', 'completed')
            """
            batch_results = list(batch_container.query_items(
                query=batch_query,
                enable_cross_partition_query=True
            ))
            return batch_results[0] if batch_results else 0
        except Exception as e:
            logger.warning("Could not query batch status: %s", str(e))
            return 0

    # ==================== Active Pipeline Jobs Management ====================

    def _get_job_document_id(self, trigger_endpoint: str) -> str:
        """Generate document ID for pipeline job: pipeline_step_{trigger_endpoint}"""
        return f"{PIPELINE_STEP_PREFIX}{trigger_endpoint}"

    def update_job_status(
        self, 
        trigger_endpoint: str, 
        runtime_status: str, 
        custom_status: Any = None,
        input_data: Any = None,
        output_data: Any = None,
        last_updated_time: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update the status of an active job.
        
        Saves current_stats based on status:
        - Completed/Failed/Terminated/Canceled: saves output_data
        - Running/Pending: saves input_data
        
        Args:
            trigger_endpoint: The trigger endpoint (used to derive document ID)
            runtime_status: The new runtime status
            custom_status: Optional custom status data
            input_data: Optional input/progress data from orchestration (for in-progress)
            output_data: Optional output data from orchestration (for completed)
            last_updated_time: Optional last updated time from orchestration
            
        Returns:
            Updated job document or None if not found
        """
        try:
            # Read existing job by document ID (pipeline_step_{trigger_endpoint})
            doc_id = self._get_job_document_id(trigger_endpoint)
            job_doc = self._stats_container.read_item(
                item=doc_id,
                partition_key=doc_id
            )
            
            # Update fields
            job_doc["runtime_status"] = runtime_status
            job_doc["updated_at"] = datetime.now(timezone.utc).isoformat()
            if custom_status is not None:
                job_doc["custom_status"] = custom_status
            if last_updated_time is not None:
                job_doc["last_updated_time"] = last_updated_time
            
            # Save current_stats based on status: output for terminal runs, input for in-progress
            terminal_statuses = ['Completed', 'Failed', 'Terminated', 'Canceled']
            if runtime_status in terminal_statuses:
                # Save output as current_stats for completed jobs
                if output_data is not None:
                    job_doc["current_stats"] = output_data
                    logger.debug("Saved output to current_stats for completed job: %s", trigger_endpoint)
            else:
                # Save input as current_stats for in-progress jobs
                if input_data is not None:
                    job_doc["current_stats"] = input_data
                    logger.debug("Saved input to current_stats for in-progress job: %s", trigger_endpoint)
            
            # Save back
            self._stats_container.upsert_item(job_doc)
            logger.info("Updated job status: %s -> %s", trigger_endpoint, runtime_status)
            return job_doc
            
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Job not found for update: %s", trigger_endpoint)
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update job status: %s", str(e))
            raise

    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """
        Get all active pipeline jobs from Cosmos DB.
        
        Returns:
            List of active job documents (keyed by trigger_endpoint)
        """
        try:
            # Use pipeline_step_ prefix to find all job documents
            query = f"""
                SELECT c.id, c.trigger_endpoint, c.instance_id, c.name, c.status_url, 
                       c.terminate_url, c.suspend_url, c.resume_url,
                       c.runtime_status, c.started_at, c.custom_status,
                       c.current_stats, c.last_updated_time,
                       c.created_at, c.updated_at
                FROM c 
                WHERE STARTSWITH(c.id, '{PIPELINE_STEP_PREFIX}')
                ORDER BY c.created_at DESC
            """
            
            results = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            logger.debug("Retrieved %d active jobs", len(results))
            return results
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get active jobs: %s", str(e))
            raise

    def _get_stage_name(self, stage_id: str) -> str:
        """Get the display name for a stage from config."""
        try:
            config = self.get_pipeline_config()
            stages = config.get("stages", DEFAULT_PIPELINE_CONFIG["stages"])
            for stage in stages:
                if stage.get("id") == stage_id:
                    return stage.get("name", stage_id)
            return stage_id
        except Exception:
            return stage_id

    def get_stage_errors(
        self, 
        stage_id: str,
        page_number: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Get errors for a specific pipeline stage, grouped by error message.
        
        Args:
            stage_id: The stage ID (e.g., 'related_assets', 'asset_details', etc.)
            page_number: Page number (1-indexed)
            page_size: Number of errors per page
            
        Returns:
            Dict with stage_id, stage_name, paginated errors list, and totals
        """
        try:
            container = self._cosmos_service.get_container()
            
            # Get stage name from config
            stage_name = self._get_stage_name(stage_id)
            
            # Map stage_id to the status and error fields
            status_field = f"{stage_id}_status"
            error_field = f"{status_field}_error"
            
            # Calculate offset for pagination
            offset = (page_number - 1) * page_size
            
            # Query 1: Get total error count
            count_query = f"""
                SELECT VALUE COUNT(1)
                FROM c
                WHERE c.{status_field} IN ('error', 'failed')
                AND IS_DEFINED(c.{error_field})
                AND c.{error_field} != null
            """
            count_results = list(container.query_items(
                query=count_query,
                enable_cross_partition_query=True
            ))
            total_error_count = count_results[0] if count_results else 0
            
            # Query 2: Get all error texts for grouping in Python
            # Handle error field being either a string or an object with 'message' property
            data_query = f"""
                SELECT 
                    IS_STRING(c.{error_field}) ? c.{error_field} : 
                    (IS_OBJECT(c.{error_field}) ? c.{error_field}.message : "Unknown error") 
                    as error_text
                FROM c
                WHERE c.{status_field} IN ('error', 'failed')
                AND IS_DEFINED(c.{error_field})
                AND c.{error_field} != null
            """
            
            results = list(container.query_items(
                query=data_query,
                enable_cross_partition_query=True
            ))
            
            # Group by error text and count in Python
            error_counts: Dict[str, int] = {}
            for record in results:
                error_text = record.get("error_text")
                
                # Handle case where error_text might still be an object (fallback)
                if isinstance(error_text, dict):
                    error_text = error_text.get("message", str(error_text))
                
                if not error_text:
                    error_text = "Unknown error"
                
                error_counts[error_text] = error_counts.get(error_text, 0) + 1
            
            # Convert to list sorted by count (descending)
            error_list = [
                {"error_text": text, "count": count}
                for text, count in error_counts.items()
            ]
            error_list.sort(key=lambda x: x["count"], reverse=True)
            
            # Calculate totals
            total_unique_errors = len(error_list)
            
            # Apply pagination
            total_pages = (total_unique_errors + page_size - 1) // page_size if total_unique_errors > 0 else 0
            paginated_errors = error_list[offset:offset + page_size]
            
            logger.info("Found %d unique error types for stage %s (page %d/%d)", 
                       len(paginated_errors), stage_id, page_number, total_pages)
            return {
                "stage_id": stage_id,
                "stage_name": stage_name,
                "errors": paginated_errors,
                "total_unique_errors": total_unique_errors,
                "total_error_count": total_error_count,
                "page_number": page_number,
                "page_size": page_size,
                "total_pages": total_pages
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get stage errors: %s", str(e))
            raise
        except Exception as e:
            logger.exception("Unexpected error getting stage errors: %s", str(e))
            raise

    def get_records_by_error(
        self, 
        stage_id: str, 
        error_text: str,
        page_number: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Get records that have a specific error message for a pipeline stage.
        
        Args:
            stage_id: The stage ID (e.g., 'related_assets', 'asset_details', etc.)
            error_text: The error message to filter by
            page_number: Page number (1-indexed)
            page_size: Number of records per page
            
        Returns:
            Dict with stage_id, stage_name, records list, total count, and pagination info
        """
        try:
            container = self._cosmos_service.get_container()
            
            # Get stage name from config
            stage_name = self._get_stage_name(stage_id)
            
            # Map stage_id to the status and error fields
            status_field = f"{stage_id}_status"
            error_field = f"{status_field}_error"
            
            # Build WHERE clause for error matching (exact match)
            # Handle error field being either a string or an object with 'message' property
            error_condition = f"""
                (
                    (IS_STRING(c.{error_field}) AND c.{error_field} = @error_text)
                    OR (IS_OBJECT(c.{error_field}) AND c.{error_field}.message = @error_text)
                )
            """
            
            # Calculate pagination
            offset = (page_number - 1) * page_size
            
            # Query 1: Get total count (separate query)
            count_query = f"""
                SELECT VALUE COUNT(1)
                FROM c
                WHERE c.{status_field} IN ('error', 'failed')
                AND IS_DEFINED(c.{error_field})
                AND c.{error_field} != null
                AND {error_condition}
            """
            count_results = list(container.query_items(
                query=count_query,
                parameters=[{"name": "@error_text", "value": error_text}],
                enable_cross_partition_query=True
            ))
            total_count = count_results[0] if count_results else 0
            
            # Calculate total pages
            total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
            
            # Query 2: Get paginated records with OFFSET/LIMIT
            data_query = f"""
                SELECT c.id, c.title, c.source_record_id, c.repository, c.collection_name,
                       c.{status_field} as status,
                       IS_STRING(c.{error_field}) ? c.{error_field} : 
                           (IS_OBJECT(c.{error_field}) ? c.{error_field}.message : null) as error_message,
                       IS_STRING(c.{error_field}) ? c.{error_field} :
                           (IS_OBJECT(c.{error_field}) ? c.{error_field}.detail : null) as error_detail,
                       c.created_at, c.updated_at
                FROM c
                WHERE c.{status_field} IN ('error', 'failed')
                AND IS_DEFINED(c.{error_field})
                AND c.{error_field} != null
                AND {error_condition}
                ORDER BY c.updated_at DESC
                OFFSET @offset LIMIT @limit
            """
            
            paginated_results = list(container.query_items(
                query=data_query,
                parameters=[
                    {"name": "@error_text", "value": error_text},
                    {"name": "@offset", "value": offset},
                    {"name": "@limit", "value": page_size}
                ],
                enable_cross_partition_query=True
            ))
            
            logger.info("Found %d records for error '%s' in stage %s (page %d/%d)", 
                       len(paginated_results), error_text[:50], stage_id, page_number, total_pages)
            
            return {
                "stage_id": stage_id,
                "stage_name": stage_name,
                "records": paginated_results,
                "total_count": total_count,
                "page_number": page_number,
                "page_size": page_size,
                "total_pages": total_pages
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get records by error: %s", str(e))
            raise
        except Exception as e:
            logger.exception("Unexpected error getting records by error: %s", str(e))
            raise

    def reset_all_jobs(self) -> Dict[str, Any]:
        """
        Reset all pipeline jobs by deleting them from Cosmos DB.
        Deletes all documents with ID prefix 'pipeline_step_'.
        
        Returns:
            Dict with deleted_count and error_count
        """
        try:
            # Query all documents with pipeline_step_ prefix
            query = f"""
                SELECT c.id
                FROM c 
                WHERE STARTSWITH(c.id, '{PIPELINE_STEP_PREFIX}')
            """
            
            results = list(self._stats_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            deleted_count = 0
            error_count = 0
            
            for job in results:
                job_id = job["id"]
                try:
                    self._stats_container.delete_item(
                        item=job_id,
                        partition_key=job_id
                    )
                    deleted_count += 1
                except Exception as e:
                    logger.warning("Failed to delete job %s: %s", job_id, str(e))
                    error_count += 1
            
            logger.info("Reset complete: %d deleted, %d errors", deleted_count, error_count)
            
            return {
                "deleted_count": deleted_count,
                "error_count": error_count
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to reset all jobs: %s", str(e))
            raise

    # ==================== Pipeline Configuration ====================

    def get_pipeline_config(self) -> Dict[str, Any]:
        """
        Get the saved pipeline configuration.
        
        Returns:
            Full pipeline configuration including stages, actions, and runtime settings
        """
        try:
            config_doc = self._stats_container.read_item(
                item=PIPELINE_CONFIG_ID,
                partition_key=PIPELINE_CONFIG_ID
            )
            
            # Merge with defaults to ensure all fields exist
            return {
                "collection_ids": config_doc.get("collection_ids", DEFAULT_PIPELINE_CONFIG["collection_ids"]),
                "batch_size": config_doc.get("batch_size", DEFAULT_PIPELINE_CONFIG["batch_size"]),
                "parallel_batches": config_doc.get("parallel_batches", DEFAULT_PIPELINE_CONFIG["parallel_batches"]),
                "stages": config_doc.get("stages", DEFAULT_PIPELINE_CONFIG["stages"]),
                "actions": config_doc.get("actions", DEFAULT_PIPELINE_CONFIG["actions"]),
                "updated_at": config_doc.get("updated_at")
            }
            
        except exceptions.CosmosResourceNotFoundError:
            # Return defaults if no config saved
            return {
                **DEFAULT_PIPELINE_CONFIG,
                "updated_at": None
            }
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get pipeline config: %s", str(e))
            raise

    def save_pipeline_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Save pipeline configuration.
        
        Args:
            config: Configuration with runtime settings (collection_ids, batch_size, parallel_batches)
                   Optionally can include stages and actions to customize pipeline
            
        Returns:
            The saved configuration
        """
        try:
            # Start with defaults and merge in provided config
            config_doc = {
                "id": PIPELINE_CONFIG_ID,
                "type": "pipeline_config",
                "collection_ids": config.get("collection_ids", DEFAULT_PIPELINE_CONFIG["collection_ids"]),
                "batch_size": config.get("batch_size", DEFAULT_PIPELINE_CONFIG["batch_size"]),
                "parallel_batches": config.get("parallel_batches", DEFAULT_PIPELINE_CONFIG["parallel_batches"]),
                "stages": config.get("stages", DEFAULT_PIPELINE_CONFIG["stages"]),
                "actions": config.get("actions", DEFAULT_PIPELINE_CONFIG["actions"]),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            self._stats_container.upsert_item(config_doc)
            logger.info("Saved pipeline config: batch_size=%d, parallel_batches=%d, stages=%d, actions=%d",
                       config_doc["batch_size"], config_doc["parallel_batches"], 
                       len(config_doc["stages"]), len(config_doc["actions"]))
            
            return {
                "collection_ids": config_doc["collection_ids"],
                "batch_size": config_doc["batch_size"],
                "parallel_batches": config_doc["parallel_batches"],
                "stages": config_doc["stages"],
                "actions": config_doc["actions"],
                "updated_at": config_doc["updated_at"]
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to save pipeline config: %s", str(e))
            raise

    def reset_pipeline_config(self) -> Dict[str, Any]:
        """
        Reset pipeline configuration to defaults.
        
        Deletes the saved configuration document from CosmosDB.
        After deletion, get_pipeline_config will return DEFAULT_PIPELINE_CONFIG.
        
        Returns:
            Success message and the default configuration
        """
        try:
            # Try to delete the config document
            try:
                self._stats_container.delete_item(
                    item=PIPELINE_CONFIG_ID,
                    partition_key=PIPELINE_CONFIG_ID
                )
                logger.info("Deleted pipeline config document")
            except exceptions.CosmosResourceNotFoundError:
                logger.info("Pipeline config document not found, already using defaults")
            
            # Return the default configuration
            return {
                "message": "Pipeline configuration reset to defaults",
                "config": {
                    "collection_ids": DEFAULT_PIPELINE_CONFIG["collection_ids"],
                    "batch_size": DEFAULT_PIPELINE_CONFIG["batch_size"],
                    "parallel_batches": DEFAULT_PIPELINE_CONFIG["parallel_batches"],
                    "stages": DEFAULT_PIPELINE_CONFIG["stages"],
                    "actions": DEFAULT_PIPELINE_CONFIG["actions"],
                    "updated_at": None
                }
            }
            
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to reset pipeline config: %s", str(e))
            raise

    # ==================== Search Index Restore Job Management ====================

    # Fixed job ID for search index restore (only one job at a time)
    SEARCH_INDEX_RESTORE_JOB_ID = "search_index_restore"

    def get_search_index_restore_status(self) -> Dict[str, Any]:
        """
        Get the status of the search index restore job.
        
        Uses point read with fixed job ID for efficiency.
        
        Returns:
            Job status including progress, management URLs, and any errors
        """
        try:
            # Point read with fixed job ID
            job = self._stats_container.read_item(
                item=self.SEARCH_INDEX_RESTORE_JOB_ID,
                partition_key=self.SEARCH_INDEX_RESTORE_JOB_ID
            )
            
            return {
                "has_job": True,
                "job_id": job.get("id"),
                "status": job.get("status"),
                "total_documents": job.get("total_documents", 0),
                "processed_documents": job.get("processed_documents", 0),
                "success_documents": job.get("success_documents", 0),
                "failed_documents": job.get("failed_documents", 0),
                "started_at": job.get("started_at"),
                "started_by": job.get("started_by"),
                "updated_at": job.get("updated_at"),
                "completed_at": job.get("completed_at"),
                "error_message": job.get("error_message"),
                # Orchestration management URLs
                "instance_id": job.get("instance_id"),
                "status_url": job.get("status_url"),
                "terminate_url": job.get("terminate_url"),
                "suspend_url": job.get("suspend_url"),
                "resume_url": job.get("resume_url")
            }
        except exceptions.CosmosResourceNotFoundError:
            return {
                "has_job": False,
                "message": "No restore job found"
            }
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get search index restore status: %s", str(e))
            raise

    def update_search_index_restore_job(
        self,
        status: Optional[str] = None,
        processed_documents: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update the search index restore job status from UI polling.
        
        Uses Durable Functions status names: Pending, Running, Completed, Failed, Terminated, Suspended
        
        Args:
            status: New status (Running, Completed, Failed, Terminated, Suspended)
            processed_documents: Number of documents processed
            error_message: Error message if failed
            
        Returns:
            Updated job record or None if not found
        """
        try:
            # Read existing job
            job = self._stats_container.read_item(
                item=self.SEARCH_INDEX_RESTORE_JOB_ID,
                partition_key=self.SEARCH_INDEX_RESTORE_JOB_ID
            )
            
            # Update fields
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            if status is not None:
                job["status"] = status
                if status == "Completed":
                    job["completed_at"] = job["updated_at"]
            
            if processed_documents is not None:
                job["processed_documents"] = processed_documents
            
            if error_message is not None:
                job["error_message"] = error_message
            
            # Upsert the updated record
            self._stats_container.upsert_item(job)
            
            logger.info(
                "Updated search index restore job: status=%s, processed=%s",
                job.get("status"),
                job.get("processed_documents")
            )
            
            return job
            
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Search index restore job not found for update")
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update search index restore job: %s", str(e))
            raise

    # ==================== Bulk Ingestion Job Management ====================

    # Fixed job ID for bulk ingestion (only one job at a time)
    BULK_INGESTION_JOB_ID = "bulk_ingestion"

    def get_bulk_ingestion_status(self) -> Dict[str, Any]:
        """
        Get the status of the bulk ingestion job.
        
        Uses point read with fixed job ID for efficiency.
        
        Returns:
            Job status including progress, management URLs, and any errors
        """
        try:
            # Point read with fixed job ID
            job = self._stats_container.read_item(
                item=self.BULK_INGESTION_JOB_ID,
                partition_key=self.BULK_INGESTION_JOB_ID
            )
            
            return {
                "has_job": True,
                "job_id": job.get("id"),
                "operation_type": job.get("operation_type"),  # "bulk", "retry_failed", or "reindex"
                "status": job.get("status"),
                "total_documents": job.get("total_documents", 0),
                "processed_documents": job.get("processed_documents", 0),
                "success_documents": job.get("success_documents", 0),
                "failed_documents": job.get("failed_documents", 0),
                "started_at": job.get("started_at"),
                "started_by": job.get("started_by"),
                "updated_at": job.get("updated_at"),
                "completed_at": job.get("completed_at"),
                "error_message": job.get("error_message"),
                # Orchestration management URLs
                "instance_id": job.get("instance_id"),
                "status_url": job.get("status_url"),
                "terminate_url": job.get("terminate_url"),
                "suspend_url": job.get("suspend_url"),
                "resume_url": job.get("resume_url"),
                # Job details
                "query_filters": job.get("query_filters"),
                "document_count": job.get("document_count")
            }
        except exceptions.CosmosResourceNotFoundError:
            return {
                "has_job": False,
                "message": "No bulk ingestion job found"
            }
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get bulk ingestion status: %s", str(e))
            raise

    def update_bulk_ingestion_job(
        self,
        status: Optional[str] = None,
        processed_documents: Optional[int] = None,
        total_documents: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update the bulk ingestion job status from UI polling.
        
        Uses Durable Functions status names: Pending, Running, Completed, Failed, Terminated, Suspended
        
        Args:
            status: New status (Running, Completed, Failed, Terminated, Suspended)
            processed_documents: Number of documents processed
            total_documents: Total documents to process
            error_message: Error message if failed
            
        Returns:
            Updated job record or None if not found
        """
        try:
            # Read existing job
            job = self._stats_container.read_item(
                item=self.BULK_INGESTION_JOB_ID,
                partition_key=self.BULK_INGESTION_JOB_ID
            )
            
            # Update fields
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            if status is not None:
                job["status"] = status
                if status == "Completed":
                    job["completed_at"] = job["updated_at"]
            
            if processed_documents is not None:
                job["processed_documents"] = processed_documents
            
            if total_documents is not None:
                job["total_documents"] = total_documents
            
            if error_message is not None:
                job["error_message"] = error_message
            
            self._stats_container.upsert_item(job)
            logger.info("Updated bulk ingestion job: status=%s, processed=%s", 
                       job.get("status"), job.get("processed_documents"))
            
            return job
            
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Bulk ingestion job not found for update")
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update bulk ingestion job: %s", str(e))
            raise

    def cancel_bulk_ingestion_job(self) -> Dict[str, Any]:
        """
        Mark the bulk ingestion job as cancelled.
        
        Note: This only updates the job record. The actual orchestration
        termination should be done via the terminate_url.
        
        Returns:
            Updated job record or error dict
        """
        try:
            job = self._stats_container.read_item(
                item=self.BULK_INGESTION_JOB_ID,
                partition_key=self.BULK_INGESTION_JOB_ID
            )
            
            if job.get("status") not in ("Pending", "Running", "Suspended"):
                return {
                    "success": False,
                    "message": f"Cannot cancel job in '{job.get('status')}' status"
                }
            
            job["status"] = "Terminated"  # Durable Functions status
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            job["completed_at"] = job["updated_at"]
            
            self._stats_container.upsert_item(job)
            logger.info("Marked bulk ingestion job as cancelled")
            
            return {
                "success": True,
                "terminate_url": job.get("terminate_url"),
                "message": "Job marked as cancelled"
            }
            
        except exceptions.CosmosResourceNotFoundError:
            return {
                "success": False,
                "message": "No bulk ingestion job found"
            }
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to cancel bulk ingestion job: %s", str(e))
            raise

    # =========================================================================
    # Failed Documents Count
    # =========================================================================

    def get_failed_documents_count(self) -> int:
        """
        Get the count of documents with archivist_status = 'Failed'.
        
        Returns:
            Number of failed documents
        """
        try:
            cosmos_service = get_cosmos_service()
            query = "SELECT VALUE COUNT(1) FROM c WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')"
            container = cosmos_service.get_container()
            results = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            return results[0] if results else 0
        except Exception as e:
            logger.exception("Failed to get failed documents count: %s", str(e))
            return 0

    def get_failed_documents(
        self, 
        page: int = 1, 
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Get paginated list of documents with archivist_status = 'failed'.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            Dictionary with documents, pagination info, and error summaries
        """
        try:
            cosmos_service = get_cosmos_service()
            container = cosmos_service.get_container()
            
            # Get total count
            count_query = "SELECT VALUE COUNT(1) FROM c WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')"
            count_results = list(container.query_items(
                query=count_query,
                enable_cross_partition_query=True
            ))
            total_count = count_results[0] if count_results else 0
            
            # Get paginated documents
            offset = (page - 1) * page_size
            query = """
                SELECT 
                    c.id, 
                    c.record_id,
                    c.title,
                    c.metadata.Label as label,
                    c.metadata.Repository.label as repository,
                    c.metadata.Collection.label as collection_name,
                    c.archivist_status,
                    c.archivist_error_message,
                    c.published_at,
                    c._ts
                FROM c 
                WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')
                ORDER BY c._ts DESC
                OFFSET @offset LIMIT @limit
            """
            
            documents = list(container.query_items(
                query=query,
                parameters=[
                    {"name": "@offset", "value": offset},
                    {"name": "@limit", "value": page_size}
                ],
                enable_cross_partition_query=True
            ))
            
            total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
            
            return {
                "documents": documents,
                "total_count": total_count,
                "page_number": page,
                "page_size": page_size,
                "total_pages": total_pages
            }
        except Exception as e:
            logger.exception("Failed to get failed documents: %s", str(e))
            return {
                "documents": [],
                "total_count": 0,
                "page_number": page,
                "page_size": page_size,
                "total_pages": 0
            }

    def get_failed_documents_errors_summary(
        self, 
        page: int = 1, 
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Get unique error messages with counts from failed documents.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            Dictionary with unique errors, their counts, and pagination info
        """
        try:
            cosmos_service = get_cosmos_service()
            container = cosmos_service.get_container()
            
            # Fetch all failed documents with their error messages
            # Note: GROUP BY in Cosmos DB can have limitations, so we aggregate client-side
            query = """
                SELECT 
                    c.archivist_error_message
                FROM c 
                WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed')
            """
            
            docs = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            # Aggregate errors client-side
            error_counts: Dict[str, int] = {}
            for doc in docs:
                error_msg = doc.get('archivist_error_message')
                if error_msg and str(error_msg).strip():
                    key = str(error_msg)
                else:
                    key = "(No error message recorded)"
                error_counts[key] = error_counts.get(key, 0) + 1
            
            # Convert to list of dicts
            all_errors = [
                {"error_text": k, "count": v}
                for k, v in error_counts.items()
            ]
            
            # Sort by count descending
            all_errors.sort(key=lambda x: x.get('count', 0), reverse=True)
            
            total_unique_errors = len(all_errors)
            total_error_count = sum(e.get('count', 0) for e in all_errors)
            
            # Paginate
            offset = (page - 1) * page_size
            paginated_errors = all_errors[offset:offset + page_size]
            total_pages = (total_unique_errors + page_size - 1) // page_size if total_unique_errors > 0 else 0
            
            return {
                "errors": paginated_errors,
                "total_unique_errors": total_unique_errors,
                "total_error_count": total_error_count,
                "page_number": page,
                "page_size": page_size,
                "total_pages": total_pages
            }
        except Exception as e:
            logger.exception("Failed to get failed documents error summary: %s", str(e))
            return {
                "errors": [],
                "total_unique_errors": 0,
                "total_error_count": 0,
                "page_number": page,
                "page_size": page_size,
                "total_pages": 0
            }

    def get_failed_documents_by_error(
        self,
        error_text: str,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Get documents with a specific error message.
        
        Args:
            error_text: The error message to filter by
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            Dictionary with documents matching the error and pagination info
        """
        try:
            cosmos_service = get_cosmos_service()
            container = cosmos_service.get_container()
            
            # Special case: documents without error message
            is_no_error_query = error_text == "(No error message recorded)"
            
            if is_no_error_query:
                # Get count for documents without error message
                count_query = """
                    SELECT VALUE COUNT(1) 
                    FROM c 
                    WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed') 
                        AND (NOT IS_DEFINED(c.archivist_error_message) 
                             OR c.archivist_error_message = null 
                             OR c.archivist_error_message = '')
                """
                count_results = list(container.query_items(
                    query=count_query,
                    enable_cross_partition_query=True
                ))
                
                # Get paginated documents
                offset = (page - 1) * page_size
                query = """
                    SELECT 
                        c.id,
                        c.record_id,
                        c.title,
                        c.metadata.Label as label,
                        c.metadata.Repository.label as repository,
                        c.metadata.Collection.label as collection_name,
                        c.archivist_status,
                        c.archivist_error_message,
                        c._ts
                    FROM c 
                    WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed') 
                        AND (NOT IS_DEFINED(c.archivist_error_message) 
                             OR c.archivist_error_message = null 
                             OR c.archivist_error_message = '')
                    ORDER BY c._ts DESC
                    OFFSET @offset LIMIT @limit
                """
                documents = list(container.query_items(
                    query=query,
                    parameters=[
                        {"name": "@offset", "value": offset},
                        {"name": "@limit", "value": page_size}
                    ],
                    enable_cross_partition_query=True
                ))
            else:
                # Get count for this specific error
                count_query = """
                    SELECT VALUE COUNT(1) 
                    FROM c 
                    WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed') 
                        AND c.archivist_error_message = @error_text
                """
                count_results = list(container.query_items(
                    query=count_query,
                    parameters=[{"name": "@error_text", "value": error_text}],
                    enable_cross_partition_query=True
                ))
                
                # Get paginated documents
                offset = (page - 1) * page_size
                query = """
                    SELECT 
                        c.id,
                        c.record_id,
                        c.title,
                        c.metadata.Label as label,
                        c.metadata.Repository.label as repository,
                        c.metadata.Collection.label as collection_name,
                        c.archivist_status,
                        c.archivist_error_message,
                        c._ts
                    FROM c 
                    WHERE (IS_DEFINED(c.archivist_status) AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'failed') 
                        AND c.archivist_error_message = @error_text
                    ORDER BY c._ts DESC
                    OFFSET @offset LIMIT @limit
                """
                documents = list(container.query_items(
                    query=query,
                    parameters=[
                        {"name": "@error_text", "value": error_text},
                        {"name": "@offset", "value": offset},
                        {"name": "@limit", "value": page_size}
                    ],
                    enable_cross_partition_query=True
                ))
            
            total_count = count_results[0] if count_results else 0
            total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
            
            return {
                "documents": documents,
                "error_text": error_text,
                "total_count": total_count,
                "page_number": page,
                "page_size": page_size,
                "total_pages": total_pages
            }
        except Exception as e:
            logger.exception("Failed to get failed documents by error: %s", str(e))
            return {
                "documents": [],
                "error_text": error_text,
                "total_count": 0,
                "page_number": page,
                "page_size": page_size,
                "total_pages": 0
            }

    # =========================================================================
    # Unpublish Job Management
    # =========================================================================
    
    UNPUBLISH_JOB_ID = "unpublish_documents"
    
    def get_unpublish_status(self) -> Dict[str, Any]:
        """
        Get the status of the unpublish job.
        
        Returns:
            Job status including progress, management URLs, and any errors
        """
        try:
            job = self._stats_container.read_item(
                item=self.UNPUBLISH_JOB_ID,
                partition_key=self.UNPUBLISH_JOB_ID
            )
            
            return {
                "has_job": True,
                "job_id": job.get("id"),
                "status": job.get("status"),
                "total_documents": job.get("total_documents", 0),
                "processed_documents": job.get("processed_documents", 0),
                "success_documents": job.get("success_documents", 0),
                "failed_documents": job.get("failed_documents", 0),
                "started_at": job.get("started_at"),
                "started_by": job.get("started_by"),
                "published_by": job.get("published_by"),
                "published_date": job.get("published_date"),
                "updated_at": job.get("updated_at"),
                "completed_at": job.get("completed_at"),
                "error_message": job.get("error_message"),
                "instance_id": job.get("instance_id"),
                "status_url": job.get("status_url"),
                "terminate_url": job.get("terminate_url"),
                "suspend_url": job.get("suspend_url"),
                "resume_url": job.get("resume_url")
            }
            
        except exceptions.CosmosResourceNotFoundError:
            return {"has_job": False}
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to get unpublish status: %s", str(e))
            raise

    def update_unpublish_status(
        self,
        status: Optional[str] = None,
        processed_documents: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update the unpublish job status.
        
        Args:
            status: New status
            processed_documents: Number of documents processed
            error_message: Error message if failed
            
        Returns:
            Updated job record or None if not found
        """
        try:
            job = self._stats_container.read_item(
                item=self.UNPUBLISH_JOB_ID,
                partition_key=self.UNPUBLISH_JOB_ID
            )
            
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            if status is not None:
                job["status"] = status
                if status == "Completed":
                    job["completed_at"] = job["updated_at"]
            
            if processed_documents is not None:
                job["processed_documents"] = processed_documents
            
            if error_message is not None:
                job["error_message"] = error_message
            
            self._stats_container.upsert_item(job)
            logger.info("Updated unpublish job: status=%s, processed=%s", 
                       job.get("status"), job.get("processed_documents"))
            
            return job
            
        except exceptions.CosmosResourceNotFoundError:
            logger.warning("Unpublish job not found for update")
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to update unpublish job: %s", str(e))
            raise

    def cancel_unpublish_job(self) -> Optional[Dict[str, Any]]:
        """
        Mark the unpublish job as cancelled.
        
        Returns:
            Updated job record or None if not found
        """
        try:
            job = self._stats_container.read_item(
                item=self.UNPUBLISH_JOB_ID,
                partition_key=self.UNPUBLISH_JOB_ID
            )
            
            if job.get("status") not in ("Pending", "Running", "Suspended"):
                return None
            
            job["status"] = "Terminated"
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            job["completed_at"] = job["updated_at"]
            
            self._stats_container.upsert_item(job)
            logger.info("Marked unpublish job as cancelled")
            
            return job
            
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.exception("Failed to cancel unpublish job: %s", str(e))
            raise


# Singleton instance
_pipeline_service: Optional[PipelineService] = None


def get_pipeline_service() -> PipelineService:
    """
    Get the singleton PipelineService instance.
    
    Returns:
        PipelineService instance
    """
    global _pipeline_service
    if _pipeline_service is None:
        _pipeline_service = PipelineService()
    return _pipeline_service
