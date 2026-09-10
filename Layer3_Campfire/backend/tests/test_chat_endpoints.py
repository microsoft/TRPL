"""Integration tests for REST endpoints in main.py.

Covers happy paths and auth failures for:
- POST /api/chat
- GET /api/chat-history/{user_id}
- GET /api/chat-history/{user_id}/{chat_id}/messages
- DELETE /api/chat-history/{user_id}/{chat_id}
- POST /api/artifacts
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from main import app


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['RAG_API_KEY']}"}


# ─── POST /api/chat ────────────────────────────────────────────────────


def test_rest_chat_returns_final_response_on_happy_path():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "delta", "delta": "Hello "}
            yield {"type": "delta", "delta": "world"}
            yield {
                "type": "final",
                "text": "Hello world",
                "citations": [{"title": "TR Letter", "page": 7}],
            }

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={"message": "Hi TR", "chat_id": "c1", "user_id": "u1"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "final"
    assert data["text"] == "Hello world"
    assert data["chat_id"] == "c1"
    assert data["user_id"] == "u1"
    assert data["citations"] == [{"title": "TR Letter", "page": 7}]


def test_rest_chat_generates_ids_when_missing():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "final", "text": "ok", "citations": []}

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        response = client.post("/api/chat", json={"message": "hi"}, headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["chat_id"]  # uuid generated
    assert data["user_id"]
    assert len(data["chat_id"]) == 32  # uuid4().hex


def test_rest_chat_returns_error_when_no_final_received():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent = MagicMock()

        async def mock_stream(_message: str):
            yield {"type": "delta", "delta": "partial"}

        mock_agent.ask_stream = mock_stream
        mock_agent_class.return_value = mock_agent

        client = TestClient(app)
        response = client.post("/api/chat", json={"message": "hi"}, headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "error"
    assert "No final response" in data["error"]


def test_rest_chat_recovers_from_unexpected_exception():
    with patch("main.EndToEndAgent") as mock_agent_class:
        mock_agent_class.side_effect = RuntimeError("boom")

        client = TestClient(app)
        response = client.post("/api/chat", json={"message": "hi"}, headers=_auth_headers())

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "error"
    assert "boom" in data["error"]


def test_rest_chat_rejects_missing_auth():
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert "Missing Authorization" in response.json()["detail"]


def test_rest_chat_rejects_non_bearer_scheme():
    """HTTPBearer drops non-Bearer headers before scheme check, so the user
    sees 'Missing Authorization header'. Either rejection is acceptable."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "hi"},
        headers={"Authorization": f"Basic {os.environ['RAG_API_KEY']}"},
    )
    assert response.status_code == 401


def test_rest_chat_rejects_wrong_api_key():
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


def test_rest_chat_rejects_invalid_body():
    client = TestClient(app)
    response = client.post("/api/chat", json={}, headers=_auth_headers())
    assert response.status_code == 422


# ─── GET /api/chat-history/{user_id} ─────────────────────────────────


