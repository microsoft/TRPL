"""Unit tests for ChatHistoryManager implementation."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from chatbot.chat_history import ChatHistoryManager
from models import ChatMessageItem, ChatSummary


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client."""
    redis = AsyncMock()
    # Mock pipeline context manager with proper async methods
    pipeline = MagicMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=None)
    pipeline.rpush = MagicMock(return_value=None)
    pipeline.ltrim = MagicMock(return_value=None)
    pipeline.zadd = MagicMock(return_value=None)
    pipeline.expire = MagicMock(return_value=None)
    pipeline.delete = MagicMock(return_value=None)
    pipeline.zrem = MagicMock(return_value=None)
    pipeline.lset = MagicMock(return_value=None)
    pipeline.get = MagicMock(return_value=None)
    pipeline.execute = AsyncMock(return_value=[None, None, None, None, None])
    redis.pipeline = MagicMock(return_value=pipeline)
    return redis


@pytest.fixture
def chat_history(mock_redis_client):
    """Create a ChatHistoryManager instance with mocked Redis client."""
    return ChatHistoryManager(redis_client=mock_redis_client, max_messages=100)


def test_get_user_chats_success(chat_history, mock_redis_client):
    """Returns chat IDs paired with their stored mode (or 'discovery' when unset)."""

    async def run():
        mock_redis_client.zrevrange.return_value = ["chat-1", "chat-2", "chat-3"]
        pipeline = mock_redis_client.pipeline.return_value
        # Pipelined metadata GETs: chat-1 has research stored, chat-2 is raw
        # discovery, chat-3 has no metadata at all (predates the feature).
        pipeline.execute = AsyncMock(return_value=[b'{"mode": "research"}', b'{"mode": "discovery"}', None])

        chats = await chat_history.get_user_chats("user-1")

        assert chats == [
            ChatSummary(chat_id="chat-1", mode="research"),
            ChatSummary(chat_id="chat-2", mode="discovery"),
            ChatSummary(chat_id="chat-3", mode="discovery"),
        ]
        mock_redis_client.zrevrange.assert_called_once_with("user:user-1:chats", 0, 49)

    asyncio.run(run())


def test_get_user_chats_returns_empty_list_when_no_chats(chat_history, mock_redis_client):
    """Empty zrevrange skips the metadata pipeline entirely."""

    async def run():
        mock_redis_client.zrevrange.return_value = []

        chats = await chat_history.get_user_chats("user-1")

        assert chats == []

    asyncio.run(run())


def test_get_user_chats_raises_on_redis_error(chat_history, mock_redis_client):
    """Test that get_user_chats raises exception on Redis error."""

    async def run():
        mock_redis_client.zrevrange.side_effect = Exception("redis down")

        with pytest.raises(Exception, match="redis down"):
            await chat_history.get_user_chats("user-1")

    asyncio.run(run())


def test_get_chat_messages_success(chat_history, mock_redis_client):
    """Test retrieving chat messages successfully."""

    async def run():
        # Create mock messages
        user_msg = ChatMessageItem(role="user", text="What did TR do for conservation?", timestamp=1234567890.0)
        assistant_msg = ChatMessageItem(
            role="assistant",
            text="TR created many national parks...",
            timestamp=1234567891.0,
            citations=[{"index": 1, "title": "Conservation Legacy"}],
        )

        # Mock Redis lrange to return serialized messages
        mock_redis_client.lrange.return_value = [user_msg.model_dump_json(), assistant_msg.model_dump_json()]

        messages = await chat_history.get_chat_messages("chat-123", "user-1")

        # Verify we got both messages
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].text == "What did TR do for conservation?"
        assert messages[1].role == "assistant"
        assert messages[1].citations is not None
        assert len(messages[1].citations) == 1

        mock_redis_client.lrange.assert_called_once_with("user:user-1:chatmessages:chat-123", 0, -1)

    asyncio.run(run())


def test_get_chat_messages_empty(chat_history, mock_redis_client):
    """Test retrieving messages from empty chat."""

    async def run():
        mock_redis_client.lrange.return_value = []

        messages = await chat_history.get_chat_messages("chat-123", "user-1")

        assert messages == []
        mock_redis_client.lrange.assert_called_once()

    asyncio.run(run())


