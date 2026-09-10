import asyncio
from datetime import datetime
import logging
from typing import Type, TypeVar, overload
from pydantic import BaseModel, ConfigDict
from dataclasses import dataclass

from api.config import config
from debate.models.inputs import (
    CameraEventInput,
    DebateInput,
    HandRaiseInput,
    ParticipantJoinedInput,
    ParticipantReactionInput,
    SpokenTextInput,
    TimeoutInput,
    TouchscreenVoteInput,
)
from debate.models.sockets import (
    CameraEvent as CameraEventMessage,
    HandRaisePrompt,
    InboundSocketMessage,
    ParticipantInfo as ParticipantInfoMessage,
    ParticipantInput as ParticipantInputMessage,
    ParticipantJoined as ParticipantJoinedMessage,
    ParticipantReaction as ParticipantReactionMessage,
    StartDebate as StartDebateMessage,
    StartIntro as StartIntroMessage,
)
from debate.models.state import (
    DebateState,
    Participant,
    ParticipantReaction as ParticipantReactionRecord,
    SupplementalNotes,
)
from debate.utils import BroadcastQueue
from debate.services.session_store import DebateSession
from debate.services.notes_updater import NotesUpdater
from debate.services.safety import SafetyService, StreamCancelled

logger = logging.getLogger(f"lia.{__name__}")

STREAM_FLUSH_CHARS = 60
STREAM_FLUSH_BOUNDARIES = (".", "!", "?", "\n")


