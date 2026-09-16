# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the WebSocket chat endpoint."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from models import ChatRequest


_WS_PATH = "/ws/chat"


def _ws_headers() -> dict[str, str]:
    """Bearer-auth header used on the WebSocket handshake."""
    return {"Authorization": f"Bearer {os.environ['RAG_API_KEY']}"}


class TestWebSocketChatBasic:
    """Basic WebSocket chat endpoint tests."""

    def test_websocket_accepts_connection(self):
        """Connection succeeds and yields a response with a valid token."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Hi", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hello").model_dump_json())
                response = websocket.receive_text()
                assert response is not None

    def test_websocket_returns_chat_id(self):
        """Final response includes a chat_id (server-generated when not provided)."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Response", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hello").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert "chat_id" in data
                assert data["chat_id"]


class TestWebSocketAuth:
    """Authentication checks before the WebSocket upgrade completes."""

    def test_websocket_rejects_missing_auth_header(self):
        """Connection without an Authorization header should be closed."""
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app)
        try:
            with client.websocket_connect(_WS_PATH) as websocket:
                # The server closes immediately; the next receive raises.
                websocket.receive_text()
            raise AssertionError("Expected the connection to be closed")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008  # Policy violation

    def test_websocket_rejects_invalid_token(self):
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app)
        try:
            with client.websocket_connect(
                _WS_PATH, headers={"Authorization": "Bearer wrong-key"}
            ) as websocket:
                websocket.receive_text()
            raise AssertionError("Expected the connection to be closed")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008

    def test_websocket_rejects_url_query_token(self):
        """A token in the URL query must no longer be accepted as auth."""
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(app)
        try:
            with client.websocket_connect(
                f"{_WS_PATH}?token={os.environ['RAG_API_KEY']}"
            ) as websocket:
                websocket.receive_text()
            raise AssertionError("Expected the connection to be closed")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008


