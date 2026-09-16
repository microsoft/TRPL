# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Bridge between lia_agent_api (the brain) and a LiveKit AgentSession.

Design
------
We intentionally do NOT use a custom LiveKit LLM plugin. Instead we keep the
pipeline fully streaming by driving it at the session level:

    user mic ──► LiveKit STT ──► on_user_transcribed ──► lia WS send
                                                         (participant_input)

    lia WS recv (debate_output) ──► utterance async iterator ──►
                                     session.say(<iterator>, …) ──►
                                     LiveKit Azure TTS ──► LemonSlice ──► browser

Each `debate_output` chunk is forwarded to the TTS the instant it arrives.
The only buffering on the text path is the 60-char / `.!?\\n` boundary that
already exists inside lia_agent_api's OutputStreamSession (see
`src/debate/services/io.py`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import AsyncIterator, Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger("lia_bridge")

PoseCallback = Callable[[str], Awaitable[None]]

# Inline pose marker the LLM is asked to emit, e.g. "<pose:ted_wave/>Hey!".
# Stripped from the TTS-bound text; each match fires PoseCallback(name).
# Names may contain hyphens too (e.g. "ted-sway-small") — LemonSlice pose
# ids are not uniformly underscore-cased.
_POSE_TAG_RE = re.compile(r"<pose:([A-Za-z0-9_-]+)/>")
# Matches any prefix that could still grow into a complete <pose:NAME/>
# tag, so we know when to hold a partial chunk back instead of flushing it
# as text. Covers everything from "<" through "<pose:abc/".
_POSE_PARTIAL_RE = re.compile(r"^<(?:p(?:o(?:s(?:e(?::[A-Za-z0-9_-]*/?)?)?)?)?)?$")
# Safety cap so a never-closed "<pose:..." can't pin text in the buffer
# forever (which would silently block TTS playback).
_POSE_BUFFER_MAX = 64


class _PoseTagParser:
    """Streaming parser: strips <pose:NAME/> markers from text and fires
    on_pose(name) for each. Stateful across chunks because a tag can be
    split between two deltas (e.g. "<pose:te" + "d_wave/>Hey").
    """

    def __init__(self, on_pose: Optional[PoseCallback]):
        self._on_pose = on_pose
        self._buf = ""

    def feed(self, chunk: str) -> str:
        # Always strip pose tags so they never reach TTS — Azure TTS rejects
        # the unknown <pose:…> prefix as malformed SSML. The on_pose callback
        # is only an optional side-effect (triggers LemonSlice poses); when
        # absent (jailbreak room, or the brief window before set_pose_callback
        # is invoked), we still strip silently.
        self._buf += chunk
        out: list[str] = []
        i = 0
        n = len(self._buf)
        while i < n:
            lt = self._buf.find("<", i)
            if lt < 0:
                out.append(self._buf[i:])
                i = n
                break
            if lt > i:
                out.append(self._buf[i:lt])
                i = lt
            m = _POSE_TAG_RE.match(self._buf, i)
            if m:
                name = m.group(1)
                logger.info("pose tag → %s", name)
                if self._on_pose:
                    asyncio.create_task(self._on_pose(name))
                i = m.end()
                continue
            rest = self._buf[i:]
            if _POSE_PARTIAL_RE.match(rest) and len(rest) <= _POSE_BUFFER_MAX:
                break  # incomplete tag — wait for next chunk
            out.append("<")
            i += 1
        self._buf = self._buf[i:]
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest

    def reset(self) -> None:
        self._buf = ""


