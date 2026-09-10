"""Configuration for the Content export API."""
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
load_dotenv(".env.local")


class Settings:
    """Application settings loaded from environment."""

    def __init__(self) -> None:
        self.environment: str = os.getenv("ENVIRONMENT", "production").lower()

        self.cosmos_db_endpoint: Optional[str] = os.getenv("COSMOS_DB_ENDPOINT")
        if not self.cosmos_db_endpoint and os.getenv("COSMOS_DB_ACCOUNT_NAME"):
            account = os.getenv("COSMOS_DB_ACCOUNT_NAME", "").strip()
            if account:
                self.cosmos_db_endpoint = f"https://{account}.documents.azure.com:443/"

        self.cosmos_db_database_name: str = os.getenv("COSMOS_DB_DATABASE_NAME", "contentdb")
        self.cosmos_db_container_name: str = os.getenv("COSMOS_DB_CONTAINER_NAME", "recordsmetadata")
        self.cosmos_db_connection_string: Optional[str] = os.getenv(
            "COSMOS_DB_CONNECTION_STRING"
        )

        self.outbound_api_key: Optional[str] = os.getenv("OUTBOUND_API_KEY")
        self.outbound_caller_org_id: str = os.getenv(
            "OUTBOUND_CALLER_ORG_ID",
            "external-client",
        )
        self.outbound_caller_display_name: str = os.getenv(
            "OUTBOUND_CALLER_DISPLAY_NAME",
            "External client",
        )

        # Optional; used by azure_credential.py for local multi-tenant az login.
        self.entra_tenant_id: Optional[str] = os.getenv("ENTRA_TENANT_ID")

        self.applicationinsights_connection_string: Optional[str] = os.getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING"
        )
        self.app_insights_log_level: str = os.getenv("APP_INSIGHTS_LOG_LEVEL", "INFO")

        self.list_default_limit: int = int(os.getenv("OUTBOUND_LIST_DEFAULT_LIMIT", "50"))
        self.list_max_limit: int = int(os.getenv("OUTBOUND_LIST_MAX_LIMIT", "200"))

        # Approved-records semantics.
        self.max_date_range_days: int = int(os.getenv("OUTBOUND_MAX_DATE_RANGE_DAYS", "90"))
        self.sas_expiry_hours: int = int(os.getenv("OUTBOUND_SAS_EXPIRY_HOURS", "24"))

        # Blob storage used for content hydration (inline reads / reference SAS URLs).
        self.storage_account_name: Optional[str] = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        self.storage_container_name: Optional[str] = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        self.storage_connection_string: Optional[str] = os.getenv(
            "AZURE_STORAGE_CONNECTION_STRING"
        )
        self.storage_blob_endpoint: Optional[str] = os.getenv(
            "AZURE_STORAGE_BLOB_ENDPOINT"
        )


# Archivist status that means "approved" for this API. Approval == published
# (archivist sign-off completed AND indexed). The transient 'publishing' state is
# excluded because it has no stable, queryable approval timestamp.
APPROVED_STATUSES = ("published",)
# Statuses a record reverts to when approval is revoked.
UNAPPROVED_STATUSES = ("pending", "reviewed")

# The only metadata fields returned by the API: source-system fields editable by
# archivists. Keyed by display name; source_key is resolved per-record via metadata_key.
EDITABLE_METADATA_FIELDS = (
    "Title",
    "Description",
    "Creator",
    "Recipient",
    "Creation Date",
    "Resource Type",
    "Period",
    "Production Method",
)


settings = Settings()