def test_get_user_chats_returns_summaries_with_mode():
    with patch("main.chat_history_service") as mock_history:
        mock_history.get_user_chats = AsyncMock(
            return_value=[
                {"chat_id": "chat-a", "mode": "research"},
                {"chat_id": "chat-b", "mode": "discovery"},
            ]
        )

        client = TestClient(app)
        response = client.get("/api/chat-history/user-1", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == [
        {"chat_id": "chat-a", "mode": "research"},
        {"chat_id": "chat-b", "mode": "discovery"},
    ]
    mock_history.get_user_chats.assert_awaited_once_with("user-1")


def test_get_user_chats_returns_500_on_backend_failure():
    with patch("main.chat_history_service") as mock_history:
        mock_history.get_user_chats = AsyncMock(side_effect=Exception("redis down"))

        client = TestClient(app)
        response = client.get("/api/chat-history/user-1", headers=_auth_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to retrieve chat history"


def test_get_user_chats_requires_auth():
    client = TestClient(app)
    response = client.get("/api/chat-history/user-1")
    assert response.status_code == 401


# ─── GET /api/chat-history/{user_id}/{chat_id}/messages ──────────────


def test_get_chat_messages_returns_messages():
    with patch("main.chat_history_service") as mock_history:
        mock_history.get_chat_messages = AsyncMock(
            return_value=[
                {"role": "user", "text": "hi", "timestamp": 1.0, "citations": None},
                {"role": "assistant", "text": "hello", "timestamp": 2.0, "citations": []},
            ]
        )

        client = TestClient(app)
        response = client.get(
            "/api/chat-history/user-1/chat-a/messages", headers=_auth_headers()
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"
    mock_history.get_chat_messages.assert_awaited_once_with(chat_id="chat-a", user_id="user-1")


def test_get_chat_messages_strips_debug_fields():
    """search_queries and fact_check are backend-only; REST must not expose them."""
    from models import ChatMessageItem

    with patch("main.chat_history_service") as mock_history:
        mock_history.get_chat_messages = AsyncMock(
            return_value=[
                ChatMessageItem(role="user", text="hi", timestamp=1.0),
                ChatMessageItem(
                    role="assistant",
                    text="hello",
                    timestamp=2.0,
                    citations=[],
                    search_queries={"historical_query": "q", "book_query": None},
                    fact_check={"flagged": True, "issues": [{"claim": "c", "explanation": "e"}]},
                ),
            ]
        )

        client = TestClient(app)
        response = client.get(
            "/api/chat-history/user-1/chat-a/messages", headers=_auth_headers()
        )

    assert response.status_code == 200
    data = response.json()
    assert "search_queries" not in data[1]
    assert "fact_check" not in data[1]
    assert set(data[1].keys()) == {"role", "text", "timestamp", "citations"}


def test_get_chat_messages_returns_500_on_failure():
    with patch("main.chat_history_service") as mock_history:
        mock_history.get_chat_messages = AsyncMock(side_effect=Exception("redis down"))

        client = TestClient(app)
        response = client.get(
            "/api/chat-history/user-1/chat-a/messages", headers=_auth_headers()
        )

    assert response.status_code == 500


def test_get_chat_messages_requires_auth():
    client = TestClient(app)
    response = client.get("/api/chat-history/user-1/chat-a/messages")
    assert response.status_code == 401


# ─── DELETE /api/chat-history/{user_id}/{chat_id} ────────────────────


def test_delete_chat_returns_success():
    with patch("main.chat_history_service") as mock_history:
        mock_history.delete_chat = AsyncMock(return_value=None)

        client = TestClient(app)
        response = client.delete(
            "/api/chat-history/user-1/chat-a", headers=_auth_headers()
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Chat deleted"}
    mock_history.delete_chat.assert_awaited_once_with("chat-a", "user-1")


def test_delete_chat_returns_500_on_failure():
    with patch("main.chat_history_service") as mock_history:
        mock_history.delete_chat = AsyncMock(side_effect=Exception("redis down"))

        client = TestClient(app)
        response = client.delete(
            "/api/chat-history/user-1/chat-a", headers=_auth_headers()
        )

    assert response.status_code == 500


def test_delete_chat_requires_auth():
    client = TestClient(app)
    response = client.delete("/api/chat-history/user-1/chat-a")
    assert response.status_code == 401


# ─── POST /api/artifacts ─────────────────────────────────────────────


def test_get_artifact_letter_happy_path():
    with patch("main.letter_search_client") as mock_letters:
        mock_letters.get_document = AsyncMock(
            return_value={"id": "letter-1", "title": "To Cabot Lodge"}
        )

        client = TestClient(app)
        response = client.post(
            "/api/artifacts",
            json={"index": "letter", "id": "letter-1"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"id": "letter-1", "title": "To Cabot Lodge"}
    mock_letters.get_document.assert_awaited_once_with("letter-1")


def test_get_artifact_book_happy_path():
    with patch("main.book_search_client") as mock_books:
        mock_books.get_document = AsyncMock(
            return_value={"id": "book-1", "title": "Rough Riders"}
        )

        client = TestClient(app)
        response = client.post(
            "/api/artifacts",
            json={"index": "book", "id": "book-1"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Rough Riders"
    mock_books.get_document.assert_awaited_once_with("book-1")


def test_get_artifact_returns_404_when_not_found():
    with patch("main.letter_search_client") as mock_letters:
        mock_letters.get_document = AsyncMock(side_effect=ResourceNotFoundError("not found"))

        client = TestClient(app)
        response = client.post(
            "/api/artifacts",
            json={"index": "letter", "id": "missing"},
            headers=_auth_headers(),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found"


def test_get_artifact_returns_500_on_other_failure():
    with patch("main.letter_search_client") as mock_letters:
        mock_letters.get_document = AsyncMock(side_effect=Exception("search down"))

        client = TestClient(app)
        response = client.post(
            "/api/artifacts",
            json={"index": "letter", "id": "x"},
            headers=_auth_headers(),
        )

    assert response.status_code == 500


def test_get_artifact_rejects_invalid_index():
    client = TestClient(app)
    response = client.post(
        "/api/artifacts",
        json={"index": "image", "id": "x"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422


def test_get_artifact_requires_auth():
    client = TestClient(app)
    response = client.post("/api/artifacts", json={"index": "letter", "id": "x"})
    assert response.status_code == 401


# ─── Mode plumbing on POST /api/chat ─────────────────────────────────


def _final_only_agent_factory():
    """Build a mock EndToEndAgent class that captures init kwargs and yields a
    single final response. Returns (mock_class, captured_kwargs_list)."""
    captured: list[dict] = []
    mock_class = MagicMock()

    def _make_agent(**kwargs):
        captured.append(kwargs)
        agent = MagicMock()

        async def _stream(_msg):
            yield {"type": "final", "text": "ok", "citations": []}

        agent.ask_stream = _stream
        return agent

    mock_class.side_effect = _make_agent
    return mock_class, captured


def test_rest_chat_passes_mode_through_to_agent():
    mock_class, captured = _final_only_agent_factory()
    with (
        patch("main.EndToEndAgent", mock_class),
        patch("main.chat_history_service") as mock_history,
    ):
        mock_history.get_chat_metadata = AsyncMock(return_value=None)
        mock_history.set_chat_metadata = AsyncMock(return_value=None)

        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={"message": "hi", "chat_id": "c1", "user_id": "u1", "mode": "research"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["type"] == "final"
    assert captured[0]["mode"] == "research"
    mock_history.set_chat_metadata.assert_awaited_once_with("c1", "u1", mode="research")


def test_rest_chat_defaults_mode_to_discovery():
    mock_class, captured = _final_only_agent_factory()
    with (
        patch("main.EndToEndAgent", mock_class),
        patch("main.chat_history_service") as mock_history,
    ):
        mock_history.get_chat_metadata = AsyncMock(return_value=None)
        mock_history.set_chat_metadata = AsyncMock(return_value=None)

        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={"message": "hi", "chat_id": "c1", "user_id": "u1"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert captured[0]["mode"] == "discovery"


def test_rest_chat_loads_stored_mode_when_request_omits_it():
    mock_class, captured = _final_only_agent_factory()
    with (
        patch("main.EndToEndAgent", mock_class),
        patch("main.chat_history_service") as mock_history,
    ):
        mock_history.get_chat_metadata = AsyncMock(return_value={"mode": "teachers"})
        mock_history.set_chat_metadata = AsyncMock(return_value=None)

        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={"message": "hi", "chat_id": "c1", "user_id": "u1"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert captured[0]["mode"] == "teachers"


def test_rest_chat_rejects_mode_change_for_existing_chat():
    mock_class, _ = _final_only_agent_factory()
    with (
        patch("main.EndToEndAgent", mock_class),
        patch("main.chat_history_service") as mock_history,
    ):
        mock_history.get_chat_metadata = AsyncMock(return_value={"mode": "teachers"})

        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={"message": "hi", "chat_id": "c1", "user_id": "u1", "mode": "research"},
            headers=_auth_headers(),
        )

    assert response.status_code == 400
    assert "Mode cannot change" in response.json()["detail"]


def test_rest_chat_rejects_unknown_mode():
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "hi", "mode": "expert"},
        headers=_auth_headers(),
    )
    assert response.status_code == 422