class TestWebSocketChatWithChatId:
    """Tests for chat_id handling in WebSocket."""

    def test_preserves_provided_chat_id(self):
        """Test that provided chat_id is preserved in responses."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Hi", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="Hello", chat_id="my-custom-id").model_dump_json()
                )

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["chat_id"] == "my-custom-id"

    def test_generates_chat_id_when_not_provided(self):
        """Test that chat_id is generated when not provided."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Hi", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hello").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["chat_id"]
                assert len(data["chat_id"]) > 0

    def test_rejects_chat_id_change_mid_session(self):
        """Once a chat_id is bound, sending a different one returns an error."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Hi", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="First", chat_id="chat-a").model_dump_json()
                )
                # Drain first response + [END]
                while websocket.receive_text() != "[END]":
                    pass

                websocket.send_text(
                    ChatRequest(message="Second", chat_id="chat-b").model_dump_json()
                )
                err = json.loads(websocket.receive_text())
                assert err["type"] == "error"
                assert "chat_id cannot be changed" in err["error"]


class TestWebSocketModeLock:
    """Mode is bound on the first message and locked for the session."""

    def test_first_message_mode_is_passed_to_agent(self):
        with (
            patch("main.EndToEndAgent") as mock_agent_class,
            patch("main.chat_history_service") as mock_history,
        ):
            captured: list[dict] = []

            def _make_agent(**kwargs):
                captured.append(kwargs)
                a = MagicMock()

                async def _stream(_msg):
                    yield {"type": "final", "text": "ok", "citations": []}

                a.ask_stream = _stream
                return a

            mock_agent_class.side_effect = _make_agent
            mock_history.get_chat_metadata = AsyncMock(return_value=None)
            mock_history.set_chat_metadata = AsyncMock(return_value=None)

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="hi", chat_id="c1", user_id="u1", mode="students").model_dump_json()
                )
                while websocket.receive_text() != "[END]":
                    pass

            assert captured[0]["mode"] == "students"
            mock_history.set_chat_metadata.assert_awaited_with("c1", "u1", mode="students")

    def test_rejects_mode_change_mid_session(self):
        with (
            patch("main.EndToEndAgent") as mock_agent_class,
            patch("main.chat_history_service") as mock_history,
        ):
            mock_agent = MagicMock()

            async def _stream(_msg):
                yield {"type": "final", "text": "ok", "citations": []}

            mock_agent.ask_stream = _stream
            mock_agent_class.return_value = mock_agent
            mock_history.get_chat_metadata = AsyncMock(return_value=None)
            mock_history.set_chat_metadata = AsyncMock(return_value=None)

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="first", chat_id="c1", user_id="u1", mode="research").model_dump_json()
                )
                while websocket.receive_text() != "[END]":
                    pass

                websocket.send_text(
                    ChatRequest(message="second", chat_id="c1", user_id="u1", mode="students").model_dump_json()
                )
                err = json.loads(websocket.receive_text())
                assert err["type"] == "error"
                assert "mode cannot be changed" in err["error"]

    def test_restores_stored_mode_when_first_message_omits_it(self):
        with (
            patch("main.EndToEndAgent") as mock_agent_class,
            patch("main.chat_history_service") as mock_history,
        ):
            captured: list[dict] = []

            def _make_agent(**kwargs):
                captured.append(kwargs)
                a = MagicMock()

                async def _stream(_msg):
                    yield {"type": "final", "text": "ok", "citations": []}

                a.ask_stream = _stream
                return a

            mock_agent_class.side_effect = _make_agent
            mock_history.get_chat_metadata = AsyncMock(return_value={"mode": "teachers"})
            mock_history.set_chat_metadata = AsyncMock(return_value=None)

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="resume", chat_id="c1", user_id="u1").model_dump_json()
                )
                while websocket.receive_text() != "[END]":
                    pass

            assert captured[0]["mode"] == "teachers"


class TestWebSocketResponseTypes:
    """Tests for different response types (delta, final, error)."""

    def test_delta_response_format(self):
        """Test that delta responses have correct format."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "delta", "delta": "Hello"}
                yield {"type": "final", "text": "Hello", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["type"] == "delta"
                assert data["delta"] == "Hello"
                assert "chat_id" in data

    def test_final_response_format(self):
        """Test that final responses have correct format."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()
            citations = [{"title": "Test Book", "page": 42}]

            async def mock_stream(_msg):
                yield {
                    "type": "final",
                    "text": "Complete response",
                    "citations": citations,
                }

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["type"] == "final"
                assert data["text"] == "Complete response"
                assert data["citations"] == citations
                assert "chat_id" in data

    def test_error_response_format(self):
        """Test that error responses have correct format."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "error", "error": "Something went wrong"}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["type"] == "error"
                assert data["error"] == "Something went wrong"
                assert "chat_id" in data

    def test_streaming_multiple_deltas(self):
        """Test that multiple delta chunks are received in order."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "delta", "delta": "Hello, "}
                yield {"type": "delta", "delta": "I am "}
                yield {"type": "delta", "delta": "TR!"}
                yield {"type": "final", "text": "Hello, I am TR!", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                deltas = []
                while True:
                    response = websocket.receive_text()
                    if response == "[END]":
                        break
                    data = json.loads(response)
                    if data["type"] == "delta":
                        deltas.append(data["delta"])

                assert deltas == ["Hello, ", "I am ", "TR!"]


class TestWebSocketEndMarker:
    """Tests for [END] marker handling."""

    def test_end_marker_sent_after_response(self):
        """Test that [END] marker is sent after all responses."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Done", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                websocket.receive_text()  # Final response
                assert websocket.receive_text() == "[END]"

    def test_end_marker_sent_after_error(self):
        """Test that [END] marker is sent even after error response."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "error", "error": "Failed"}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                websocket.receive_text()  # Error response
                assert websocket.receive_text() == "[END]"


class TestWebSocketMultipleMessages:
    """Tests for multiple messages in same WebSocket session."""

    def test_first_message_works(self):
        """Test that first message in session works correctly."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "Response 1", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(
                    ChatRequest(message="First", chat_id="session-1").model_dump_json()
                )
                response = json.loads(websocket.receive_text())

                assert response["text"] == "Response 1"
                assert response["chat_id"] == "session-1"

    def test_reuses_engine_across_messages(self):
        """The agent class should be instantiated exactly once per WS session."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                yield {"type": "final", "text": "ok", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                # Two messages on the same chat_id
                for _ in range(2):
                    websocket.send_text(
                        ChatRequest(message="hi", chat_id="chat-1").model_dump_json()
                    )
                    while websocket.receive_text() != "[END]":
                        pass

            # Engine constructed only once
            assert mock_agent_class.call_count == 1


class TestWebSocketExceptionHandling:
    """Tests for exception handling in WebSocket endpoint."""

    def test_agent_exception_returns_error_response(self):
        """Test that exceptions from agent return error response."""
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()

            async def mock_stream(_msg):
                raise ValueError("Agent failed")
                yield  # pragma: no cover - keeps this an async generator

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text(ChatRequest(message="Hi").model_dump_json())

                response = websocket.receive_text()
                data = json.loads(response)
                assert data["type"] == "error"
                assert "Agent failed" in data["error"]

    def test_invalid_json_request(self):
        """Test that invalid JSON in request is handled."""
        with patch("main.EndToEndAgent"):
            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                websocket.send_text("not valid json")
                response = websocket.receive_text()
                data = json.loads(response)
                assert data["type"] == "error"
                assert "Invalid request" in data["error"]

    def test_session_recovers_after_per_message_stream_error(self):
        """REPORT.md #8: a stream failure on message N must NOT tear down the
        WS session — the per-message try/except (main.py:218-271) sends an
        error frame then loops back to receive the next message. A user that
        retries the same chat session must get a normal response.
        """
        with patch("main.EndToEndAgent") as mock_agent_class:
            mock_agent = MagicMock()
            call_count = {"n": 0}

            async def mock_stream(_msg):
                # First call raises mid-stream. Second call streams a normal
                # final answer on the SAME engine instance (the session
                # reuses the engine — see test_reuses_engine_across_messages).
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("transient stream blowup")
                    yield  # pragma: no cover - keeps this an async generator
                else:
                    yield {"type": "final", "text": "recovered", "citations": []}

            mock_agent.ask_stream = mock_stream
            mock_agent_class.return_value = mock_agent

            client = TestClient(app)
            with client.websocket_connect(_WS_PATH, headers=_ws_headers()) as websocket:
                # Message 1: trigger the agent exception.
                websocket.send_text(
                    ChatRequest(message="first", chat_id="recover-1").model_dump_json()
                )
                first_response = json.loads(websocket.receive_text())
                assert first_response["type"] == "error", (
                    f"Expected error from first message, got: {first_response}"
                )
                assert "transient stream blowup" in first_response["error"]
                # Drain the [END] marker for message 1.
                assert websocket.receive_text() == "[END]"

                # Message 2: same socket, same chat_id. Session must still be
                # alive and the second call should succeed.
                websocket.send_text(
                    ChatRequest(message="second", chat_id="recover-1").model_dump_json()
                )
                second_response = json.loads(websocket.receive_text())
                assert second_response["type"] == "final", (
                    f"Session died — second message did not produce a normal response. "
                    f"Got: {second_response}"
                )
                assert second_response["text"] == "recovered"
                assert second_response["chat_id"] == "recover-1"
                assert websocket.receive_text() == "[END]"

            # Engine was constructed once (session reused across the failure).
            assert mock_agent_class.call_count == 1, (
                f"Engine was re-instantiated; per-message error tore down session state. "
                f"call_count={mock_agent_class.call_count}"
            )
            # And ask_stream was invoked for both messages.
            assert call_count["n"] == 2
