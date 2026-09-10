"""Cosmos DB client for the digital-items container.

Digital items (Moore Chronology, TR Cyclopedia, Genealogy Papers) live in the
same Cosmos account as the main DF containers but in a separate container.

Authentication is always via DefaultAzureCredential (managed identity in Azure,
az-cli locally).  No API key is ever used.

Environment variables (all optional — fallback chain shown):
  COSMOS_DB_DIGITAL_ITEMS_ENDPOINT   → COSMOS_DB_ENDPOINT
  COSMOS_DB_DIGITAL_ITEMS_DATABASE   → COSMOS_DB_DATABASE_NAME  → 'contentdb'
  COSMOS_DB_DIGITAL_ITEMS_CONTAINER  → 'digital-items'
"""

import logging
import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Module-level singletons — reused across function invocations in the same
# worker process to avoid the overhead of re-creating the Cosmos client and
# re-negotiating the connection on every HTTP trigger call.
_client: CosmosClient | None = None
_container = None


def get_digital_items_container():
    """Return a Cosmos ContainerClient for the digital-items container.

    Uses DefaultAzureCredential so the function app's system-assigned managed
    identity (which already has Cosmos RBAC access) is used in Azure, and
    az-login / azd-login is used locally.

    The client and container proxy are cached at module level so that repeated
    calls within the same worker process (multiple function invocations) reuse
    the same connection instead of re-negotiating credentials each time.
    """
    global _client, _container
    if _container is not None:
        return _container

    endpoint = (
        os.getenv("COSMOS_DB_DIGITAL_ITEMS_ENDPOINT")
        or os.getenv("COSMOS_DB_ENDPOINT")
    )
    if not endpoint:
        raise ValueError(
            "Cosmos endpoint not configured. "
            "Set COSMOS_DB_DIGITAL_ITEMS_ENDPOINT (or COSMOS_DB_ENDPOINT)."
        )

    database = (
        os.getenv("COSMOS_DB_DIGITAL_ITEMS_DATABASE")
        or os.getenv("COSMOS_DB_DATABASE_NAME")
        or "contentdb"
    )

    container_name = (
        os.getenv("COSMOS_DB_DIGITAL_ITEMS_CONTAINER")
        or "digital-items"
    )

    logger.info(
        "digital_items_cosmos: initialising client endpoint=%s database=%s container=%s",
        endpoint,
        database,
        container_name,
    )

    _client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())
    _container = _client.get_database_client(database).get_container_client(container_name)
    return _container
