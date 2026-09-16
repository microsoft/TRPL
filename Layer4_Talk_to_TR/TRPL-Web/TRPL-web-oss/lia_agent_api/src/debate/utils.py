# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from enum import Enum
from typing import Any, TYPE_CHECKING
import asyncio
import threading
import time
from collections import Counter
from api.config import config

if TYPE_CHECKING:
    from autogen import AssistantAgent

import logging

logger = logging.getLogger(f"lia.{__name__}")


class LLMErrorType(Enum):
    """Types of LLM API errors"""

    CONTENT_FILTER = "content_filter"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


class LLMError(Exception):
    """Exception raised when LLM API calls fail.

    Attributes:
        error_type: The type of error (content filter, rate limit, etc.)
        message: Human-readable error message
        details: Optional dict with additional error details
    """

    def __init__(
        self,
        error_type: LLMErrorType,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.error_type = error_type
        self.message = message
        self.details = details or {}
        super().__init__(message)

    @classmethod
    def from_exception(cls, e: Exception) -> "LLMError":
        """Parse an exception and create an appropriate LLMError."""
        error_str = str(e).lower()

        # Check for content filter errors
        if any(
            x in error_str
            for x in [
                "content_filter",
                "content management policy",
                "responsibleaipolicyviolation",
            ]
        ):
            return cls(
                error_type=LLMErrorType.CONTENT_FILTER,
                message="Response filtered due to content policy",
                details={"original_error": str(e)},
            )

        # Check for rate limit errors
        if any(
            x in error_str
            for x in ["rate limit", "ratelimit", "429", "too many requests"]
        ):
            return cls(
                error_type=LLMErrorType.RATE_LIMIT,
                message="Rate limit exceeded",
                details={"original_error": str(e)},
            )

        # Check for timeout errors
        if any(x in error_str for x in ["timeout", "timed out", "deadline exceeded"]):
            return cls(
                error_type=LLMErrorType.TIMEOUT,
                message="Request timed out",
                details={"original_error": str(e)},
            )

        # Check for server errors (5xx)
        if any(
            x in error_str
            for x in ["500", "502", "503", "504", "server error", "internal error"]
        ):
            return cls(
                error_type=LLMErrorType.SERVER_ERROR,
                message="Server error occurred",
                details={"original_error": str(e)},
            )

        # Unknown error type
        return cls(
            error_type=LLMErrorType.UNKNOWN,
            message=f"LLM API error: {str(e)}",
            details={"original_error": str(e)},
        )


class BroadcastQueue:
    """Queue that broadcasts messages to multiple consumers"""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._message_buffer: list[Any] = []
        self._websocket_queue: asyncio.Queue | None = None
        self._subscriber_meta: dict[asyncio.Queue, dict[str, Any]] = {}
        self._audio_subscriber_count: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def _capture_loop(self) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    def _put_nowait_internal(self, item: Any) -> None:
        with self._thread_lock:
            if not self._websocket_subscribed:
                self._message_buffer.append(item)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait(item)

    @property
    def _websocket_subscribed(self) -> bool:
        return self._websocket_queue is not None

    @property
    def websocket_subscribed(self) -> bool:
        return self._websocket_subscribed

    @property
    def any_audio_subscribers(self) -> bool:
        return self._audio_subscriber_count > 0

    async def subscribe(self, *, audio_enabled: bool = False) -> asyncio.Queue:
        """Subscribe to messages and return a queue for this subscriber (for debug agents)"""
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                queue = asyncio.Queue()
                self._subscribers.append(queue)
                self._subscriber_meta[queue] = {"audio_enabled": audio_enabled}
                if audio_enabled:
                    self._audio_subscriber_count += 1
                return queue

    async def subscribe_websocket(
        self, *, audio_enabled: bool = False
    ) -> asyncio.Queue:
        """Subscribe to messages as the WebSocket client and replay buffered messages"""
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                queue = asyncio.Queue()
                self._subscribers.append(queue)
                self._websocket_queue = queue
                self._subscriber_meta[queue] = {"audio_enabled": audio_enabled}
                if audio_enabled:
                    self._audio_subscriber_count += 1

                # Replay all buffered messages to the WebSocket queue
                buffered_messages = list(self._message_buffer)
                self._message_buffer.clear()

        # Replay messages without holding the lock
        for message in buffered_messages:
            await queue.put(message)

        return queue

    async def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribe a queue"""
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)
                if queue == self._websocket_queue:
                    self._websocket_queue = None
                meta = self._subscriber_meta.pop(queue, None)
                if meta and meta.get("audio_enabled"):
                    self._audio_subscriber_count = max(
                        0, self._audio_subscriber_count - 1
                    )

    async def set_audio_enabled(self, queue: asyncio.Queue, enabled: bool) -> None:
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                meta = self._subscriber_meta.get(queue)
                if meta is None:
                    return
                current = bool(meta.get("audio_enabled", False))
                enabled = bool(enabled)
                if current == enabled:
                    return
                meta["audio_enabled"] = enabled
                if enabled:
                    self._audio_subscriber_count += 1
                else:
                    self._audio_subscriber_count = max(
                        0, self._audio_subscriber_count - 1
                    )

    async def put(self, item):
        """Put an item into all subscriber queues, buffering if WebSocket not subscribed"""
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                subscribers = list(self._subscribers)
                # If WebSocket hasn't subscribed yet, buffer the message
                if not self._websocket_subscribed:
                    self._message_buffer.append(item)

        for queue in subscribers:
            await queue.put(item)

    async def put_if_websocket_subscribed(self, item) -> bool:
        """Broadcast without buffering, only while a WebSocket is subscribed."""
        self._capture_loop()
        async with self._lock:
            with self._thread_lock:
                if not self._websocket_subscribed:
                    return False
                subscribers = list(self._subscribers)

        for queue in subscribers:
            await queue.put(item)
        return True

    def put_nowait(self, item):
        """Put an item into all subscriber queues, buffering if WebSocket not subscribed"""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        loop = self._loop
        if loop is not None:
            if current_loop is loop:
                self._put_nowait_internal(item)
                return
            if loop.is_closed():
                return
            loop.call_soon_threadsafe(self._put_nowait_internal, item)
            return

        # Fallback before first async subscription/put establishes the loop.
        self._put_nowait_internal(item)


def get_usage_dict(agent: "AssistantAgent") -> dict[str, Any]:
    """Get the usage dictionary for an agent"""
    usage = agent.client.total_usage_summary
    if not usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
        }
    model_key = [k for k in usage.keys() if k != "total_cost"][0]
    return usage[model_key]


def format_usage(agents: list[AssistantAgent], include_per_agent: bool = True) -> str:
    """Format total token usage and cost for a list of agents"""
    data = {agent.name: get_usage_dict(agent) for agent in agents}
    totals = Counter()
    for usage in data.values():
        totals += Counter(usage)

    lines = [
        "=== LLM Usage Summary ===",
        f"Total Tokens: {totals['total_tokens']:,} "
        f"(Prompt: {totals['prompt_tokens']:,}, "
        f"Completion: {totals['completion_tokens']:,})",
        f"Total Cost: ${totals['cost']:.5f}",
    ]

    if include_per_agent:
        lines.append("\nPer-Agent Breakdown:")
        for agent_name, usage in data.items():
            lines.append(
                f"  {agent_name}: {usage['total_tokens']:,} tokens (${usage['cost']:.5f})"
            )

    return "\n".join(lines)


async def _a_generate_reply_threaded(
    agent: AssistantAgent,
    messages: list[dict],
) -> str | dict[str, Any] | None:
    """
    Wrapper for generate_reply that runs in a thread to prevent event loop blocking.

    Args:
        agent: The AssistantAgent instance
        messages: List of message dicts

    Raises:
        LLMError: When LLM API call fails (content filter, rate limit, timeout, etc.)
    """

    logger.debug(f"{agent.__class__.__name__} payload: {messages}")
    usage_before = get_usage_dict(agent)
    start_time = time.perf_counter()

    try:
        # Make the call
        response = await asyncio.to_thread(agent.generate_reply, messages=messages)
    except asyncio.CancelledError:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.info(
            f"[LLM Call CANCELLED] agent={agent.name} " f"duration={duration:.2f}s"
        )
        raise
    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time
        logger.error(
            f"[LLM Call FAILED] agent={agent.name} "
            f"duration={duration:.2f}s "
            f"error={str(e)}"
        )
        # Convert to LLMError and re-raise
        raise LLMError.from_exception(e) from e

    end_time = time.perf_counter()
    duration = end_time - start_time
    usage_after = get_usage_dict(agent)
    usage_diff = {k: v - usage_before[k] for k, v in usage_after.items()}

    # Extract the actual content and usage from response
    # Response might be a dict with "content" and "usage" keys, or just a string
    if isinstance(response, dict):
        result = response.get("content", response)
    else:
        result = response

    # Check if the response itself contains an error message (some APIs return errors in content)
    if isinstance(result, str) and "error" in result.lower():
        # Check for content filter in the response content
        if any(
            x in result.lower()
            for x in [
                "content_filter",
                "content management policy",
                "responsibleaipolicyviolation",
            ]
        ):
            logger.error(
                f"[LLM Call CONTENT FILTERED] agent={agent.name} "
                f"duration={duration:.2f}s"
            )
            raise LLMError(
                error_type=LLMErrorType.CONTENT_FILTER,
                message="Response filtered due to content policy",
                details={"response": result},
            )

    # Log the call
    logger.info(
        f"[LLM Call] agent={agent.name} "
        f"tokens={usage_diff['total_tokens']} "
        f"(prompt={usage_diff['prompt_tokens']}, "
        f"completion={usage_diff['completion_tokens']}) "
        f"cost=${usage_diff['cost']:.5f} "
        f"duration={duration:.2f}s"
    )

    return result


async def call_agent_json(
    agent: AssistantAgent,
    payload: dict[str, Any],
) -> dict[str, Any]:
    reply = (
        await _a_generate_reply_threaded(
            agent,
            messages=[
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
            ],
        )
        or ""
    )
    if config.print_agent_json:
        print(f"\n-- {agent.name} (RAW) --\n{reply}")
    try:
        start, end = reply.find("{"), reply.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(reply[start : end + 1])
    except Exception:
        pass
    return {}


def build_agent(name: str, sys_msg: str, llm_cfg: dict) -> AssistantAgent:
    return AssistantAgent(name=name, system_message=sys_msg, llm_config=llm_cfg)
