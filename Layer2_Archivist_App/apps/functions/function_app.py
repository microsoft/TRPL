# pylint: disable=invalid-name

"""Azure Functions App for ingesting CosmosDB documents into Azure AI Search."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import azure.durable_functions as df
import azure.functions as func

from helper.config import CosmosDBConfig
from helper.cosmos_client import CosmosDBClient, execute_with_retry
from helper.cosmos_ingest import get_publish_batch_document_snapshot, process_cosmos
from helper.epub_processor import (
    process_epub_delete,
    process_epub_extract, 
    process_epub_ingest,
    process_epub_parse,
)
from helper.job_manager import JobType, JobStatus, extract_management_urls, get_job_manager

app = func.FunctionApp()

job_manager = get_job_manager()
BULK_INGESTION_JOB_ID = job_manager.get_job_id(JobType.BULK_INGESTION)
UNPUBLISH_JOB_ID = job_manager.get_job_id(JobType.UNPUBLISH_DOCUMENTS)


class OperationType:
    """Constants for document processing operation types."""
    BULK = "bulk"
    INDIVIDUAL = "individual"
    REINDEX = "reindex"
    RETRY_FAILED = "retry_failed"
    
    @classmethod
    def valid_types(cls) -> set:
        return {cls.BULK, cls.INDIVIDUAL, cls.REINDEX, cls.RETRY_FAILED}


def _validate_operation_type(operation_type: str) -> Optional[str]:
    """Return error message if operation_type is invalid, None otherwise."""
    if operation_type and operation_type not in OperationType.valid_types():
        return f"Invalid operation_type: '{operation_type}'. Must be one of: {OperationType.valid_types()}"
    return None


def _check_active_ingestion_job() -> Optional[Dict[str, Any]]:
    """Return conflict info dict if an ingestion job is active, None otherwise."""
    existing_job = get_bulk_ingestion_job_status()
    active_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUSPENDED.value)
    if existing_job and existing_job.get("status") in active_statuses:
        return {
            "error": "An ingestion job is already in progress",
            "operation_type": existing_job.get("operation_type", "unknown"),
            "status": existing_job.get("status"),
            "started_by": existing_job.get("started_by"),
            "started_at": existing_job.get("started_at")
        }
    return None


def _create_conflict_response(conflict_info: Dict[str, Any]) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(conflict_info), status_code=409, mimetype="application/json")


def _create_no_documents_response(message: str) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"message": message, "total_documents": 0}), status_code=200, mimetype="application/json")


def _extract_where_clause(query: str) -> Optional[str]:
    """Extract WHERE clause from a SQL query. Returns None if no WHERE clause."""
    where_match = query.upper().find(" WHERE ")
    if where_match == -1:
        return None
    where_start = where_match + 7
    for terminator in (" ORDER BY", " OFFSET", " LIMIT"):
        term_match = query.upper().find(terminator, where_start)
        if term_match != -1:
            return query[where_start:term_match].strip()
    return query[where_start:].strip()


async def _start_ingestion_orchestrator(
    client: df.DurableOrchestrationClient,
    operation_type: str,
    batch_size: int,
    started_by: str,
    action: Optional[str] = None,
    doc_ids: Optional[List[str]] = None,
    query_config: Optional[Dict[str, Any]] = None
) -> str:
    """Start DataIngestOrchestrator with doc_ids or query_config for batched fetching."""
    orchestrator_input = {
        "job_id": BULK_INGESTION_JOB_ID,
        "operation_type": operation_type,
        "batch_size": batch_size,
        "started_by": started_by
    }
    if action:
        orchestrator_input["action"] = action
    if query_config:
        orchestrator_input["query_config"] = query_config
    elif doc_ids:
        orchestrator_input["document_ids"] = doc_ids
    
    return await client.start_new("DataIngestOrchestrator", None, orchestrator_input)


def create_bulk_ingestion_job(total_documents: int, started_by: str, operation_type: str = "bulk", **mgmt_urls) -> str:
    """Create ingestion job (shared by bulk, retry, and reindex operations)."""
    return job_manager.create_job(
        JobType.BULK_INGESTION, total_documents=total_documents, started_by=started_by,
        operation_type=operation_type, **mgmt_urls
    )


def get_bulk_ingestion_job_status() -> Optional[Dict[str, Any]]:
    return job_manager.get_job(JobType.BULK_INGESTION)


def update_bulk_ingestion_job(
    status: Optional[str] = None, processed_documents: Optional[int] = None,
    success_documents: Optional[int] = None, failed_documents: Optional[int] = None,
    total_documents: Optional[int] = None, error_message: Optional[str] = None, **mgmt_urls
) -> None:
    job_manager.update_job(
        JobType.BULK_INGESTION, status=status, processed_documents=processed_documents,
        success_documents=success_documents, failed_documents=failed_documents,
        total_documents=total_documents, error_message=error_message, **mgmt_urls
    )


def create_unpublish_job(total_documents: int, started_by: str, published_by: str, published_date: str, **mgmt_urls) -> str:
    return job_manager.create_job(
        JobType.UNPUBLISH_DOCUMENTS, total_documents=total_documents, started_by=started_by,
        published_by=published_by, published_date=published_date, **mgmt_urls
    )


def get_unpublish_job_status() -> Optional[Dict[str, Any]]:
    return job_manager.get_job(JobType.UNPUBLISH_DOCUMENTS)


def update_unpublish_job(
    status: Optional[str] = None, processed_documents: Optional[int] = None,
    success_documents: Optional[int] = None, failed_documents: Optional[int] = None,
    error_message: Optional[str] = None
) -> None:
    job_manager.update_job(
        JobType.UNPUBLISH_DOCUMENTS, status=status, processed_documents=processed_documents,
        success_documents=success_documents, failed_documents=failed_documents, error_message=error_message
    )


BATCH_SIZE_PER_ACTIVITY = int(os.environ.get("BATCH_SIZE_PER_ACTIVITY", "100"))
SEMAPHORE_LIMIT = int(os.environ.get("SEMAPHORE_LIMIT", "100"))
# Unpublish batch: retry failed document IDs (large batches + transient errors).
UNPUBLISH_MAX_RETRY_PASSES = max(1, int(os.environ.get("UNPUBLISH_MAX_RETRY_PASSES", "4")))
UNPUBLISH_RETRY_BACKOFF_SEC = float(os.environ.get("UNPUBLISH_RETRY_BACKOFF_SEC", "2"))


@app.function_name(name="DataIngestHttp")
@app.route(route="data-ingest", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.durable_client_input(client_name="client")
async def data_ingest_http_trigger(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger for data ingestion - returns management URLs immediately.
    
    This provides the same experience as data pipeline HTTP triggers,
    allowing immediate access to status_url, terminate_url, etc.
    
    Uses DataIngestOrchestrator with parallel batch processing for scalability.
    
    Expected request body (one of):
    {
        "job_id": "bulk_ingestion",
        "filter": {
            "query": "SELECT c.id FROM c WHERE ...",
            "parameters": [...]
        },
        "operation_type": "bulk"
    }
    OR
    {
        "job_id": "bulk_ingestion",
        "document_ids": ["id1", "id2", "id3"],
        "operation_type": "bulk"
    }
    OR
    {
        "action": "reindex",
        "started_by": "user@example.com"
    }
        
    Returns:
        HTTP response with orchestration management URLs
    """
    logging.info("HTTP trigger: Data ingestion request received")
    
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON in request body"}), status_code=400, mimetype="application/json")
    
    action = payload.get("action")
    started_by = payload.get("started_by", "unknown")
    operation_type = payload.get("operation_type", OperationType.BULK)
    batch_size = payload.get("batch_size", BATCH_SIZE_PER_ACTIVITY)
    
    validation_error = _validate_operation_type(operation_type)
    if validation_error:
        return func.HttpResponse(json.dumps({"error": validation_error}), status_code=400, mimetype="application/json")
    
    try:
        conflict_info = _check_active_ingestion_job()
        if conflict_info:
            return _create_conflict_response(conflict_info)
        
        config = CosmosDBConfig.from_env()
        cosmos_client = CosmosDBClient(config)
        
        doc_ids: Optional[List[str]] = None
        query_config: Optional[Dict[str, Any]] = None
        total_documents = 0
        effective_operation_type = operation_type
        effective_action = action
        no_docs_message = ""
        
        if action == "reindex":
            logging.info("HTTP trigger: Processing reindex request from user: %s", started_by)
            effective_operation_type = OperationType.REINDEX
            effective_action = "reindex"
            no_docs_message = "No published documents to reindex"
            doc_ids = cosmos_client.query_document_ids(
                query=(
                    "SELECT c.id FROM c WHERE (IS_DEFINED(c.archivist_status) AND "
                    "c.archivist_status != null AND IS_STRING(c.archivist_status) AND "
                    "LOWER(c.archivist_status) = 'published')"
                ),
                parameters=[]
            )
            total_documents = len(doc_ids)
            if total_documents > 0:
                logging.info("HTTP trigger: Reindex will process %d documents", total_documents)
            
        elif "filter" in payload:
            filter_data = payload.get("filter")
            if not filter_data or not isinstance(filter_data, dict):
                return func.HttpResponse(
                    json.dumps({"error": "Filter must be a dictionary with 'query' and optional 'parameters'"}),
                    status_code=400,
                    mimetype="application/json"
                )
            
            query = filter_data.get("query")
            if not query:
                return func.HttpResponse(
                    json.dumps({"error": "Filter must contain a 'query' field"}),
                    status_code=400,
                    mimetype="application/json"
                )
            
            logging.info(
                "HTTP trigger: filter query (first 500 chars): %s",
                query[:500],
            )
            logging.info(
                "HTTP trigger: Cosmos DB=%s, container=%s",
                config.database_name, config.container_name,
            )
            no_docs_message = "No documents found matching the filter"
            parameters = filter_data.get("parameters", [])
            doc_ids = cosmos_client.query_document_ids(query=query, parameters=parameters)
            total_documents = len(doc_ids)
            
            if total_documents > 0:
                logging.info("HTTP trigger: Filter will process %d documents", total_documents)
            
        elif "document_ids" in payload:
            doc_ids = payload.get("document_ids")
            if not doc_ids or not isinstance(doc_ids, list):
                return func.HttpResponse(json.dumps({"error": "document_ids must be a non-empty list"}), status_code=400, mimetype="application/json")
            if not all(isinstance(doc_id, str) and doc_id.strip() for doc_id in doc_ids):
                return func.HttpResponse(json.dumps({"error": "All document_ids must be non-empty strings"}), status_code=400, mimetype="application/json")
            no_docs_message = "No document IDs provided"
            total_documents = len(doc_ids)
            
        else:
            return func.HttpResponse(
                json.dumps({"error": "Request must contain 'action', 'filter', or 'document_ids'", "received_fields": list(payload.keys())}),
                status_code=400, mimetype="application/json"
            )
        
        if total_documents == 0:
            return _create_no_documents_response(no_docs_message)
        
        # Re-check for active job (in case one started while we were querying)
        conflict_info = _check_active_ingestion_job()
        if conflict_info:
            return _create_conflict_response(conflict_info)
        
        instance_id = await _start_ingestion_orchestrator(
            client=client, operation_type=effective_operation_type, batch_size=batch_size,
            started_by=started_by, action=effective_action, doc_ids=doc_ids, query_config=query_config
        )
        
        mgmt_urls = extract_management_urls(client, instance_id)
        create_bulk_ingestion_job(total_documents=total_documents, started_by=started_by, operation_type=effective_operation_type, **mgmt_urls)
        
        logging.info(
            "HTTP trigger: Started %s orchestrator ID='%s' for %d documents (%d batches)",
            effective_operation_type, instance_id, total_documents, (total_documents + batch_size - 1) // batch_size
        )
        return client.create_check_status_response(req, instance_id)
        
    except Exception as e:
        logging.exception("HTTP trigger: Error starting orchestration: %s", str(e))
        update_bulk_ingestion_job(status="Failed", error_message=str(e))
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


