# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
StorysNode — camera-aware storytelling conversation node.

Main conversation loop. Delegates to:
  - greeting.py: pre-greeting selection (round 1)
  - person_memory.py: returning visitor recognition
  - threat_guard.py: threat detection on input
  - camera_utils.py: shared camera helpers
"""
import asyncio
import logging
import random
import time
from datetime import datetime

from api.config import config
from debate.graph import MiniGraph
from debate.models.constants import TR_SPEAKER
from debate.models.inputs import CameraEventInput, SpokenTextInput
from debate.models.state import CameraState, DebateState, HistoryEntry
from debate.nodes.base import BaseNode
from debate.nodes.camera_utils import (
    apply_camera_event,
    build_camera_context,
    calc_time_pressure,
    count_people_waiting,
    get_active_visitor_appearance,
    pick_next_visitor,
)
from debate.nodes.storys import person_memory, threat_guard
from debate.services import prompt_injection
from debate.nodes.storys.greeting import select_pre_greeting
from debate.nodes.storys.left_ack import pick_left_ack
from debate.services.io import OutputStreamSession, TimeoutInput

logger = logging.getLogger(f"lia.{__name__}")


class StorysNode(BaseNode):
    """Camera-aware storytelling node."""

    def __init__(self, io, agent_registry):
        super().__init__(io, agent_registry)
        self._turn_start: float | None = None
        self._rag_service = None
        self._history_service = None
        self._history_watermark: int = 0
        self._pending_safety_directive: str | None = None

    # ------------------------------------------------------------------
    # Watermark
    # ------------------------------------------------------------------

    def _advance_watermark(self, state: DebateState) -> None:
        self._history_watermark = len(state.history)
        if state.phase_memory is None:
            state.phase_memory = {}
        state.phase_memory["_history_watermark"] = self._history_watermark

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        if not state.camera_state:
            state.camera_state = CameraState()

        # Restore watermark from phase_memory (survives restarts)
        if state.phase_memory and "_history_watermark" in state.phase_memory:
            self._history_watermark = state.phase_memory["_history_watermark"]

        if self._turn_start is None:
            self._turn_start = time.time()

        people_waiting = self._count_people_waiting(state)
        time_pressure = self._time_pressure(state, people_waiting)

        if time_pressure == "hard_limit":
            return await self._hand_off_to_next(state)

        round_num = self._count_rounds(state) + 1

        # Round 1: pre-greeting (static, instant, no LLM)
        if round_num == 1:
            return await self._send_pre_greeting(state)

        # Round 2+: LLM generation
        (response, done, memory, stream_session) = await self._generate_and_stream(
            state,
            round_num=round_num,
            wrapping_up=(time_pressure == "soft_limit"),
            people_waiting=people_waiting,
        )

        if not response:
            logger.warning("No response generated, ending storys phase")
            stream_session.cancel()
            return MiniGraph.END

        self._record_tr_response(state, response)
        self._store_memory(state, memory)

        if done:
            logger.info("Storys agent marked done=true, ending phase")
            await stream_session.finish(text=response, waiting_for_input=False)
            return MiniGraph.END

        participants = list(state.roster.keys()) if state.roster else ["visitor"]
        speaker_prompt = {"participant_id": participants[0], "text": ""}
        await stream_session.finish(
            text=response,
            waiting_for_input=True,
            speaker_prompt=speaker_prompt,
        )

        event = await self._wait_for_input_with_camera(state)

        if isinstance(event, CameraEventInput) and event.event_type == "MIC_ZONE_LEFT":
            logger.info("[Storys] Person left mic zone, returning to camera")
            await self._say_left_ack(state)
            self._advance_watermark(state)
            state.camera_state.return_to_camera = True
            return MiniGraph.END

        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)

        return "TimeoutNudgeNode"

    # ------------------------------------------------------------------
    # Camera input
    # ------------------------------------------------------------------

    async def _wait_for_input_with_camera(self, state: DebateState):
        while True:
            event = await self.io.input.wait_for_input(
                SpokenTextInput, CameraEventInput, TimeoutInput
            )
            if isinstance(event, CameraEventInput):
                try:
                    apply_camera_event(state.camera_state, event)
                except Exception as e:
                    logger.warning("[Storys] Camera event error %s: %s",
                                   event.event_type, e)
                if (
                    event.event_type == "MIC_ZONE_LEFT"
                    and state.camera_state
                    and state.camera_state.active_mic_person is None
                ):
                    return event
                continue
            return event

    # ------------------------------------------------------------------
    # Round 1: Pre-greeting
    # ------------------------------------------------------------------

    async def _send_pre_greeting(self, state: DebateState) -> str:
        text, is_returning = select_pre_greeting(
            state,
            get_memory_fn=self._get_memory,
            current_mode_fn=self._current_mode,
        )

        self._record_tr_response(state, text)

        participants = list(state.roster.keys()) if state.roster else ["visitor"]
        speaker_prompt = {"participant_id": participants[0], "text": ""}
        await self.io.output.send_output(
            text=text,
            waiting_for_input=True,
            speaker_prompt=speaker_prompt,
            phase="storys",
        )

        event = await self._wait_for_input_with_camera(state)

        if isinstance(event, CameraEventInput) and event.event_type == "MIC_ZONE_LEFT":
            logger.info("[Storys] Person left during pre-greeting")
            await self._say_left_ack(state)
            self._advance_watermark(state)
            state.camera_state.return_to_camera = True
            return MiniGraph.END

        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)

        return "TimeoutNudgeNode"

    # ------------------------------------------------------------------
    # LLM generation
    # ------------------------------------------------------------------

    async def _generate_and_stream(
        self,
        state: DebateState,
        *,
        round_num: int,
        wrapping_up: bool,
        people_waiting: int,
    ) -> tuple[str, bool, dict, OutputStreamSession]:
        history = self._build_history(state)
        memory = self._get_memory(state)
        visitor_appearance = get_active_visitor_appearance(state)
        camera_context = build_camera_context(state)
        engagement = (
            state.camera_state.latest_engagement
            if state.camera_state else None
        )

        # RAG: read story + KB context
        active_story = None
        knowledge_context = None
        curator_hint = None
        rag_data = state.phase_memory.get("storys_rag") if state.phase_memory else None

        if rag_data:
            title = rag_data.get("title")
            narrative = rag_data.get("narrative")
            kb_ctx = rag_data.get("knowledge_context", "")

            # Unload stale stories
            stories_told = memory.get("stories_told", []) if memory else []
            already_told = title and any(
                title.lower() in told.lower() or told.lower() in title.lower()
                for told in stories_told
            )
            active_turns = rag_data.get("_active_turns", 0) + 1
            rag_data["_active_turns"] = active_turns

            if already_told:
                logger.info("[Storys] Unloading story '%s' (already told)", title)
                state.phase_memory.pop("storys_rag", None)
            elif active_turns > 3:
                logger.info("[Storys] Unloading story '%s' (stale)", title)
                state.phase_memory.pop("storys_rag", None)
            else:
                if narrative:
                    active_story = {
                        "title": title,
                        "narrative": narrative,
                        "reason": rag_data.get("reason", ""),
                    }
                if kb_ctx:
                    knowledge_context = kb_ctx
                curator_hint = {"title": title, "reason": rag_data.get("reason", "")}

        # Visitor history stack
        recent_visitors = None
        if state.phase_memory:
            stack = state.phase_memory.get("visitor_stack", [])
            if stack:
                recent_visitors = stack

        # Returning visitor context
        returning_visitor = person_memory.lookup(state)

        mode = self._current_mode(state)
        agent = self._select_agent(mode)
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase="storys",
        )

        today_in_history = None
        if mode != "child":
            from debate.scenarios.this_day import get_this_day
            today_in_history = get_this_day().for_today() or None

        safety_directive = self._pending_safety_directive
        self._pending_safety_directive = None

        # Buffer the first call instead of streaming it straight to TTS. We
        # don't yet know whether the model gave a real answer or just a short
        # "let me think" ack (needs_more_kb), and we want full control over
        # whether that ack is ever voiced. Audio timing is unaffected: the
        # worker synthesizes per-utterance on stream_end, so writing the text
        # at once is equivalent to streaming it.
        first_chunks: list[str] = []

        async def _buffer_first(text: str) -> None:
            first_chunks.append(text)

        parsed = await agent.generate_response_streaming(
            round=round_num,
            max_rounds=state.max_rounds,
            history=history,
            participants=list(state.roster.keys()) if state.roster else [],
            last_speaker=state.last_speaker,
            memory=memory or {},
            final=False,
            on_response_chunk=_buffer_first,
            visitor_appearance=visitor_appearance,
            camera_context=camera_context,
            engagement=engagement,
            active_story=active_story,
            knowledge_context=knowledge_context,
            recent_visitors=recent_visitors,
            returning_visitor=returning_visitor,
            today_in_history=today_in_history,
            curator_hint=curator_hint,
            wrapping_up=wrapping_up,
            people_waiting=people_waiting,
            audience_mode=mode,
            safety_directive=safety_directive,
        )

        routing_fired = False
        ack_text_before_pause = ""
        needs_kb = (
            config.storys_self_routing
            and self._rag_service
            and bool(parsed.get("needs_more_kb"))
        )

        if not needs_kb:
            # Direct answer — emit what the model produced into the live stream.
            direct = (parsed.get("response") or "".join(first_chunks)).strip()
            if direct:
                await stream_session.write(direct)
        else:
            # Self-routing: the buffered text is a short ack. Voice it only
            # some of the time (storys_kb_ack_probability); otherwise fetch KB
            # silently and go straight to the answer. Either way the real
            # answer streams into a FRESH utterance below.
            routing_fired = True
            ack = (parsed.get("response") or "".join(first_chunks)).strip()
            speak_ack = random.random() < config.storys_kb_ack_probability
            logger.info(
                "[Storys] self-routing: needs_more_kb=true "
                "(speak_ack=%s), fetching RAG sync",
                speak_ack,
            )
            if speak_ack and ack:
                # Voice the ack as its own utterance so it plays immediately,
                # then (optionally) a beat of silence before the answer.
                await stream_session.write(ack)
                await stream_session.finish()
                ack_text_before_pause = ack
                pause_sec = config.storys_kb_ack_pause_sec
                if pause_sec > 0:
                    await asyncio.sleep(pause_sec)
            else:
                # Discard the unspoken ack (nothing was sent to TTS).
                stream_session.cancel()
            stream_session = await self.io.output.start_stream(
                speaker=TR_SPEAKER,
                phase="storys",
            )
            # retrieve_now returns False if the fetch raised OR the RAG
            # came back empty. We tell the second LLM call so it answers
            # honestly instead of citing prior-turn context that's
            # irrelevant to the current question.
            try:
                kb_available = await self._rag_service.retrieve_now()
            except Exception as e:
                logger.warning("[Storys] retrieve_now raised: %s", e)
                kb_available = False

            new_active_story = None
            new_knowledge_context = None
            new_curator_hint = None
            if kb_available:
                rag_data = (
                    state.phase_memory.get("storys_rag")
                    if state.phase_memory else None
                ) or {}
                new_knowledge_context = rag_data.get("knowledge_context") or None
                if rag_data.get("title") and rag_data.get("narrative"):
                    new_active_story = {
                        "title": rag_data["title"],
                        "narrative": rag_data["narrative"],
                        "reason": rag_data.get("reason", ""),
                    }
                    new_curator_hint = {
                        "title": rag_data["title"],
                        "reason": rag_data.get("reason", ""),
                    }
            else:
                logger.info(
                    "[Storys] self-routing: RAG unavailable, "
                    "asking LLM to answer honestly"
                )

            parsed = await agent.generate_response_streaming(
                round=round_num,
                max_rounds=state.max_rounds,
                history=history,
                participants=list(state.roster.keys()) if state.roster else [],
                last_speaker=state.last_speaker,
                memory=memory or {},
                final=False,
                on_response_chunk=stream_session.write,
                visitor_appearance=visitor_appearance,
                camera_context=camera_context,
                engagement=engagement,
                active_story=new_active_story,
                knowledge_context=new_knowledge_context,
                recent_visitors=recent_visitors,
                returning_visitor=returning_visitor,
                today_in_history=today_in_history,
                curator_hint=new_curator_hint,
                wrapping_up=wrapping_up,
                people_waiting=people_waiting,
                audience_mode=mode,
                safety_directive=safety_directive,
                # Tells the prompt's SELF-ROUTING block to stop asking
                # for more KB and to answer with what's available now.
                is_kb_retry=True,
                # When True, the prompt asks the model to admit
                # uncertainty rather than answer from training memory.
                kb_unavailable=(not kb_available),
            )

            # Belt-and-suspenders: if the model ignores `is_kb_retry` and
            # asks for MORE KB on the retry, we don't recurse — we
            # overwrite the response with an honest fallback so the user
            # never sees two acks in a row with no real answer.
            if bool(parsed.get("needs_more_kb")):
                logger.warning(
                    "[Storys] self-routing: model asked for KB on retry; "
                    "serving honest fallback"
                )
                fallback = (
                    " I don't recall the particulars of that. "
                    "You might check theodorerooseveltcenter.org."
                )
                await stream_session.write(fallback)
                parsed = {**parsed, "response": fallback}

        # When self-routing kicked in we want history to record the full
        # user-visible utterance (ack + real answer), so prefer the
        # buffered stream text over the second call's `response` field
        # alone. When we split into two utterances (pause_sec > 0), the
        # current stream_session only holds the answer, so we prepend the
        # ack text captured before the split.
        if routing_fired:
            answer_text = stream_session.full_text.strip()
            if ack_text_before_pause:
                response = (ack_text_before_pause.strip() + " " + answer_text).strip()
            else:
                response = answer_text
            if not response:
                response = (parsed.get("response") or "").strip()
        else:
            response = (parsed.get("response") or "").strip()
            if not response:
                response = stream_session.full_text.strip()

        done = bool(parsed.get("done", False))
        updated_memory = parsed.get("memory", memory)

        return response, done, updated_memory, stream_session

    # ------------------------------------------------------------------
    # Participant input
    # ------------------------------------------------------------------

    async def _handle_participant_input(
        self, state: DebateState, result: SpokenTextInput
    ) -> str:
        participant_text = result.text.strip()
        participant_name = result.participant_id

        state.history.append(
            HistoryEntry(
                speaker=participant_name,
                text=participant_text,
                audience="all",
                phase="storys",
                timestamp=datetime.now(),
                target=None,
            )
        )
        logger.info(f"[Storys] Received from {participant_name}: {participant_text[:50]}...")

        if threat_guard.is_threat(participant_text):
            await threat_guard.emit_threat_alert(self.io, participant_text)
            self._pending_safety_directive = (
                "The visitor's last message contained threatening, violent, "
                "or otherwise unsafe language. In your next reply, stay in "
                "Theodore Roosevelt's voice: acknowledge the discomfort "
                "briefly without lecturing, decline to engage with that "
                "topic, and gently pivot to a different subject (invite them "
                "to ask about one of your stories). Do not continue the "
                "previous narrative thread. Keep it warm and move on."
            )

        participant_text = self._apply_input_guardrail(
            state, participant_text, participant_name, result,
        )

        if self._rag_service:
            self._rag_service.enqueue()

        return self.name  # dynamic: "StorysNode" for base, "VipNode" for VIP

    # ------------------------------------------------------------------
    # Hand-off
    # ------------------------------------------------------------------

    async def _hand_off_to_next(self, state: DebateState) -> str:
        next_person = pick_next_visitor(state)
        next_desc: str | None = None
        if next_person:
            app = next_person.appearance or {}
            parts = [v for k, v in app.items() if v and k in ("top", "bottom", "notable")]
            next_desc = f"the friend in the {parts[0]}" if parts else "the next visitor"

        wrap_text = await self._generate_hand_off_text(state, next_desc)
        if not wrap_text:
            if next_desc:
                wrap_text = (
                    f"I have truly enjoyed our conversation. "
                    f"Now come on up, {next_desc} — I've been waiting for you."
                )
            else:
                wrap_text = (
                    "I have truly enjoyed our conversation. "
                    "Go well — and keep thinking about the things we talked about."
                )
            await self.io.output.send_output(
                text=wrap_text, waiting_for_input=False, phase="storys",
            )

        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER, text=wrap_text, audience="all",
                phase="storys", timestamp=datetime.now(), target=None,
            )
        )

        self._turn_start = time.time()
        if state.camera_state and next_person:
            state.camera_state.active_mic_person = next_person.person_id

        person_memory.save(state, self._get_memory(state), self._count_rounds(state))

        if self._history_service:
            try:
                await self._history_service.on_visitor_leave()
            except Exception as e:
                logger.warning("[Storys] Failed to summarize visitor: %s", e)

        self._advance_watermark(state)

        if state.phase_memory:
            state.phase_memory.pop("storys_rag", None)
            mem = state.phase_memory.get("storys", {})
            mem.pop("visitor_interests", None)
            mem.pop("visitor_name", None)
            mem.pop("hooks_found", None)
            mem.pop("last_topic", None)

        return self.name  # dynamic: "StorysNode" for base, "VipNode" for VIP

    async def _generate_hand_off_text(
        self, state: DebateState, next_person_description: str | None
    ) -> str:
        history = self._build_history(state)
        memory = self._get_memory(state)

        recent_visitors = None
        if state.phase_memory:
            stack = state.phase_memory.get("visitor_stack", [])
            if stack:
                recent_visitors = stack

        mode = self._current_mode(state)
        agent = self._select_agent(mode)
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase="storys",
        )

        try:
            parsed = await agent.generate_response_streaming(
                round=self._count_rounds(state) + 1,
                max_rounds=state.max_rounds,
                history=history,
                participants=list(state.roster.keys()) if state.roster else [],
                last_speaker=state.last_speaker,
                memory=memory or {},
                final=True,
                on_response_chunk=stream_session.write,
                recent_visitors=recent_visitors,
                hand_off=True,
                next_person_description=next_person_description,
                audience_mode=mode,
            )
            response = (parsed.get("response") or "").strip()
            if not response:
                response = stream_session.full_text.strip()
            if not response:
                stream_session.cancel()
                return ""
            await stream_session.finish(text=response, waiting_for_input=False, done=False)
            return response
        except Exception as e:
            logger.warning("[Storys] Hand-off LLM failed: %s", e)
            stream_session.cancel()
            return ""

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _count_rounds(self, state: DebateState) -> int:
        return sum(
            1 for e in state.history[self._history_watermark:]
            if e.phase == "storys" and e.speaker == TR_SPEAKER
        )

    def _build_history(self, state: DebateState) -> list[dict]:
        return [
            {"speaker": e.speaker, "text": e.text, "target": e.target}
            for e in state.history[self._history_watermark:]
            if e.phase == "storys"
        ]

    def _record_tr_response(self, state: DebateState, response: str) -> None:
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER, text=response, audience="all",
                phase="storys", timestamp=datetime.now(), target=None,
            )
        )

    async def _say_left_ack(self, state: DebateState) -> None:
        """Short static line TR murmurs when a visitor slips out mid-chat
        (neither hard-timed out nor said goodbye nor tripped the threat
        guard). Keeps the transition feeling intentional rather than a
        silent cutoff. No LLM call — latency matters here since the
        avatar is about to go back to idle."""
        text = pick_left_ack()
        self._record_tr_response(state, text)
        try:
            await self.io.output.send_output(
                text=text,
                waiting_for_input=False,
                phase="storys",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[Storys] Failed to send left-ack line: %s", e)

    def _get_memory(self, state: DebateState) -> dict:
        if not state.phase_memory:
            return {}
        return state.phase_memory.get("storys", {})

    def _current_mode(self, state: DebateState) -> str:
        cs = state.camera_state
        if cs and cs.active_mic_person is not None:
            entry = cs.persons.get(cs.active_mic_person)
            if entry and entry.audience_label in ("child", "adult"):
                return entry.audience_label
        return getattr(self.agent_registry, "storys_default_mode", None) or "adult"

    # ------------------------------------------------------------------
    # Overridable hooks (VipNode swaps these for honored-guest behavior)
    # ------------------------------------------------------------------

    def _select_agent(self, mode: str):
        """Pick the scenario agent for this turn. Overridable.

        Base storys picks the adult/child agent by audience mode. VipNode
        overrides this to use the VIP agent (looser/warmer prompt).
        """
        return self.agent_registry.storys_for_mode(mode)

    def _count_people_waiting(self, state: DebateState) -> int:
        """How many people are queued behind the active speaker. Overridable.

        Feeds both time-pressure and the agent's "people waiting" urgency.
        VipNode returns 0 so a camera-detected crowd never rushes a VIP.
        """
        return count_people_waiting(state)

    def _time_pressure(self, state: DebateState, people_waiting: int) -> str:
        """Compute soft/hard time pressure for this turn. Overridable.

        VipNode returns "none" so the VIP conversation is never cut short by
        the crowd/time hand-off (VIP visits end only via the idle-watchdog).
        """
        if not config.storys_time_pressure_enabled:
            return "none"
        return calc_time_pressure(
            self._turn_start or time.time(),
            people_waiting,
            soft_limit=config.storys_soft_limit_sec,
            hard_limit=config.storys_hard_limit_sec,
            solo_soft_limit=config.storys_solo_soft_limit_sec,
            solo_hard_limit=config.storys_solo_hard_limit_sec,
        )

    def _apply_input_guardrail(
        self,
        state: DebateState,
        participant_text: str,
        participant_name: str,
        result,
    ) -> str:
        """Prompt-injection audit. Returns the (possibly sanitized) text.

        Two paths:
          • L1 (regex patterns) runs SYNC right here — ~0ms. If it catches in
            enforce mode we sanitize the input + arm a safety directive BEFORE
            RAG / the scenario agent ever see it.
          • L2 (LLM detector) fires async — adds zero latency; covers semantic
            cases L1 misses; logs + auto-reports to /admin/reports for the PM
            dashboard.

        Overridable: VipNode loosens this to observe-only for recognized guests.
        """
        if not config.prompt_injection_enabled:
            return participant_text
        l1 = prompt_injection.detect_l1(participant_text) if config.prompt_injection_use_l1 else None
        if l1 and config.prompt_injection_mode == "enforce":
            logger.warning(
                "[PI/enforce] sanitizing input — category=%s reason=%s",
                l1.category, l1.reason,
            )
            # Rewrite text everywhere downstream sees it
            participant_text = l1.sanitized_text
            if state.history and state.history[-1].speaker == participant_name:
                state.history[-1].text = participant_text
            # Tell main agent how to handle it
            self._pending_safety_directive = prompt_injection.get_directive(l1.category)
            # Persist a report so the admin dashboard reflects enforcement
            if config.prompt_injection_auto_report:
                try:
                    prompt_injection._auto_report(result.text, l1, session_id=None)
                except Exception as e:  # noqa: BLE001
                    logger.error("[PI/enforce] auto-report failed: %s", e)
        else:
            # Either observe mode, or L1 didn't catch — fire full check
            # in background (L2 + auto-report).
            asyncio.create_task(
                prompt_injection.check_input(participant_text),
                name="prompt_injection_check",
            )
        return participant_text

    def _store_memory(self, state: DebateState, memory: dict | None) -> None:
        if not isinstance(memory, dict):
            return
        if not state.phase_memory:
            state.phase_memory = {}
        state.phase_memory["storys"] = memory

    def _save_person_memory(self, state: DebateState) -> None:
        """Convenience method for StorysEngine.after_run()."""
        person_memory.save(state, self._get_memory(state), self._count_rounds(state))