class OutputManager:
    """Manages output events for a debate session"""

    def __init__(
        self,
        output_queue: asyncio.Queue,
        state: DebateState | None = None,
        session: DebateSession | None = None,
        safety: SafetyService | None = None,
    ):
        self.output_queue = output_queue
        self.state = state
        self.session = session
        self.safety = safety
        self._local_utterance_counter = -1

    def _next_utterance_id(self) -> int:
        if self.session is not None:
            self.session.utterance_counter = self.session.utterance_counter + 1
            return self.session.utterance_counter
        self._local_utterance_counter = self._local_utterance_counter + 1
        return self._local_utterance_counter

    def _format_utterance_id(self, utterance_id: int) -> str:
        return f"T{utterance_id:07d}"

    async def emit(self, event_type: str, data: dict):
        """Emit an output event (DEPRECATED - use send_output or send_debug)"""
        event = {"type": event_type, "timestamp": datetime.now().isoformat(), **data}
        await self.output_queue.put(event)

    def _build_output_event(
        self,
        text: str | None = None,
        touchscreen_prompts: list[dict] | None = None,
        waiting_for_input: bool = False,
        done: bool = False,
        speaker_prompt: dict[str, str] | None = None,
        tr_question_summary: str | None = None,
        eligible_speakers: list[HandRaisePrompt] | None = None,
        phase: str | None = None,
        utterance_id: str | None = None,
        tts_utterance_id: int | None = None,
        streaming: bool | None = None,
        websocket_only: bool | None = None,
    ) -> dict:
        """Send debate_output message (user-facing)"""
        if (
            text
            and self.state is not None
            and config.tts_enabled
            and getattr(self.output_queue, "any_audio_subscribers", False)
        ):
            self.state.inputs.audio_idle = False

        event = {
            "type": "debate_output",
            "timestamp": datetime.now().isoformat(),
            "debug": False,
            "waiting_for_input": waiting_for_input,
            "done": done,
        }
        if text:
            event["text"] = text
        if utterance_id is not None:
            event["utterance_id"] = utterance_id
        if tts_utterance_id is not None:
            event["tts_utterance_id"] = tts_utterance_id
        if streaming is not None:
            event["streaming"] = streaming
        if websocket_only is not None:
            event["websocket_only"] = websocket_only
        if touchscreen_prompts:
            # backward compat: frontend does not yet support touchscreen_prompts with eligible_speakers
            if config.hand_raise_hints_enabled or eligible_speakers is None:
                event["touchscreen_prompts"] = touchscreen_prompts
        if speaker_prompt:
            event["speaker_prompt"] = speaker_prompt
        if tr_question_summary:
            event["tr_question_summary"] = tr_question_summary
        if eligible_speakers is not None:
            event["eligible_speakers"] = [s.model_dump() for s in eligible_speakers]

        # Automatically get phase from state if not explicitly provided
        if phase is None and self.state is not None:
            phase = self.state.phase

        if phase:
            event["phase"] = phase
        return event

    def _log_output(
        self,
        text: str | None,
        speaker: str | None,
        touchscreen_prompts: list[dict] | None,
    ) -> None:
        if not text and not touchscreen_prompts:
            return

        # Determine speaker name - default to TR for text outputs (TR speaking), System otherwise
        if speaker:
            speaker_name = speaker
        elif text:
            speaker_name = "TR"  # Default to TR when text is spoken
        else:
            speaker_name = "System"  # For non-text outputs

        if text:
            logger.info(f"[speaker={speaker_name}] {text}")

        if touchscreen_prompts:
            for prompt in touchscreen_prompts:
                participant_id = prompt.get("participant_id", "unknown")
                question = prompt.get("question", "")
                options = prompt.get("options", [])
                logger.info(
                    f"Outbound touchscreen prompt: participant={participant_id}, question={question}, options={options}"
                )

    async def send_output(
        self,
        text: str | None = None,
        touchscreen_prompts: list[dict] | None = None,
        waiting_for_input: bool = False,
        done: bool = False,
        speaker: str | None = None,
        speaker_prompt: dict[str, str] | None = None,
        tr_question_summary: str | None = None,
        eligible_speakers: list[HandRaisePrompt] | None = None,
        phase: str | None = None,
    ):
        if text:
            await self._wait_for_audio_ready()

        tts_utterance_id = self._next_utterance_id() if text else None
        utterance_id = (
            self._format_utterance_id(tts_utterance_id)
            if tts_utterance_id is not None
            else None
        )

        event = self._build_output_event(
            text=text,
            touchscreen_prompts=touchscreen_prompts,
            waiting_for_input=waiting_for_input,
            done=done,
            speaker_prompt=speaker_prompt,
            tr_question_summary=tr_question_summary,
            eligible_speakers=eligible_speakers,
            phase=phase,
            utterance_id=utterance_id,
            tts_utterance_id=tts_utterance_id,
        )

        self._log_output(text, speaker, touchscreen_prompts)

        await self.output_queue.put(event)

    async def start_stream(
        self,
        speaker: str | None = None,
        phase: str | None = None,
        stream_to_websocket: bool | None = None,
    ) -> "OutputStreamSession":
        await self._wait_for_audio_ready()
        if phase is None and self.state is not None:
            phase = self.state.phase
        if stream_to_websocket is None:
            if self.session is not None:
                stream_to_websocket = (
                    self.session.websocket_streaming
                    and self.output_queue.websocket_subscribed
                )
            else:
                stream_to_websocket = self.output_queue.websocket_subscribed

        tts_utterance_id = self._next_utterance_id()
        utterance_id = self._format_utterance_id(tts_utterance_id)

        # Pre-emptively kill the stream if a user-side safety hit
        # arrived before this utterance was created.
        if self.safety is not None:
            self.safety.consume_kill_next(utterance_id)

        return OutputStreamSession(
            manager=self,
            utterance_id=utterance_id,
            tts_utterance_id=tts_utterance_id,
            stream_to_websocket=True,
            allow_websocket_stream=bool(stream_to_websocket),
            speaker=speaker,
            phase=phase,
        )

    async def _wait_for_audio_ready(self) -> None:
        if not config.tts_enabled or not getattr(self.output_queue, "any_audio_subscribers", False):
            return

    def _should_flush(self, pending: str) -> bool:
        if len(pending) >= STREAM_FLUSH_CHARS:
            return True
        return any(ch in pending for ch in STREAM_FLUSH_BOUNDARIES)

    async def _send_stream_chunk(
        self,
        utterance_id: str,
        tts_utterance_id: int,
        text: str | None,
        *,
        waiting_for_input: bool = False,
        done: bool = False,
        speaker_prompt: dict[str, str] | None = None,
        tr_question_summary: str | None = None,
        touchscreen_prompts: list[dict] | None = None,
        eligible_speakers: list[HandRaisePrompt] | None = None,
        phase: str | None = None,
        allow_empty: bool = False,
        stream_end: bool = False,
        stream_to_websocket: bool = True,
    ) -> None:
        if not text and not allow_empty:
            return
        event = self._build_output_event(
            text=text,
            touchscreen_prompts=touchscreen_prompts,
            waiting_for_input=waiting_for_input,
            done=done,
            speaker_prompt=speaker_prompt,
            tr_question_summary=tr_question_summary,
            eligible_speakers=eligible_speakers,
            phase=phase,
            utterance_id=utterance_id,
            tts_utterance_id=tts_utterance_id,
            streaming=True,
        )
        event["stream_to_websocket"] = stream_to_websocket
        if stream_end:
            event["stream_end"] = True
        await self.output_queue.put(event)

    async def _send_stream_final(
        self,
        text: str,
        waiting_for_input: bool,
        done: bool,
        speaker: str | None,
        speaker_prompt: dict[str, str] | None,
        tr_question_summary: str | None,
        touchscreen_prompts: list[dict] | None,
        eligible_speakers: list[HandRaisePrompt] | None,
        phase: str | None,
        utterance_id: str,
        tts_utterance_id: int,
        websocket_only: bool | None = None,
    ) -> None:
        event = self._build_output_event(
            text=text,
            touchscreen_prompts=touchscreen_prompts,
            waiting_for_input=waiting_for_input,
            done=done,
            speaker_prompt=speaker_prompt,
            tr_question_summary=tr_question_summary,
            eligible_speakers=eligible_speakers,
            phase=phase,
            utterance_id=utterance_id,
            tts_utterance_id=tts_utterance_id,
            streaming=False,
            websocket_only=websocket_only,
        )
        self._log_output(text, speaker, touchscreen_prompts)
        await self.output_queue.put(event)

    async def send_debug(self, agent: str, content: str, phase: str):
        """Send debug_message (internal agent communication)"""
        event = {
            "type": "debug_message",
            "timestamp": datetime.now().isoformat(),
            "debug": True,
            "agent": agent,
            "content": content,
            "phase": phase,
        }

        # Log LLM outputs that are not sent to frontend (debug messages)
        logger.info(f"[agent={agent}] " f"LLM output (not sent to frontend): {content}")

        await self.output_queue.put(event)

    async def send_supplemental_notes(self, notes: SupplementalNotes):
        """Send supplemental_notes message to clients"""
        event = {
            "type": "supplemental_notes",
            "timestamp": datetime.now().isoformat(),
            "notes": notes.model_dump(),
        }
        await self.output_queue.put(event)


