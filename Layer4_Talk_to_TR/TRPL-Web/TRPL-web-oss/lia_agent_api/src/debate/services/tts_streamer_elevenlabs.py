# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
import os
import time
from typing import Any

import requests

from api.config import config, VoicePreset
from debate.utils import BroadcastQueue

logger = logging.getLogger(f"lia.{__name__}")

_tts_engine: "TTSEngine | None" = None
_tts_engine_initialized = False

_DEFAULT_OUTPUT_FORMAT = "pcm_22050"
_DEFAULT_MODEL_ID = "eleven_v3"
_DEFAULT_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM
_DEFAULT_CHANNELS = 1


def _build_audio_header(utterance_id: int, chunk_index: int, is_done: bool) -> bytes:
    header = ((utterance_id & 0xFFF) << 16) | (chunk_index & 0xFFFF)
    if is_done:
        header |= 0x10000000
    return header.to_bytes(4, "big")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_tts_engine() -> "TTSEngine | None":
    global _tts_engine, _tts_engine_initialized
    if _tts_engine_initialized:
        return _tts_engine

    _tts_engine_initialized = True
    if not config.tts_enabled:
        logger.info("TTS disabled: TTS_ENABLED=false.")
        return None

    api_key = _env("ELEVEN_API_KEY")
    voice_id = _env("ELEVEN_VOICE_ID")
    if not api_key or not voice_id:
        logger.info("TTS disabled: missing ELEVEN_API_KEY or ELEVEN_VOICE_ID.")
        return None

    _tts_engine = TTSEngine(
        api_key=api_key,
        voice_id=voice_id,
        model_id=_env("ELEVEN_MODEL_ID", _DEFAULT_MODEL_ID),
        output_format=_env("ELEVEN_OUTPUT_FORMAT", _DEFAULT_OUTPUT_FORMAT),
        base_url=_env("ELEVEN_BASE_URL", "https://api.elevenlabs.io"),
        sample_width_bytes=int(_env("ELEVEN_SAMPLE_WIDTH_BYTES", str(_DEFAULT_SAMPLE_WIDTH_BYTES))),
        channels=int(_env("ELEVEN_CHANNELS", str(_DEFAULT_CHANNELS))),
    )
    return _tts_engine


@dataclass
class TTSSession:
    utterance_id: int
    _output_queue: BroadcastQueue
    _loop: asyncio.AbstractEventLoop
    _chunk_size_bytes: int | None
    _engine: "TTSEngine"
    _chunk_index: int = 0
    _pending_chunk: tuple[int, bytes] | None = None
    _buffer: bytearray = field(default_factory=bytearray)
    _done_event: asyncio.Event = field(default_factory=asyncio.Event)
    _pending_puts: list[Any] = field(default_factory=list)
    _pending_puts_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _text_parts: list[str] = field(default_factory=list)
    _started: bool = False
    _closed: bool = False
    _producer_thread: threading.Thread | None = None
    _remainder: bytes = b""

    def write(self, text: str) -> None:
        if not text or self._closed:
            return
        self._text_parts.append(text)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._start_if_needed()

    async def wait_done(self, timeout: float | None = None) -> None:
        logger.debug("TTS waiting for done: utterance_id=%d", self.utterance_id)
        try:
            if timeout is None:
                await self._done_event.wait()
            else:
                await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("TTS wait timeout")
        logger.debug("TTS done: utterance_id=%d", self.utterance_id)

    def _start_if_needed(self) -> None:
        if self._started:
            return
        self._started = True
        text = "".join(self._text_parts).strip()
        self._text_parts.clear()
        if not text:
            self._on_completed()
            return
        self._producer_thread = threading.Thread(
            target=self._producer, args=(text,), daemon=True
        )
        self._producer_thread.start()

    def _producer(self, text: str) -> None:
        try:
            self._engine.stream_tts(text, self._on_audio_chunk)
        except Exception:
            logger.exception("TTS ElevenLabs stream failed: utterance_id=%d", self.utterance_id)
        finally:
            self._on_completed()

    def _on_audio_chunk(self, audio_data: bytes) -> None:
        if not audio_data:
            return
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            return
        max_chunk_size = 4096
        chunk_size = self._chunk_size_bytes or max_chunk_size
        if chunk_size > max_chunk_size:
            chunk_size = max_chunk_size

        self._buffer.extend(audio_data)
        while len(self._buffer) >= chunk_size:
            chunk = bytes(self._buffer[:chunk_size])
            del self._buffer[:chunk_size]
            self._push_chunk(chunk)

    def _push_chunk(self, audio_data: bytes) -> None:
        chunk_index = self._chunk_index & 0xFFFF
        self._chunk_index = (self._chunk_index + 1) & 0xFFFF

        pending = self._pending_chunk
        self._pending_chunk = (chunk_index, audio_data)
        if pending is None:
            return

        self._emit_audio_chunk(pending[0], pending[1], is_done=False)

    def _emit_audio_chunk(self, chunk_index: int, audio_data: bytes, is_done: bool) -> None:
        packet = _build_audio_header(self.utterance_id, chunk_index, is_done) + audio_data
        event = {
            "type": "audio_chunk",
            "utterance_id": self.utterance_id,
            "chunk_index": chunk_index,
            "is_done": is_done,
            "data": packet,
        }
        try:
            future = asyncio.run_coroutine_threadsafe(self._output_queue.put(event), self._loop)
            with self._pending_puts_lock:
                self._pending_puts.append(future)
        except Exception:
            logger.debug("Failed to enqueue audio chunk", exc_info=True)

    async def _wait_pending_puts(self) -> None:
        with self._pending_puts_lock:
            futures = list(self._pending_puts)
            self._pending_puts.clear()
        if not futures:
            return
        for fut in futures:
            try:
                await asyncio.wrap_future(fut)
            except Exception:
                logger.debug("Failed to await audio chunk enqueue", exc_info=True)

    def _on_completed(self) -> None:
        logger.debug("TTS session completed: utterance_id=%d", self.utterance_id)
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            self._pending_chunk = None
            self._buffer.clear()
            try:
                self._loop.call_soon_threadsafe(self._done_event.set)
            except Exception:
                logger.debug("Failed to finalize TTS session", exc_info=True)
            return

        if self._chunk_size_bytes is not None and self._buffer:
            remaining = bytes(self._buffer)
            self._buffer.clear()
            self._push_chunk(remaining)
        pending = self._pending_chunk
        self._pending_chunk = None
        if pending is not None:
            self._emit_audio_chunk(pending[0], pending[1], is_done=True)
        try:
            def _finalize() -> None:
                async def _await_and_set() -> None:
                    await self._wait_pending_puts()
                    self._done_event.set()
                asyncio.create_task(_await_and_set())
            self._loop.call_soon_threadsafe(_finalize)
        except Exception:
            logger.debug("Failed to finalize TTS session", exc_info=True)