def test_add_message_without_citations(chat_history, mock_redis_client):
    """Test adding a user message without citations."""

    async def run():
        message = ChatMessageItem(role="user", text="Tell me about TR", timestamp=time.time())

        await chat_history.add_messages("chat-123", "user-1", [message])

        # Verify pipeline operations were called
        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.rpush.assert_called_once()
        pipeline.ltrim.assert_called_once()
        pipeline.zadd.assert_called_once()
        assert pipeline.expire.call_count == 2
        pipeline.execute.assert_called_once()

        # Verify the message was serialized correctly
        call_args = pipeline.rpush.call_args[0]
        assert call_args[0] == "user:user-1:chatmessages:chat-123"

        # Deserialize and verify message content
        stored_message = ChatMessageItem.model_validate_json(call_args[1])
        assert stored_message.role == "user"
        assert stored_message.text == "Tell me about TR"
        assert stored_message.citations is None

    asyncio.run(run())


def test_add_message_with_citations(chat_history, mock_redis_client):
    """Test adding an assistant message with citations."""

    async def run():
        citations = [
            {"index": 1, "title": "Letter to John Muir", "date": "1903"},
            {"index": 3, "title": "Conservation Speech", "date": "1908"},
        ]

        message = ChatMessageItem(
            role="assistant", text="TR was a passionate conservationist...", timestamp=time.time(), citations=citations
        )

        await chat_history.add_messages("chat-123", "user-1", [message])

        # Verify pipeline operations
        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.rpush.assert_called_once()

        # Verify the message was serialized with citations
        call_args = pipeline.rpush.call_args[0]
        stored_message = ChatMessageItem.model_validate_json(call_args[1])

        assert stored_message.role == "assistant"
        assert stored_message.citations is not None
        assert stored_message.citations == citations
        assert len(stored_message.citations) == 2

    asyncio.run(run())


def test_add_message_updates_chat_list(chat_history, mock_redis_client):
    """Test that adding a message updates the user's chat list."""

    async def run():
        message = ChatMessageItem(role="user", text="Test", timestamp=1234567890.5)

        await chat_history.add_messages("chat-123", "user-1", [message])

        # Verify zadd was called with timestamp
        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.zadd.assert_called_once()

        call_args = pipeline.zadd.call_args[0]
        assert call_args[0] == "user:user-1:chats"
        assert "chat-123" in call_args[1]
        assert call_args[1]["chat-123"] == message.timestamp

    asyncio.run(run())


def test_delete_chat_success(chat_history, mock_redis_client):
    """Test deleting a chat successfully."""

    async def run():
        await chat_history.delete_chat("chat-123", "user-1")

        pipeline = await mock_redis_client.pipeline().__aenter__()
        deleted_keys = [c.args[0] for c in pipeline.delete.call_args_list]
        assert "user:user-1:chatmessages:chat-123" in deleted_keys
        assert "user:user-1:chatmeta:chat-123" in deleted_keys
        pipeline.zrem.assert_called_once_with("user:user-1:chats", "chat-123")
        pipeline.execute.assert_called_once()

    asyncio.run(run())


def test_delete_chat_raises_on_redis_error(chat_history, mock_redis_client):
    """Test that delete_chat raises exception on Redis error."""

    async def run():
        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.execute.side_effect = Exception("redis down")

        with pytest.raises(Exception, match="redis down"):
            await chat_history.delete_chat("chat-123", "user-1")

    asyncio.run(run())


def test_redis_key_format(chat_history):
    """Test that Redis keys follow the correct format."""
    assert chat_history._redis_user_chats_key("user-1") == "user:user-1:chats"
    assert chat_history._redis_chat_messages_key("chat-123", "user-1") == "user:user-1:chatmessages:chat-123"


