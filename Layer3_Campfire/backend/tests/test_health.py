"""Unit tests for health check endpoint."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from main import app


class TestHealthEndpoint:
    """Tests for /healthz endpoint."""

    def test_health_returns_ok_when_all_services_healthy(self):
        """Test health endpoint returns ok status when all services are healthy."""
        with patch("main.redis_client") as mock_redis, patch(
            "main.book_search_client"
        ) as mock_book, patch("main.letter_search_client") as mock_letter:
            mock_redis.ping = AsyncMock(return_value=True)
            mock_book.print_document_count = AsyncMock(return_value=1000)
            mock_letter.print_document_count = AsyncMock(return_value=500)

            client = TestClient(app)
            response = client.get("/healthz")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["book_documents"] == 1000
            assert data["letter_documents"] == 500

    def test_health_returns_error_when_redis_fails(self):
        """Test health endpoint returns error when Redis connection fails."""
        with patch("main.redis_client") as mock_redis:
            mock_redis.ping = AsyncMock(side_effect=Exception("Redis connection failed"))

            client = TestClient(app)
            response = client.get("/healthz")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "Redis" in data["error"]

    def test_health_returns_error_when_search_client_fails(self):
        """Test health endpoint returns error when search client fails."""
        with patch("main.redis_client") as mock_redis, patch(
            "main.book_search_client"
        ) as mock_book:
            mock_redis.ping = AsyncMock(return_value=True)
            mock_book.print_document_count = AsyncMock(
                side_effect=Exception("Search service unavailable")
            )

            client = TestClient(app)
            response = client.get("/healthz")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "Search service unavailable" in data["error"]
