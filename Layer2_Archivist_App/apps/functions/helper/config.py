"""
Configuration management for Azure AI Foundry OCR and Entity Extraction utility.
"""

import os
from typing import Optional
from dataclasses import dataclass

@dataclass
class CosmosDBConfig:
    """Configuration for Azure CosmosDB."""

    endpoint: str
    database_name: str
    container_name: str
    key: Optional[str] = None
    connection_mode: str = "Gateway"  # Gateway or Direct
    enable_diagnostics: bool = True
    content_source_container_name: str = None
    ocr_container_name: str = None
    audit_container_name: str = None
    record_chunks_container_name: str = None

    @classmethod
    def from_env(cls) -> 'CosmosDBConfig':
        """Create configuration from environment variables."""
        endpoint = os.getenv('COSMOS_DB_ENDPOINT')
        database_name = os.getenv('COSMOS_DB_DATABASE_NAME')

        container_name = os.getenv('COSMOS_DB_CONTAINER_NAME')

        if not endpoint:
            raise ValueError(
                "Missing required environment variables: "
                "COSMOS_DB_ENDPOINT"
            )

        if not database_name:
            raise ValueError(
                "Missing required environment variable: COSMOS_DB_DATABASE_NAME"
            )
        return cls(
            endpoint=endpoint,
            database_name=database_name,
            container_name=container_name,
            key=os.getenv('COSMOS_DB_KEY'),
            connection_mode=os.getenv('COSMOS_CONNECTION_MODE', 'Gateway'),
            enable_diagnostics=os.getenv('COSMOS_ENABLE_DIAGNOSTICS', 'true').lower() == 'true',
            audit_container_name=os.getenv('COSMOS_DB_AUDIT_CONTAINER_NAME', 'ocrmetadataaudit'),
            record_chunks_container_name=os.getenv('COSMOS_DB_RECORD_CHUNKS_CONTAINER_NAME', 'record-chunks')
        )

@dataclass
class IndexingConfig:
    """Configuration for the indexing process."""

    # OpenAI/Azure OpenAI (managed identity / DefaultAzureCredential in Azure)
    azure_openai_embedding_deployment_name: str = 'text-embedding-3-large'
    azure_openai_vision_deployment_name: str = 'gpt-4o'  # For OCR/image processing
    azure_openai_max_tokens: int = 8192  # Max tokens for vision/chat completions
    azure_openai_endpoint: Optional[str] = None

    # Azure Cognitive Search
    search_endpoint: Optional[str] = None
    search_index: Optional[str] = None

    # Service Bus (optional identity path)
    service_bus_fully_qualified_namespace: Optional[str] = None

    # Text Processing
    chunk_size_tokens: int = 1000
    chunk_overlap_tokens: int = 200

    @classmethod
    def from_env(cls) -> 'IndexingConfig':
        """Create configuration from environment variables."""
        return cls(
            # OpenAI
            azure_openai_embedding_deployment_name=os.getenv(
                'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME', 'text-embedding-3-large'
            ),
            azure_openai_vision_deployment_name=os.getenv(
                'AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4.1'
            ),
            azure_openai_max_tokens=int(os.getenv('AZURE_OPENAI_MAX_TOKENS', '8192')),
            azure_openai_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),

            # Search
            search_endpoint=os.getenv('AZURE_SEARCH_ENDPOINT'),
            search_index=os.getenv('AZURE_SEARCH_INDEX'),

            service_bus_fully_qualified_namespace=os.getenv(
                'AZURE_SERVICEBUS_FULLY_QUALIFIED_NAMESPACE'
            ),

            # Text Processing
            chunk_size_tokens=int(os.getenv('CHUNK_SIZE_TOKENS', '1000')),
            chunk_overlap_tokens=int(os.getenv('CHUNK_OVERLAP_TOKENS', '200'))
        )

def get_cosmosdb_config() -> CosmosDBConfig:
    """Get CosmosDB configuration from environment variables."""
    return CosmosDBConfig.from_env()

def get_indexing_config() -> IndexingConfig:
    """Get CosmosDB configuration from environment variables."""
    return IndexingConfig.from_env()

@dataclass
class StorageConfig:
    """Configuration for Azure Storage."""

    azure_storage_account_name: Optional[str] = None
    archivist_storage_account_name: Optional[str] = None
    connection_string: Optional[str] = None

    @classmethod
    def from_env(cls) -> 'StorageConfig':
        """Create configuration from environment variables."""
        df_account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
        archivist_account = (
            os.getenv('ARCHIVIST_STORAGE_ACCOUNT_NAME', '').strip() or df_account
        )
        return cls(
            azure_storage_account_name=df_account,
            archivist_storage_account_name=archivist_account,
            connection_string=os.getenv('AZURE_STORAGE_CONNECTION_STRING'),
        )


def get_storage_config() -> StorageConfig:
    """Get Storage configuration from environment variables."""
    return StorageConfig.from_env()


@dataclass
class EpubConfig:
    """Configuration for EPUB processing."""

    # Single Blob Storage container for all EPUB files
    # Structure: {container}/{document_id}/file.epub, sections.json, original_text.json, etc.
    storage_container_name: str = "epub-storage"

    # CosmosDB containers
    epub_cosmos_container: str = "epub"
    epub_chunks_container: str = "epub-chunks"

    # Service Bus queue
    epub_queue_name: str = "epub-processing-queue"

    # Azure AI Search (separate index for EPUB content)
    search_index_name: str = "epub-documents"

    # Chunking configuration (optimized for semantic retrieval)
    # 462 tokens is optimal for focused retrieval, 50 token overflow keeps sentences complete
    chunk_size_tokens: int = 462
    chunk_overlap_tokens: int = 50

    # Parallel processing
    embedding_concurrency: int = 20

    @classmethod
    def from_env(cls) -> 'EpubConfig':
        """Create configuration from environment variables."""
        return cls(
            storage_container_name=os.getenv('EPUB_STORAGE_CONTAINER_NAME', 'epub-storage'),
            epub_cosmos_container=os.getenv('EPUB_COSMOS_CONTAINER', 'epub'),
            epub_chunks_container=os.getenv('EPUB_CHUNKS_CONTAINER', 'epub-chunks'),
            epub_queue_name=os.getenv('EPUB_QUEUE_NAME', 'epub-processing-queue'),
            search_index_name=os.getenv('EPUB_SEARCH_INDEX_NAME', 'epub-documents'),
            chunk_size_tokens=int(os.getenv('EPUB_CHUNK_SIZE_TOKENS', '462')),
            chunk_overlap_tokens=int(os.getenv('EPUB_CHUNK_OVERLAP_TOKENS', '50')),
            embedding_concurrency=int(os.getenv('EPUB_EMBEDDING_CONCURRENCY', '20'))
        )


# Singleton instance for EPUB config
_epub_config: Optional[EpubConfig] = None


def get_epub_config() -> EpubConfig:
    """Get EPUB configuration from environment variables (singleton)."""
    global _epub_config
    if _epub_config is None:
        _epub_config = EpubConfig.from_env()
    return _epub_config
