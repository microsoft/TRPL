# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Job Manager - Centralized job management for Durable Functions.

This module provides a unified interface for creating, reading, and updating
job records in CosmosDB for all job types (bulk ingestion, search restore, unpublish).
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, TypedDict
from dataclasses import dataclass, asdict
from enum import Enum

from helper.cosmos_client import CosmosDBClient, execute_with_retry
from helper.config import CosmosDBConfig


class ManagementUrls(TypedDict, total=False):
    """Type definition for Durable Functions management URLs."""
    instance_id: Optional[str]
    status_url: Optional[str]
    terminate_url: Optional[str]
    suspend_url: Optional[str]
    resume_url: Optional[str]


def extract_management_urls(client, instance_id: str) -> ManagementUrls:
    """
    Extract management URLs from Durable Functions client payload.
    
    Args:
        client: DurableOrchestrationClient instance
        instance_id: The orchestration instance ID
        
    Returns:
        ManagementUrls dict with all orchestration management URLs
    """
    mgmt_payload = client.create_http_management_payload(instance_id)
    return {
        "instance_id": instance_id,
        "status_url": mgmt_payload.get("statusQueryGetUri"),
        "terminate_url": mgmt_payload.get("terminatePostUri"),
        "suspend_url": mgmt_payload.get("suspendPostUri"),
        "resume_url": mgmt_payload.get("resumePostUri"),
    }


class JobType(str, Enum):
    """Supported job types."""
    BULK_INGESTION = "bulk_ingestion"
    UNPUBLISH_DOCUMENTS = "unpublish_documents"


class JobStatus(str, Enum):
    """Durable Functions job statuses."""
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    TERMINATED = "Terminated"
    SUSPENDED = "Suspended"


# Job ID constants
# Note: Bulk ingestion, retry failed, and restore index all share the same job ID
# because they are the same operation with different queries
JOB_IDS = {
    JobType.BULK_INGESTION: "bulk_ingestion",
    JobType.UNPUBLISH_DOCUMENTS: "unpublish_documents",
}