def test_update_last_assistant_message_attaches_fact_check(chat_history, mock_redis_client):
    """Happy path: tail is the expected assistant turn — fact_check is written back."""

    async def run():
        assistant_ts = 1234567891.0
        existing = ChatMessageItem(
            role="assistant",
            text="answer",
            timestamp=assistant_ts,
            citations=[{"index": 1, "title": "t"}],
            search_queries={"historical_query": "q1", "book_query": None},
        )
        mock_redis_client.lindex.return_value = existing.model_dump_json()

        fact_check = {"flagged": True, "issues": [{"claim": "c", "explanation": "e"}]}
        await chat_history.update_last_assistant_message(
            "chat-123", "user-1", expected_timestamp=assistant_ts, fact_check=fact_check
        )

        mock_redis_client.lindex.assert_called_once_with("user:user-1:chatmessages:chat-123", -1)

        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.lset.assert_called_once()
        lset_args = pipeline.lset.call_args[0]
        assert lset_args[0] == "user:user-1:chatmessages:chat-123"
        assert lset_args[1] == -1

        stored = ChatMessageItem.model_validate_json(lset_args[2])
        assert stored.fact_check == fact_check
        assert stored.text == "answer"
        assert stored.search_queries == {"historical_query": "q1", "book_query": None}
        assert pipeline.expire.call_count == 2

    asyncio.run(run())


def test_update_last_assistant_message_empty_chat_noops(chat_history, mock_redis_client):
    """No tail message: skip the write."""

    async def run():
        mock_redis_client.lindex.return_value = None

        await chat_history.update_last_assistant_message(
            "chat-123", "user-1", expected_timestamp=1.0, fact_check={"flagged": False, "issues": []}
        )

        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.lset.assert_not_called()

    asyncio.run(run())


def test_update_last_assistant_message_timestamp_mismatch_noops(chat_history, mock_redis_client):
    """Concurrent-turn race guard: timestamp differs — skip the write."""

    async def run():
        existing = ChatMessageItem(role="assistant", text="answer", timestamp=999.0)
        mock_redis_client.lindex.return_value = existing.model_dump_json()

        await chat_history.update_last_assistant_message(
            "chat-123", "user-1", expected_timestamp=1.0, fact_check={"flagged": False, "issues": []}
        )

        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.lset.assert_not_called()

    asyncio.run(run())


def test_update_last_assistant_message_user_tail_noops(chat_history, mock_redis_client):
    """Defensive: tail is a user message (shouldn't happen) — skip the write."""

    async def run():
        existing = ChatMessageItem(role="user", text="q", timestamp=1.0)
        mock_redis_client.lindex.return_value = existing.model_dump_json()

        await chat_history.update_last_assistant_message(
            "chat-123", "user-1", expected_timestamp=1.0, fact_check={"flagged": False, "issues": []}
        )

        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.lset.assert_not_called()

    asyncio.run(run())


def test_message_ttl_is_30_days(chat_history, mock_redis_client):
    """Test that messages expire after 30 days."""

    async def run():
        message = ChatMessageItem(role="user", text="Test message", timestamp=time.time())

        await chat_history.add_messages("chat-123", "user-1", [message])

        # Verify TTL is set to 30 days (2592000 seconds)
        pipeline = await mock_redis_client.pipeline().__aenter__()
        expire_calls = pipeline.expire.call_args_list

        # Should expire both the messages key and the user chats key
        assert len(expire_calls) == 2

        # Both should have 2592000 seconds TTL
        for call in expire_calls:
            assert call[0][1] == 2592000  # 30 * 24 * 60 * 60

    asyncio.run(run())


def test_max_messages_limit_enforced(chat_history, mock_redis_client):
    """Test that messages are trimmed to max_messages limit."""

    async def run():
        message = ChatMessageItem(role="user", text="Test", timestamp=time.time())

        await chat_history.add_messages("chat-123", "user-1", [message])

        # Verify ltrim was called with max_messages limit
        pipeline = await mock_redis_client.pipeline().__aenter__()
        pipeline.ltrim.assert_called_once_with("user:user-1:chatmessages:chat-123", -100, -1)

    asyncio.run(run())


def test_max_chat_history_limit(chat_history, mock_redis_client):
    """Test that get_user_chats respects the MAX_CHAT_HISTORY limit."""

    async def run():
        # Mock 50 chat IDs
        mock_redis_client.zrevrange.return_value = [f"chat-{i}" for i in range(50)]
        pipeline = mock_redis_client.pipeline.return_value
        pipeline.execute = AsyncMock(return_value=[None] * 50)

        chats = await chat_history.get_user_chats("user-1")

        # Verify zrevrange was called with limit of 50
        mock_redis_client.zrevrange.assert_called_once_with("user:user-1:chats", 0, 49)
        assert len(chats) == 50

    asyncio.run(run())