@dataclass
class OutputStreamSession:
    manager: OutputManager
    utterance_id: str
    tts_utterance_id: int
    stream_to_websocket: bool
    allow_websocket_stream: bool
    speaker: str | None
    phase: str | None
    _buffer: str = ""
    _pending: str = ""

    @property
    def full_text(self) -> str:
        return self._buffer

    async def write(self, text: str) -> None:
        if not text:
            return

        # Safety: if this utterance has been flagged unsafe, raise so
        # the LLM consumer (stream_text_response / stream_json_field)
        # can break out of its loop and close the OpenAI stream.
        safety = self.manager.safety
        if safety is not None and safety.is_killed(self.utterance_id):
            raise StreamCancelled(
                f"safety killed utterance {self.utterance_id}"
            )

        # Out-of-band moderation. Non-blocking — TTS is not delayed
        # waiting on a moderation result.
        if safety is not None:
            safety.feed_model_delta(self.utterance_id, text)

        # L3 output reviewer (parallel) — accumulates stream chunks and,
        # past a threshold, fires an LLM safety check. In enforce mode,
        # a detection calls back into safety.kill_utterance, which the
        # safety guard at the top of this method will catch on the next
        # write, halting the OpenAI stream.
        if safety is not None and config.output_reviewer_enabled:
            from debate.services import output_reviewer
            output_reviewer.feed_chunk(self.utterance_id, text, safety)

        self._buffer += text

        if not self.stream_to_websocket:
            return

        self._pending += text
        if self.manager._should_flush(self._pending):
            await self.manager._send_stream_chunk(
                self.utterance_id,
                self.tts_utterance_id,
                self._pending,
                stream_to_websocket=self.allow_websocket_stream,
            )
            self._pending = ""

    async def finish(
        self,
        text: str | None = None,
        waiting_for_input: bool = False,
        done: bool = False,
        speaker_prompt: dict[str, str] | None = None,
        tr_question_summary: str | None = None,
        touchscreen_prompts: list[dict] | None = None,
        eligible_speakers: list[HandRaisePrompt] | None = None,
    ) -> None:
        if text is not None:
            self._buffer = text

        # Flush whatever's left in the safety buffer before this
        # utterance closes (will moderate the tail unless killed).
        safety = self.manager.safety
        if safety is not None:
            safety.flush_model(self.utterance_id)

        # Final-flush the L3 reviewer too (in case the response ended
        # below the flush threshold).
        if config.output_reviewer_enabled:
            from debate.services import output_reviewer
            output_reviewer.flush(self.utterance_id)

        if self.stream_to_websocket:
            logger.info(
                "Stream finish: utterance_id=%s tts_utterance_id=%s pending_len=%d",
                self.utterance_id,
                self.tts_utterance_id,
                len(self._pending),
            )
            pending = self._pending
            self._pending = ""
            await self.manager._send_stream_chunk(
                self.utterance_id,
                self.tts_utterance_id,
                pending if pending else None,
                waiting_for_input=waiting_for_input,
                done=done,
                speaker_prompt=speaker_prompt,
                tr_question_summary=tr_question_summary,
                touchscreen_prompts=touchscreen_prompts,
                eligible_speakers=eligible_speakers,
                phase=self.phase,
                allow_empty=True,
                stream_end=True,
                stream_to_websocket=self.allow_websocket_stream,
            )
            if not self.allow_websocket_stream:
                await self.manager._send_stream_final(
                    text=self._buffer,
                    waiting_for_input=waiting_for_input,
                    done=done,
                    speaker=self.speaker,
                    speaker_prompt=speaker_prompt,
                    tr_question_summary=tr_question_summary,
                    touchscreen_prompts=touchscreen_prompts,
                    eligible_speakers=eligible_speakers,
                    phase=self.phase,
                    utterance_id=self.utterance_id,
                    tts_utterance_id=self.tts_utterance_id,
                    websocket_only=True,
                )
            return

        await self.manager._send_stream_final(
            text=self._buffer,
            waiting_for_input=waiting_for_input,
            done=done,
            speaker=self.speaker,
            speaker_prompt=speaker_prompt,
            tr_question_summary=tr_question_summary,
            touchscreen_prompts=touchscreen_prompts,
            eligible_speakers=eligible_speakers,
            phase=self.phase,
            utterance_id=self.utterance_id,
            tts_utterance_id=self.tts_utterance_id,
        )

    def cancel(self) -> None:
        self._pending = ""


