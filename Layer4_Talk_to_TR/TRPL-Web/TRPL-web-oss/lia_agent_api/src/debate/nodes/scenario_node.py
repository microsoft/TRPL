# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from datetime import datetime

from api.config import config
from debate.graph import MiniGraph
from debate.models.constants import TR_SPEAKER
from debate.models.inputs import HandRaiseInput, SpokenTextInput
from debate.models.sockets import HandRaisePrompt
from debate.models.state import DebateState, HistoryEntry, BrainstormIdea
from debate.nodes.base import BaseNode
from debate.services.io import OutputStreamSession, TimeoutInput

logger = logging.getLogger(f"lia.{__name__}")


class ScenarioNode(BaseNode):
    """Generic scenario node that uses (response, done, memory) agent protocol."""

    def __init__(
        self,
        io,
        agent_registry,
        *,
        phase: str,
        agent_attr: str = "scenario",
        max_rounds: int | None = None,
    ):
        super().__init__(io, agent_registry)
        self.phase = phase
        self.agent_attr = agent_attr
        self.max_rounds = max_rounds

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        round_num = self._count_rounds(state) + 1

        next_node = await self._maybe_prompt_initial_hand_raise(state)
        if next_node:
            return next_node

        next_node = await self._maybe_handle_raised_hand(state)
        if next_node:
            return next_node

        (
            response,
            done,
            memory,
            target,
            participant_hint,
            question_summary,
            tracked_idea,
            stream_session,
        ) = await self._generate_and_stream_prompt(
            state,
            round_num=round_num,
        )
        if not response:
            logger.warning("No response generated, ending scenario phase")
            stream_session.cancel()
            return MiniGraph.END

        self._record_tr_response(state, response, target)
        self._store_memory(state, memory)
        self._store_tracked_idea(state, tracked_idea)

        if done:
            logger.info("Scenario agent marked done=true, ending phase")
            await stream_session.finish(
                text=response,
                waiting_for_input=False,
            )
            return MiniGraph.END

        participants = list(state.roster.keys())
        speaker_prompt = self._build_speaker_prompt(
            target, participant_hint, question_summary
        )
        self._store_pending_hand_raise_context(
            state=state,
            target=target,
            participant_hint=participant_hint,
            question_summary=question_summary,
        )
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
            tr_question_summary=question_summary,
            eligible_speakers=eligible_speakers,
        )

        if eligible_speakers is not None:
            event = await self._wait_after_hand_raise_prompt(
                state=state,
                phase=self.phase,
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
            logger.info("Hand raise received during scenario phase, continuing")
            return self.name
        else:  # TimeoutInput
            logger.info("Empty or no input received, continuing scenario phase")
        return "TimeoutNudgeNode"

    async def _maybe_prompt_initial_hand_raise(self, state: DebateState) -> str | None:
        if not config.hand_raise_enabled:
            return None

        if self._has_phase_history(state):
            return None
        if self.phase in state.initial_hand_raise_prompted:
            return None

        participants = list(state.roster.keys())
        if not participants:
            return None

        state.initial_hand_raise_prompted.add(self.phase)
        eligible_speakers = self._build_eligible_speakers(participants)
        await self.io.output.send_output(
            text=None,
            waiting_for_input=True,
            eligible_speakers=eligible_speakers,
            phase=self.phase,
        )

        event = await self._wait_after_hand_raise_prompt(
            state=state,
            phase=self.phase,
            eligible_speakers=eligible_speakers,
            participants=participants,
        )
        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)
        if isinstance(event, HandRaiseInput):
            logger.info("Hand raise received during initial scenario prompt")
            return self.name
        return None

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
                phase=self.phase,
                timestamp=datetime.now(),
                target=None,
            )
        )

        logger.info(f"Received from {participant_name}: {participant_text[:50]}...")
        await self._update_notes_after_input(state)

        if self._should_short_circuit(participant_text):
            logger.info("Short-circuit requested during scenario phase")
            await self.io.output.send_output(
                text="All right. Let's move on.",
                waiting_for_input=False,
                phase=self.phase,
            )
            return MiniGraph.END

        return self.name

    async def _update_notes_after_input(self, state: DebateState) -> None:
        if self.io.notes_updater:
            self.io.notes_updater.enqueue_update(phase=self.phase)

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
        question_summary = self._pop_pending_tr_question_summary(state)
        hint_text = question_summary or self._pop_pending_hand_raise_hint(state)
        if not hint_text:
            hint_text = "Try asking a question"

        if raised_hand in state.inputs.raised_hands:
            state.inputs.raised_hands.remove(raised_hand)

        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=ack_text,
                audience="all",
                phase=self.phase,
                timestamp=datetime.now(),
                target=raised_hand,
            )
        )

        await self.io.output.send_output(
            text=ack_text,
            speaker_prompt={"participant_id": raised_hand, "text": hint_text},
            tr_question_summary=question_summary,
            waiting_for_input=True,
            phase=self.phase,
        )

        event = await self.io.input.wait_for_input(SpokenTextInput, TimeoutInput)
        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)
        else:  # TimeoutInput
            logger.info(f"No input received from raised hand participant {raised_hand}")
            return "TimeoutNudgeNode"

    async def _generate_and_stream_prompt(
        self,
        state: DebateState,
        *,
        round_num: int,
    ) -> tuple[
        str,
        bool,
        dict,
        str | None,
        str | None,
        str | None,
        str | None,
        OutputStreamSession,
    ]:
        history = self._build_history(state)
        memory = self._get_memory(state)
        participants = list(
            {
                *state.speaking_participants(),
                *state.inputs.raised_hands,
            }
        )
        last_speaker = state.last_speaker

        agent = getattr(self.agent_registry, self.agent_attr)
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase=self.phase,
        )

        max_rounds = self.max_rounds or state.max_rounds

        parsed = await agent.generate_response_streaming(
            round=round_num,
            max_rounds=max_rounds,
            final=False,
            history=history,
            participants=participants,
            last_speaker=last_speaker,
            memory=memory,
            on_response_chunk=stream_session.write,
        )

        response = (parsed.get("response") or "").strip()
        if not response:
            response = stream_session.full_text.strip()

        target = parsed.get("target")
        if not isinstance(target, str) or target not in state.roster:
            target = None

        done = bool(parsed.get("done", False))
        updated_memory = parsed.get("memory", memory)

        participant_hint = parsed.get("participant_hint")
        if not isinstance(participant_hint, str) or not participant_hint.strip():
            participant_hint = None

        question_summary = parsed.get("question_summary")
        if not isinstance(question_summary, str) or not question_summary.strip():
            question_summary = None
        else:
            question_summary = question_summary.strip()

        tracked_idea = parsed.get("tracked_idea")
        if not isinstance(tracked_idea, str) or not tracked_idea.strip():
            tracked_idea = None

        return (
            response,
            done,
            updated_memory,
            target,
            participant_hint,
            question_summary,
            tracked_idea,
            stream_session,
        )

    def _record_tr_response(
        self, state: DebateState, response: str, target: str | None
    ) -> None:
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=response,
                audience="all",
                phase=self.phase,
                timestamp=datetime.now(),
                target=target,
            )
        )

    def _build_eligible_speakers(
        self, participants: list[str]
    ) -> list[HandRaisePrompt]:
        return [HandRaisePrompt(participant_id=name) for name in participants]

    def _store_pending_hand_raise_context(
        self,
        *,
        state: DebateState,
        target: str | None,
        participant_hint: str | None,
        question_summary: str | None,
    ) -> None:
        if target:
            state.pending_hand_raise_hint = None
            state.pending_tr_question_summary = None
            return

        state.pending_hand_raise_hint = (
            participant_hint.strip() if participant_hint else None
        )
        state.pending_tr_question_summary = (
            question_summary.strip() if question_summary else None
        )

    def _pop_pending_hand_raise_hint(self, state: DebateState) -> str | None:
        hint = state.pending_hand_raise_hint
        state.pending_hand_raise_hint = None
        return hint.strip() if hint else None

    def _pop_pending_tr_question_summary(self, state: DebateState) -> str | None:
        summary = state.pending_tr_question_summary
        state.pending_tr_question_summary = None
        return summary.strip() if summary else None

    def _build_speaker_prompt(
        self,
        target: str | None,
        participant_hint: str | None,
        question_summary: str | None,
    ) -> dict[str, str] | None:
        if not target:
            return None
        prompt_text = question_summary or participant_hint
        if not prompt_text:
            return None
        return {"participant_id": target, "text": prompt_text}

    def _count_rounds(self, state: DebateState) -> int:
        return sum(
            1
            for entry in state.history
            if entry.phase == self.phase and entry.speaker == TR_SPEAKER
        )

    def _build_history(self, state: DebateState) -> list[dict]:
        history = []
        for entry in state.history:
            if entry.phase == self.phase:
                history.append(
                    {
                        "speaker": entry.speaker,
                        "text": entry.text,
                        "target": entry.target,
                    }
                )
        return history

    def _has_phase_history(self, state: DebateState) -> bool:
        return any(entry.phase == self.phase for entry in state.history)

    def _get_memory(self, state: DebateState) -> dict:
        if not hasattr(state, "phase_memory") or state.phase_memory is None:
            return {}
        return state.phase_memory.get(self.phase, {})

    def _store_memory(self, state: DebateState, memory: dict | None) -> None:
        if not isinstance(memory, dict):
            logger.warning("Scenario memory was not a JSON object; keeping prior")
            return
        if not hasattr(state, "phase_memory") or state.phase_memory is None:
            state.phase_memory = {}
        state.phase_memory[self.phase] = memory

    def _store_tracked_idea(self, state: DebateState, idea: str | None) -> None:
        if not idea:
            return

        normalized = " ".join(idea.lower().split())
        if not normalized:
            return

        existing = {
            " ".join(item.text.lower().split()) for item in state.brainstorm_ideas
        }
        if normalized in existing:
            return

        if len(state.brainstorm_ideas) >= 6:
            return

        idea_id = f"idea_{len(state.brainstorm_ideas) + 1}"
        state.brainstorm_ideas.append(
            BrainstormIdea(
                idea_id=idea_id,
                text=idea.strip(),
                source_participant=state.last_speaker,
            )
        )

    def _should_short_circuit(self, text: str) -> bool:
        if not text:
            return False
        normalized = " ".join(text.lower().split())
        return "end phase" in normalized