class TTSEngine:
    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        model_id: str,
        output_format: str,
        base_url: str,
        sample_width_bytes: int,
        channels: int,
    ):
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._output_format = output_format or _DEFAULT_OUTPUT_FORMAT
        self._base_url = base_url.rstrip("/")
        self._sample_width_bytes = max(1, sample_width_bytes)
        self._channels = max(1, channels)
        self._bytes_per_frame = self._sample_width_bytes * self._channels

    def start_session(
        self,
        utterance_id: int,
        output_queue: BroadcastQueue,
        loop: asyncio.AbstractEventLoop,
        chunk_size_bytes: int | None,
    ) -> TTSSession:
        return TTSSession(
            utterance_id=utterance_id,
            _output_queue=output_queue,
            _loop=loop,
            _chunk_size_bytes=chunk_size_bytes,
            _engine=self,
        )

    def stream_tts(self, text: str, on_audio_chunk) -> None:
        url = (
            f"{self._base_url}/v1/text-to-speech/{self._voice_id}/stream"
            f"?output_format={self._output_format}"
        )
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {"text": text, "model_id": self._model_id}

        remainder = b""
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=30) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                chunk = remainder + chunk
                extra = len(chunk) % self._bytes_per_frame
                if extra:
                    remainder = chunk[-extra:]
                    chunk = chunk[:-extra]
                else:
                    remainder = b""
                if chunk:
                    on_audio_chunk(chunk)
        if remainder:
            pad = (-len(remainder)) % self._bytes_per_frame
            on_audio_chunk(remainder + (b"\x00" * pad))


@dataclass
class _ActiveTTSSession:
    session: TTSSession
    streamed: bool = False
    inside_tag: bool = False
    started_at: float = field(default_factory=time.monotonic)


