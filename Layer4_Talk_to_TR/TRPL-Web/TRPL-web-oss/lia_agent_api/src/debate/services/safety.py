# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Brain-side content moderation.

Lives in lia_agent_api so that:
  * User-input checks can short-circuit the LLM call entirely (saves tokens
    on rejected inputs).
  * Model-output checks can halt the LLM stream mid-flight (saves tokens
    on rejected outputs — the OpenAI stream is closed, not just discarded).
  * Multiple front-ends (livekit, REST, mobile, …) inherit the same policy
    via the existing WS / output-queue contract.

Wire model
----------
SafetyService is owned per-DebateSession. It runs a single asyncio task
that pulls from an internal queue and calls OpenAI's moderation endpoint.
Callers feed it via non-blocking `check_user` / `feed_model_delta` —
the hot path is never gated on a moderation RPC.

When a check trips, the service:
  1. Marks the affected utterance_id (or, for user-side hits, the next
     upcoming utterance) as "killed".
  2. Emits a `safety_interrupt` event on the session's output_queue so
     all WS subscribers (livekit_worker, browser observers, …) can act.

OutputStreamSession.write() consults `is_killed(uid)` and raises
`StreamCancelled` to halt the LLM consumer loop, which propagates to
llm_streaming.* and closes the OpenAI stream.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger(f"lia.{__name__}")

_MODERATION_URL = "https://api.openai.com/v1/moderations"


class StreamCancelled(Exception):
    """Raised by OutputStreamSession.write() when safety has flagged the
    in-flight utterance. Caught by stream_text_response / stream_json_field
    to break out of the LLM consumer loop without raising LLMError.
    """


