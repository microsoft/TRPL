"""Shared pytest configuration for functions unit tests."""
import os
from unittest.mock import MagicMock, patch

# Minimal env so helper modules can import without Azure resources.
os.environ.setdefault("AZURE_STORAGE_ACCOUNT_NAME", "teststorage")
os.environ.setdefault("AZURE_STORAGE_CONTAINER_NAME", "testcontainer")
os.environ.setdefault("COSMOS_ENDPOINT", "https://test-cosmos.documents.azure.com:443/")
os.environ.setdefault("COSMOS_DATABASE_NAME", "testdb")
os.environ.setdefault("COSMOS_CONTAINER_NAME", "recordsmetadata")
os.environ.setdefault("COSMOS_LOG_CONTAINER_NAME", "ingestion-errors")
# Avoid live Azure calls when content_source_client is imported at collection time.
_mock_cosmos_client = MagicMock()
patch(
    "helper.cosmos_client.CosmosClientManager.get_client",
    return_value=_mock_cosmos_client,
).start()
patch(
    "azure.storage.blob.BlobServiceClient.get_container_client",
    return_value=MagicMock(),
).start()
