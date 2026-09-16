# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pipeline Job Tracking Helper.

Provides functions to track pipeline job status in Cosmos DB statistics container.
Used by orchestrators to report progress and completion status.
"""

import os
import logging
from datetime import datetime

from azure.cosmos import exceptions as cosmos_exceptions

from .cosmos_client import get_container


def _get_statistics_container():
    """Get the statistics container for pipeline job tracking using shared CosmosClient."""
    container_name = os.getenv("COSMOS_DB_STATS_CONTAINER_NAME", "statistics")
    try:
        # get_container handles caching internally via cosmos_client module
        return get_container(container_name)
    except Exception as e:
        logging.exception(
            "Failed to connect to statistics container '%s': %s",
            container_name,
            e
        )
        return None


def get_pipeline_job_id(trigger_endpoint: str) -> str:
    """Generate document ID for pipeline job: pipeline_step_{trigger_endpoint}"""
    return f"pipeline_step_{trigger_endpoint}"


def get_orchestration_management_urls(starter, instance_id: str, request=None) -> dict:
    """
    Build durable management URLs for Cosmos and API clients.

    When an HTTP request is available, use the same link builder as
    ``create_check_status_response`` so stored URLs match the trigger response
    (including host and auth query parameters). Timer/schedule starts omit
    ``request`` and fall back to the extension default payload.
    """
    if request is not None:
        return starter.get_client_response_links(request, instance_id)
    return starter.create_http_management_payload(instance_id)


def get_running_job(trigger_endpoint: str) -> dict:
    """
    Check if there's already a running job for the given trigger endpoint.

    Args:
        trigger_endpoint: The trigger endpoint to check (e.g., 'fetch-related-assets')

    Returns:
        Job document dict if a running job exists, None otherwise
    """
    container = _get_statistics_container()
    if container is None:
        logging.warning("Cannot check running job '%s': statistics container not available", trigger_endpoint)
        return None

    try:
        doc_id = get_pipeline_job_id(trigger_endpoint)
        existing = container.read_item(item=doc_id, partition_key=doc_id)

        # Check if job is in a running state
        status = existing.get("runtime_status", "")
        if status in ("Pending", "Running"):
            logging.info(
                "Found existing %s job for %s (instance=%s)",
                status,
                trigger_endpoint,
                existing.get("instance_id"),
            )
            return existing

        return None

    except cosmos_exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logging.exception("Error checking for running job %s: %s", trigger_endpoint, e)
        return None


def update_job_status_if_stale(
    trigger_endpoint: str,
    actual_status: str,
    output_data: dict = None,
) -> bool:
    """
    Update job status in Cosmos DB if orchestration was terminated/completed externally.

    Args:
        trigger_endpoint: The trigger endpoint
        actual_status: The actual runtime status from Durable Functions
        output_data: Optional output data to store

    Returns:
        True if updated, False otherwise
    """
    container = _get_statistics_container()
    if container is None:
        logging.warning("Cannot update job status '%s': statistics container not available", trigger_endpoint)
        return False

    try:
        doc_id = get_pipeline_job_id(trigger_endpoint)
        existing = container.read_item(item=doc_id, partition_key=doc_id)

        now = datetime.utcnow().isoformat()
        existing["runtime_status"] = actual_status
        existing["updated_at"] = now
        existing["last_updated_time"] = now

        if output_data:
            existing["current_stats"] = output_data

        container.upsert_item(existing)
        logging.info(
            "Updated stale job status for %s: %s (instance=%s)",
            trigger_endpoint,
            actual_status,
            existing.get("instance_id"),
        )
        return True

    except Exception as e:
        logging.exception("Error updating stale job status %s: %s", trigger_endpoint, e)
        return False


async def check_and_handle_running_job(trigger_endpoint: str, starter) -> dict:
    """
    Check if there's already a running job and verify its actual status.

    This function:
    1. Checks Cosmos DB for an existing job with Pending/Running status
    2. Verifies the actual orchestration status from Durable Functions
    3. Updates Cosmos if the job was terminated externally
    4. Returns appropriate result

    Args:
        trigger_endpoint: The trigger endpoint to check
        starter: DurableOrchestrationClient instance

    Returns:
        dict with:
        - "allow_new_job": True if a new job can be started
        - "response": HttpResponse to return if job is blocked (only if allow_new_job is False)
        - "existing_job": The existing job dict if found
    """
    import azure.functions as func
    import json

    existing_job = get_running_job(trigger_endpoint)

    if not existing_job:
        return {"allow_new_job": True, "existing_job": None}

    existing_instance_id = existing_job.get("instance_id")

    if not existing_instance_id:
        return {"allow_new_job": True, "existing_job": existing_job}

    # Verify actual orchestration status from Durable Functions
    try:
        actual_status = await starter.get_status(existing_instance_id)

        if actual_status and actual_status.runtime_status:
            actual_runtime_status = actual_status.runtime_status.value

            # If orchestration is actually terminated/completed, update Cosmos and allow new job
            if actual_runtime_status in ("Completed", "Failed", "Terminated", "Canceled"):
                update_job_status_if_stale(
                    trigger_endpoint,
                    actual_runtime_status,
                    {"status": "externally_terminated", "message": f"Job was {actual_runtime_status} externally"},
                )
                logging.info(
                    "Previous job %s was %s, allowing new job for %s",
                    existing_instance_id,
                    actual_runtime_status,
                    trigger_endpoint
                )
                return {"allow_new_job": True, "existing_job": existing_job}

            # Job is truly still running
            return {
                "allow_new_job": False,
                "existing_job": existing_job,
                "response": func.HttpResponse(
                    json.dumps({
                        "status": "already_running",
                        "message": "A job is already running for this endpoint",
                        "instance_id": existing_instance_id,
                        "runtime_status": actual_runtime_status,
                        "started_at": existing_job.get("started_at"),
                    }),
                    status_code=409,
                    mimetype="application/json",
                )
            }

    except Exception as e:
        logging.exception("Error checking orchestration status for %s: %s", existing_instance_id, e)

    # Could not verify status, assume job is still running (safer)
    return {
        "allow_new_job": False,
        "existing_job": existing_job,
        "response": func.HttpResponse(
            json.dumps({
                "status": "already_running",
                "message": "A job is already running for this endpoint",
                "instance_id": existing_instance_id,
                "runtime_status": existing_job.get("runtime_status"),
                "started_at": existing_job.get("started_at"),
            }),
            status_code=409,
            mimetype="application/json",
        )
    }


def save_pipeline_job(
    trigger_endpoint: str,
    instance_id: str,
    name: str,
    runtime_status: str = "Pending",
    status_url: str = None,
    terminate_url: str = None,
    suspend_url: str = None,
    resume_url: str = None,
    started_at: str = None,
    custom_status: any = None,
    input_data: dict = None,
    output_data: dict = None,
    error: str = None,
) -> bool:
    """
    Save/update a pipeline job record in Cosmos DB statistics container.

    Args:
        trigger_endpoint: The trigger endpoint (e.g., 'fetch-related-assets')
        instance_id: Durable Functions orchestration instance ID
        name: Human-readable name for the job
        runtime_status: Status (Pending, Running, Completed, Failed, Terminated)
        status_url: URL to check orchestration status
        terminate_url: URL to terminate the orchestration
        suspend_url: URL to suspend the orchestration
        resume_url: URL to resume the orchestration
        started_at: ISO timestamp when job started (defaults to now)
        custom_status: Custom status data from orchestration
        input_data: Input/progress data (stored as current_stats for Running)
        output_data: Output data (stored as current_stats for Completed)
        error: Error message (for Failed status)

    Returns:
        True if saved successfully, False otherwise
    """
    container = _get_statistics_container()
    if container is None:
        container_name = os.getenv("COSMOS_DB_STATS_CONTAINER_NAME", "statistics")
        logging.error(
            "Cannot save pipeline job '%s': statistics container not available (container=%s)",
            trigger_endpoint,
            container_name
        )
        return False

    try:
        doc_id = get_pipeline_job_id(trigger_endpoint)
        now = datetime.utcnow().isoformat()

        # Try to read existing document to preserve certain fields
        existing = None
        try:
            existing = container.read_item(item=doc_id, partition_key=doc_id)
        except cosmos_exceptions.CosmosResourceNotFoundError:
            pass

        # Determine started_at and created_at
        # For new jobs (Pending status), always reset started_at to now
        # For running/updating jobs, preserve existing started_at
        if started_at:
            job_started_at = started_at
        elif runtime_status == "Pending":
            # New job starting - always use current time
            job_started_at = now
        elif existing:
            job_started_at = existing.get("started_at", now)
        else:
            job_started_at = now

        created_at = existing.get("created_at", now) if existing else now
        same_instance = (
            existing is not None
            and existing.get("instance_id") == instance_id
        )

        job_doc = {
            "id": doc_id,
            "trigger_endpoint": trigger_endpoint,
            "instance_id": instance_id,
            "name": name,
            "runtime_status": runtime_status,
            "started_at": job_started_at,
            "created_at": created_at,
            "updated_at": now,
            "last_updated_time": now,
        }

        # Management URLs are instance-specific; never copy from a prior run.
        url_fields = (
            ("status_url", status_url),
            ("terminate_url", terminate_url),
            ("suspend_url", suspend_url),
            ("resume_url", resume_url),
        )
        for field_name, field_value in url_fields:
            if field_value is not None:
                job_doc[field_name] = field_value
            elif same_instance and existing.get(field_name):
                job_doc[field_name] = existing[field_name]

        # Add custom_status if provided
        if custom_status is not None:
            job_doc["custom_status"] = custom_status

        # Add current_stats based on status
        if runtime_status in ("Completed", "Failed", "Terminated", "Canceled"):
            if output_data is not None:
                job_doc["current_stats"] = output_data
            if error:
                job_doc["error"] = error
        else:
            if input_data is not None:
                job_doc["current_stats"] = input_data

        container.upsert_item(job_doc)
        logging.info(
            "Pipeline job saved successfully: endpoint=%s, doc_id=%s, status=%s, instance=%s",
            trigger_endpoint,
            doc_id,
            runtime_status,
            instance_id,
        )
        return True

    except Exception as e:
        logging.exception("Failed to save pipeline job %s (doc_id=%s): %s", trigger_endpoint, doc_id, e)
        return False


def persist_pipeline_job_status(payload: dict) -> dict:
    """Validate and persist a Durable pipeline job status transition."""
    payload = payload or {}
    required_fields = ("trigger_endpoint", "instance_id", "name")
    missing_fields = [field for field in required_fields if not payload.get(field)]
    if missing_fields:
        raise ValueError(
            f"Missing required pipeline job fields: {', '.join(missing_fields)}"
        )

    saved = save_pipeline_job(
        trigger_endpoint=payload["trigger_endpoint"],
        instance_id=payload["instance_id"],
        name=payload["name"],
        runtime_status=payload.get("runtime_status", "Running"),
        custom_status=payload.get("custom_status"),
        input_data=payload.get("input_data"),
        output_data=payload.get("output_data"),
        error=payload.get("error"),
    )
    if not saved:
        raise RuntimeError(
            f"Failed to persist pipeline job status for {payload['trigger_endpoint']}"
        )

    return {"success": True}
