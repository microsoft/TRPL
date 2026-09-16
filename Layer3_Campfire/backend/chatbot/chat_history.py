# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import time

from models import ChatMessageItem, ChatMode, ChatSummary
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CHAT_TTL_DAYS = 30
MAX_CHAT_HISTORY = 50


class ChatHistoryManager:
    """Manages chat message persistence in Redis with 30-day TTL."""

    def __init__(self, redis_client: Redis, max_messages: int = 100):
        self.redis_client = redis_client
        self.max_messages = max_messages

    def _redis_user_chats_key(self, user_id: str) -> str:
        """Redis key for user's chat list (sorted set)."""
        return f"user:{user_id}:chats"

    def _redis_chat_messages_key(self, chat_id: str, user_id: str) -> str:
        """Redis key for chat messages list."""
        return f"user:{user_id}:chatmessages:{chat_id}"

    def _redis_chat_meta_key(self, chat_id: str, user_id: str) -> str:
        """Redis key for chat-level metadata (e.g. selected mode)."""
        return f"user:{user_id}:chatmeta:{chat_id}"

    async def get_user_chats(self, user_id: str) -> list[ChatSummary]:
        """Get this user's chats (most-recent first) with their stored mode.

        Chats with no stored metadata fall back to mode="discovery" so the
        response is always well-formed for the frontend's mode selector.

        Metadata fetches are pipelined: at MAX_CHAT_HISTORY=50, that's one
        zrevrange + one pipelined GET batch — 2 Redis round trips total.
        """
        cutoff_ts = time.time() - (CHAT_TTL_DAYS * 24 * 60 * 60)
        await self.redis_client.zremrangebyscore(
            self._redis_user_chats_key(user_id),
            "-inf",
            cutoff_ts,
        )
        chat_ids = await self.redis_client.zrevrange(self._redis_user_chats_key(user_id), 0, MAX_CHAT_HISTORY - 1)
        if not chat_ids:
            return []

        async with self.redis_client.pipeline() as pipe:
            for cid in chat_ids:
                cid_str = cid.decode("utf-8") if isinstance(cid, bytes) else cid
                pipe.get(self._redis_chat_meta_key(cid_str, user_id))
            raw_metas = await pipe.execute()

        summaries: list[ChatSummary] = []
        for cid, raw in zip(chat_ids, raw_metas):
            cid_str = cid.decode("utf-8") if isinstance(cid, bytes) else cid
            mode: ChatMode = "discovery"
            if raw is not None:
                payload = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                try:
                    mode = json.loads(payload).get("mode", "discovery")
                except (ValueError, TypeError):
                    pass
            summaries.append(ChatSummary(chat_id=cid_str, mode=mode))
        return summaries

    async def get_chat_messages(self, chat_id: str, user_id: str) -> list[ChatMessageItem]:
        """Retrieve message history from Redis as JSON."""
        messages = await self.redis_client.lrange(self._redis_chat_messages_key(chat_id, user_id), 0, -1)  # ty:ignore[invalid-await]  # pyright: ignore[reportGeneralTypeIssues]
        return [ChatMessageItem.model_validate_json(msg) for msg in messages]

    async def add_messages(self, chat_id: str, user_id: str, messages: list[ChatMessageItem]):
        """Add message to Redis list with TTL using pipeline."""
        ttl_seconds = CHAT_TTL_DAYS * 24 * 60 * 60

        # Use pipeline to batch Redis operations
        async with self.redis_client.pipeline() as pipe:
            # Add message to chat history
            for message in messages:
                pipe.rpush(self._redis_chat_messages_key(chat_id, user_id), message.model_dump_json())
            pipe.ltrim(self._redis_chat_messages_key(chat_id, user_id), -self.max_messages, -1)
            # Update user's chat list (sorted set by timestamp)
            pipe.zadd(self._redis_user_chats_key(user_id), {chat_id: messages[-1].timestamp})
            # Set TTL on both keys
            pipe.expire(self._redis_chat_messages_key(chat_id, user_id), ttl_seconds)
            pipe.expire(self._redis_user_chats_key(user_id), ttl_seconds)

            await pipe.execute()

    async def update_last_assistant_message(
        self,
        chat_id: str,
        user_id: str,
        *,
        expected_timestamp: float,
        fact_check: dict | None = None,
        follow_up_questions: list[str] | None = None,
    ) -> None:
        """Attach post-final debug fields to the most recent assistant message.

        Currently writes fact-check and follow-up question results. Best-effort:
        skips and logs if the tail is missing or doesn't match the expected
        assistant turn (defends against concurrent turns racing on the same
        chat). Only fields explicitly passed (non-None) are written.
        """
        if fact_check is None and follow_up_questions is None:
            return

        key = self._redis_chat_messages_key(chat_id, user_id)
        raw = await self.redis_client.lindex(key, -1)  # ty:ignore[invalid-await]  # pyright: ignore[reportGeneralTypeIssues]
        if raw is None:
            logger.warning(
                "update_last_assistant_message: no messages in chat",
                extra={"chat_id": chat_id, "user_id": user_id},
            )
            return

        message = ChatMessageItem.model_validate_json(raw)
        if message.role != "assistant" or message.timestamp != expected_timestamp:
            logger.warning(
                "update_last_assistant_message: tail does not match expected assistant turn — skipping",
                extra={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "tail_role": message.role,
                    "tail_timestamp": message.timestamp,
                    "expected_timestamp": expected_timestamp,
                },
            )
            return

        if fact_check is not None:
            message.fact_check = fact_check
        if follow_up_questions is not None:
            message.follow_up_questions = follow_up_questions
        ttl_seconds = CHAT_TTL_DAYS * 24 * 60 * 60
        async with self.redis_client.pipeline() as pipe:
            pipe.lset(key, -1, message.model_dump_json())
            pipe.expire(key, ttl_seconds)
            pipe.expire(self._redis_user_chats_key(user_id), ttl_seconds)
            await pipe.execute()

    async def delete_chat(self, chat_id: str, user_id: str) -> None:
        """Delete this chat's messages, metadata, and remove from user's chat list."""
        async with self.redis_client.pipeline() as pipe:
            pipe.delete(self._redis_chat_messages_key(chat_id, user_id))
            pipe.delete(self._redis_chat_meta_key(chat_id, user_id))
            pipe.zrem(self._redis_user_chats_key(user_id), chat_id)
            await pipe.execute()

    async def get_chat_metadata(self, chat_id: str, user_id: str) -> dict | None:
        """Return stored chat metadata (e.g. {"mode": "research"}) or None if absent.

        Used both to restore mode when a user resumes a chat and to enforce that a
        chat's mode does not change mid-session.
        """
        raw = await self.redis_client.get(self._redis_chat_meta_key(chat_id, user_id))  # ty:ignore[invalid-await]  # pyright: ignore[reportGeneralTypeIssues]
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def set_chat_metadata(self, chat_id: str, user_id: str, *, mode: ChatMode) -> None:
        """Persist chat-level metadata. Idempotent — call on every message after the
        first to refresh TTL alongside the message history.
        """
        ttl_seconds = CHAT_TTL_DAYS * 24 * 60 * 60
        payload = json.dumps({"mode": mode})
        await self.redis_client.set(  # ty:ignore[invalid-await]  # pyright: ignore[reportGeneralTypeIssues]
            self._redis_chat_meta_key(chat_id, user_id),
            payload,
            ex=ttl_seconds,
        )