def test_citation_persistence_workflow(chat_history, mock_redis_client):
    """Test full workflow: add messages with citations, then retrieve them."""

    async def run():
        # Add user message
        user_msg = ChatMessageItem(role="user", text="What were TR's views on national parks?", timestamp=time.time())
        await chat_history.add_messages("chat-123", "user-1", [user_msg])

        # Add assistant message with citations
        citations = [
            {"index": 1, "title": "Yellowstone Speech", "date": "1903"},
            {"index": 4, "title": "Grand Canyon Visit", "date": "1908"},
        ]
        assistant_msg = ChatMessageItem(
            role="assistant",
            text="TR established the National Park Service...",
            timestamp=time.time(),
            citations=citations,
        )
        await chat_history.add_messages("chat-123", "user-1", [assistant_msg])

        # Mock retrieval
        mock_redis_client.lrange.return_value = [user_msg.model_dump_json(), assistant_msg.model_dump_json()]

        # Retrieve messages
        retrieved = await chat_history.get_chat_messages("chat-123", "user-1")

        # Verify citations are preserved
        assert len(retrieved) == 2
        assert retrieved[1].citations is not None
        assert len(retrieved[1].citations) == 2
        assert retrieved[1].citations[0]["index"] == 1
        assert retrieved[1].citations[1]["index"] == 4

    asyncio.run(run())


def test_multiple_chats_per_user(chat_history, mock_redis_client):
    """Test that a user can have multiple chats."""

    async def run():
        message1 = ChatMessageItem(role="user", text="Chat 1 message", timestamp=time.time())
        message2 = ChatMessageItem(role="user", text="Chat 2 message", timestamp=time.time())

        # Add messages to different chats for same user
        await chat_history.add_messages("chat-1", "user-1", [message1])
        await chat_history.add_messages("chat-2", "user-1", [message2])

        # Mock user chats response
        mock_redis_client.zrevrange.return_value = ["chat-2", "chat-1"]
        pipeline = mock_redis_client.pipeline.return_value
        pipeline.execute = AsyncMock(return_value=[None, None])

        chats = await chat_history.get_user_chats("user-1")

        # Verify both chats are tracked
        assert len(chats) == 2
        chat_ids = [c.chat_id for c in chats]
        assert "chat-1" in chat_ids
        assert "chat-2" in chat_ids

    asyncio.run(run())


def test_set_and_get_chat_metadata(chat_history, mock_redis_client):
    """Round-trip the mode through Redis."""

    async def run():
        mock_redis_client.set = AsyncMock(return_value=True)
        mock_redis_client.get = AsyncMock(return_value=b'{"mode": "research"}')

        await chat_history.set_chat_metadata("chat-1", "user-1", mode="research")

        # Verify the set call: correct key, JSON payload, TTL parity with chat history.
        call = mock_redis_client.set.call_args
        assert call.args[0] == "user:user-1:chatmeta:chat-1"
        assert '"mode": "research"' in call.args[1]
        assert call.kwargs["ex"] == 30 * 24 * 60 * 60

        meta = await chat_history.get_chat_metadata("chat-1", "user-1")
        assert meta == {"mode": "research"}
        mock_redis_client.get.assert_awaited_once_with("user:user-1:chatmeta:chat-1")

    asyncio.run(run())


def test_get_chat_metadata_returns_none_when_missing(chat_history, mock_redis_client):
    async def run():
        mock_redis_client.get = AsyncMock(return_value=None)
        meta = await chat_history.get_chat_metadata("chat-1", "user-1")
        assert meta is None

    asyncio.run(run())


def test_delete_chat_clears_metadata(chat_history, mock_redis_client):
    """delete_chat should issue a delete for the metadata key alongside messages."""

    async def run():
        await chat_history.delete_chat("chat-1", "user-1")

        pipeline = mock_redis_client.pipeline.return_value
        # Inspect every `pipe.delete(...)` call argument.
        deleted_keys = [c.args[0] for c in pipeline.delete.call_args_list]
        assert "user:user-1:chatmessages:chat-1" in deleted_keys
        assert "user:user-1:chatmeta:chat-1" in deleted_keys

    asyncio.run(run())