class SafetyService:
    def __init__(
        self,
        output_queue,
        *,
        enabled: bool,
        api_key: str,
        model: str = "omni-moderation-latest",
        flush_chars: int = 80,
        # Sliding-window overlap. After flushing a chunk, we keep the
        # last `overlap_chars` characters so phrases that straddle a
        # flush boundary still appear together in the next call.
        overlap_chars: int = 32,
        request_timeout: float = 10.0,
    ):
        self._output_queue = output_queue
        self._enabled_flag = enabled
        self._api_key = api_key
        self._model = model
        self._flush_chars = flush_chars
        self._overlap_chars = max(0, overlap_chars)
        self._request_timeout = request_timeout

        self._http: Optional[aiohttp.ClientSession] = None
        # Items are (role, text, utterance_id-or-None).
        self._queue: asyncio.Queue[tuple[str, str, Optional[str]]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

        # Per-utterance accumulator. flush_chars triggers a moderation
        # call; overlap_chars are kept after the flush.
        self._model_buffer: dict[str, str] = {}
        self._buffer_has_new: dict[str, bool] = {}

        # Utterances that have been flagged unsafe — OutputStreamSession
        # checks this set before each write.
        self._killed: set[str] = set()
        # Pre-flag for the next utterance (kill on creation). Holds
        # the originating event metadata so consume_kill_next can fire
        # the right event_type ("safety_interrupt" vs "user_interrupt").
        self._kill_next: Optional[dict] = None

        self._closed = False

    # ─── lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._enabled_flag:
            logger.info("SAFETY_ENABLED=false — safety service disabled")
            return
        if not self._api_key:
            logger.warning("OPENAI_API_KEY missing — safety service disabled")
            return
        self._http = aiohttp.ClientSession()
        self._worker_task = asyncio.create_task(
            self._run(), name="lia_safety_service"
        )
        logger.info(
            "safety service started (model=%s, flush=%d, overlap=%d)",
            self._model, self._flush_chars, self._overlap_chars,
        )

    async def close(self) -> None:
        self._closed = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._http and not self._http.closed:
            await self._http.close()

    @property
    def enabled(self) -> bool:
        return self._worker_task is not None

    # ─── kill-state queries (called on the hot path) ──────────────────

    def is_killed(self, utterance_id: str) -> bool:
        return utterance_id in self._killed

    async def kill_utterance(
        self,
        utterance_id: str | None,
        *,
        event_type: str = "user_interrupt",
        role: str = "user",
        reason: str = "",
    ) -> None:
        """Public kill switch. Marks the utterance dead so OutputStreamSession
        raises StreamCancelled on next write (closing the OpenAI stream),
        and emits the corresponding interrupt event on the output queue.

        Works regardless of the SAFETY_ENABLED flag — the kill machinery
        is independent of the moderation worker. Used by barge-in (user
        interrupts mid-speech) as well as by the safety worker itself.
        """
        if utterance_id is None:
            self._kill_next = {
                "event_type": event_type, "role": role, "reason": reason,
            }
            return
        self._killed.add(utterance_id)
        self._model_buffer.pop(utterance_id, None)
        self._buffer_has_new.pop(utterance_id, None)
        await self._emit_interrupt(
            utterance_id, role=role, reason=reason, event_type=event_type,
        )

    def consume_kill_next(self, utterance_id: str) -> bool:
        """If something pre-flagged the next utterance, mark this one
        as killed and clear the pending flag. Called by OutputManager
        when a new OutputStreamSession is created. Re-emits the
        original event type (safety_interrupt or user_interrupt) now
        that the uid is known.
        """
        meta = self._kill_next
        if not meta:
            return False
        self._kill_next = None
        self._killed.add(utterance_id)
        asyncio.create_task(
            self._emit_interrupt(
                utterance_id,
                role=meta.get("role", "user"),
                reason=meta.get("reason", ""),
                event_type=meta.get("event_type", "safety_interrupt"),
            )
        )
        return True

    # ─── ingestion (non-blocking) ─────────────────────────────────────

    def check_user(self, text: str) -> None:
        if not self.enabled or not text or not text.strip():
            return
        try:
            self._queue.put_nowait(("user", text, None))
        except asyncio.QueueFull:
            logger.warning("safety queue full — dropping user check")

    def feed_model_delta(self, utterance_id: str, delta: str) -> None:
        if not self.enabled or not delta:
            return
        if utterance_id in self._killed:
            return  # already cut, don't keep moderating the tail
        buf = self._model_buffer.get(utterance_id, "") + delta
        self._buffer_has_new[utterance_id] = True
        if len(buf) >= self._flush_chars:
            try:
                self._queue.put_nowait(("model", buf, utterance_id))
            except asyncio.QueueFull:
                logger.warning("safety queue full — dropping model chunk")
            self._model_buffer[utterance_id] = (
                buf[-self._overlap_chars:] if self._overlap_chars else ""
            )
            self._buffer_has_new[utterance_id] = False
        else:
            self._model_buffer[utterance_id] = buf

    def flush_model(self, utterance_id: str) -> None:
        buf = self._model_buffer.pop(utterance_id, "")
        has_new = self._buffer_has_new.pop(utterance_id, False)
        if (
            self.enabled
            and has_new
            and buf.strip()
            and utterance_id not in self._killed
        ):
            try:
                self._queue.put_nowait(("model", buf, utterance_id))
            except asyncio.QueueFull:
                pass

    def reset_utterance(self, utterance_id: str) -> None:
        self._model_buffer.pop(utterance_id, None)
        self._buffer_has_new.pop(utterance_id, None)
        self._killed.discard(utterance_id)

    # ─── worker ───────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._closed:
            try:
                role, text, uid = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                unsafe, reason = await self._moderate(text)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("moderation call crashed")
                continue
            if not unsafe:
                continue
            logger.warning("UNSAFE %s [%s] uid=%s text=%r",
                           role, reason, uid, text[:120])
            try:
                await self._dispatch_unsafe(role, reason, uid)
            except Exception:  # noqa: BLE001
                logger.exception("safety dispatch failed")

    async def _dispatch_unsafe(
        self,
        role: str,
        reason: str,
        utterance_id: Optional[str],
    ) -> None:
        if role == "model" and utterance_id:
            # Mark current utterance dead. OutputStreamSession.write()
            # will raise StreamCancelled on next call, halting the LLM.
            self._killed.add(utterance_id)
            await self._emit_interrupt(utterance_id, role=role, reason=reason)
            return

        if role == "user":
            # User input flagged. The reply may not have started yet —
            # arm the next-utterance kill flag. If a reply is already
            # streaming, also kill it now.
            self._kill_next = {
                "event_type": "safety_interrupt",
                "role": role,
                "reason": reason,
            }
            for active_uid in list(self._model_buffer.keys()):
                self._killed.add(active_uid)
                await self._emit_interrupt(active_uid, role=role, reason=reason)

    async def _emit_interrupt(
        self,
        utterance_id: str,
        *,
        role: str,
        reason: str = "",
        event_type: str = "safety_interrupt",
    ) -> None:
        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "utterance_id": utterance_id,
            "role": role,
            "reason": reason,
        }
        try:
            await self._output_queue.put(event)
        except Exception:  # noqa: BLE001
            logger.exception("failed to emit safety_interrupt")

    async def _moderate(self, text: str) -> tuple[bool, str]:
        assert self._http is not None
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {"model": self._model, "input": text}
        try:
            async with self._http.post(
                _MODERATION_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            ) as r:
                if r.status >= 400:
                    body = await r.text()
                    logger.warning("moderations HTTP %d: %s", r.status, body[:200])
                    return False, ""
                data = await r.json()
        except asyncio.TimeoutError:
            logger.warning(
                "moderations timeout (%.1fs) — skipping",
                self._request_timeout,
            )
            return False, ""
        results = data.get("results") or []
        if not results:
            return False, ""
        result = results[0]
        if not result.get("flagged"):
            return False, ""
        cats = [k for k, v in (result.get("categories") or {}).items() if v]
        return True, ",".join(cats) or "flagged"


def safety_from_env(output_queue) -> SafetyService:
    return SafetyService(
        output_queue,
        enabled=os.getenv("SAFETY_ENABLED", "false").lower() == "true",
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("SAFETY_MODEL", "omni-moderation-latest"),
        flush_chars=int(os.getenv("SAFETY_FLUSH_CHARS", "80")),
        overlap_chars=int(os.getenv("SAFETY_OVERLAP_CHARS", "32")),
    )
