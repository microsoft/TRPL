"""Tests for safety/content-filter refusal normalization."""

import json
import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "CAMPFIRE_SAFETY_ERROR_PATTERNS",
    json.dumps(["deployment-safety-signal"]),
)

from chatbot.orchestrated_agent.safety import SAFE_REFUSAL_MESSAGE, is_safety_filter_error
from main import app


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['RAG_API_KEY']}"}


def test_is_safety_filter_error_detects_content_filter_patterns():
    assert is_safety_filter_error("Request blocked: deployment-safety-signal") is True
    assert is_safety_filter_error(
        {"error": {"code": "deployment-safety-signal"}}
    ) is True


def test_is_safety_filter_error_ignores_generic_errors():
    assert is_safety_filter_error("connection timeout") is False
    assert is_safety_filter_error({"error": {"code": "internal_server_error"}}) is False


def test_rest_chat_safety_error_returns_final_refusal():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "error", "error": "deployment-safety-signal"}

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        response = client.post("/api/chat", json={"message": "test"}, headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "final"
    assert data["text"] == SAFE_REFUSAL_MESSAGE
    assert data["citations"] == []


def test_rest_chat_non_safety_error_returns_error():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "error", "error": "backend unavailable"}

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        response = client.post("/api/chat", json={"message": "test"}, headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "error"
    assert data["error"] == "backend unavailable"


def test_websocket_chat_safety_error_returns_final_refusal():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "error", "error": "deployment-safety-signal"}

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        with client.websocket_connect("/ws/chat", headers=_auth_headers()) as websocket:
            websocket.send_text(json.dumps({"message": "test"}))

            first = json.loads(websocket.receive_text())
            assert first["type"] == "final"
            assert first["text"] == SAFE_REFUSAL_MESSAGE
            assert first["citations"] == []

            assert websocket.receive_text() == "[END]"
