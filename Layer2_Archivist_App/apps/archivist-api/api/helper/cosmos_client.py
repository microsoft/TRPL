# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Azure CosmosDB client for OCR extractor document management.

This module provides both a CosmosDBClient class for document operations
and a singleton CosmosClientManager for optimal connection management.
"""

import os
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, fields
from datetime import datetime

import requests

from azure.core.pipeline.transport import RequestsTransport
from azure.cosmos import CosmosClient, exceptions
from azure.cosmos.container import ContainerProxy
from azure.cosmos.database import DatabaseProxy

from .credential import get_credential


# Use module logger; don't call basicConfig in library modules
logger = logging.getLogger(__name__)


class CosmosClientManager:
    """
    Singleton manager for shared CosmosClient instance.

    This class ensures only one CosmosClient instance is created
    for the entire application lifetime, which is the recommended pattern
    for optimal performance and connection management.
    """

    _instance: Optional["CosmosClientManager"] = None
    _cosmos_client: Optional[CosmosClient] = None
    _database: Optional[DatabaseProxy] = None
    _database_name: Optional[str] = None

    def __new__(cls) -> "CosmosClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_client(self) -> CosmosClient:
        """
        Get or create the shared CosmosClient instance.

        Authenticates with the Function App's managed identity via
        :func:`helper.credential.get_credential` — Cosmos data-plane RBAC
        (e.g. ``Cosmos DB Built-in Data Contributor``) must be granted in
        infra for this to succeed.

        Returns:
            CosmosClient: The shared CosmosClient instance
        """
        if self._cosmos_client is None:
            endpoint = os.getenv("COSMOS_ENDPOINT") or os.getenv("COSMOS_END_POINT")

            if not endpoint:
                raise ValueError(
                    "Missing required environment variable: COSMOS_ENDPOINT"
                )

            connection_mode = os.getenv("COSMOS_CONNECTION_MODE", "Gateway")

            logger.info(
                "Initializing shared CosmosClient instance (endpoint=%s, connection_mode=%s)",
                endpoint,
                connection_mode,
            )

            # Create CosmosClient with connection pool configuration
            # The Python SDK manages connection pooling internally and automatically
            # scales connections based on workload. Using a single client instance
            # ensures optimal connection reuse and pooling.
            #
            # For Gateway mode: HTTP connections are pooled automatically
            # For Direct mode: TCP connections are pooled with automatic scaling
            sess = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
            sess.mount('https://', adapter)

            CosmosClientManager._cosmos_client = CosmosClient(
                url=endpoint,
                credential=get_credential(),
                connection_mode=connection_mode,
                transport=RequestsTransport(session=sess, session_owner=False)
            )

            logger.info("Shared CosmosClient instance initialized successfully")

        return self._cosmos_client

    def get_database(self, database_name: Optional[str] = None) -> DatabaseProxy:
        """
        Get or create the shared DatabaseProxy instance.

        Args:
            database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

        Returns:
            DatabaseProxy: The shared DatabaseProxy instance
        """
        if database_name is None:
            database_name = os.getenv("COSMOS_DATABASE_NAME")
            if not database_name:
                raise ValueError(
                    "Missing required environment variable: COSMOS_DATABASE_NAME"
                )

        # Return cached database if it matches the requested name
        if self._database is not None and self._database_name == database_name:
            return self._database

        client = self.get_client()
        CosmosClientManager._database = client.get_database_client(database_name)
        CosmosClientManager._database_name = database_name
        logger.info("Shared DatabaseProxy instance initialized for database: %s", database_name)

        return self._database

    def get_container(self, container_name: str, database_name: Optional[str] = None) -> ContainerProxy:
        """
        Get a container client from the shared CosmosClient instance.

        Args:
            container_name: Container name
            database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

        Returns:
            ContainerProxy: Container client instance
        """
        database = self.get_database(database_name)
        return database.get_container_client(container_name)


# Module-level singleton instance
_manager = CosmosClientManager()


def get_cosmos_client() -> CosmosClient:
    """
    Get the shared CosmosClient instance.

    Returns:
        CosmosClient: The shared CosmosClient instance
    """
    return _manager.get_client()


def get_database(database_name: Optional[str] = None) -> DatabaseProxy:
    """
    Get the shared DatabaseProxy instance.

    Args:
        database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

    Returns:
        DatabaseProxy: The shared DatabaseProxy instance
    """
    return _manager.get_database(database_name)


def get_container(container_name: str, database_name: Optional[str] = None) -> ContainerProxy:
    """
    Get a container client from the shared CosmosClient instance.

    Args:
        container_name: Container name
        database_name: Database name (defaults to COSMOS_DATABASE_NAME env var)

    Returns:
        ContainerProxy: Container client instance
    """
    return _manager.get_container(container_name, database_name)