class AsyncInputHandler:
    """Async input handler that replaces console_input for WebSocket-based I/O"""

    def __init__(
        self,
        input_queue: asyncio.Queue[InboundSocketMessage],
        output_queue: BroadcastQueue,
        session: DebateSession | None = None,
        state: DebateState | None = None,
    ):
        self._input_queue = input_queue
        self._output_queue = output_queue
        self._session = session
        self._state = state

        # Queue for notifying nodes of new input events
        self._input_event_queue = BroadcastQueue()

        # Background processing task
        self._processor_task = None
        self._local_participant_utterance_counter = -1

    def _next_participant_utterance_id(self) -> str:
        if self._session is not None:
            self._session.participant_utterance_counter += 1
            counter = self._session.participant_utterance_counter
        else:
            self._local_participant_utterance_counter += 1
            counter = self._local_participant_utterance_counter
        return f"P{counter:07d}"

    T = TypeVar("T", bound="DebateInput")

    @overload
    async def wait_for_input(self) -> DebateInput: ...

    @overload
    async def wait_for_input(self, __t: Type[T]) -> T: ...

    @overload
    async def wait_for_input(self, __t1: Type[T], __t2: Type[T], *ts: Type[T]) -> T: ...

    async def wait_for_input(
        self,
        *input_types: Type[DebateInput],
    ) -> DebateInput:
        """Wait for the next input of the specified type that arrives after this call

        Args:
            input_types: The DebateInput subclass or list of subclasses to wait for
        Returns:
            The input message when it is received
        """
        input_types = input_types or (DebateInput,)

        logger.info("Waiting for input types: %s", input_types)
        queue = await self._input_event_queue.subscribe()
        try:
            while True:
                data = await queue.get()
                logger.info("Got event from input_event_queue: %s", data)
                logger.info(f"{type(data)}")
                if any(isinstance(data, input_type) for input_type in input_types):
                    logger.info("Matched input type: %s", type(data))
                    return data
        finally:
            await self._input_event_queue.unsubscribe(queue)

    async def start(self):
        """Start the background input processor"""
        if self._processor_task is None:
            self._processor_task = asyncio.create_task(self._process_inputs())
            logger.info("AsyncInputHandler: Started background input processor")

    async def stop(self):
        """Stop the background input processor"""
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            logger.info("AsyncInputHandler: Stopped background input processor")

    async def _wait_for_connection_if_needed(self):
        """Wait for websocket connection if disconnected."""
        if self._session and not self._session.websocket_connected:
            logger.info("Input handler paused - waiting for websocket reconnection")
            await self._session.connection_event.wait()
            logger.info("Input handler resumed - websocket reconnected")
            return True
        return True

    async def _handle_participant_joined(self, message: ParticipantJoinedMessage) -> None:
        if not self._state:
            logger.warning("participant_joined received without state")
            return

        participant_id = message.participant_id
        agent_type = message.agent_type

        if participant_id in self._state.roster:
            logger.warning("Participant %s already in roster", participant_id)
            return

        participant = Participant(
            name=participant_id,
            camp=None,
            reason=None,
        )
        self._state.roster[participant_id] = participant
        logger.info("Added participant %s to roster", participant_id)

        if self._state.welcome_state:
            await self._state.welcome_state.visitor_queue.put(participant.name)
        self._input_event_queue.put_nowait(ParticipantJoinedInput(participant=participant))

        if agent_type and self._session:
            try:
                from debate.debug_agents import DebugAgent

                if self._session.debug_manager is None:
                    from debate.debug_manager import DebugAgentManager

                    self._session.debug_manager = DebugAgentManager(
                        self._session,
                        config.llm_config_small.model_dump(),
                        {},
                    )
                    self._session.debug_manager.running = True
                    logger.info("Created DebugAgentManager for dynamic agent creation")

                agent_queue = await self._output_queue.subscribe()
                agent = DebugAgent(
                    participant=participant,
                    debate_state=self._state,
                    input_queue=self._input_queue,
                    llm_config=config.llm_config_small.model_dump(),
                    session_id=self._session.session_id,
                    agent_type=agent_type,
                )
                self._session.debug_manager.agents[participant_id] = agent
                await agent.start(agent_queue)
                logger.info(
                    "Created debug agent for %s with type %s",
                    participant_id,
                    agent_type,
                )
            except Exception as e:
                logger.error(
                    "Failed to create debug agent for %s: %s",
                    participant_id,
                    e,
                    exc_info=True,
                )

        if self._output_queue:
            await self._output_queue.put(
                {
                    "type": "debug_message",
                    "timestamp": datetime.now().isoformat(),
                    "debug": True,
                    "agent": "System",
                    "content": (
                        f"Participant {participant_id} joined the session"
                        + (f" (agent_type: {agent_type})" if agent_type else "")
                    ),
                    "phase": self._state.phase,
                }
            )

    async def _handle_start_debate(self, _: StartDebateMessage) -> None:
        if not self._session:
            logger.warning("start_debate received without session")
            return
        if not self._state:
            logger.warning("start_debate received without state")
            return
        if not self._session.orchestrator:
            logger.warning("No orchestrator found for session, cannot end phase")
            return
        if self._state.phase == "scenario":
            logger.info("start_debate received but already in scenario")
            return

        logger.info("start_debate received, jumping to scenario from %s", self._state.phase)
        self._session.orchestrator.request_jump_to_phase("scenario")
        await self._session.orchestrator.end_current_phase()

    async def _handle_start_intro(self, _: StartIntroMessage) -> None:
        if not self._state:
            logger.warning("start_intro received without state")
            return
        if self._state.phase != "welcome":
            logger.warning("start_intro received but phase is %s", self._state.phase)
            return
        if not self._session or not self._session.orchestrator:
            logger.warning("No orchestrator found for session, cannot end phase")
            return

        logger.info("Starting intro phase from %s", self._state.phase)
        await self._session.orchestrator.end_current_phase()

    async def _process_inputs(self):
        """Background task that continuously processes inputs and updates state"""
        while True:
            try:
                # Check if session is completed or errored
                if self._session and self._session.status in ("completed", "error"):
                    logger.info(
                        f"Input processor stopping: session status is {self._session.status}"
                    )
                    break

                # Wait for connection if needed
                if not await self._wait_for_connection_if_needed():
                    await asyncio.sleep(1.0)  # Wait a bit before retrying
                    continue

                # Get input from queue
                message = await self._input_queue.get()

                if isinstance(message, ParticipantJoinedMessage):
                    await self._handle_participant_joined(message)

                elif isinstance(message, ParticipantInfoMessage):
                    participant_id = message.participant_id
                    if not self._state:
                        logger.warning(
                            "participant_info message received without state"
                        )
                        continue

                    participant = self._state.roster.get(participant_id)
                    if not participant:
                        participant = Participant(name=participant_id)
                        logger.warning(
                            "participant_info for unknown participant: %s, creating",
                            participant_id,
                        )
                        self._state.roster[participant_id] = participant

                    if message.is_speaker is not None:
                        participant.can_speak = bool(message.is_speaker)

                elif isinstance(message, StartDebateMessage):
                    await self._handle_start_debate(message)

                elif isinstance(message, StartIntroMessage):
                    await self._handle_start_intro(message)

                elif isinstance(message, ParticipantInputMessage):
                    # Process spoken text
                    spoken = message.spoken
                    if spoken:
                        text = spoken.text.strip()
                        participant_id = spoken.participant_id or "Unknown Participant"
                        utterance_id = spoken.utterance_id
                        if not utterance_id:
                            utterance_id = self._next_participant_utterance_id()
                            logger.warning(
                                "participant_input missing utterance_id; generated %s",
                                utterance_id,
                            )
                        if text:
                            if self._state:
                                self._state.inputs.last_spoken_text = text
                                self._state.inputs.last_spoken_participant = (
                                    participant_id
                                )
                                self._state.inputs.last_spoken_utterance_id = (
                                    utterance_id
                                )
                            logger.info(
                                f"Inbound text message: participant={participant_id}, text={text}"
                            )
                            # Notify text waiters
                            self._input_event_queue.put_nowait(
                                SpokenTextInput(
                                    participant_id=participant_id,
                                    text=text,
                                    utterance_id=utterance_id,
                                )
                            )
                        else:
                            logger.info(f"Inbound empty text message: participant={participant_id}")
                            self._input_event_queue.put_nowait(
                                TimeoutInput(
                                    phase=self._state.phase if self._state else "unknown"
                                )
                            )

                    # Process raised hands
                    # Always update and trigger event if raised_hands is explicitly provided
                    # This handles the case where client times out and sends raised_hands=[]
                    raised_hands = None
                    if message.raised_hands is not None and self._state:
                        raised_hands = message.raised_hands
                        self._state.inputs.raised_hands = raised_hands
                        logger.info(f"Inbound raised hands: {raised_hands}")
                        # Notify raised hand waiters (even if empty list, to signal timeout)
                        self._input_event_queue.put_nowait(
                            HandRaiseInput(participant_ids=raised_hands)
                        )

                    # Process touchscreen responses
                    touchscreen_responses = message.touchscreen_responses
                    if touchscreen_responses:
                        if self._state:
                            self._state.inputs.touchscreen_responses.update(
                                touchscreen_responses
                            )
                        for pid, response in touchscreen_responses.items():
                            logger.info(
                                f"Inbound touchscreen entry: participant={pid}, response={response}"
                            )
                        # Notify touchscreen waiters
                        self._input_event_queue.put_nowait(
                            TouchscreenVoteInput(data=touchscreen_responses)
                        )

                    if spoken is None and touchscreen_responses is None and raised_hands is None:
                        logger.info("Treating empty input as frontend timeout")
                        self._input_event_queue.put_nowait(
                            TimeoutInput(
                                phase=self._state.phase if self._state else "unknown"
                            )
                        )
                        continue

                    # Echo as debug message for UI display
                    if self._output_queue:
                        parts = []
                        if spoken and spoken.text:
                            parts.append(
                                f"{spoken.participant_id}: {spoken.text}"
                            )
                        if touchscreen_responses:
                            response_parts = [
                                f"{pid}: {resp}"
                                for pid, resp in touchscreen_responses.items()
                            ]
                            if response_parts:
                                parts.append(
                                    f"Touchscreen responses: {', '.join(response_parts)}"
                                )
                        if raised_hands:
                            parts.append(f"Raised hands: {', '.join(raised_hands)}")

                        content = (
                            " | ".join(parts) if parts else "Empty participant input"
                        )

                        agent_name = (
                            spoken.participant_id
                            if spoken and spoken.text
                            else "System"
                        )

                        debug_message = {
                            "type": "debug_message",
                            "timestamp": datetime.now().isoformat(),
                            "debug": True,
                            "agent": agent_name,
                            "content": content,
                            "phase": "participant_input",
                        }
                        try:
                            await self._output_queue.put(debug_message)
                        except Exception as e:
                            logger.error(
                                f"Error echoing participant input: {e}",
                                exc_info=True,
                            )

                elif isinstance(message, CameraEventMessage):
                    logger.info(
                        f"Camera event received: {message.event_type} id={message.event_id}"
                    )
                    self._input_event_queue.put_nowait(
                        CameraEventInput(
                            event_type=message.event_type,
                            event_id=message.event_id,
                            payload=message.payload,
                            timestamp=datetime.now().timestamp(),
                        )
                    )

                    if self._output_queue:
                        debug_message = {
                            "type": "debug_message",
                            "timestamp": datetime.now().isoformat(),
                            "debug": True,
                            "agent": "CameraService",
                            "content": f"Camera event: {message.event_type}",
                            "phase": "camera",
                        }
                        try:
                            await self._output_queue.put(debug_message)
                        except Exception as e:
                            logger.error(
                                f"Error echoing camera event: {e}",
                                exc_info=True,
                            )

                elif isinstance(message, ParticipantReactionMessage):
                    participant_id = message.participant_id
                    utterance_id = message.utterance_id
                    reaction = message.reaction

                    if self._state:
                        self._state.reactions.append(
                            ParticipantReactionRecord(
                                participant_id=participant_id,
                                utterance_id=utterance_id,
                                reaction=reaction,
                            )
                        )

                    self._input_event_queue.put_nowait(
                        ParticipantReactionInput(
                            participant_id=participant_id,
                            utterance_id=utterance_id,
                            reaction=reaction,
                        )
                    )

                    if self._output_queue:
                        debug_message = {
                            "type": "debug_message",
                            "timestamp": datetime.now().isoformat(),
                            "debug": True,
                            "agent": participant_id,
                            "content": f"Reaction: {reaction} -> {utterance_id}",
                            "phase": "participant_reaction",
                        }
                        try:
                            await self._output_queue.put(debug_message)
                        except Exception as e:
                            logger.error(
                                f"Error echoing participant reaction: {e}",
                                exc_info=True,
                            )

                else:
                    logger.warning(
                        "Ignoring unsupported input queue message type: %s",
                        type(message).__name__,
                    )

            except asyncio.CancelledError:
                logger.info("Input processor task cancelled")
                break
            except Exception as e:
                logger.error(
                    f"[AsyncInputHandler] Error processing input: {e}",
                    exc_info=True,
                )
                # Continue processing even if one input fails
                await asyncio.sleep(0.1)
        logger.info("AsyncInputHandler: Input processor task finished")


class IOService(BaseModel):
    """Service providing input/output handlers"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    input: AsyncInputHandler
    output: OutputManager
    notes_updater: NotesUpdater | None = None
