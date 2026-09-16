# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration settings for the Archivist API."""
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv('.env.local')


def _cosmos_container_name(db_key: str, alt_key: str, default: str) -> str:
    """Prefer COSMOS_DB_* (shared with functions); allow COSMOS_* alias without _DB_."""
    v = os.getenv(db_key)
    if v is not None and str(v).strip():
        return str(v).strip()
    v = os.getenv(alt_key)
    if v is not None and str(v).strip():
        return str(v).strip()
    return default


class Settings:
    """Application settings."""

    def __init__(self):
        self.app_name: str = "Archivist API"
        self.debug: bool = True
        self.environment: str = os.getenv("ENVIRONMENT", "production").strip().lower()

        # CosmosDB Configuration (RBAC + managed identity in Azure; use az login locally)
        self.cosmos_db_endpoint: Optional[str] = os.getenv('COSMOS_DB_ENDPOINT')
        # Optional key fallback for local/dev compatibility. In Azure prod this is typically unset with MI/RBAC.
        self.cosmos_db_key: Optional[str] = os.getenv('COSMOS_DB_KEY')
        self.cosmos_db_database_name: Optional[str] = os.getenv('COSMOS_DB_DATABASE_NAME')
        # OCR Container (main container for documents)
        # DataFoundations names this container 'recordsmetadata'
        self.cosmos_db_container_name: Optional[str] = os.getenv(
            'COSMOS_DB_CONTAINER_NAME', 'recordsmetadata')
        # Audit Container (for change history tracking)
        # DataFoundations names this container 'ocrmetadataaudit'
        self.cosmos_db_audit_container_name: Optional[str] = os.getenv(
            'COSMOS_DB_AUDIT_CONTAINER_NAME', 'ocrmetadataaudit')
        # Statistics Container (for pre-computed aggregates)
        self.cosmos_db_stats_container_name: Optional[str] = os.getenv(
            'COSMOS_DB_STATS_CONTAINER_NAME', 'statistics')
        cr_container = (
            os.getenv('COSMOS_DB_CORRECTION_REQUESTS_CONTAINER_NAME', '').strip()
            or os.getenv('COSMOS_CORRECTION_REQUESTS_CONTAINER_NAME', '').strip()
            or 'correction-requests'
        )
        self.cosmos_db_correction_requests_container_name: str = cr_container
        self.cosmos_db_bulk_publish_batches_container_name: Optional[str] = _cosmos_container_name(
            'COSMOS_DB_BULK_PUBLISH_BATCHES_CONTAINER_NAME',
            'COSMOS_BULK_PUBLISH_BATCHES_CONTAINER_NAME',
            'bulk-publish-batches',
        )
        self.cosmos_db_periodic_run_history_container_name: Optional[str] = _cosmos_container_name(
            'COSMOS_DB_PERIODIC_RUN_HISTORY_CONTAINER_NAME',
            'COSMOS_PERIODIC_RUN_HISTORY_CONTAINER_NAME',
            'periodic_run_history',
        )
        self.cosmos_db_connection_mode: str = os.getenv(
            'COSMOS_DB_CONNECTION_MODE', 'Gateway')
        # Use pre-computed statistics for dashboard (recommended)
        self.use_precomputed_stats: bool = os.getenv(
            'USE_PRECOMPUTED_STATS', 'true').lower() == 'true'

        # DF storage: content-assets read/SAS. Archivist storage: epub-files writes.
        self.azure_storage_account_name: Optional[str] = os.getenv(
            'AZURE_STORAGE_ACCOUNT_NAME')
        self.archivist_storage_account_name: Optional[str] = (
            os.getenv('ARCHIVIST_STORAGE_ACCOUNT_NAME', '').strip()
            or self.azure_storage_account_name
        )
        self.azure_storage_connection_string: Optional[str] = os.getenv(
            'AZURE_STORAGE_CONNECTION_STRING'
        )
        self.sas_token_expiry_hours: int = int(os.getenv('SAS_TOKEN_EXPIRY_HOURS', '24'))
        self.data_ingestion_queue_name: str = os.getenv(
            'DATA_INGESTION_QUEUE_NAME', 'data-ingestion-queue')
        self.service_bus_fully_qualified_namespace: Optional[str] = os.getenv(
            'AZURE_SERVICEBUS_FULLY_QUALIFIED_NAMESPACE')
        self.service_bus_connection_string: Optional[str] = os.getenv(
            'AZURE_SERVICEBUS_CONNECTION_STRING')

        # EPUB Configuration
        # Single storage container for all EPUB files (organized by document_id folders)
        self.epub_storage_container_name: str = os.getenv(
            'EPUB_STORAGE_CONTAINER_NAME', 'epub-files')
        self.epub_cosmos_container_name: str = os.getenv('EPUB_COSMOS_CONTAINER', 'epub')
        self.epub_chunks_container_name: str = os.getenv('EPUB_CHUNKS_CONTAINER', 'epub-chunks')
        self.epub_queue_name: str = os.getenv('EPUB_QUEUE_NAME', 'epub-processing-queue')

        # Azure Application Insights Configuration
        self.applicationinsights_connection_string: Optional[str] = os.getenv(
            'APPLICATIONINSIGHTS_CONNECTION_STRING')
        self.app_insights_log_level: str = os.getenv('APP_INSIGHTS_LOG_LEVEL', 'INFO')

        # Azure Functions Pipeline Configuration (data pipeline stages)
        self.azure_functions_base_url: Optional[str] = os.getenv('AZURE_FUNCTIONS_BASE_URL')
        self.azure_functions_code: Optional[str] = os.getenv('AZURE_FUNCTIONS_CODE')
        self.azure_functions_task_hub: str = os.getenv('AZURE_FUNCTIONS_TASK_HUB', 'BatchOrchestrationHub')

        # Azure Functions - Data Ingestion (separate function app for reindex, bulk ingestion, retry failed)
        self.data_ingest_function_url: Optional[str] = os.getenv('DATA_INGEST_FUNCTION_URL')
        self.data_ingest_function_code: Optional[str] = os.getenv('DATA_INGEST_FUNCTION_CODE')
        self.data_ingest_task_hub: str = os.getenv('DATA_INGEST_TASK_HUB', 'DurableIngestTaskHub')

        # Correction request intake (POST /correction-requests) — Entra workload JWT or local key
        self.correction_intake_local_enabled: bool = (
            os.getenv('CORRECTION_INTAKE_LOCAL_ENABLED', 'false').lower() == 'true'
        )
        self.correction_intake_local_key: Optional[str] = os.getenv('CORRECTION_INTAKE_LOCAL_KEY')
        self.correction_intake_local_source_app_id: str = os.getenv(
            'CORRECTION_INTAKE_LOCAL_SOURCE_APP_ID', 'local-test'
        )
        self.correction_intake_local_source_display_name: str = os.getenv(
            'CORRECTION_INTAKE_LOCAL_SOURCE_DISPLAY_NAME', 'Local test'
        )
        self.correction_intake_tenant_id: Optional[str] = os.getenv('CORRECTION_INTAKE_TENANT_ID')
        self.correction_intake_audience: Optional[str] = os.getenv('CORRECTION_INTAKE_AUDIENCE')
        self.correction_intake_issuer: Optional[str] = os.getenv('CORRECTION_INTAKE_ISSUER')
        raw_map = os.getenv('CORRECTION_INTAKE_SOURCE_MAP', '{}').strip()
        parsed: Dict[str, Any] = {}
        if raw_map:
            try:
                loaded = json.loads(raw_map)
                if isinstance(loaded, dict):
                    parsed = {str(k).strip().lower(): v for k, v in loaded.items()}
            except json.JSONDecodeError:
                parsed = {}
        self.correction_intake_source_map: Dict[str, Any] = parsed
        # Intake note max length (env clamped to 2000–5000; default 4000).
        _note_max = int(os.getenv('CORRECTION_NOTE_MAX_LENGTH', '4000'))
        self.correction_note_max_length: int = max(2000, min(5000, _note_max))
        # When true, POST intake rejects if record_id is not in OCR/document Cosmos.
        self.correction_intake_require_record_exists: bool = (
            os.getenv('CORRECTION_INTAKE_REQUIRE_RECORD_EXISTS', 'true').lower() == 'true'
        )
        # Max JSON body size for correction intake (Content-Length pre-check).
        self.correction_intake_max_body_bytes: int = int(
            os.getenv('CORRECTION_INTAKE_MAX_BODY_BYTES', str(64 * 1024))
        )
        # Hourly POST rate limit per caller app id (Cosmos docs in correction-requests, doc_kind=intake_rate_limit).
        self.correction_intake_rate_limit_enabled: bool = (
            os.getenv('CORRECTION_INTAKE_RATE_LIMIT_ENABLED', 'false').lower() == 'true'
        )
        self.correction_intake_rate_limit_max_per_hour: int = int(
            os.getenv('CORRECTION_INTAKE_RATE_LIMIT_MAX_PER_HOUR', '100')
        )

        # Azure AI Foundry (GPT-4o OCR)
        self.azure_ai_foundry_endpoint: Optional[str] = os.getenv('AZURE_AI_FOUNDRY_ENDPOINT')
        self.azure_ai_foundry_model_name: str = os.getenv('AZURE_AI_FOUNDRY_MODEL_NAME', 'gpt-4.1')
        self.azure_ai_foundry_api_version: str = os.getenv('AZURE_AI_FOUNDRY_API_VERSION', '2024-06-01')
        self.azure_ai_foundry_max_tokens: int = int(os.getenv('AZURE_AI_FOUNDRY_MAX_TOKENS', '16000'))
        self.azure_ai_foundry_temperature: float = float(os.getenv('AZURE_AI_FOUNDRY_TEMPERATURE', '0.1'))
        self.azure_ai_foundry_timeout: int = int(os.getenv('AZURE_AI_FOUNDRY_TIMEOUT', '120'))

        # Digital Items container (Moore Chronology, Cyclopedia, Genealogy)
        self.cosmos_db_digital_items_endpoint: Optional[str] = (
            os.getenv('COSMOS_DB_DIGITAL_ITEMS_ENDPOINT')
            or os.getenv('COSMOS_DIGITAL_ITEMS_ENDPOINT')
            or self.cosmos_db_endpoint
        )
        self.cosmos_db_digital_items_database: str = (
            os.getenv('COSMOS_DB_DIGITAL_ITEMS_DATABASE')
            or os.getenv('COSMOS_DIGITAL_ITEMS_DATABASE')
            or self.cosmos_db_database_name
            or 'contentdb'
        )
        self.cosmos_db_digital_items_container: str = _cosmos_container_name(
            'COSMOS_DB_DIGITAL_ITEMS_CONTAINER',
            'COSMOS_DIGITAL_ITEMS_CONTAINER_NAME',
            'digital-items',
        )
        self.cosmos_db_digital_items_audit_container: str = _cosmos_container_name(
            'COSMOS_DB_DIGITAL_ITEMS_AUDIT_CONTAINER',
            'COSMOS_DIGITAL_ITEMS_AUDIT_CONTAINER_NAME',
            'digital-items-audit',
        )


settings = Settings()