class TTSOutputBridge:

    def __init__(
        self,
        output_queue: BroadcastQueue,
        *,
        voice_preset: VoicePreset,
        audio_format: str | None = None,
        audio_chunk_size_bytes: int | None = None,
    ):
        self._output_queue = output_queue
        self._audio_format = audio_format
        self._audio_chunk_size_bytes = audio_chunk_size_bytes
        self._voice_preset = voice_preset
        if voice_preset is not None and voice_preset.provider == "elevenlabs":
            self._tts_engine = TTSEngine(
                api_key=voice_preset.eleven_api_key,
                voice_id=voice_preset.eleven_voice_id,
                model_id=voice_preset.eleven_model_id,
                output_format=voice_preset.eleven_output_format,
                base_url=voice_preset.eleven_base_url,
                sample_width_bytes=_DEFAULT_SAMPLE_WIDTH_BYTES,
                channels=_DEFAULT_CHANNELS,
            )
        else:
            self._tts_engine = get_tts_engine()
        self._subscription_queue: asyncio.Queue | None = None
        self._active_sessions: dict[int, _ActiveTTSSession] = {}
        self._utterance_counter = -1
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._flush_waiters: dict[str, asyncio.Event] = {}

    async def run(self) -> None:
        if self._tts_engine is None:
            return

        self._subscription_queue = await self._output_queue.subscribe()
        try:
            while True:
                event = await self._subscription_queue.get()
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        finally:
            await self._shutdown()

    async def _handle_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        if event.get("type") == "tts_flush":
            token = event.get("token")
            waiter = self._flush_waiters.pop(token, None)
            if waiter is not None:
                asyncio.create_task(self._await_flush(waiter))
            return
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            self._close_all_sessions()
            return
        if event.get("type") != "debate_output":
            return
        if event.get("websocket_only") is True:
            return
        text = event.get("text")

        utterance_id = event.get("tts_utterance_id")
        if utterance_id is None:
            utterance_id = self._extract_tts_utterance_id(event.get("utterance_id"))
        if utterance_id is None:
            utterance_id = self._next_utterance_id()

        streaming = event.get("streaming") is True
        stream_end = event.get("stream_end") is True
        if not streaming and not text:
            logger.debug("got non-streaming output without text, avoiding hung session bug")
            return
        active = self._active_sessions.get(utterance_id)
        if active is None:
            active = self._start_session(utterance_id)
            if active is None:
                return
            self._active_sessions[utterance_id] = active
            self._idle_event.clear()

        if streaming:
            active.streamed = True
            if text:
                active.session.write(self._sanitize_text(text, active))
            if stream_end:
                logger.info(
                    "TTS stream end: utterance_id=%s streamed=%s",
                    utterance_id,
                    active.streamed,
                )
                active.session.close()
                await active.session.wait_done(
                    timeout=config.tts_audio_idle_timeout_seconds
                )
                self._active_sessions.pop(utterance_id, None)
                if not self._active_sessions:
                    self._idle_event.set()
            return

        if not text:
            return
        if not active.streamed:
            active.session.write(self._sanitize_text(text, active))
        active.session.close()
        await active.session.wait_done(timeout=config.tts_audio_idle_timeout_seconds)
        self._active_sessions.pop(utterance_id, None)
        if not self._active_sessions:
            self._idle_event.set()

    async def _await_flush(self, waiter: asyncio.Event) -> None:
        logger.info(
            "TTS flush waiting for idle: active_sessions=%d",
            len(self._active_sessions),
        )
        if not await self.wait_for_idle(timeout=30.0):
            stuck = [
                (utterance_id, time.monotonic() - active.started_at)
                for utterance_id, active in self._active_sessions.items()
            ]
            if stuck:
                logger.warning(
                    "TTS flush timed out; forcing close of %d session(s): %s",
                    len(stuck),
                    stuck,
                )
            self._close_all_sessions()
            await self.wait_for_idle(timeout=5.0)
        waiter.set()

    def _start_session(self, utterance_id: int) -> _ActiveTTSSession | None:
        try:
            session = self._tts_engine.start_session(
                utterance_id=utterance_id,
                output_queue=self._output_queue,
                loop=asyncio.get_running_loop(),
                chunk_size_bytes=self._audio_chunk_size_bytes,
            )
            return _ActiveTTSSession(session=session)
        except Exception:
            logger.exception("Failed to start TTS session")
            return None

    def _sanitize_text(self, text: str, active: _ActiveTTSSession) -> str:
        if not text:
            return ""
        cleaned = []
        for ch in text:
            if active.inside_tag:
                if ch == ">":
                    active.inside_tag = False
                continue
            if ch == "<":
                active.inside_tag = True
                continue
            cleaned.append(ch)
        return "".join(cleaned)

    def _extract_tts_utterance_id(self, raw: Any) -> int | None:
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            if raw.startswith("T") and raw[1:].isdigit():
                return int(raw[1:])
            if raw.isdigit():
                return int(raw)
        return None

    def _next_utterance_id(self) -> int:
        self._utterance_counter = (self._utterance_counter + 1) & 0x7F
        return self._utterance_counter

    def _close_all_sessions(self) -> None:
        for active in self._active_sessions.values():
            try:
                active.session.close()
            except Exception:
                logger.debug("Failed to close TTS session", exc_info=True)
        self._active_sessions.clear()
        self._idle_event.set()

    async def wait_for_idle(self, timeout: float | None) -> bool:
        if self._tts_engine is None:
            return True
        if not self._active_sessions:
            return True
        try:
            if timeout is None:
                await self._idle_event.wait()
            else:
                await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def flush(self) -> None:
        if self._tts_engine is None:
            return
        token = f"tts_flush_{time.time_ns()}"
        waiter = asyncio.Event()
        self._flush_waiters[token] = waiter
        await self._output_queue.put({"type": "tts_flush", "token": token})
        await waiter.wait()

    async def _shutdown(self) -> None:
        self._close_all_sessions()
        if self._subscription_queue is not None:
            try:
                await self._output_queue.unsubscribe(self._subscription_queue)
            except Exception:
                logger.debug("Failed to unsubscribe TTS bridge queue", exc_info=True)
