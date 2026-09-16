# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from datetime import datetime

from api.config import config
from debate.graph import MiniGraph
from debate.models.constants import TR_SPEAKER
from debate.models.inputs import HandRaiseInput, ParticipantJoinedInput, SpokenTextInput
from debate.models.sockets import HandRaisePrompt
from debate.models.state import DebateState, HistoryEntry, WelcomeState
from debate.nodes.base import BaseNode
from debate.services.io import OutputStreamSession, TimeoutInput
from debate.scenarios import get_scenario_context
from debate.services.shared import get_knowledge_base_service, get_kb_search_manager

logger = logging.getLogger(f"lia.{__name__}")


class WelcomeNode(BaseNode):
    """Agent-driven small-talk welcome phase."""

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        if not state.welcome_state:
            state.welcome_state = WelcomeState(
                max_rounds_per_visitor=config.welcome_max_rounds_per_visitor
            )

        participants = list(state.roster.keys())
        if not participants:
            logger.info("No participants in roster, waiting for join")
            await self._wait_for_participants(state)
            participants = list(state.roster.keys())

        next_node = await self._maybe_handle_raised_hand(state)
        if next_node:
            return next_node

        welcome_round = self._count_welcome_rounds(state) + 1

        final = False
        (
            response,
            target,
            stream_session,
            memory_update,
            relations,
        ) = await self._generate_and_stream_prompt(
            state,
            welcome_round=welcome_round,
            final=final,
        )

        if not response:
            logger.warning("No response generated, ending welcome")
            stream_session.cancel()
            return MiniGraph.END

        self._record_tr_response(state, response, target)
        self._apply_memory_updates(state, memory_update)
        self._record_relations(state, relations)

        speaker_prompt = self._build_speaker_prompt(target)
        eligible_speakers = (
            None
            if speaker_prompt is not None
            else (
                self._build_eligible_speakers(participants)
                if config.hand_raise_enabled
                else None
            )
        )
        await stream_session.finish(
            text=response,
            waiting_for_input=True,
            speaker_prompt=speaker_prompt,
            eligible_speakers=eligible_speakers,
        )

        if eligible_speakers is not None:
            event = await self._wait_after_hand_raise_prompt(
                state=state,
                phase="welcome",
                eligible_speakers=eligible_speakers,
                participants=participants,
            )
        else:
            event = await self.io.input.wait_for_input(
                SpokenTextInput, HandRaiseInput, TimeoutInput
            )
        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)
        elif isinstance(event, HandRaiseInput):
            logger.info("Hand raise received during welcome, continuing")
            return "WelcomeNode"
        else:  # TimeoutInput
            logger.info("Empty or no input received, sending timeout nudge")
            return "TimeoutNudgeNode"

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
                phase="welcome",
                timestamp=datetime.now(),
                target=participant_name,
            )
        )

        logger.info(f"Received from {participant_name}: {participant_text[:50]}...")

        # Schedule background KB search for next round (if async mode enabled)
        await self._schedule_kb_search_if_needed(state, participant_text)

        return "WelcomeNode"

    async def _maybe_handle_raised_hand(self, state: DebateState) -> str | None:
        if not config.hand_raise_enabled:
            return None

        raised_hand = self._choose_raised_hand(
            state.inputs.raised_hands, list(state.roster.keys())
        )
        if not raised_hand:
            return None

        logger.info(f"Participant {raised_hand} has hand raised, letting them speak")
        ack_text = f"Go ahead, {raised_hand}."

        if raised_hand in state.inputs.raised_hands:
            state.inputs.raised_hands.remove(raised_hand)

        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=ack_text,
                audience="all",
                phase="welcome",
                timestamp=datetime.now(),
                target=raised_hand,
            )
        )

        await self.io.output.send_output(
            text=ack_text,
            waiting_for_input=True,
            phase="welcome",
            speaker_prompt={
                "participant_id": raised_hand,
                "text": "",
            },
        )

        event = await self.io.input.wait_for_input(SpokenTextInput, TimeoutInput)
        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)
        else:  # TimeoutInput
            logger.info(f"No input received from raised hand participant {raised_hand}")
        return "TimeoutNudgeNode"

    async def _wait_for_participants(self, state: DebateState) -> None:
        while not state.roster:
            logger.debug("waiting for ParticipantJoinedInput")
            event = await self.io.input.wait_for_input(ParticipantJoinedInput)
            logger.debug(f"got event {event}")
        logger.debug("returning")

    async def _generate_and_stream_prompt(
        self,
        state: DebateState,
        *,
        welcome_round: int,
        final: bool,
    ) -> tuple[
        str,
        str | None,
        OutputStreamSession,
        dict | None,
        list | None,
    ]:
        history = self._build_welcome_history(state)
        last_speaker = state.last_speaker
        participants = self._build_participants(state)
        kb_context = await self._get_kb_context(state)

        agent = self.agent_registry.welcome
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase="welcome",
        )

        scenario_context = get_scenario_context(state.scenario_id)
        parsed = await agent.generate_response_streaming(
            round=welcome_round,
            history=history,
            last_speaker=last_speaker,
            participants=participants,
            knowledge_base_context=kb_context,
            upcoming_scenario=scenario_context,
            final=final,
            on_response_chunk=stream_session.write,
        )

        response = (parsed.get("response") or "").strip()
        if not response:
            response = stream_session.full_text.strip()

        target = parsed.get("target")
        if not isinstance(target, str) or target not in state.roster:
            target = None

        memory_update = parsed.get("memory_update")
        relations = parsed.get("relations")

        return response, target, stream_session, memory_update, relations

    def _record_tr_response(
        self, state: DebateState, response: str, target: str | None
    ) -> None:
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=response,
                audience="all",
                phase="welcome",
                timestamp=datetime.now(),
                target=target,
            )
        )

    def _apply_memory_updates(
        self, state: DebateState, memory_update: dict | None
    ) -> None:
        if not isinstance(memory_update, dict):
            return

        for name, fields in memory_update.items():
            if name not in state.roster or not isinstance(fields, dict):
                continue
            participant = state.roster[name]
            if "from" in fields and fields["from"] is not None:
                participant.from_location = fields["from"]
            if "Occupation" in fields and fields["Occupation"] is not None:
                participant.occupation = fields["Occupation"]
            if "other_info" in fields and fields["other_info"] is not None:
                participant.other_info = fields["other_info"]

    def _record_relations(self, state: DebateState, relations: list | None) -> None:
        if not isinstance(relations, list):
            return
        if not state.welcome_state:
            return
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            a = rel.get("person_a")
            b = rel.get("person_b")
            reason = rel.get("reason")
            if a and b and reason:
                state.welcome_state.relations.append(
                    {
                        "person_a": a,
                        "person_b": b,
                        "reason": reason,
                    }
                )

    def _build_welcome_history(self, state: DebateState) -> list[dict]:
        history = []
        for entry in state.history:
            if entry.phase == "welcome":
                history.append(
                    {
                        "speaker": entry.speaker,
                        "text": entry.text,
                        "target": entry.target,
                    }
                )
        return history

    def _build_participants(self, state: DebateState) -> dict[str, dict]:
        people = {}
        for name, participant in state.roster.items():
            people[name] = {
                "name": participant.name,
                "from": participant.from_location or "",
                "Occupation": participant.occupation or "Unknown",
                "other_info": participant.other_info or "",
                "conversation_summary": participant.conversation_history or "",
                "can_speak": participant.can_speak,
            }
        return people

    def _should_query_kb(self, message: str) -> bool:
        """
        Determine if KB search is needed.
        Query KB when user is asking a question or making a request.
        """
        if not message:
            return False
        
        # Direct question
        if "?" in message:
            logger.info("[KB Query] Triggered - user asked a question")
            return True
        
        # Polite requests (often questions without ?)
        message_lower = message.lower()
        request_patterns = [
            "could you", "would you", "can you", "will you",
            "tell me", "tell us", "show me", "show us",
            "please tell", "please share", "please explain",
        ]
        for pattern in request_patterns:
            if pattern in message_lower:
                logger.info(f"[KB Query] Triggered - request pattern: '{pattern}'")
                return True
        
        logger.debug("[KB Query] Skipped - not a question or request")
        return False

    async def _schedule_kb_search_if_needed(
        self, state: DebateState, participant_text: str
    ) -> None:
        """
        Schedule a background KB search for the participant's message.
        Only used when kb_async_search_enabled is True.
        """
        if not config.kb_async_search_enabled:
            return
        
        should_query = self._should_query_kb(participant_text)
        if not should_query:
            logger.debug("[KB Async] Skipping - message is not a question/request")
            return

        session_id = self._get_session_id(state)
        kb_manager = await get_kb_search_manager()
        await kb_manager.schedule_search(
            session_id=session_id,
            query=participant_text,
            should_query=True,
        )

    async def _get_kb_context(self, state: DebateState) -> str:
        """
        Get KB context for LLM.
        
        When kb_async_search_enabled=True:
            - Returns cached result from previous round's background search
            - Non-blocking, uses pre-fetched data
            
        When kb_async_search_enabled=False:
            - Falls back to synchronous KB fetch (original behavior)
        """
        if config.kb_async_search_enabled:
            return await self._get_cached_kb_context(state)
        else:
            return await self._fetch_kb_context_sync(state)

    async def _get_cached_kb_context(self, state: DebateState) -> str:
        """
        Get cached KB result from background search (async mode).
        Non-blocking - returns whatever is available from previous round.
        """
        session_id = self._get_session_id(state)
        kb_manager = await get_kb_search_manager()
        
        kb_context, query = kb_manager.get_cached_result(session_id)
        
        if kb_context:
            logger.info(
                f"[KB Async] Using cached context for session {session_id} "
                f"(from query: '{query[:30] if query else ''}...')"
            )
            # Store in state for debugging
            if state.welcome_state:
                state.welcome_state.cached_kb_context = kb_context
                state.welcome_state.cached_kb_query = query
            # Clear cache after use (will be refreshed by next participant input)
            kb_manager.clear_cache(session_id)
            
            # Add context header to help LLM understand this is from previous round
            kb_context = (
                f"[Note: This context is from the previous question: \"{query[:80]}...\"]\n"
                f"[Use only if still relevant to current conversation]\n\n"
                f"{kb_context}"
            )
        else:
            logger.debug(f"[KB Async] No cached context available for session {session_id}")
        
        return kb_context

    async def _fetch_kb_context_sync(self, state: DebateState) -> str:
        """
        Synchronous KB fetch (original behavior when async is disabled).
        Blocks until KB search completes.
        """
        import time
        
        last_participant_message = self._get_last_participant_message(state)
        if not last_participant_message:
            logger.info("[KB Sync] Skipped - no message")
            return ""

        if not self._should_query_kb(last_participant_message):
            logger.info("[KB Sync] Skipped - not a question or request")
            return ""

        start_time = time.perf_counter()
        try:
            kb_service = await get_knowledge_base_service()
            service_init_time = time.perf_counter()
            logger.info(
                "[KB Sync] Service init: %.3fs",
                service_init_time - start_time
            )
            
            kb_results = await kb_service.search(
                query=last_participant_message,
                top_k=6,
                search_letters=True,
                search_books=True,
            )
            search_time = time.perf_counter()
            logger.info(
                "[KB Sync] Search completed: %.3fs (total: %.3fs)",
                search_time - service_init_time,
                search_time - start_time
            )
            
            if kb_results:
                logger.info(
                    "[KB Sync] Found %s results for query: '%s...'",
                    len(kb_results),
                    last_participant_message[:50]
                )
                result = kb_service.format_results_for_llm(
                    kb_results,
                    max_results=6,
                )
                end_time = time.perf_counter()
                logger.info(
                    "[KB Sync] Total KB fetch time: %.3fs",
                    end_time - start_time
                )
                return result
            else:
                logger.info("[KB Sync] No results found")
        except Exception as e:
            end_time = time.perf_counter()
            logger.warning(
                "[KB Sync] Error after %.3fs: %s",
                end_time - start_time,
                e
            )

        return ""

    def _get_session_id(self, state: DebateState) -> str:
        """Get a unique session identifier for KB caching."""
        # Use the id of the state object as session identifier
        # In production, you might want to use a proper session ID
        return str(id(state))

    def _get_last_participant_message(self, state: DebateState) -> str | None:
        for entry in reversed(state.history):
            if entry.phase == "welcome" and entry.speaker in state.roster:
                return entry.text
        return None

    def _build_eligible_speakers(
        self, participants: list[str]
    ) -> list[HandRaisePrompt]:
        return [HandRaisePrompt(participant_id=name) for name in participants]

    def _build_speaker_prompt(
        self, target: str | None, hint: str | None = None
    ) -> dict[str, str] | None:
        if not target:
            return None
        return {"participant_id": target, "text": hint or ""}

    def _count_welcome_rounds(self, state: DebateState) -> int:
        return sum(
            1
            for entry in state.history
            if entry.phase == "welcome" and entry.speaker == TR_SPEAKER
        )