class LiaAgentApiBridge:
    """Owns one lia_agent_api session + a WebSocket to it.

    Lifecycle:
      * await bridge.start()            — POSTs /api/debate/start, opens WS
      * await bridge.send_user_text(t)  — pushes `participant_input`
      * async for text in bridge.utterances()
                                        — yields (utterance_id, text_iter)
                                          where text_iter is an async iterator
                                          of delta strings until stream_end
      * await bridge.close()            — clean shutdown

    The bridge is agnostic about TTS/avatars; the caller glues it into an
    AgentSession via session.say(text_iter).
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        *,
        scenario_id: str = "storys",
        phases: Optional[list[str]] = None,
        visitor_mode: str = "adult",
        participant_id: str = "visitor",
        on_pose: Optional[PoseCallback] = None,
    ):
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._scenario_id = scenario_id
        # Browser rooms accept participant input immediately. Kiosk rooms pass
        # an explicit camera-first phase chain from livekit_agent.entrypoint.
        self._phases = phases or ["storys"]
        self._visitor_mode = visitor_mode
        self._participant_id = participant_id

        self._http: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session_id: Optional[str] = None

        # Each utterance gets an asyncio.Queue[str | None].
        # None is the end-of-utterance sentinel. A brand-new queue is pushed
        # to `_utterance_feed` whenever the first chunk of a new utterance
        # arrives, so the consumer can iterate over utterances one-by-one.
        self._utterance_feed: asyncio.Queue[
            tuple[str, asyncio.Queue[Optional[str]]]
        ] = asyncio.Queue()
        self._current_queue: Optional[asyncio.Queue[Optional[str]]] = None
        self._current_utterance_id: Optional[str] = None

        # Phase tracking — observed by reading "Transitioning to X phase"
        # debug_message events from lia. Exposed to the worker so it can
        # forward phase changes to the browser for a debug overlay.
        self._current_phase: str = self._phases[0] if self._phases else "unknown"
        self._phase_feed: asyncio.Queue[str] = asyncio.Queue()

        # Camera event tracking — lia emits a debug_message with agent
        # "CameraService" and content "Camera event: <event_type>" for every
        # event routed into the session. We forward those to the worker so
        # the browser can show a live log of what the brain is seeing.
        self._camera_event_feed: asyncio.Queue[dict] = asyncio.Queue()

        # Safety interrupt feed — lia emits `safety_interrupt` events
        # when its server-side moderation flags content. The worker
        # consumes this to call session.interrupt(force=True) on the
        # livekit AgentSession; the bridge itself drops deltas
        # internally as the events arrive.
        self._safety_event_feed: asyncio.Queue[dict] = asyncio.Queue()

        self._reader_task: Optional[asyncio.Task] = None
        self._closed = False

        # Pose-trigger parser. When on_pose is None, feed() is a no-op
        # passthrough — env toggle in the worker controls this.
        self._pose_parser = _PoseTagParser(on_pose)

        # Safety/interrupt support.
        # `_dropped_utterance_ids` — silence further deltas for utterances
        # the safety layer has cut. `_drop_next_utterance` — if the cut
        # arrives before lia has even started a response, mark the next
        # one to be auto-closed on first chunk.
        self._dropped_utterance_ids: set[str] = set()
        self._drop_next_utterance: bool = False

    def set_pose_callback(self, on_pose: Optional[PoseCallback]) -> None:
        """Late-bind the pose callback. The worker only knows the
        LemonSlice session_id after avatar.start(), which happens after
        the bridge is constructed.
        """
        self._pose_parser = _PoseTagParser(on_pose)

    # ─── lifecycle ────────────────────────────────────────────────────

    async def start(self) -> str:
        """Create the lia_agent_api session and open the WebSocket.

        Returns the session_id.
        """
        self._http = aiohttp.ClientSession()

        start_url = f"{self._api_base}/api/debate/start"
        payload = {
            "scenario_id": self._scenario_id,
            "phases": self._phases,
            "visitor_mode": self._visitor_mode,
            "players": {},
            "wants_audio": False,          # LiveKit does TTS — don't double-up
            "websocket_streaming": True,   # we NEED the streaming deltas
        }
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        logger.info("POST %s  scenario=%s phases=%s", start_url,
                    self._scenario_id, self._phases)
        async with self._http.post(start_url, json=payload, headers=headers) as r:
            r.raise_for_status()
            data = await r.json()
        self._session_id = data["session_id"]
        ws_path = data["ws_url"]  # e.g. "/api/debate/ws/<sid>"
        # When the brain has WS auth enabled (the default), /start returns a
        # one-time controller token. Pass it via header (not the URL, so it is
        # never logged). It is simply absent when WS auth is turned off.
        controller_token = data.get("controller_token")

        ws_scheme = "wss" if self._api_base.startswith("https") else "ws"
        host = self._api_base.split("://", 1)[1]
        ws_url = f"{ws_scheme}://{host}{ws_path}"
        ws_headers = {"x-ws-token": controller_token} if controller_token else {}
        logger.info("Opening WS %s", ws_url)
        self._ws = await self._http.ws_connect(ws_url, heartbeat=20, headers=ws_headers)

        # Announce the participant so the storys node accepts our input.
        await self._ws.send_json({
            "type": "participant_joined",
            "participant_id": self._participant_id,
        })

        # Reader pump runs for the whole session.
        self._reader_task = asyncio.create_task(self._read_loop())

        # Seed the phase feed with the initial phase so UI can show it
        # even before the first transition.
        await self._phase_feed.put(self._current_phase)
        return self._session_id

    async def close(self) -> None:
        self._closed = True
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._http and not self._http.closed:
            await self._http.close()

    async def register_as_kiosk_session(self) -> None:
        """Publish self._session_id as the active kiosk session so camera_platform
        can fetch it and attach its events to the right session. Caller must have
        already awaited start(). Raises on HTTP failure.
        """
        if not self._http or not self._session_id:
            raise RuntimeError("register_as_kiosk_session called before start()")
        url = f"{self._api_base}/api/admin/register-kiosk-session"
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        logger.info("POST %s session_id=%s", url, self._session_id)
        async with self._http.post(
            url,
            json={"session_id": self._session_id},
            headers=headers,
        ) as r:
            r.raise_for_status()

    # ─── inbound (to the brain) ───────────────────────────────────────

    async def send_user_interrupt(
        self,
        utterance_id: str | None,
        reason: str = "barge_in",
    ) -> None:
        """Tell the brain the user is barging in. Brain kills the named
        utterance (or pre-flags the next one if uid is None) and emits
        a user_interrupt event back over the WS for all subscribers.
        """
        if self._ws is None:
            return
        payload = {
            "type": "user_interrupt",
            "utterance_id": utterance_id,
            "reason": reason,
        }
        logger.info("→ lia user_interrupt: utterance=%s reason=%s",
                    utterance_id, reason)
        try:
            await self._ws.send_json(payload)
        except Exception:  # noqa: BLE001
            logger.exception("failed to send user_interrupt")

    async def send_user_text(self, text: str, utterance_id: str | None = None) -> None:
        if not text.strip() or not self._ws:
            return
        payload = {
            "type": "participant_input",
            "spoken": {
                "participant_id": self._participant_id,
                "text": text,
            },
        }
        if utterance_id:
            payload["spoken"]["utterance_id"] = utterance_id
        logger.info("→ lia user_input: %r", text[:80])
        await self._ws.send_json(payload)

    def drop_utterance(self, utterance_id: str | None = None) -> str | None:
        """Silently kill an in-flight or upcoming model utterance.

        - utterance_id matches the in-flight one: close its queue,
          add to dropped set so any trailing deltas are swallowed.
        - utterance_id is given but doesn't match current: just add
          to the dropped set (deltas haven't started, or already ended).
        - utterance_id is None: drop whatever's playing or pre-flag
          the next one. Kept for callers that don't have a uid.
        """
        if utterance_id and utterance_id != self._current_utterance_id:
            self._dropped_utterance_ids.add(utterance_id)
            return utterance_id

        target = utterance_id or self._current_utterance_id
        if self._current_queue is not None:
            if target:
                self._dropped_utterance_ids.add(target)
            try:
                self._current_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            self._current_queue = None
            self._current_utterance_id = None
            self._pose_parser.reset()
            return target
        # No in-flight utterance and no uid → block the next one.
        if utterance_id is None:
            self._drop_next_utterance = True
        else:
            self._dropped_utterance_ids.add(utterance_id)
        return utterance_id

    # ─── outbound (from the brain, one utterance at a time) ───────────

    @property
    def current_phase(self) -> str:
        return self._current_phase

    async def phase_changes(self) -> AsyncIterator[str]:
        """Yield phase names as lia transitions between them. The first item
        is the initial phase (seeded in start())."""
        while not self._closed:
            phase = await self._phase_feed.get()
            yield phase

    async def camera_events(self) -> AsyncIterator[dict]:
        """Yield {event_type, phase, timestamp} for every camera event lia
        routed into this session (logged via debug_message)."""
        while not self._closed:
            yield await self._camera_event_feed.get()

    async def safety_events(self) -> AsyncIterator[dict]:
        """Yield {utterance_id, role, reason, ...} for every
        safety_interrupt the brain emits. Consumer is expected to
        call session.interrupt() on the livekit AgentSession; the
        bridge has already cut the corresponding text stream."""
        while not self._closed:
            yield await self._safety_event_feed.get()

    async def utterances(
        self,
    ) -> AsyncIterator[tuple[str, AsyncIterator[str]]]:
        """Yield `(utterance_id, text_chunk_iter)` for each TR reply.

        text_chunk_iter yields string deltas as they stream in, and terminates
        when the underlying debate_output event has `stream_end: true`.
        """
        while not self._closed:
            utterance_id, queue = await self._utterance_feed.get()
            yield utterance_id, _queue_as_iter(queue)

    # ─── internal ─────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    ev = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message: %r", msg.data[:120])
                    continue
                await self._handle_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Reader loop crashed")
        finally:
            # Flush any in-flight utterance so consumers unblock.
            if self._current_queue is not None:
                await self._current_queue.put(None)
                self._current_queue = None

    async def _handle_event(self, ev: dict) -> None:
        etype = ev.get("type")

        # Safety interrupt: brain-side moderation flagged content for
        # this session. user_interrupt: barge-in echo for the same
        # utterance. Both feed the same downstream pump (worker calls
        # session.interrupt + publishes data channel event for browser).
        if etype in ("safety_interrupt", "user_interrupt"):
            utt_id = ev.get("utterance_id")
            self.drop_utterance(utt_id if isinstance(utt_id, str) else None)
            await self._safety_event_feed.put(ev)
            return

        # Phase transitions arrive as debug_message events with a "phase"
        # field. We track them so the worker can surface the current phase
        # to the browser for debug purposes.
        if etype == "debug_message":
            content = ev.get("content") or ""
            phase = ev.get("phase")
            if (
                isinstance(phase, str)
                and phase
                and phase != self._current_phase
                and "Transitioning" in content
            ):
                self._current_phase = phase
                await self._phase_feed.put(phase)

            # Camera-event breadcrumb (agent=CameraService, content
            # "Camera event: X"). This is a per-event receipt from lia; it
            # fires BEFORE CameraNode decides whether to respond. The
            # browser colour-codes "actionable" vs "ignored" based on its
            # own static knowledge of the event name.
            if ev.get("agent") == "CameraService" and content.startswith("Camera event:"):
                event_type = content.split("Camera event:", 1)[1].strip()
                await self._camera_event_feed.put({
                    "event_type": event_type,
                    "phase": phase or self._current_phase,
                    "timestamp": ev.get("timestamp"),
                })
            return

        if etype != "debate_output":
            # Other event types: log/debug noise we don't need for the audio
            # path. Keep at DEBUG so it's inspectable when needed.
            logger.debug("ignore event %s", etype)
            return

        # lia_agent_api emits three kinds of debate_output event:
        #   (a) streaming delta  → {streaming: True,  stream_end: False, text: "..."}
        #   (b) streaming final  → {streaming: True,  stream_end: True,  text: ""}
        #                          or {stream_end: True, text: ""}
        #   (c) one-shot send    → {streaming: None,  stream_end: None,  text: "FULL"}
        #                          (used for round-1 pre-greeting, non-streamed
        #                          utterances like the static pre-greeting)
        text: str = ev.get("text", "") or ""
        streaming = bool(ev.get("streaming"))
        stream_end = bool(ev.get("stream_end"))
        utt_id: str = ev.get("utterance_id") or ev.get("speaker") or "_"
        is_one_shot = not streaming and not stream_end and bool(text)

        if not (streaming or stream_end or is_one_shot):
            return

        # Already-dropped utterance: deltas keep arriving from lia after
        # safety cut us off. Swallow silently until stream_end.
        if utt_id in self._dropped_utterance_ids:
            if stream_end or is_one_shot:
                self._dropped_utterance_ids.discard(utt_id)
            return

        # Start a new utterance queue on first chunk of a new utterance_id.
        if self._current_queue is None or utt_id != self._current_utterance_id:
            if self._current_queue is not None:
                tail = self._pose_parser.flush()
                if tail:
                    await self._current_queue.put(tail)
                await self._current_queue.put(None)  # close the previous one
            self._pose_parser.reset()
            new_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
            await self._utterance_feed.put((utt_id, new_queue))
            # Safety pre-emptively flagged the upcoming utterance: hand
            # the consumer an immediately-closed queue so session.say
            # exits with no audio, and discard any deltas for this id.
            if self._drop_next_utterance:
                self._drop_next_utterance = False
                self._dropped_utterance_ids.add(utt_id)
                await new_queue.put(None)
                if stream_end or is_one_shot:
                    self._dropped_utterance_ids.discard(utt_id)
                return
            self._current_queue = new_queue
            self._current_utterance_id = utt_id

        if text:
            clean = self._pose_parser.feed(text)
            if clean:
                await self._current_queue.put(clean)

        # End the utterance on an explicit stream_end OR at the end of a
        # one-shot message (single event, whole utterance).
        if (stream_end or is_one_shot) and self._current_queue is not None:
            tail = self._pose_parser.flush()
            if tail:
                await self._current_queue.put(tail)
            await self._current_queue.put(None)
            self._current_queue = None
            self._current_utterance_id = None


async def _queue_as_iter(q: asyncio.Queue[Optional[str]]) -> AsyncIterator[str]:
    """Turn a sentinel-terminated queue into an async iterator of strings."""
    while True:
        item = await q.get()
        if item is None:
            return
        yield item


# ─── Default env-driven factory ────────────────────────────────────

def bridge_from_env(phases_override: Optional[list[str]] = None) -> LiaAgentApiBridge:
    """Build a bridge from env vars. If phases_override is given, it wins over
    LIA_PHASES — used by the kiosk-room branch in livekit_agent.entrypoint so
    a single worker process can serve both the web flow (env-driven phases)
    and the kiosk flow (full camera→welcome→storys chain).
    """
    return LiaAgentApiBridge(
        api_base=os.getenv("LIA_AGENT_API_URL", "http://localhost:8000"),
        api_key=os.getenv("LIA_AGENT_API_KEY", ""),
        visitor_mode=os.getenv("LIA_VISITOR_MODE", "adult"),
        phases=phases_override or _parse_phases(os.getenv("LIA_PHASES")),
    )


def _parse_phases(raw: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated phase list. Returns None to use default."""
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]
