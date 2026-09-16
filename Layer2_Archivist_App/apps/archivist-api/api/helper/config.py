# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Configuration management for Azure AI Foundry OCR and Entity Extraction utility.
"""

import os
from typing import Optional, List
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class AzureConfig:
    """Configuration for Azure AI Foundry services.

    Authentication is performed via :func:`helper.credential.get_credential` /
    :func:`helper.credential.get_cognitive_services_token_provider`, so no API
    key is stored.
    """

    endpoint: str
    api_version: str = "2024-06-01"
    model_name: str = "gpt-4o"
    max_tokens: int = 16000  # Increased from 4000 to handle long OCR responses
    temperature: float = 0.1
    timeout: int = 120  # Increased timeout for longer responses

    @classmethod
    def from_env(cls) -> "AzureConfig":
        """Create configuration from environment variables."""
        endpoint = (
            os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
            or os.getenv("AZURE_OPENAI_ENDPOINT")
        )

        if not endpoint:
            raise ValueError(
                "Missing required environment variable: AZURE_AI_FOUNDRY_ENDPOINT "
                "(or AZURE_OPENAI_ENDPOINT as fallback)"
            )

        return cls(
            endpoint=endpoint,
            api_version=os.getenv("AZURE_AI_FOUNDRY_API_VERSION", "2024-06-01"),
            model_name=os.getenv("AZURE_AI_FOUNDRY_MODEL_NAME", "gpt-4o"),
            max_tokens=int(os.getenv("AZURE_AI_FOUNDRY_MAX_TOKENS", "16000")),
            temperature=float(os.getenv("AZURE_AI_FOUNDRY_TEMPERATURE", "0.1")),
            timeout=int(os.getenv("AZURE_AI_FOUNDRY_TIMEOUT", "120")),
        )


@dataclass
class CosmosDBConfig:
    """Configuration for Azure CosmosDB.

    Authentication is performed via managed identity in
    :class:`helper.cosmos_client.CosmosClientManager`, so no master key is
    stored.
    """

    endpoint: str
    database_name: str
    container_name: str
    connection_mode: str = "Gateway"  # Gateway or Direct
    enable_diagnostics: bool = True
    batch_status_container_name: Optional[str] = None

    @classmethod
    def from_env(cls) -> "CosmosDBConfig":
        """Create configuration from environment variables."""
        # Support both spellings for backward compatibility
        endpoint = os.getenv("COSMOS_ENDPOINT") or os.getenv("COSMOS_END_POINT")
        database_name = os.getenv("COSMOS_DATABASE_NAME")

        # Container name for base/content source container
        container_name = os.getenv("COSMOS_CONTAINER_NAME")
        batch_status_container_name = os.getenv("COSMOS_BATCH_STATUS_CONTAINER_NAME", "batchstatus")

        if not endpoint:
            raise ValueError(
                "Missing required environment variable: COSMOS_ENDPOINT"
            )

        if not database_name:
            raise ValueError(
                "Missing required environment variable: COSMOS_DATABASE_NAME"
            )

        if not container_name:
            raise ValueError(
                "Missing required environment variable: COSMOS_CONTAINER_NAME"
            )

        return cls(
            endpoint=endpoint,
            database_name=database_name,
            container_name=container_name,  # Base/content source container
            batch_status_container_name=batch_status_container_name,
            connection_mode=os.getenv("COSMOS_CONNECTION_MODE", "Gateway"),
            enable_diagnostics=os.getenv("COSMOS_ENABLE_DIAGNOSTICS", "true").lower()
            == "true",
        )


@dataclass
class ProcessingConfig:
    """Configuration for OCR and entity extraction processing."""

    supported_image_formats: Optional[List[str]] = None
    max_image_size_mb: int = 10
    batch_size: int = 5
    output_format: str = "json"
    enable_confidence_scores: bool = True
    enable_entity_linking: bool = True

    def __post_init__(self):
        if self.supported_image_formats is None:
            self.supported_image_formats = [
                ".jpg",
                ".jpeg",
                ".png",
                ".bmp",
                ".tiff",
                ".webp",
            ]


def get_azure_config() -> AzureConfig:
    """Get Azure configuration from environment variables."""
    return AzureConfig.from_env()


def get_cosmosdb_config() -> CosmosDBConfig:
    """Get CosmosDB configuration from environment variables."""
    return CosmosDBConfig.from_env()


def get_processing_config() -> ProcessingConfig:
    """Get processing configuration with defaults."""
    return ProcessingConfig()


@dataclass
class AzureStorageConfig:
    """Configuration for Azure Blob Storage access.

    Authentication is performed via :func:`helper.credential.get_credential`,
    so only the storage account name is needed — clients derive the
    ``account_url`` (``https://<account>.blob.core.windows.net``) from it.
    """

    storage_account_name: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AzureStorageConfig":
        """Create configuration from environment variables."""
        storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")

        logger.info(
            "AZURE_STORAGE_ACCOUNT_NAME: %s",
            "SET" if storage_account_name else "NOT SET",
        )

        if not storage_account_name:
            logger.warning(
                "AZURE_STORAGE_ACCOUNT_NAME is not set; Blob clients will fail "
                "to derive an account_url."
            )

        return cls(storage_account_name=storage_account_name)


def get_storage_config() -> AzureStorageConfig:
    """Get Azure Storage configuration from environment variables."""
    return AzureStorageConfig.from_env()
