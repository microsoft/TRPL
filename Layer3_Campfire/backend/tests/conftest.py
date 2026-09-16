# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pytest configuration and shared fixtures for backend tests."""

import os

# Set required environment variables BEFORE importing any backend modules
# These are test-only values that will be used when testing without real Azure services
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-api-key")
os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_SEARCH_API_KEY", "test-search-key")
os.environ.setdefault("AZURE_SEARCH_BOOK_INDEX", "test-book-index")
os.environ.setdefault("AZURE_SEARCH_LETTER_INDEX", "test-letter-index")
os.environ.setdefault("REDIS_SSL", "false")
os.environ.setdefault("RAG_API_KEY", "test-rag-api-key")
os.environ.setdefault("CAMPFIRE_RAG_MODE", "cloud")
os.environ.setdefault(
    "CAMPFIRE_SAFETY_ERROR_PATTERNS",
    '["deployment-safety-signal"]',
)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_redis():
    """Mock Redis client for health checks."""
    with patch("main.redis_client") as mock:
        mock.ping = AsyncMock(return_value=True)
        yield mock


@pytest.fixture
def mock_search_clients():
    """Mock Azure AI Search clients."""
    with patch("main.book_search_client") as book_mock, patch(
        "main.letter_search_client"
    ) as letter_mock:
        book_mock.print_document_count = AsyncMock(return_value=1000)
        letter_mock.print_document_count = AsyncMock(return_value=500)
        yield {"book": book_mock, "letter": letter_mock}


@pytest.fixture
def mock_end_to_end_agent():
    """Mock EndToEndAgent for testing without actual LLM calls."""

    async def mock_ask_stream(message: str):
        """Simulate streaming response."""
        yield {"type": "delta", "delta": "Hello, "}
        yield {"type": "delta", "delta": "I am "}
        yield {"type": "delta", "delta": "TR!"}
        yield {
            "type": "final",
            "text": "Hello, I am TR!",
            "citations": [{"title": "TR Letters", "page": 42}],
        }

    mock_agent = MagicMock()
    mock_agent.ask_stream = mock_ask_stream
    return mock_agent


@pytest.fixture
def mock_error_agent():
    """Mock agent that yields an error response."""

    async def mock_ask_stream(message: str):
        yield {"type": "error", "error": "Something went wrong"}

    mock_agent = MagicMock()
    mock_agent.ask_stream = mock_ask_stream
    return mock_agent