@dataclass
class JobRecord:
    """Base job record structure."""
    id: str
    type: str
    status: str = JobStatus.RUNNING.value
    total_documents: int = 0
    processed_documents: int = 0
    success_documents: int = 0
    failed_documents: int = 0
    started_at: Optional[str] = None
    started_by: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    instance_id: Optional[str] = None
    status_url: Optional[str] = None
    terminate_url: Optional[str] = None
    suspend_url: Optional[str] = None
    resume_url: Optional[str] = None
    # Optional fields for specific job types
    operation_type: Optional[str] = None  # For bulk ingestion: "bulk", "retry_failed", or "reindex"
    published_by: Optional[str] = None  # For unpublish jobs
    published_date: Optional[str] = None  # For unpublish jobs
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, excluding None values for optional fields."""
        result = asdict(self)
        # Remove None values for optional fields to keep documents clean
        return {k: v for k, v in result.items() if v is not None or k in (
            'id', 'type', 'status', 'total_documents', 'processed_documents',
            'success_documents', 'failed_documents', 'error_message'
        )}


class JobManager:
    """
    Centralized job manager for all job types.
    
    Provides CRUD operations for job records stored in CosmosDB statistics container.
    """
    
    def __init__(self):
        self._container = None
    
    @property
    def container(self):
        """Lazy initialization of stats container."""
        if self._container is None:
            self._container = self._get_stats_container()
        return self._container
    
    def _get_stats_container(self):
        """Get the statistics container client."""
        try:
            config = CosmosDBConfig.from_env()
            cosmos_client = CosmosDBClient(config)
            container_name = (
                os.environ.get("COSMOS_DB_STATS_CONTAINER_NAME")
                or os.environ.get("COSMOS_DB_STATISTICS_CONTAINER_NAME")
                or "statistics"
            )
            return cosmos_client._database.get_container_client(container_name)
        except Exception as e:
            logging.warning("Failed to get stats container: %s", str(e))
            return None
    
    def _now_iso(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
    
    def get_job_id(self, jtype: JobType) -> str:
        """Get the fixed job ID for a job type."""
        return JOB_IDS.get(jtype, str(jtype))
    
    def create_job(
        self,
        jtype: JobType,
        total_documents: int,
        started_by: str,
        instance_id: Optional[str] = None,
        status_url: Optional[str] = None,
        terminate_url: Optional[str] = None,
        suspend_url: Optional[str] = None,
        resume_url: Optional[str] = None,
        **extra_fields
    ) -> str:
        """
        Create a new job record.
        
        Args:
            job_type: Type of job
            total_documents: Total documents to process
            started_by: User who started the job
            instance_id: Durable Functions instance ID
            status_url: Orchestration status URL
            terminate_url: Orchestration terminate URL
            suspend_url: Orchestration suspend URL
            resume_url: Orchestration resume URL
            **extra_fields: Additional fields (e.g., job_type, published_by, published_date)
        
        Returns:
            Job ID
        """
        job_id = self.get_job_id(jtype)
        
        try:
            if not self.container:
                return job_id
            
            timestamp = self._now_iso()
            
            job_record = JobRecord(
                id=job_id,
                type=jtype.value,
                status=JobStatus.RUNNING.value,
                total_documents=total_documents,
                started_at=timestamp,
                started_by=started_by,
                updated_at=timestamp,
                instance_id=instance_id,
                status_url=status_url,
                terminate_url=terminate_url,
                suspend_url=suspend_url,
                resume_url=resume_url,
                **extra_fields
            )
            
            execute_with_retry(
                self.container.upsert_item,
                operation_name=f"create_job({job_id})",
                body=job_record.to_dict()
            )
            logging.info(
                "Created %s job: %s (total: %d, instance: %s)",
                jtype.value, job_id, total_documents, instance_id
            )
            
        except Exception as e:
            logging.exception("Failed to create %s job record: %s", jtype.value, str(e))
        
        return job_id
    
    def get_job(self, jtype: JobType) -> Optional[Dict[str, Any]]:
        """
        Get job record by type.
        
        Args:
            jtype: Type of job
        
        Returns:
            Job record dict or None
        """
        try:
            if not self.container:
                return None
            
            job_id = self.get_job_id(jtype)
            return execute_with_retry(
                self.container.read_item,
                operation_name=f"get_job({job_id})",
                item=job_id,
                partition_key=job_id
            )
        except Exception:
            return None
    
    def update_job(
        self,
        jtype: JobType,
        status: Optional[str] = None,
        processed_documents: Optional[int] = None,
        success_documents: Optional[int] = None,
        failed_documents: Optional[int] = None,
        total_documents: Optional[int] = None,
        error_message: Optional[str] = None,
        instance_id: Optional[str] = None,
        status_url: Optional[str] = None,
        terminate_url: Optional[str] = None,
        suspend_url: Optional[str] = None,
        resume_url: Optional[str] = None
    ) -> None:
        """
        Update an existing job record.
        
        Args:
            jtype: Type of job
            status: New status
            processed_documents: Number processed
            success_documents: Number successful
            failed_documents: Number failed
            total_documents: Total to process (if updated)
            error_message: Error message if failed
            instance_id: Durable Functions instance ID
            status_url: Orchestration status URL
            terminate_url: Orchestration terminate URL
            suspend_url: Orchestration suspend URL
            resume_url: Orchestration resume URL
        """
        try:
            if not self.container:
                logging.error("update_job: Container is None - cannot update %s job", jtype.value)
                return
            
            job_id = self.get_job_id(jtype)
            logging.info("update_job: Reading job record id=%s from CosmosDB", job_id)
            
            try:
                job_record = execute_with_retry(
                    self.container.read_item,
                    operation_name=f"read_job({job_id})",
                    item=job_id,
                    partition_key=job_id
                )
                logging.info("update_job: Found existing job record with processed=%s", job_record.get("processed_documents"))
            except Exception as e:
                logging.error("update_job: Could not find %s job record (id=%s): %s", jtype.value, job_id, str(e))
                return
            
            # Update timestamp
            job_record["updated_at"] = self._now_iso()
            
            # Update status
            if status is not None:
                job_record["status"] = status
                if status == JobStatus.COMPLETED.value:
                    job_record["completed_at"] = job_record["updated_at"]
            
            # Update counters
            if processed_documents is not None:
                job_record["processed_documents"] = processed_documents
            if success_documents is not None:
                job_record["success_documents"] = success_documents
            if failed_documents is not None:
                job_record["failed_documents"] = failed_documents
            if total_documents is not None:
                job_record["total_documents"] = total_documents
            
            # Update error message
            if error_message is not None:
                job_record["error_message"] = error_message
            
            # Update management URLs
            if instance_id is not None:
                job_record["instance_id"] = instance_id
            if status_url is not None:
                job_record["status_url"] = status_url
            if terminate_url is not None:
                job_record["terminate_url"] = terminate_url
            if suspend_url is not None:
                job_record["suspend_url"] = suspend_url
            if resume_url is not None:
                job_record["resume_url"] = resume_url
            
            execute_with_retry(
                self.container.upsert_item,
                operation_name=f"update_job({job_id})",
                body=job_record
            )
            logging.info(
                "Updated %s job in CosmosDB: status=%s, processed=%s, success=%s, failed=%s",
                jtype.value,
                job_record.get("status"),
                job_record.get("processed_documents"),
                job_record.get("success_documents"),
                job_record.get("failed_documents")
            )
            
        except Exception as e:
            logging.exception("FAILED to update %s job in CosmosDB: %s", jtype.value, str(e))
    
    def is_job_active(self, jtype: JobType) -> bool:
        """
        Check if a job is currently active (running, pending, or suspended).
        
        Args:
            jtype: Type of job
        
        Returns:
            True if job is active
        """
        job = self.get_job(jtype)
        if not job:
            return False
        
        active_statuses = (
            JobStatus.PENDING.value,
            JobStatus.RUNNING.value,
            JobStatus.SUSPENDED.value
        )
        return job.get("status") in active_statuses
    
    def get_active_job_info(self, jtype: JobType) -> Optional[Dict[str, Any]]:
        """
        Get active job info for conflict response.
        
        Returns dict with status, started_by, started_at if job is active.
        """
        if not self.is_job_active(jtype):
            return None
        
        job = self.get_job(jtype)
        return {
            "status": job.get("status"),
            "started_by": job.get("started_by"),
            "started_at": job.get("started_at"),
        }


# Singleton instance
_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Get or create the singleton JobManager instance."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager
