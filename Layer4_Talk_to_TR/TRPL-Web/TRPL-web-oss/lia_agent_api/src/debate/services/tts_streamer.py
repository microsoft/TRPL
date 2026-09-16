# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import time
from typing import Any

from azure.cognitiveservices.speech import (
    PropertyId,
    SpeechConfig,
    SpeechSynthesisOutputFormat,
    SpeechSynthesisRequest,
    SpeechSynthesisRequestInputType,
    SpeechSynthesizer,
)
from azure.cognitiveservices.speech.audio import (
    AudioOutputConfig,
    PushAudioOutputStreamCallback,
    PushAudioOutputStream,
)

from api.config import config, VoicePreset
from debate.utils import BroadcastQueue

logger = logging.getLogger(f"lia.{__name__}")

_TTS_OUTPUT_FORMATS = {
    "Raw24Khz16BitMonoPcm": SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm,
    "Raw16Khz16BitMonoPcm": SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm,
}
_DEFAULT_TTS_OUTPUT_FORMAT = "Raw24Khz16BitMonoPcm"

_tts_engine: "TTSEngine | None" = None
_tts_engine_initialized = False


_AUDIO_UTTERANCE_ID_MASK = 0xFFF
_TICKS_PER_MILLISECOND = 10_000.0


def _build_audio_header(utterance_id: int, chunk_index: int, is_done: bool) -> bytes:
    header = ((utterance_id & 0xFFF) << 16) | (chunk_index & 0xFFFF)
    if is_done:
        header |= 0x10000000
    return header.to_bytes(4, "big")


def _format_utterance_id(utterance_id: int) -> str:
    return f"T{utterance_id:07d}"


def _audio_utterance_id(utterance_id: int) -> int:
    return utterance_id & _AUDIO_UTTERANCE_ID_MASK


def _ticks_to_ms(raw_ticks: Any) -> float:
    try:
        if isinstance(raw_ticks, timedelta):
            ticks = raw_ticks.total_seconds() * 10_000_000.0
        else:
            ticks = float(raw_ticks)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, ticks / _TICKS_PER_MILLISECOND)