@app.function_name(name="UnpublishDocumentsHttp")
@app.route(route="unpublish-documents", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.durable_client_input(client_name="client")
async def unpublish_documents_http_trigger(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    HTTP trigger to unpublish documents published by a specific user on a specific date.
    
    This will:
    1. Query documents where published_by = user AND published_at starts with date
    2. Set archivist_status to 'Pending' for each document
    3. Delete documents from the search index
    
    Expected request body (publisher + date):
    {
        "published_by": "user@example.com",
        "published_date": "2025-01-15",
        "started_by": "admin@example.com"
    }

    Or filter mode (Cosmos query returning document ids):
    {
        "started_by": "admin@example.com",
        "published_by": "(collection filters)",
        "published_date": "bulk-by-filters",
        "filter": { "query": "SELECT c.id FROM c WHERE ...", "parameters": [] }
    }

    Or explicit document ids (e.g. undo a tracked publish batch):
    {
        "started_by": "admin@example.com",
        "document_ids": ["id1", "id2"],
        "published_by": "publish-batch:<batch_id>",
        "published_date": "batch"
    }

    Returns:
        HTTP response with orchestration management URLs
    """
    logging.info("HTTP trigger: Unpublish documents request received")
    
    try:
        payload = req.get_json()
        logging.info("HTTP trigger payload: %s", payload)
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            status_code=400,
            mimetype="application/json"
        )
    
    started_by = payload.get("started_by", "unknown")
    filter_data = payload.get("filter")

    try:
        # Check if an unpublish job is already running
        existing_job = get_unpublish_job_status()
        active_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUSPENDED.value)
        if existing_job and existing_job.get("status") in active_statuses:
            return func.HttpResponse(
                json.dumps({
                    "error": "Unpublish job already in progress",
                    "status": existing_job.get("status"),
                    "started_by": existing_job.get("started_by"),
                    "started_at": existing_job.get("started_at"),
                    "published_by": existing_job.get("published_by"),
                    "published_date": existing_job.get("published_date")
                }),
                status_code=409,
                mimetype="application/json"
            )

        published_by: str
        published_date: str
        doc_ids: List[str]
        total_documents: int

        direct_ids = payload.get("document_ids")
        if direct_ids is not None:
            if not isinstance(direct_ids, list):
                return func.HttpResponse(
                    json.dumps({"error": "document_ids must be a list of strings"}),
                    status_code=400,
                    mimetype="application/json"
                )
            doc_ids = [str(x).strip() for x in direct_ids if isinstance(x, str) and str(x).strip()]
            total_documents = len(doc_ids)
            published_by = str(payload.get("published_by") or "document-id-list")
            published_date = str(payload.get("published_date") or "adhoc")
            if total_documents == 0:
                return func.HttpResponse(
                    json.dumps({"message": "No document IDs provided.", "total_documents": 0}),
                    status_code=200,
                    mimetype="application/json"
                )
            logging.info(
                "HTTP trigger: Unpublish by explicit document_ids count=%d (label: %s / %s)",
                total_documents,
                published_by,
                published_date,
            )
        elif filter_data and isinstance(filter_data, dict):
            query = filter_data.get("query")
            if not query or not isinstance(query, str):
                return func.HttpResponse(
                    json.dumps({"error": "filter.query is required when using filter mode"}),
                    status_code=400,
                    mimetype="application/json"
                )
            parameters = filter_data.get("parameters") or []
            if not isinstance(parameters, list):
                return func.HttpResponse(
                    json.dumps({"error": "filter.parameters must be a list when provided"}),
                    status_code=400,
                    mimetype="application/json"
                )
            published_by = str(payload.get("published_by") or "(filters)")
            published_date = str(payload.get("published_date") or "bulk-by-filters")
            config = CosmosDBConfig.from_env()
            cosmos_client = CosmosDBClient(config)
            doc_ids = cosmos_client.query_document_ids(query=query, parameters=parameters)
            total_documents = len(doc_ids)
        else:
            published_by = payload.get("published_by")
            published_date = payload.get("published_date")

            if not published_by:
                return func.HttpResponse(
                    json.dumps({"error": "published_by is required when not using filter"}),
                    status_code=400,
                    mimetype="application/json"
                )

            if not published_date:
                return func.HttpResponse(
                    json.dumps({"error": "published_date is required (YYYY-MM-DD format) when not using filter"}),
                    status_code=400,
                    mimetype="application/json"
                )

            # Build query for documents published by user on specific date
            # published_by contains the user who published, published_at contains the timestamp
            # We match date by checking if published_at starts with the date string
            query = """
                SELECT c.id FROM c 
                WHERE c.published_by = @published_by 
                AND STARTSWITH(c.published_at, @published_date)
                AND (IS_DEFINED(c.archivist_status) AND c.archivist_status != null
                AND IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = 'published')
            """
            parameters = [
                {"name": "@published_by", "value": published_by},
                {"name": "@published_date", "value": published_date}
            ]

            # Query documents
            config = CosmosDBConfig.from_env()
            cosmos_client = CosmosDBClient(config)
            doc_ids = cosmos_client.query_document_ids(query=query, parameters=parameters)
            total_documents = len(doc_ids)
        
        if total_documents == 0:
            if direct_ids is not None:
                empty_msg = "No document IDs provided."
            elif filter_data and isinstance(filter_data, dict):
                empty_msg = "No published documents match the selected filters."
            else:
                empty_msg = (
                    f"No published documents found for user '{published_by}' "
                    f"on date '{published_date}'"
                )
            return func.HttpResponse(
                json.dumps({"message": empty_msg, "total_documents": 0}),
                status_code=200,
                mimetype="application/json"
            )
        
        logging.info(
            "Found %d documents to unpublish (published_by: %s, date: %s)",
            total_documents, published_by, published_date
        )
        
        # Start orchestrator
        instance_id = await client.start_new(
            orchestration_function_name="UnpublishDocumentsOrchestrator",
            instance_id=None,
            client_input={
                "document_ids": doc_ids,
                "published_by": published_by,
                "published_date": published_date,
                "started_by": started_by,
                "batch_size": BATCH_SIZE_PER_ACTIVITY
            }
        )
        
        # Get management URLs and create job record
        mgmt_urls = extract_management_urls(client, instance_id)
        create_unpublish_job(
            total_documents=total_documents,
            started_by=started_by,
            published_by=published_by,
            published_date=published_date,
            **mgmt_urls
        )
        
        logging.info(
            "HTTP trigger: Started unpublish orchestrator ID='%s' for %d documents",
            instance_id, total_documents
        )
        
        return client.create_check_status_response(req, instance_id)
        
    except Exception as e:
        logging.exception("HTTP trigger: Error starting unpublish orchestration: %s", str(e))
        update_unpublish_job(status="Failed", error_message=str(e))
        
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )


@app.function_name(name="DataIngest")
@app.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="data-ingestion-queue",
    connection="ServiceBusConnection"
)
@app.durable_client_input(client_name="client")
async def data_ingest_trigger(
    msg: func.ServiceBusMessage, client: df.DurableOrchestrationClient
) -> None:
    """
    Service Bus trigger for individual document ingestion.
    
    This is now primarily used for:
    - Individual document approval (single document_id)
    - Backward compatibility with existing queue messages
    
    For bulk operations and reindex, prefer the HTTP trigger (DataIngestHttp)
    which returns management URLs immediately.

    Expected Service Bus message format:
    {
        "document_id": "xxxxxxxxx-xxxx-xxx-xxxx-xxxxxxxxxxxx",
        "operation_type": "individual"
    }
    
    Legacy formats (still supported for backward compatibility):
    {
        "document_ids": ["id1", "id2", "id3"]
    }
    OR
    {
        "filter": {
            "query": "SELECT * FROM c WHERE IS_STRING(c.archivist_status) AND LOWER(c.archivist_status) = @status",
            "parameters": [{"name": "@status", "value": "pending"}]
        }
    }
    OR
    {
    "action": "reindex"
    }
    """
    logging.info("Triggered AI Search Cosmos ingestion function")

    try:
        body = msg.get_body().decode("utf-8")
        payload = json.loads(body)
        logging.info("Received message: %s", payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        error_msg = f"Invalid message format (not valid JSON): {str(e)}. Message body: {body[:200] if body else 'empty'}"
        logging.error(error_msg)
        # Raise exception so Service Bus can retry/dead-letter the message
        raise ValueError(error_msg) from e

    # Extract optional fields
    action = payload.get("action")
    started_by = payload.get("started_by", "unknown")
    job_id = payload.get("job_id")  # Optional job ID from payload

    try:
        # Validate operation_type if provided
        operation_type = payload.get("operation_type", OperationType.BULK)
        if operation_type and operation_type not in OperationType.valid_types():
            raise ValueError(
                f"Invalid operation_type: '{operation_type}'. "
                f"Must be one of: {OperationType.valid_types()}"
            )
        # Handle unpublish action for single document
        if action == "unpublish":
            doc_id = payload.get("document_id")
            requested_by = payload.get("requested_by", "unknown")
            if not doc_id:
                raise ValueError("document_id is required for unpublish action")
            
            logging.info(
                "Processing unpublish request for document: %s by user: %s",
                doc_id,
                requested_by
            )
            
            # Call activity directly for single document unpublish
            instance_id = await client.start_new(
                orchestration_function_name="UnpublishSingleDocumentOrchestrator",
                instance_id=None,
                client_input={
                    "document_id": doc_id,
                    "requested_by": requested_by
                }
            )
            
            logging.info(
                "Started unpublish orchestration with ID = '%s' for document: %s by user: %s",
                instance_id,
                doc_id,
                requested_by
            )
            return

        # Handle reindex action - build query for published documents
        # Note: Reindex uses the same job as bulk ingestion (consolidated job tracking)
        if action == "reindex":
            logging.info("Processing reindex request from user: %s", started_by)
            
            # Check if any ingestion job is already running or suspended (all share same job)
            existing_job = get_bulk_ingestion_job_status()
            active_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUSPENDED.value)
            if existing_job and existing_job.get("status") in active_statuses:
                logging.warning(
                    "Ingestion job already in progress (type: %s, status: %s, started by %s at %s). Skipping.",
                    existing_job.get("operation_type", "unknown"),
                    existing_job.get("status"),
                    existing_job.get("started_by"),
                    existing_job.get("started_at")
                )
                return
            
            # Build query for all published documents
            filter_data = {
                "query": (
                    "SELECT c.id FROM c WHERE (IS_DEFINED(c.archivist_status) AND "
                    "c.archivist_status != null AND IS_STRING(c.archivist_status) AND "
                    "LOWER(c.archivist_status) = 'published')"
                ),
                "parameters": []
            }
            
            # Count documents first
            config = CosmosDBConfig.from_env()
            logging.info(
                "Reindex using database='%s', container='%s'",
                config.database_name,
                config.container_name
            )
            cosmos_client = CosmosDBClient(config)
            doc_ids = cosmos_client.query_document_ids(
                query=filter_data["query"],
                parameters=filter_data["parameters"]
            )
            total_documents = len(doc_ids)
            
            if total_documents == 0:
                logging.info("No published documents to reindex")
                return
            
            # Start orchestration first to get instance_id
            instance_id = await client.start_new(
                orchestration_function_name="DataIngestOrchestrator",
                instance_id=None,
                client_input={
                    "document_ids": doc_ids,
                    "job_id": BULK_INGESTION_JOB_ID,
                    "action": "reindex",
                    "operation_type": OperationType.REINDEX,
                    "batch_size": BATCH_SIZE_PER_ACTIVITY
                }
            )
            
            # Get management URLs and create job record (same job as bulk ingestion)
            mgmt_urls = extract_management_urls(client, instance_id)
            create_bulk_ingestion_job(
                total_documents=total_documents,
                started_by=started_by,
                operation_type=OperationType.REINDEX,
                **mgmt_urls
            )
            
            logging.info(
                "Started reindex orchestration with ID = '%s' for %d documents (job: %s)",
                instance_id,
                total_documents,
                BULK_INGESTION_JOB_ID
            )
            return

        # Check if it's a filter query, list of document_ids, or a single document_id
        if "filter" in payload:
            # Start orchestration with filter
            filter_data = payload.get("filter")
            if not filter_data or not isinstance(filter_data, dict):
                raise ValueError(
                    "Filter must be a dictionary with 'query' and optional 'parameters'"
                )

            # Defense in depth: Check if a bulk ingestion job is already in progress
            # (API already checks, but message could be delayed in queue)
            if job_id == BULK_INGESTION_JOB_ID:
                existing_job = get_bulk_ingestion_job_status()
                active_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUSPENDED.value)
                if existing_job and existing_job.get("status") in active_statuses:
                    logging.warning(
                        "Bulk ingestion job already in progress (status: %s, started by %s at %s). "
                        "Skipping duplicate request.",
                        existing_job.get("status"),
                        existing_job.get("started_by"),
                        existing_job.get("started_at")
                    )
                    return

            # Build orchestrator input with optional job tracking
            orchestrator_input = {
                "filter": filter_data,
                "operation_type": payload.get("operation_type", OperationType.BULK),
                "batch_size": BATCH_SIZE_PER_ACTIVITY
            }
            if job_id:
                orchestrator_input["job_id"] = job_id
            if action:
                orchestrator_input["action"] = action

            instance_id = await client.start_new(
                orchestration_function_name="DataIngestOrchestrator",
                instance_id=None,
                client_input=orchestrator_input
            )
            
            # Capture management URLs and update job record
            if job_id == BULK_INGESTION_JOB_ID:
                mgmt_urls = extract_management_urls(client, instance_id)
                update_bulk_ingestion_job(status="Running", **mgmt_urls)
                logging.info(
                    "Started bulk ingestion orchestration with ID = '%s' "
                    "(job_id: %s, management URLs captured)",
                    instance_id,
                    job_id
                )
            else:
                logging.info(
                    "Started orchestration with ID = '%s' for filter query (job_id: %s, action: %s)",
                    instance_id,
                    job_id,
                    action
                )
        elif "document_ids" in payload:
            # Start orchestration with list of document_ids
            doc_ids = payload.get("document_ids")
            if not doc_ids:
                raise ValueError("document_ids cannot be empty")
            if not isinstance(doc_ids, list):
                raise ValueError("document_ids must be a list")
            if not all(isinstance(doc_id, str) and doc_id for doc_id in doc_ids):
                raise ValueError("All document_ids must be non-empty strings")

            # Defense in depth: Check if a bulk ingestion job is already in progress
            if job_id == BULK_INGESTION_JOB_ID:
                existing_job = get_bulk_ingestion_job_status()
                active_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUSPENDED.value)
                if existing_job and existing_job.get("status") in active_statuses:
                    logging.warning(
                        "Bulk ingestion job already in progress (status: %s, started by %s at %s). "
                        "Skipping duplicate request.",
                        existing_job.get("status"),
                        existing_job.get("started_by"),
                        existing_job.get("started_at")
                    )
                    return

            orchestrator_input = {
                "document_ids": doc_ids,
                "operation_type": payload.get("operation_type", OperationType.BULK),
                "batch_size": BATCH_SIZE_PER_ACTIVITY
            }
            if job_id:
                orchestrator_input["job_id"] = job_id
            
            instance_id = await client.start_new(
                orchestration_function_name="DataIngestOrchestrator",
                instance_id=None,
                client_input=orchestrator_input
            )
            
            # Capture management URLs and update job record
            if job_id == BULK_INGESTION_JOB_ID:
                mgmt_urls = extract_management_urls(client, instance_id)
                update_bulk_ingestion_job(
                    status="Running",
                    total_documents=len(doc_ids),
                    **mgmt_urls
                )
                logging.info(
                    "Started bulk ingestion orchestration with ID = '%s' for %d documents "
                    "(job_id: %s, management URLs captured)",
                    instance_id,
                    len(doc_ids),
                    job_id
                )
            else:
                logging.info(
                    "Started orchestration with ID = '%s' for %d documents",
                    instance_id,
                    len(doc_ids)
                )
        elif "document_id" in payload:
            # Start orchestration with single document_id
            doc_id = payload.get("document_id")
            if not doc_id:
                raise ValueError("document_id cannot be empty")

            instance_id = await client.start_new(
                orchestration_function_name="DataIngestOrchestrator",
                instance_id=None,
                client_input={
                    "document_id": doc_id,
                    "operation_type": payload.get("operation_type", OperationType.BULK)
                }
            )
            logging.info(
                "Started orchestration with ID = '%s' for document: %s",
                instance_id,
                doc_id
            )
        else:
            # Provide helpful error message with available fields
            available_fields = list(payload.keys())
            raise ValueError(
                f"Message must contain one of: 'document_id', 'document_ids', 'filter', or 'action' field. "
                f"Received fields: {available_fields}. "
                f"Message payload: {payload}"
            )

    except (ValueError, KeyError) as e:
        logging.exception("Error starting orchestration: %s", str(e))
        update_bulk_ingestion_job(status="Failed", error_message=str(e))
        raise


def _process_documents_sequential_batches(
    context: df.DurableOrchestrationContext,
    doc_ids: List[str],
    job_id: Optional[str] = None,
    operation_type: str = "bulk",
    batch_size: int = 100,
    started_by: Optional[str] = None,
    cumulative_processed: int = 0,
    cumulative_success: int = 0,
    original_total: Optional[int] = None,
    correlation_id: Optional[str] = None,
):
    """
    Process documents one batch at a time, using continue_as_new after each batch
    to prevent orchestrator replay slowdown (keeps history at O(1) events).
    """
    total_remaining = len(doc_ids)
    total_docs = original_total if original_total else total_remaining + cumulative_processed
    
    if total_remaining == 0:
        logging.info("No documents to process")
        if job_id:
            yield context.call_activity(
                "UpdateJobStatusActivity",
                {
                    "job_id": job_id,
                    "status": "Completed",
                    "processed_documents": cumulative_processed,
                    "success_documents": cumulative_success,
                    "failed_documents": cumulative_processed - cumulative_success,
                    "total_documents": total_docs
                }
            )
        return {
            "message": "No documents to process",
            "total_processed": cumulative_processed,
            "total_success": cumulative_success,
            "total_batches": 0
        }

    # Split remaining docs into batches
    batches = [doc_ids[i:i + batch_size] for i in range(0, total_remaining, batch_size)]
    total_batches = len(batches)
    
    # Calculate batch offset for logging (based on cumulative progress)
    batch_offset = cumulative_processed // batch_size

    logging.info(
        "Processing %d remaining docs → %d batches of %d (semaphore=%d, 1 batch per orchestration) (job_id: %s, cumulative: %d/%d)",
        total_remaining, total_batches, batch_size, SEMAPHORE_LIMIT,
        job_id, cumulative_processed, total_docs
    )
    
    # Update job with total count (only on first run)
    if job_id and cumulative_processed == 0:
        yield context.call_activity(
            "UpdateJobStatusActivity",
            {"job_id": job_id, "status": "Running", "total_documents": total_docs}
        )
    
    session_processed = 0
    session_success = 0
    batches_in_session = 0
    
    # Process batches sequentially (each batch processes docs in parallel internally)
    for batch_idx, batch_doc_ids in enumerate(batches):
        batch_num = batch_offset + batch_idx + 1

        logging.info(
            "Starting batch %d (session batch %d/%d) with %d documents",
            batch_num, batch_idx + 1, total_batches, len(batch_doc_ids)
        )
        
        # Call activity - it will process documents in parallel with semaphore
        result = yield context.call_activity(
            "ProcessDocumentBatchActivity",
            {
                "document_ids": batch_doc_ids,
                "operation_type": operation_type,
                "batch_num": batch_num,
                "started_by": started_by,
                "orchestration_instance_id": context.instance_id,
                "correlation_id": correlation_id,
            }
        )
        
        if isinstance(result, dict):
            batch_processed = result.get("processed", 0)
            batch_success = result.get("success", 0)
            session_processed += batch_processed
            session_success += batch_success
            batches_in_session += 1
            
            # Calculate cumulative totals
            total_processed = cumulative_processed + session_processed
            total_success = cumulative_success + session_success
            
            logging.info(
                "Batch %d completed - %d/%d successful, session: %d, cumulative: %d/%d (job_id: %s)",
                batch_num, batch_success, batch_processed,
                session_processed, total_processed, total_docs, job_id
            )
            
            # Update job status after each batch
            if job_id:
                yield context.call_activity(
                    "UpdateJobStatusActivity",
                    {
                        "job_id": job_id,
                        "status": "Running",
                        "processed_documents": total_processed,
                        "success_documents": total_success,
                        "failed_documents": total_processed - total_success
                    }
                )
            
            # Always continue_as_new after each batch to prevent replay slowdown
            # This keeps orchestrator history minimal (only 2 events per orchestration)
            if batch_idx < total_batches - 1:
                remaining_doc_ids = []
                for remaining_batch in batches[batch_idx + 1:]:
                    remaining_doc_ids.extend(remaining_batch)
                
                logging.info(
                    "Continuing as new after %d batches (processed %d in session, %d remaining docs)",
                    batches_in_session, session_processed, len(remaining_doc_ids)
                )
                
                return context.continue_as_new({
                    "document_ids": remaining_doc_ids,
                    "job_id": job_id,
                    "operation_type": operation_type,
                    "batch_size": batch_size,
                    "started_by": started_by,
                    "cumulative_processed": total_processed,
                    "cumulative_success": total_success,
                    "original_total": total_docs,
                    "_is_continuation": True
                })
    
    # All batches complete - calculate final totals
    total_processed = cumulative_processed + session_processed
    total_success = cumulative_success + session_success
    total_batches_all = batch_offset + batches_in_session
    
    # Mark job as completed
    if job_id:
        yield context.call_activity(
            "UpdateJobStatusActivity",
            {
                "job_id": job_id,
                "status": "Completed",
                "processed_documents": total_processed,
                "success_documents": total_success,
                "failed_documents": total_processed - total_success
            }
        )
    
    logging.info(
        "DONE - %d/%d docs in %d total batches (job_id: %s)",
        total_success, total_processed, total_batches_all, job_id
    )
    
    return {
        "message": f"Completed: {total_success}/{total_processed} documents in {total_batches_all} batches",
        "total_processed": total_processed,
        "total_success": total_success,
        "total_batches": total_batches_all
    }


@app.function_name(name="DataIngestOrchestrator")
@app.orchestration_trigger(context_name="context")
def data_ingest_orchestrator(context: df.DurableOrchestrationContext) -> str:
    """
    Unified orchestrator for document ingestion. Supports doc_ids (direct) or 
    query_config (batched OFFSET/LIMIT fetching) modes. Uses continue_as_new 
    after each batch for O(1) replay performance.
    """
    input_data = context.get_input()
    
    batch_size = input_data.get("batch_size", BATCH_SIZE_PER_ACTIVITY) if isinstance(input_data, dict) else BATCH_SIZE_PER_ACTIVITY
    job_id = input_data.get("job_id") if isinstance(input_data, dict) else None
    action = input_data.get("action") if isinstance(input_data, dict) else None
    operation_type = input_data.get("operation_type", OperationType.BULK) if isinstance(input_data, dict) else OperationType.BULK
    started_by = input_data.get("started_by") if isinstance(input_data, dict) else None
    is_continuation = input_data.get("_is_continuation", False) if isinstance(input_data, dict) else False
    cumulative_processed = input_data.get("cumulative_processed", 0) if isinstance(input_data, dict) else 0
    cumulative_success = input_data.get("cumulative_success", 0) if isinstance(input_data, dict) else 0
    original_total = input_data.get("original_total") if isinstance(input_data, dict) else None
    correlation_id = input_data.get("correlation_id") if isinstance(input_data, dict) else None

    try:
        # query_config mode: always use offset=0 since processed docs exit the result set
        # (status changes from 'pending' to 'published'/'failed', so they no longer match WHERE clause)
        if isinstance(input_data, dict) and "query_config" in input_data:
            query_config = input_data.get("query_config")
            if not query_config or not query_config.get("query"):
                raise ValueError("query_config must contain 'query' field")
            total_count = query_config.get("total_count", 0)
            
            if not is_continuation:
                logging.info("Orchestrator started (query mode) for %d documents", total_count)
                if job_id:
                    yield context.call_activity(
                        "UpdateJobStatusActivity",
                        {"job_id": job_id, "status": "Running", "total_documents": total_count}
                    )
            
            # Always query with offset=0 - processed docs exit result set automatically
            doc_ids = yield context.call_activity(
                "QueryDocumentBatchActivity",
                {"query": query_config.get("query"), "parameters": query_config.get("parameters", []),
                 "offset": 0, "limit": batch_size}
            )
            
            # No docs returned = all processed (success + failed = total)
            if not doc_ids:
                if job_id:
                    yield context.call_activity(
                        "UpdateJobStatusActivity",
                        {
                            "job_id": job_id,
                            "status": "Completed",
                            "processed_documents": cumulative_processed,
                            "success_documents": cumulative_success,
                            "failed_documents": cumulative_processed - cumulative_success
                        }
                    )
                batch_num = (cumulative_processed // batch_size) if cumulative_processed > 0 else 0
                return f"Completed: {cumulative_success}/{cumulative_processed} documents in {batch_num} batches"
            
            # Batch number based on cumulative progress (not offset)
            batch_num = (cumulative_processed // batch_size) + 1
            
            result = yield context.call_activity(
                "ProcessDocumentBatchActivity",
                {
                    "document_ids": doc_ids,
                    "operation_type": operation_type,
                    "batch_num": batch_num,
                    "started_by": started_by,
                    "orchestration_instance_id": context.instance_id,
                    "correlation_id": correlation_id,
                }
            )
            
            batch_processed = result.get("processed", 0) if isinstance(result, dict) else len(doc_ids)
            batch_success = result.get("success", 0) if isinstance(result, dict) else 0
            new_cumulative_processed = cumulative_processed + batch_processed
            new_cumulative_success = cumulative_success + batch_success
            
            logging.info("Batch %d: %d/%d successful, cumulative: %d/%d", batch_num, batch_success, batch_processed, new_cumulative_processed, total_count)
            
            if job_id:
                yield context.call_activity(
                    "UpdateJobStatusActivity",
                    {"job_id": job_id, "status": "Running", "processed_documents": new_cumulative_processed,
                     "success_documents": new_cumulative_success, "failed_documents": new_cumulative_processed - new_cumulative_success}
                )
            
            # Continue if there might be more docs (processed < total)
            # Note: we always continue_as_new and let the next query determine if done
            return context.continue_as_new({
                "query_config": query_config, "job_id": job_id, "operation_type": operation_type,
                "batch_size": batch_size, "started_by": started_by,
                "cumulative_processed": new_cumulative_processed, "cumulative_success": new_cumulative_success,
                "original_total": total_count, "_is_continuation": True,
                "correlation_id": correlation_id,
            })
        
        # Handle continuation from previous orchestration (doc_ids mode)
        if is_continuation and isinstance(input_data, dict) and "document_ids" in input_data:
            doc_ids = input_data.get("document_ids")
            logging.info(
                "Orchestrator CONTINUED for %d remaining documents (cumulative: %d/%s, job_id: %s)",
                len(doc_ids), cumulative_processed, original_total or "?", job_id
            )
            
            # Process using sequential batches with continuation state
            result = yield from _process_documents_sequential_batches(
                context, doc_ids, job_id, operation_type, batch_size, started_by,
                cumulative_processed=cumulative_processed,
                cumulative_success=cumulative_success,
                original_total=original_total,
                correlation_id=correlation_id,
            )
            if result:
                return result.get("message", str(result))
            return "Continued processing"
        
        # Handle filter-based query (legacy - loads all IDs at once)
        if isinstance(input_data, dict) and "filter" in input_data:
            filter_data = input_data.get("filter")
            logging.info("Orchestrator started for filter query (job_id: %s, action: %s, started_by: %s)", job_id, action, started_by)

            # Query documents based on filter
            doc_ids = yield context.call_activity("QueryDocumentsActivity", filter_data)

            if not doc_ids:
                if job_id:
                    yield context.call_activity(
                        "UpdateJobStatusActivity",
                        {"job_id": job_id, "status": "Completed", "processed_documents": 0, "total_documents": 0}
                    )
                return "No documents found matching the filter"

            logging.info("Filter query returned %d documents", len(doc_ids))
            
            # Process using sequential batches with parallel docs within each batch
            result = yield from _process_documents_sequential_batches(
                context, doc_ids, job_id, operation_type, batch_size, started_by,
                correlation_id=correlation_id,
            )
            if result:
                return result.get("message", str(result))
            return "Processing started"

        # Handle list of document IDs
        if isinstance(input_data, dict) and "document_ids" in input_data:
            doc_ids = input_data.get("document_ids")
            logging.info("Orchestrator started for %d documents (job_id: %s, started_by: %s)", len(doc_ids), job_id, started_by)

            # Process using sequential batches with parallel docs within each batch
            result = yield from _process_documents_sequential_batches(
                context, doc_ids, job_id, operation_type, batch_size, started_by,
                correlation_id=correlation_id,
            )
            if result:
                return result.get("message", str(result))
            return "Processing started"

        # Handle single document ID
        if isinstance(input_data, dict) and "document_id" in input_data:
            doc_id = input_data.get("document_id")
            operation_type = input_data.get("operation_type", OperationType.BULK)
            logging.info("Orchestrator started for document: %s (operation: %s, started_by: %s)", doc_id, operation_type, started_by)

            result = yield context.call_activity("DataIngestActivity", {
                "doc_id": doc_id,
                "operation_type": operation_type,
                "started_by": started_by
            })
            return result

        # Backward compatibility: assume input is a string document_id
        doc_id = input_data if isinstance(input_data, str) else str(input_data)
        logging.info("Orchestrator started for document: %s", doc_id)

        result = yield context.call_activity("DataIngestActivity", {
            "doc_id": doc_id,
            "operation_type": OperationType.BULK  # Default for backward compatibility
        })
        return result
        
    except Exception as e:
        # Update job status to failed if job_id provided
        if job_id:
            yield context.call_activity(
                "UpdateJobStatusActivity",
                {"job_id": job_id, "status": "Failed", "error_message": str(e)}
            )
        raise


@app.function_name(name="UnpublishDocumentsOrchestrator")
@app.orchestration_trigger(context_name="context")
def unpublish_documents_orchestrator(context: df.DurableOrchestrationContext) -> str:
    """
    Orchestrator for unpublishing documents - sets status to Pending and removes from search index.

    Each document unpublish (in ``_unpublish_single_document_sync``): search delete, Cosmos pending,
    incremental stats. After each ``UnpublishDocumentBatchActivity``: one full statistics rebuild
    then publish-batch prune for succeeded ids (see ``bulk_publish_batch_store`` and
    ``unpublish_statistics_rebuild``).

    Uses sequential batch processing with parallel document processing within each batch.
    
    Input:
    {
        "document_ids": [...],
        "published_by": "user@example.com",
        "published_date": "2025-01-15",
        "started_by": "admin@example.com",
        "batch_size": 100
    }
    """
    input_data = context.get_input()
    
    doc_ids = input_data.get("document_ids", [])
    started_by = input_data.get("started_by")
    batch_size = input_data.get("batch_size", BATCH_SIZE_PER_ACTIVITY)
    published_by = input_data.get("published_by") or ""
    publish_batch_audit_id: Optional[str] = None
    if isinstance(published_by, str) and published_by.startswith("publish-batch:"):
        publish_batch_audit_id = published_by[len("publish-batch:") :].strip() or None

    total_docs = len(doc_ids)
    
    if total_docs == 0:
        logging.info("No documents to unpublish")
        yield context.call_activity(
            "UpdateUnpublishJobStatusActivity",
            {"status": "Completed", "processed_documents": 0}
        )
        return "No documents to unpublish"
    
    # Split into batches
    batches = [doc_ids[i:i + batch_size] for i in range(0, total_docs, batch_size)]
    total_batches = len(batches)
    
    logging.info(
        "Unpublishing %d docs → %d batches of %d (parallel within batch, semaphore=%d)",
        total_docs, total_batches, batch_size, SEMAPHORE_LIMIT
    )

    # Update job with total count
    yield context.call_activity(
        "UpdateUnpublishJobStatusActivity",
        {"status": "Running", "total_documents": total_docs}
    )
    
    total_processed = 0
    total_success = 0
    
    try:
        # Process batches sequentially (each batch processes docs in parallel internally)
        for batch_idx, batch_doc_ids in enumerate(batches):
            batch_num = batch_idx + 1
            
            logging.info(
                "Starting unpublish batch %d/%d with %d documents",
                batch_num, total_batches, len(batch_doc_ids)
            )
            
            # Call activity - it will process documents in parallel with semaphore
            result = yield context.call_activity(
                "UnpublishDocumentBatchActivity",
                {
                    "document_ids": batch_doc_ids,
                    "batch_num": batch_num,
                    "requested_by": started_by
                }
            )
            
            if isinstance(result, dict):
                batch_processed = result.get("processed", 0)
                batch_success = result.get("success", 0)
                total_processed += batch_processed
                total_success += batch_success
                
                logging.info(
                    "Unpublish Batch %d completed - %d/%d successful, total: %d/%d",
                    batch_num, batch_success, batch_processed,
                    total_processed, total_docs
                )
                
                # Update job status after each batch
                yield context.call_activity(
                    "UpdateUnpublishJobStatusActivity",
                    {
                        "status": "Running",
                        "processed_documents": total_processed,
                        "success_documents": total_success,
                        "failed_documents": total_processed - total_success
                    }
                )
        
        # Mark job as completed
        yield context.call_activity(
            "UpdateUnpublishJobStatusActivity",
            {
                "status": "Completed",
                "processed_documents": total_processed,
                "success_documents": total_success,
                "failed_documents": total_processed - total_success
            }
        )
        
        logging.info(
            "Unpublish DONE - %d/%d docs in %d batches",
            total_success, total_processed, total_batches
        )

        failed = total_processed - total_success
        if (
            publish_batch_audit_id
            and total_docs > 0
            and total_success == total_docs
            and failed == 0
        ):
            logging.info(
                "Removing publish batch audit document %s after full unpublish success",
                publish_batch_audit_id,
            )
            yield context.call_activity(
                "DeletePublishBatchAuditActivity",
                {"batch_id": publish_batch_audit_id},
            )

        return f"Unpublished: {total_success}/{total_processed} documents in {total_batches} batches"

    except Exception as e:
        yield context.call_activity(
            "UpdateUnpublishJobStatusActivity",
            {"status": "Failed", "error_message": str(e)}
        )
        raise


@app.function_name(name="DeletePublishBatchAuditActivity")
@app.activity_trigger(input_name="batch_data")
def delete_publish_batch_audit_activity(batch_data: Dict[str, Any]) -> str:
    """
    Delete the Cosmos audit row for a publish batch (publish-batch:{id} unpublish flow only).
    Swallows errors so a delete failure does not fail the whole unpublish orchestration.
    """
    from helper.bulk_publish_batch_store import delete_publish_batch

    if not isinstance(batch_data, dict):
        logging.warning("DeletePublishBatchAuditActivity: invalid payload")
        return "skipped_invalid_payload"
    bid = str(batch_data.get("batch_id") or "").strip()
    if not bid:
        logging.warning("DeletePublishBatchAuditActivity: missing batch_id")
        return "skipped_no_id"
    delete_publish_batch(bid)
    return f"deleted:{bid}"


@app.function_name(name="UnpublishDocumentBatchActivity")
@app.activity_trigger(input_name="batch_data")
def unpublish_document_batch_activity(batch_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Activity function that unpublishes a batch of documents in parallel.
    
    Uses asyncio with a semaphore to process documents concurrently
    while limiting the number of parallel operations (default 20).
    
    For each document (async worker): search delete, Cosmos pending, incremental stats.
    After all workers in the batch finish: one full stats rebuild and publish-batch prune for
    succeeded document ids (see ``_unpublish_single_document_sync``).
    
    Args:
        batch_data: Dictionary containing:
            - document_ids: List of document IDs to unpublish
            - batch_num: Batch number for logging
            - requested_by: (optional) User who requested the unpublish
    
    Returns:
        Dictionary with processed count and success count
    """
    doc_ids = batch_data.get("document_ids", [])
    batch_num = batch_data.get("batch_num", 0)
    requested_by = batch_data.get("requested_by")
    
    logging.info(
        "UnpublishDocumentBatchActivity: Starting batch %d with %d documents (requested_by: %s, semaphore: %d)",
        batch_num, len(doc_ids), requested_by or "bulk_job", SEMAPHORE_LIMIT
    )

    result = asyncio.run(_unpublish_documents_async(doc_ids, batch_num, requested_by))

    # One full stats rebuild + publish-batch prune per activity (not per document) avoids parallel
    # rebuilds when many docs unpublish concurrently inside the batch.
    succeeded_ids = result.get("succeeded_document_ids") or []
    if succeeded_ids:
        try:
            from helper.unpublish_statistics_rebuild import trigger_statistics_rebuild_after_unpublish

            trigger_statistics_rebuild_after_unpublish()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning(
                "UnpublishDocumentBatchActivity: post-batch statistics rebuild failed (batch %d): %s",
                batch_num,
                exc,
            )
        try:
            from helper.bulk_publish_batch_store import (
                find_publish_batch_ids_with_successful_record,
                maybe_delete_publish_batch_if_all_unpublished,
            )

            for did in succeeded_ids:
                for batch_id in find_publish_batch_ids_with_successful_record(did):
                    maybe_delete_publish_batch_if_all_unpublished(batch_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning(
                "UnpublishDocumentBatchActivity: publish-batch prune after batch %d failed: %s",
                batch_num,
                exc,
            )

    return result


async def _unpublish_documents_async(
    doc_ids: List[str],
    batch_num: int,
    requested_by: Optional[str]
) -> Dict[str, Any]:
    """
    Unpublish documents in parallel with semaphore-limited concurrency.

    If any document fails in a pass, failed IDs are retried on subsequent passes (exponential
    backoff between passes) up to ``UNPUBLISH_MAX_RETRY_PASSES`` total passes. This mitigates
    transient failures when batch size is large.
    """
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial

    initial_ids = [str(x).strip() for x in doc_ids if x and str(x).strip()]
    initial_count = len(initial_ids)
    if initial_count == 0:
        return {"processed": 0, "success": 0, "failed": 0, "batch_num": batch_num}

    max_passes = UNPUBLISH_MAX_RETRY_PASSES
    backoff_sec = UNPUBLISH_RETRY_BACKOFF_SEC

    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    executor = ThreadPoolExecutor(max_workers=SEMAPHORE_LIMIT)

    async def unpublish_single_doc(doc_id: str) -> None:
        async with semaphore:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor,
                partial(_unpublish_single_document_sync, doc_id, requested_by),
            )

    async def run_pass(ids: List[str]) -> Tuple[List[str], List[str]]:
        if not ids:
            return [], []
        tasks = [unpublish_single_doc(doc_id) for doc_id in ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        succeeded: List[str] = []
        failed: List[str] = []
        for idx, result in enumerate(results):
            rid = ids[idx]
            if isinstance(result, Exception):
                try:
                    raise result
                except Exception:
                    logging.exception(
                        "UnpublishDocumentBatchActivity: Error unpublishing document %s (batch %d)",
                        rid,
                        batch_num,
                    )
                failed.append(rid)
            else:
                succeeded.append(rid)
        return succeeded, failed

    remaining = list(initial_ids)
    succeeded_all: set[str] = set()

    try:
        for pass_idx in range(max_passes):
            if not remaining:
                break
            if pass_idx > 0:
                delay = min(backoff_sec * (2 ** (pass_idx - 1)), 60.0)
                logging.info(
                    "UnpublishDocumentBatchActivity: batch %d retry pass %d/%d after %.1fs for %d failed document(s)",
                    batch_num,
                    pass_idx + 1,
                    max_passes,
                    delay,
                    len(remaining),
                )
                await asyncio.sleep(delay)
            succeeded, failed = await run_pass(remaining)
            succeeded_all.update(succeeded)
            remaining = failed
    finally:
        executor.shutdown(wait=False)

    failed_final = [x for x in initial_ids if x not in succeeded_all]
    success_count = len(succeeded_all)
    failed_count = len(failed_final)

    logging.info(
        "UnpublishDocumentBatchActivity: Batch %d finished - %d/%d successful after up to %d pass(es) (parallel=%d)",
        batch_num,
        success_count,
        initial_count,
        max_passes,
        SEMAPHORE_LIMIT,
    )
    if failed_count:
        logging.warning(
            "UnpublishDocumentBatchActivity: batch %d still has %d failed document(s) after retries (sample: %s)",
            batch_num,
            failed_count,
            failed_final[:5],
        )

    return {
        "processed": initial_count,
        "success": success_count,
        "failed": failed_count,
        "batch_num": batch_num,
        "succeeded_document_ids": sorted(succeeded_all),
    }


def _unpublish_single_document_sync(doc_id: str, requested_by: Optional[str]) -> None:
    """
    Synchronous helper to unpublish a single document.
    
    Args:
        doc_id: Document ID to unpublish
        requested_by: User who requested the unpublish
    """
    import copy
    from helper.config import get_indexing_config
    from helper.search_document_util import SearchDocumentReader
    from helper.statistics_helper import get_statistics_helper
    from azure.search.documents import SearchClient
    from helper.credential import get_search_credential
    
    # Initialize clients
    config = CosmosDBConfig.from_env()
    cosmos_client = CosmosDBClient(config)
    index_config = get_indexing_config()
    
    # Step 1: Delete from search index FIRST
    if index_config.search_endpoint:
        search_cred = get_search_credential()
        # Get all chunk IDs for this document
        doc_reader = SearchDocumentReader(
            endpoint=index_config.search_endpoint,
            index_name=index_config.search_index,
        )
        chunk_ids = doc_reader.get_all_chunks_for_document(doc_id)
        
        if chunk_ids:
            search_client = SearchClient(
                endpoint=index_config.search_endpoint,
                index_name=index_config.search_index,
                credential=search_cred
            )

            delete_batch_size = 500
            for i in range(0, len(chunk_ids), delete_batch_size):
                delete_batch = [{"id": cid} for cid in chunk_ids[i : i + delete_batch_size]]
                delete_results = search_client.delete_documents(documents=delete_batch)
                failed_keys: List[str] = []
                if delete_results is None:
                    dr_iter = []
                elif isinstance(delete_results, (list, tuple)):
                    dr_iter = delete_results
                else:
                    dr_iter = list(delete_results)
                for dr in dr_iter:
                    if not getattr(dr, "succeeded", True):
                        failed_keys.append(
                            str(getattr(dr, "key", None) or getattr(dr, "id", None) or "?")
                        )
                if failed_keys:
                    raise RuntimeError(
                        f"Azure Search delete failed for {len(failed_keys)} chunk(s) on document {doc_id}: "
                        f"{failed_keys[:25]}"
                    )

            logging.info(
                "Deleted %d chunks from search index for document %s",
                len(chunk_ids),
                doc_id,
            )
        else:
            logging.warning(
                "Unpublish: no search chunks listed for document %s — if this document was indexed, "
                "verify AZURE_SEARCH_INDEX matches ingest and chunk keys (sanitized id + _chunk_ / record_id).",
                doc_id,
            )
    
    # Step 2: Update document status to Pending in CosmosDB
    document = cosmos_client.get_document(doc_id)
    if document:
        # Capture old document state before modifying (for statistics update)
        old_document = copy.deepcopy(document)
        old_status = document.get("archivist_status")
        
        now = datetime.now(timezone.utc).isoformat()
        document["archivist_status"] = "pending"
        document["updated_at"] = now
        # Record who unpublished and when
        document["unpublished_by"] = requested_by
        document["unpublished_at"] = now
        # Clear publish fields
        document["published_by"] = None
        document["published_at"] = None
        
        # pylint: disable=protected-access
        execute_with_retry(
            cosmos_client._container.upsert_item,
            operation_name=f"unpublish_upsert({doc_id})",
            body=document
        )
        # pylint: enable=protected-access
        logging.info(
            "Document %s status changed from %s to Pending by %s",
            doc_id, old_status, requested_by or "bulk_job"
        )
        
        # Update statistics: track transition from old status to pending
        try:
            stats_helper = get_statistics_helper()
            stats_helper.update_on_document_change(
                old_doc=old_document,
                new_doc=document
            )
            logging.info("Updated statistics for unpublished document %s", doc_id)
        except Exception as stats_error:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Failed to update statistics for document %s: %s",
                doc_id, str(stats_error)
            )


@app.function_name(name="UpdateUnpublishJobStatusActivity")
@app.activity_trigger(input_name="job_data")
def update_unpublish_job_status_activity(job_data: Dict[str, Any]) -> str:
    """
    Activity function that updates the status of an unpublish job in Cosmos DB.

    Args:
        job_data: Dictionary containing:
            - status: New status
            - processed_documents: Number of documents processed (optional)
            - success_documents: Number successful (optional)
            - failed_documents: Number failed (optional)
            - total_documents: Total documents (optional)
            - error_message: Error message if failed (optional)

    Returns:
        Status message
    """
    status = job_data.get("status")
    processed_documents = job_data.get("processed_documents")
    success_documents = job_data.get("success_documents")
    failed_documents = job_data.get("failed_documents")
    error_message = job_data.get("error_message")
    
    logging.info(
        "Updating unpublish job status: status=%s, processed=%s, success=%s, failed=%s",
        status, processed_documents, success_documents, failed_documents
    )
    
    update_unpublish_job(
        status=status,
        processed_documents=processed_documents,
        success_documents=success_documents,
        failed_documents=failed_documents,
        error_message=error_message
    )
    
    return f"Updated unpublish job to status {status}"


@app.function_name(name="UnpublishSingleDocumentOrchestrator")
@app.orchestration_trigger(context_name="context")
def unpublish_single_document_orchestrator(context: df.DurableOrchestrationContext) -> str:
    """
    Simple orchestrator for unpublishing a single document.
    
    Called via Service Bus with action="unpublish" and document_id.
    Reuses UnpublishDocumentBatchActivity with a single-item list.
    
    Input:
    {
        "document_id": "uuid-of-document",
        "requested_by": "user@example.com"
    }
    """
    input_data = context.get_input()
    doc_id = input_data.get("document_id")
    requested_by = input_data.get("requested_by", "unknown")
    
    if not doc_id:
        return "Error: document_id is required"
    
    logging.info(
        "UnpublishSingleDocumentOrchestrator: Processing document %s requested by %s",
        doc_id,
        requested_by
    )

    # Reuse batch activity with single document
    result = yield context.call_activity(
        "UnpublishDocumentBatchActivity",
        {
            "document_ids": [doc_id],
            "batch_num": 1,
            "requested_by": requested_by
        }
    )
    
    # Format result for single document
    if isinstance(result, dict):
        if result.get("success", 0) > 0:
            return f"Successfully unpublished document {doc_id} (requested by {requested_by})"
        else:
            return f"Failed to unpublish document {doc_id} (requested by {requested_by})"
    
    return str(result)


@app.function_name(name="QueryDocumentsActivity")
@app.activity_trigger(input_name="filter_data")
def query_documents_activity(filter_data: Dict[str, Any]) -> List[str]:
    """
    Activity function that queries CosmosDB for documents matching the filter.

    Args:
        filter_data: Dictionary containing 'query' (SQL query string) and 
                     optional 'parameters' (list of parameter dicts)

    Returns:
        List of document IDs matching the filter
    """
    logging.info("QueryDocumentsActivity started with filter: %s", filter_data)

    try:
        query = filter_data.get("query")
        if not query:
            raise ValueError("Filter must contain a 'query' field")

        parameters = filter_data.get("parameters", [])

        # Initialize CosmosDB client
        config = CosmosDBConfig.from_env()
        logging.info(
            "QueryDocumentsActivity using database='%s', container='%s'",
            config.database_name,
            config.container_name
        )
        cosmos_client = CosmosDBClient(config)

        # Query document IDs using CosmosDB client method
        doc_ids = cosmos_client.query_document_ids(query=query, parameters=parameters)

        logging.info("Query returned %d document IDs", len(doc_ids))
        return doc_ids

    except Exception as e:
        logging.exception("Error querying documents: %s", str(e))
        raise


@app.function_name(name="QueryDocumentBatchActivity")
@app.activity_trigger(input_name="batch_params")
def query_document_batch_activity(batch_params: Dict[str, Any]) -> List[str]:
    """
    Activity function that queries a batch of document IDs with pagination.
    
    Uses OFFSET/LIMIT for memory-efficient batch fetching from CosmosDB.
    This is more efficient than loading all IDs at once for large datasets.
    
    Args:
        batch_params: Dictionary containing:
            - query: Base SQL query (should select c.id)
            - parameters: Query parameters
            - offset: Number of records to skip
            - limit: Maximum records to return
    
    Returns:
        List of document IDs for this batch
    """
    query = batch_params.get("query", "")
    parameters = batch_params.get("parameters", [])
    offset = batch_params.get("offset", 0)
    limit = batch_params.get("limit", 100)
    
    logging.info(
        "QueryDocumentBatchActivity: query batch offset=%d, limit=%d",
        offset, limit
    )
    
    try:
        # Initialize CosmosDB client
        config = CosmosDBConfig.from_env()
        cosmos_client = CosmosDBClient(config)
        
        # Use the dedicated paginated method
        doc_ids = cosmos_client.query_document_ids_paginated(
            query=query,
            parameters=parameters,
            offset=offset,
            limit=limit
        )
        
        logging.info(
            "QueryDocumentBatchActivity: Retrieved %d document IDs (offset=%d, limit=%d)",
            len(doc_ids), offset, limit
        )
        return doc_ids
        
    except Exception as e:
        logging.exception("Error querying document batch: %s", str(e))
        raise


def _is_job_stopped(job_id: str = BULK_INGESTION_JOB_ID) -> bool:
    """Check if the job has been terminated. Called from activity functions for graceful stopping."""
    try:
        job = job_manager.get_job(JobType.BULK_INGESTION)
        if not job:
            return False
        return job.get("status") == JobStatus.TERMINATED.value
    except Exception as e:
        logging.warning("Error checking job status: %s - continuing", str(e))
        return False


@app.function_name(name="ProcessDocumentBatchActivity")
@app.activity_trigger(input_name="batch_data")
def process_document_batch_activity(batch_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Activity function that processes a batch of documents in parallel.
    
    Uses asyncio with a semaphore to process documents concurrently
    while limiting the number of parallel operations (default 20).
    
    Checks job status before processing to allow graceful stopping even
    after the orchestrator has been terminated.
    
    Args:
        batch_data: Dictionary containing:
            - document_ids: List of document IDs to process
            - operation_type: Type of operation (bulk, reindex, individual)
            - batch_num: Batch number for logging
            - started_by: User who initiated the operation (optional)
    
    Returns:
        Dictionary with processed count, success count, and stopped flag
    """
    from helper.bulk_publish_batch_store import record_publish_batch, should_record_batch

    doc_ids = batch_data.get("document_ids", [])
    operation_type = batch_data.get("operation_type", OperationType.BULK)
    batch_num = batch_data.get("batch_num", 0)
    started_by = batch_data.get("started_by")
    orch_id = batch_data.get("orchestration_instance_id")
    correlation_id = batch_data.get("correlation_id")

    logging.info(
        "ProcessDocumentBatchActivity: Starting batch %d with %d documents (operation: %s, started_by: %s, semaphore: %d)",
        batch_num, len(doc_ids), operation_type, started_by or "unknown", SEMAPHORE_LIMIT
    )

    def _persist_batch(
        *,
        successful_record_ids: List[str],
        failed_records: List[Dict[str, Any]],
        successful_record_details: List[Dict[str, Any]],
        repository: str = "",
        collection: str = "",
        skipped: bool,
    ) -> None:
        if not should_record_batch(operation_type) or not orch_id:
            return
        # One Cosmos audit document per orchestration; each activity chunk merges into it.
        batch_doc_id = str(orch_id).strip()
        record_publish_batch(
            batch_doc_id=batch_doc_id,
            orchestration_instance_id=batch_doc_id,
            batch_num=1,
            operation_type=str(operation_type),
            started_by=started_by,
            document_ids=list(doc_ids),
            successful_record_ids=successful_record_ids,
            failed_records=failed_records,
            successful_record_details=successful_record_details,
            repository=repository or None,
            collection=collection or None,
            correlation_id=correlation_id,
            skipped=skipped,
        )

    # Check job status BEFORE processing - this works even after orchestrator is terminated
    if _is_job_stopped():
        logging.info(
            "ProcessDocumentBatchActivity: Batch %d skipped - job has been stopped",
            batch_num
        )
        _persist_batch(
            successful_record_ids=[],
            failed_records=[],
            successful_record_details=[],
            repository="",
            collection="",
            skipped=True,
        )
        return {
            "processed": 0,
            "success": 0,
            "batch_num": batch_num,
            "stopped": True,
            "skipped": len(doc_ids),
            "successful_record_ids": [],
            "successful_record_details": [],
            "failed_records": [],
        }

    # Run async processing
    out = asyncio.run(_process_documents_async(doc_ids, operation_type, batch_num, started_by))
    if isinstance(out, dict):
        _persist_batch(
            successful_record_ids=out.get("successful_record_ids") or [],
            failed_records=out.get("failed_records") or [],
            successful_record_details=out.get("successful_record_details") or [],
            repository=str(out.get("batch_repository") or ""),
            collection=str(out.get("batch_collection") or ""),
            skipped=False,
        )
    return out


async def _process_documents_async(
    doc_ids: List[str],
    operation_type: str,
    batch_num: int,
    started_by: Optional[str]
) -> Dict[str, Any]:
    """
    Process documents in parallel with semaphore-limited concurrency.
    
    Uses return_exceptions=True pattern for maximum parallelism - no lock
    contention during processing, results counted after all tasks complete.
    """
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial
    
    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    executor = ThreadPoolExecutor(max_workers=SEMAPHORE_LIMIT)
    
    async def process_single_doc(doc_id: str) -> bool:
        """Process a single document. Returns True on success, raises on failure."""
        async with semaphore:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor,
                partial(process_cosmos, doc_id=doc_id, operation_type=operation_type, started_by=started_by)
            )
            return True
    
    try:
        tasks = [process_single_doc(doc_id) for doc_id in doc_ids]
        # return_exceptions=True: all tasks run to completion, exceptions collected as results
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        executor.shutdown(wait=False)
    
    # Count results after all tasks complete (no lock contention during processing)
    successful_record_ids: List[str] = []
    failed_records: List[Dict[str, Any]] = []
    for idx, result in enumerate(results):
        doc_id = doc_ids[idx]
        if isinstance(result, Exception):
            try:
                raise result
            except Exception:
                logging.exception(
                    "ProcessDocumentBatchActivity: Error processing document %s",
                    doc_id
                )
            snap = get_publish_batch_document_snapshot(doc_id)
            failed_records.append(
                {
                    "record_id": doc_id,
                    "error": str(result)[:2000],
                    "title": snap.get("title") or "",
                    "repository": snap.get("repository") or "",
                    "collection": snap.get("collection") or "",
                }
            )
        else:
            successful_record_ids.append(doc_id)

    # Batch-level repository/collection (same for all docs in a bulk publish batch); per row: record_id + title only.
    batch_repository = ""
    batch_collection = ""
    successful_record_details: List[Dict[str, Any]] = []
    for doc_id in successful_record_ids:
        snap = get_publish_batch_document_snapshot(doc_id)
        if not batch_repository and (snap.get("repository") or "").strip():
            batch_repository = (snap.get("repository") or "").strip()
        if not batch_collection and (snap.get("collection") or "").strip():
            batch_collection = (snap.get("collection") or "").strip()
        successful_record_details.append(
            {
                "record_id": doc_id,
                "title": (snap.get("title") or "").strip(),
            }
        )

    success_count = len(successful_record_ids)
    failed_count = len(failed_records)

    logging.info(
        "ProcessDocumentBatchActivity: Batch %d completed - %d/%d successful (parallel=%d)",
        batch_num, success_count, len(doc_ids), SEMAPHORE_LIMIT
    )

    return {
        "processed": len(doc_ids),
        "success": success_count,
        "failed": failed_count,
        "batch_num": batch_num,
        "stopped": False,
        "successful_record_ids": successful_record_ids,
        "successful_record_details": successful_record_details,
        "failed_records": failed_records,
        "batch_repository": batch_repository,
        "batch_collection": batch_collection,
    }


@app.function_name(name="DataIngestActivity")
@app.activity_trigger(input_name="activity_input")
def data_ingest_activity(activity_input: dict) -> str:
    """
    Activity function that performs the actual CosmosDB document ingestion.

    Args:
        activity_input: Dictionary containing:
            - doc_id: The document ID to process from CosmosDB
            - operation_type: Type of operation (bulk, individual, reindex)
            - started_by: User who initiated the operation (optional)

    Returns:
        Success message
    """
    doc_id = activity_input["doc_id"]
    operation_type = activity_input.get("operation_type", "bulk")
    started_by = activity_input.get("started_by")
    logging.info("Activity function started for document: %s (operation: %s, started_by: %s)", doc_id, operation_type, started_by or "unknown")
    process_cosmos(doc_id=doc_id, operation_type=operation_type, started_by=started_by)

    return f"Successfully processed document: {doc_id}"


@app.function_name(name="UpdateJobStatusActivity")
@app.activity_trigger(input_name="job_data")
def update_job_status_activity(job_data: Dict[str, Any]) -> str:
    """
    Activity function that updates the status of an ingestion job in Cosmos DB.
    
    All ingestion operations (bulk, retry, reindex) share the same job record.

    Args:
        job_data: Dictionary containing:
            - job_id: The job ID to update (always bulk_ingestion)
            - status: New status (Running, Completed, Failed, Terminated, Suspended)
            - processed_documents: Number of documents processed (optional)
            - success_documents: Number of documents successfully processed (optional)
            - failed_documents: Number of documents that failed (optional)
            - total_documents: Total documents to process (optional)
            - error_message: Error message if failed (optional)

    Returns:
        Status message
    """
    job_id = job_data.get("job_id", BULK_INGESTION_JOB_ID)
    status = job_data.get("status")
    processed_documents = job_data.get("processed_documents")
    success_documents = job_data.get("success_documents")
    failed_documents = job_data.get("failed_documents")
    total_documents = job_data.get("total_documents")
    error_message = job_data.get("error_message")
    
    logging.info(
        "Updating job status: job_id=%s, status=%s, processed=%s, success=%s, failed=%s",
        job_id, status, processed_documents, success_documents, failed_documents
    )
    
    # All ingestion operations share the same job record
    update_bulk_ingestion_job(
        status=status,
        processed_documents=processed_documents,
        success_documents=success_documents,
        failed_documents=failed_documents,
        total_documents=total_documents,
        error_message=error_message
    )
    
    return f"Updated job {job_id} to status {status}"


@app.function_name(name="EpubProcessing")
@app.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="%EPUB_QUEUE_NAME%",
    connection="ServiceBusConnection"
)
def epub_processing_trigger(msg: func.ServiceBusMessage) -> None:
    """
    Service Bus trigger for EPUB processing.

    Expected message format:
    {
        "document_id": "uuid",
        "action": "parse" | "extract" | "ingest" | "delete"
    }

    Flow:
    1. parse - Extract TOC structure from EPUB (uploaded -> parsed)
    2. extract - Extract original + filtered text for selected sections (parsed -> validate)
    3. ingest - Generate embeddings and index filtered text (processing -> completed)
    4. delete - Delete document from search index, CosmosDB, and blob storage
    """
    logging.info("EPUB processing function triggered")

    try:
        body = msg.get_body().decode("utf-8")
        payload = json.loads(body)
        logging.info("EPUB message received: %s", payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logging.exception("Invalid EPUB message format: %s", str(e))
        return

    document_id = payload.get("document_id")
    action = payload.get("action")

    if not document_id:
        logging.error("Missing document_id in EPUB message")
        return

    if not action:
        logging.error("Missing action in EPUB message")
        return

    try:
        if action == "parse":
            logging.info("Starting EPUB parsing for document: %s", document_id)
            process_epub_parse(document_id)
            logging.info("Completed EPUB parsing for document: %s", document_id)
        elif action == "extract":
            enable_ocr = payload.get("enable_ocr", False)
            logging.info("Starting EPUB text extraction for document: %s (OCR: %s)", document_id, enable_ocr)
            process_epub_extract(document_id, enable_ocr=enable_ocr)
            logging.info("Completed EPUB text extraction for document: %s", document_id)
        elif action == "ingest":
            logging.info("Starting EPUB ingestion for document: %s", document_id)
            process_epub_ingest(document_id)
            logging.info("Completed EPUB ingestion for document: %s", document_id)
        elif action == "delete":
            logging.info("Starting EPUB deletion for document: %s", document_id)
            process_epub_delete(document_id)
            logging.info("Completed EPUB deletion for document: %s", document_id)
        else:
            logging.error("Unknown action '%s' for EPUB document: %s", action, document_id)
    except Exception as e:
        logging.exception("Error processing EPUB document %s: %s", document_id, str(e))


@app.function_name(name="ReconcileStuckPublishingTimer")
@app.timer_trigger(
    schedule="0 */20 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False,
)
def reconcile_stuck_publishing_timer(timer: func.TimerRequest) -> None:
    """
    Periodically move documents stuck in publishing back to reviewed (no failure message).

    Configure with environment variables:
    - STUCK_PUBLISHING_RECONCILE_ENABLED: true/false (default true)
    - STUCK_PUBLISHING_MINUTES: threshold in minutes (default 20)
    """
    if timer.past_due:
        logging.info("ReconcileStuckPublishingTimer is past due, running now")
    try:
        minutes = int(os.environ.get("STUCK_PUBLISHING_MINUTES", "20"))
        from helper.publishing_reconcile import reconcile_stuck_publishing

        result = reconcile_stuck_publishing(
            threshold_minutes=minutes,
            action="reviewed",
            reason_suffix="",
        )
        if not result.get("skipped"):
            logging.info("ReconcileStuckPublishingTimer: %s", result)
    except Exception:
        logging.exception("ReconcileStuckPublishingTimer failed")


# ---------------------------------------------------------------------------
# Digital Items publish/unpublish — HTTP triggers live in a separate Blueprint
# so existing function code above is not modified.
# ---------------------------------------------------------------------------
from digital_items_blueprint import blueprint as _digital_items_blueprint  # noqa: E402
app.register_blueprint(_digital_items_blueprint)
