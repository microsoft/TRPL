# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for Pydantic models (request/response validation)."""

import pytest
from models import (
    ChatDeltaResponse,
    ChatErrorResponse,
    ChatFinalResponse,
    ChatRequest,
)
from pydantic import ValidationError


class TestChatRequest:
    """Tests for ChatRequest model validation."""

    def test_valid_request_minimal(self):
        """Test minimal valid request with just message."""
        request = ChatRequest(message="Hello TR!")
        assert request.message == "Hello TR!"
        assert request.chat_id is None
        assert request.user_id is None

    def test_valid_request_with_chat_id(self):
        """Test request with chat_id provided."""
        request = ChatRequest(message="Hello", chat_id="abc123")
        assert request.message == "Hello"
        assert request.chat_id == "abc123"

    def test_valid_request_with_user_id(self):
        """Test request with user_id provided."""
        request = ChatRequest(message="Hello", user_id="user-1")
        assert request.user_id == "user-1"

    def test_missing_message_raises_error(self):
        """Test that missing message field raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            ChatRequest()  # pyright: ignore[reportCallIssue]
        assert "message" in str(exc_info.value)

    def test_empty_message_allowed(self):
        """Test that empty message is technically allowed (validation at app level)."""
        request = ChatRequest(message="")
        assert request.message == ""

    def test_json_serialization(self):
        """Test JSON serialization/deserialization."""
        request = ChatRequest(message="Hello", chat_id="123", user_id="u1")
        json_str = request.model_dump_json()
        parsed = ChatRequest.model_validate_json(json_str)
        assert parsed.message == request.message
        assert parsed.chat_id == request.chat_id
        assert parsed.user_id == request.user_id


class TestChatDeltaResponse:
    """Tests for ChatDeltaResponse model."""

    def test_valid_delta_response(self):
        """Test valid delta response creation."""
        response = ChatDeltaResponse(chat_id="123", user_id="u1", type="delta", delta="Hello")
        assert response.chat_id == "123"
        assert response.user_id == "u1"
        assert response.type == "delta"
        assert response.delta == "Hello"

    def test_delta_response_type_literal(self):
        """Test that type must be 'delta'."""
        with pytest.raises(ValidationError):
            ChatDeltaResponse(chat_id="123", user_id="u1", type="final", delta="Hello")  # pyright: ignore[reportArgumentType]

    def test_delta_response_json_serialization(self):
        """Test JSON serialization."""
        response = ChatDeltaResponse(chat_id="abc", user_id="u1", type="delta", delta="chunk")
        json_str = response.model_dump_json()
        assert '"type":"delta"' in json_str
        assert '"delta":"chunk"' in json_str

    def test_delta_response_empty_delta(self):
        """Test delta response with empty delta string."""
        response = ChatDeltaResponse(chat_id="123", user_id="u1", type="delta", delta="")
        assert response.delta == ""

    def test_delta_response_missing_user_id_raises(self):
        """user_id is required on every response after the schema alignment."""
        with pytest.raises(ValidationError):
            ChatDeltaResponse(chat_id="123", type="delta", delta="Hello")  # pyright: ignore[reportCallIssue]


class TestChatFinalResponse:
    """Tests for ChatFinalResponse model."""

    def test_valid_final_response(self):
        """Test valid final response creation."""
        citations = [{"title": "Book", "page": 1}]
        response = ChatFinalResponse(
            chat_id="123", user_id="u1", type="final", text="Full response", citations=citations
        )
        assert response.chat_id == "123"
        assert response.user_id == "u1"
        assert response.type == "final"
        assert response.text == "Full response"
        assert response.citations == citations

    def test_final_response_type_literal(self):
        """Test that type must be 'final'."""
        with pytest.raises(ValidationError):
            ChatFinalResponse(chat_id="123", user_id="u1", type="delta", text="text", citations=[])  # pyright: ignore[reportArgumentType]

    def test_final_response_empty_citations(self):
        """Test final response with empty citations list."""
        response = ChatFinalResponse(chat_id="123", user_id="u1", type="final", text="Response", citations=[])
        assert response.citations == []

    def test_final_response_multiple_citations(self):
        """Test final response with multiple citations."""
        citations = [
            {"title": "Letters Vol 1", "page": 10, "source": "archive"},
            {"title": "Autobiography", "chapter": "Youth"},
        ]
        response = ChatFinalResponse(chat_id="123", user_id="u1", type="final", text="Response", citations=citations)
        assert len(response.citations) == 2

    def test_final_response_json_serialization(self):
        """Test JSON serialization."""
        response = ChatFinalResponse(
            chat_id="abc",
            user_id="u1",
            type="final",
            text="Complete",
            citations=[{"title": "Test"}],
        )
        json_str = response.model_dump_json()
        assert '"type":"final"' in json_str
        assert '"text":"Complete"' in json_str


class TestChatErrorResponse:
    """Tests for ChatErrorResponse model."""

    def test_valid_error_response(self):
        """Test valid error response creation."""
        response = ChatErrorResponse(chat_id="123", user_id="u1", type="error", error="Something went wrong")
        assert response.chat_id == "123"
        assert response.user_id == "u1"
        assert response.type == "error"
        assert response.error == "Something went wrong"

    def test_error_response_type_literal(self):
        """Test that type must be 'error'."""
        with pytest.raises(ValidationError):
            ChatErrorResponse(chat_id="123", user_id="u1", type="delta", error="Error message")  # pyright: ignore[reportArgumentType]

    def test_error_response_json_serialization(self):
        """Test JSON serialization."""
        response = ChatErrorResponse(chat_id="abc", user_id="u1", type="error", error="Failed")
        json_str = response.model_dump_json()
        assert '"type":"error"' in json_str
        assert '"error":"Failed"' in json_str

    def test_error_response_empty_chat_id(self):
        """Test error response with empty chat_id (for error before ID assignment)."""
        response = ChatErrorResponse(chat_id="", user_id="", type="error", error="Early error")
        assert response.chat_id == ""


class TestResponseValidation:
    """Tests for validating raw dict data into response models."""

    def test_validate_delta_from_dict(self):
        """Test creating delta response from dict (as returned by agent)."""
        raw = {"chat_id": "123", "user_id": "u1", "type": "delta", "delta": "chunk"}
        response = ChatDeltaResponse.model_validate(raw)
        assert response.delta == "chunk"

    def test_validate_final_from_dict(self):
        """Test creating final response from dict."""
        raw = {
            "chat_id": "123",
            "user_id": "u1",
            "type": "final",
            "text": "Complete",
            "citations": [{"source": "test"}],
        }
        response = ChatFinalResponse.model_validate(raw)
        assert response.text == "Complete"

    def test_validate_error_from_dict(self):
        """Test creating error response from dict."""
        raw = {"chat_id": "123", "user_id": "u1", "type": "error", "error": "Oops"}
        response = ChatErrorResponse.model_validate(raw)
        assert response.error == "Oops"

    def test_invalid_dict_raises_error(self):
        """Test that invalid dict raises validation error."""
        with pytest.raises(ValidationError):
            ChatDeltaResponse.model_validate({"chat_id": "123"})