def _to_ticks(raw_value: Any) -> int:
    if isinstance(raw_value, timedelta):
        return max(0, int(raw_value.total_seconds() * 10_000_000.0))
    try:
        return max(0, int(raw_value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_boundary_type(raw_boundary_type: Any) -> str:
    value = str(raw_boundary_type or "").strip().lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    if value in {"word", "punctuation", "sentence"}:
        return value
    return "word"


def get_tts_engine() -> "TTSEngine | None":
    # TODO: TTSEngine doesn't hold state, no need for singleton
    global _tts_engine, _tts_engine_initialized
    if _tts_engine_initialized:
        return _tts_engine

    _tts_engine_initialized = True
    _tts_engine = TTSEngine()
    return _tts_engine


@dataclass
class TTSSession:
    utterance_id: int
    phase: str | None
    _synthesizer: SpeechSynthesizer | None
    _request: SpeechSynthesisRequest | None
    _output_queue: BroadcastQueue
    _loop: asyncio.AbstractEventLoop
    _audio_stream: PushAudioOutputStream | None
    _chunk_size_bytes: int | None
    _chunk_index: int = 0
    _pending_chunk: tuple[int, bytes] | None = None
    _buffer: bytearray = field(default_factory=bytearray)
    _done_event: asyncio.Event = field(default_factory=asyncio.Event)
    _pending_puts: list[Any] = field(default_factory=list)
    _pending_puts_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _caption_timing_enabled: bool = True
    _completion_reason: str = "completed"
    _caption_timing_end_emitted: bool = False
    _caption_timing_count: int = 0
    _caption_timing_dropped: int = 0
    _first_audio_chunk_monotonic: float | None = None
    _first_boundary_monotonic: float | None = None

    def write(self, text: str) -> None:
        if not text:
            return
        try:
            if self._request is not None:
                logger.debug(
                    "TTS writing to input stream: utterance_id=%d text=%s",
                    self.utterance_id,
                    text,
                )
                self._request.input_stream.write(text)
        except Exception:
            logger.debug("TTS write failed (stream closed?)", exc_info=True)

    def close(self) -> None:
        try:
            if self._request is not None:
                self._request.input_stream.close()
        except Exception:
            logger.debug("TTS close failed", exc_info=True)

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

    def _on_audio_chunk(self, audio_data: bytes) -> None:
        if not audio_data:
            return
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            return
        if self._first_audio_chunk_monotonic is None:
            self._first_audio_chunk_monotonic = time.monotonic()
        if self._chunk_size_bytes is None:
            self._push_chunk(audio_data)
            return

        self._buffer.extend(audio_data)
        chunk_size = self._chunk_size_bytes
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
        # logger.debug(
        #     "Enqueuing audio chunk: utterance_id=%d chunk_index=%d size=%d done=%s",
        #     self.utterance_id,
        #     chunk_index,
        #     len(packet),
        #     is_done,
        # )
        self._enqueue_output_event(event, error_message="Failed to enqueue audio chunk")

    def _enqueue_output_event(self, event: dict[str, Any], *, error_message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._output_queue.put(event), self._loop
            )
            with self._pending_puts_lock:
                self._pending_puts.append(future)
        except Exception:
            logger.debug(error_message, exc_info=True)

    def _build_caption_timing_event(self, evt: Any) -> dict[str, Any]:
        audio_offset_ticks = _to_ticks(getattr(evt, "audio_offset", 0))
        duration_ticks = _to_ticks(getattr(evt, "duration", 0))
        event: dict[str, Any] = {
            "type": "caption_timing",
            "timestamp": datetime.now().isoformat(),
            "utterance_id": _format_utterance_id(self.utterance_id),
            "tts_utterance_id": self.utterance_id,
            "audio_utterance_id": _audio_utterance_id(self.utterance_id),
            "boundary_type": _normalize_boundary_type(getattr(evt, "boundary_type", None)),
            "text": str(getattr(evt, "text", "") or ""),
            "text_offset": int(getattr(evt, "text_offset", 0) or 0),
            "word_length": int(getattr(evt, "word_length", 0) or 0),
            "audio_offset_ticks": audio_offset_ticks,
            "audio_offset_ms": _ticks_to_ms(audio_offset_ticks),
            "duration_ms": _ticks_to_ms(duration_ticks),
        }
        if self.phase:
            event["phase"] = self.phase
        return event

    def _emit_caption_timing(self, evt: Any) -> None:
        if not self._caption_timing_enabled:
            return
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            self._caption_timing_dropped += 1
            return
        if self._first_boundary_monotonic is None:
            self._first_boundary_monotonic = time.monotonic()
        self._caption_timing_count += 1
        self._enqueue_output_event(
            self._build_caption_timing_event(evt),
            error_message="Failed to enqueue caption_timing event",
        )

    def _emit_caption_timing_end(self, reason: str) -> None:
        if not self._caption_timing_enabled:
            return
        if self._caption_timing_end_emitted:
            return
        if getattr(self._output_queue, "any_audio_subscribers", False) is False:
            return
        self._caption_timing_end_emitted = True
        self._enqueue_output_event(
            {
                "type": "caption_timing_end",
                "timestamp": datetime.now().isoformat(),
                "utterance_id": _format_utterance_id(self.utterance_id),
                "tts_utterance_id": self.utterance_id,
                "audio_utterance_id": _audio_utterance_id(self.utterance_id),
                "reason": reason,
            },
            error_message="Failed to enqueue caption_timing_end event",
        )

    def mark_force_closed(self) -> None:
        self._completion_reason = "force_closed"

    def _on_word_boundary(self, evt: Any) -> None:
        if not self._caption_timing_enabled:
            return
        try:
            self._emit_caption_timing(evt)
        except Exception:
            logger.debug("Failed to process TTS word boundary event", exc_info=True)

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

    def _on_canceled(self, evt):
        r = evt.result
        cd = r.cancellation_details
        self._completion_reason = "canceled"
        logger.warning(
            "TTS synthesis canceled: utterance_id=%d - %s - %s - %s",
            self.utterance_id,
            cd.reason,
            cd.error_code,
            cd.error_details,
        )
        self._on_completed(evt)

    def _on_completed(self, _evt) -> None:
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
        self._emit_caption_timing_end(self._completion_reason)
        if self._caption_timing_count or self._caption_timing_dropped:
            first_boundary_latency_ms: float | None = None
            if (
                self._first_audio_chunk_monotonic is not None
                and self._first_boundary_monotonic is not None
            ):
                first_boundary_latency_ms = (
                    self._first_boundary_monotonic - self._first_audio_chunk_monotonic
                ) * 1000.0
            logger.info(
                "TTS caption timing summary: utterance_id=%d reason=%s boundaries=%d dropped=%d first_boundary_latency_ms=%s",
                self.utterance_id,
                self._completion_reason,
                self._caption_timing_count,
                self._caption_timing_dropped,
                (
                    f"{first_boundary_latency_ms:.1f}"
                    if first_boundary_latency_ms is not None
                    else "n/a"
                ),
            )
        try:
            def _finalize() -> None:
                async def _await_and_set() -> None:
                    await self._wait_pending_puts()
                    self._done_event.set()
                asyncio.create_task(_await_and_set())
            self._loop.call_soon_threadsafe(_finalize)
        except Exception:
            logger.debug("Failed to finalize TTS session", exc_info=True)


class StreamCallback(PushAudioOutputStreamCallback):
    def __init__(self, session: TTSSession):
        super().__init__()
        self._session = session

    # SDK calls this many times with streaming audio.
    def write(self, audio_buffer: memoryview) -> int:
        # logger.debug(
        #     "TTS StreamCallback write: utterance_id=%d size=%d",
        #     self._session.utterance_id,
        #     len(audio_buffer),
        # )
        chunk = audio_buffer.tobytes()  # convert memoryview -> bytes
        self._session._on_audio_chunk(chunk)
        return len(chunk)

    def close(self) -> None:
        self._session._on_completed(None)


class TTSEngine:

    def _build_speech_config(
        self,
        output_format: SpeechSynthesisOutputFormat,
        preset: VoicePreset,
        *,
        include_caption_timing: bool,
    ):
        api_key = preset.azure_api_key
        region = preset.azure_region
        voice = preset.azure_voice
        deployment = preset.azure_deployment
        endpoint = (
            f"wss://{region}.tts.speech.microsoft.com/"
            "cognitiveservices/websocket/v2"
        )
        speech_config = SpeechConfig(
            endpoint=endpoint,
            subscription=api_key,
        )
        speech_config.set_speech_synthesis_output_format(
            output_format
        )
        if voice:
            speech_config.speech_synthesis_voice_name = voice
        if deployment:
            speech_config.endpoint_id = deployment
        logger.info(
            "TTS Azure config: region=%s voice=%s deployment=%s",
            region,
            voice or "<default>",
            deployment or "<none>",
        )
        speech_config.set_property(
            PropertyId.SpeechSynthesis_FrameTimeoutInterval,
            "100000000",
        )
        if include_caption_timing:
            speech_config.set_property(
                PropertyId.SpeechServiceResponse_RequestWordBoundary,
                "true",
            )
            speech_config.set_property(
                PropertyId.SpeechServiceResponse_RequestPunctuationBoundary,
                "true",
            )
            speech_config.set_property(
                PropertyId.SpeechServiceResponse_RequestSentenceBoundary,
                "true",
            )
        return speech_config

    def start_session(
        self,
        utterance_id: int,
        output_queue: BroadcastQueue,
        loop: asyncio.AbstractEventLoop,
        output_format: SpeechSynthesisOutputFormat,
        chunk_size_bytes: int | None,
        preset: VoicePreset,
        phase: str | None = None,
        include_caption_timing: bool = True,
    ) -> TTSSession:
        session = TTSSession(
            utterance_id=utterance_id,
            phase=phase,
            _synthesizer=None,
            _request=None,
            _output_queue=output_queue,
            _loop=loop,
            _audio_stream=None,
            _chunk_size_bytes=chunk_size_bytes,
            _caption_timing_enabled=include_caption_timing,
        )

        speech_config = self._build_speech_config(
            output_format,
            preset=preset,
            include_caption_timing=include_caption_timing,
        )
        callback = StreamCallback(session=session)
        push_stream = PushAudioOutputStream(callback)
        session._audio_stream = push_stream
        audio_config = AudioOutputConfig(stream=push_stream)
        synthesizer = SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        request = SpeechSynthesisRequest(SpeechSynthesisRequestInputType.TextStream)

        session._synthesizer = synthesizer
        session._request = request

        synthesizer.synthesis_canceled.connect(session._on_canceled)
        synthesizer.synthesis_completed.connect(session._on_completed)
        if include_caption_timing:
            synthesizer.synthesis_word_boundary.connect(session._on_word_boundary)

        try:
            synthesizer.speak_async(request)
        except Exception:
            logger.exception("Failed to start TTS synthesizer")
            raise

        return session


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
        self._tts_engine = get_tts_engine()
        self._audio_format = audio_format or _DEFAULT_TTS_OUTPUT_FORMAT
        self._audio_chunk_size_bytes = audio_chunk_size_bytes
        self._voice_preset = voice_preset
        self._caption_timing_enabled = config.tts_caption_timing_enabled
        if self._audio_format not in _TTS_OUTPUT_FORMATS:
            logger.warning(
                "Unknown TTS audio format '%s'; defaulting to %s",
                self._audio_format,
                _DEFAULT_TTS_OUTPUT_FORMAT,
            )
            self._audio_format = _DEFAULT_TTS_OUTPUT_FORMAT
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
            phase = event.get("phase")
            active = self._start_session(
                utterance_id,
                phase=phase if isinstance(phase, str) else None,
            )
            if active is None:
                return
            self._active_sessions[utterance_id] = active
            self._idle_event.clear()
        else:
            phase = event.get("phase")
            if not active.session.phase and isinstance(phase, str):
                active.session.phase = phase

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
        # Prevent hangs if a streaming session never receives stream_end.
        # Sessions should never exceed ~30s, so force-close after that.
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

    def _start_session(
        self,
        utterance_id: int,
        *,
        phase: str | None = None,
    ) -> _ActiveTTSSession | None:
        try:
            output_format = _TTS_OUTPUT_FORMATS.get(
                self._audio_format, _TTS_OUTPUT_FORMATS[_DEFAULT_TTS_OUTPUT_FORMAT]
            )
            session = self._tts_engine.start_session(
                utterance_id=utterance_id,
                output_queue=self._output_queue,
                loop=asyncio.get_running_loop(),
                output_format=output_format,
                chunk_size_bytes=self._audio_chunk_size_bytes,
                preset=self._voice_preset,
                phase=phase,
                include_caption_timing=self._caption_timing_enabled,
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
                active.session.mark_force_closed()
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
