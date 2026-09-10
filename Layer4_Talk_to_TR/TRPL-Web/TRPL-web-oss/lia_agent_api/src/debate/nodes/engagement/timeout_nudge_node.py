# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from api.config import config
from debate.models.inputs import HandRaiseInput, SpokenTextInput, TimeoutInput
from debate.models.sockets import HandRaisePrompt
from debate.models.state import DebateState, HistoryEntry
from debate.nodes.base import BaseNode
from debate.nodes.engagement.timeout_nudge import send_timeout_nudge
from debate.models.constants import Phase

logger = logging.getLogger(f"lia.{__name__}")


class TimeoutNudgeNode(BaseNode):
    """Handles frontend timeout nudges and routes back to the active phase."""

    def __init__(self, io, agent_registry, phase: str, return_node: str):
        super().__init__(io, agent_registry)
        self._phase = phase
        self._return_node = return_node

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        speaking_participants = state.speaking_participants()
        participants = list(state.roster.keys())
        eligible_speakers = self._build_eligible_speakers(participants)
        participants_for_agent = list(
            {
                *speaking_participants,
                *state.inputs.raised_hands,
            }
        )
        last_prompt = self._get_last_prompt(state, self._phase)

        nudge_text = await send_timeout_nudge(
            io=self.io,
            agent_registry=self.agent_registry,
            state=state,
            phase=self._phase,
            last_prompt=last_prompt,
            participants=participants_for_agent,
            eligible_speakers=eligible_speakers if config.hand_raise_enabled else None,
        )
        if not nudge_text:
            return self._return_node

        if config.hand_raise_enabled:
            event = await self._wait_after_hand_raise_prompt(
                state=state,
                phase=self._phase,
                eligible_speakers=eligible_speakers,
                participants=participants,
            )
        else:
            event = await self.io.input.wait_for_input(
                SpokenTextInput, TimeoutInput
            )

        if isinstance(event, SpokenTextInput):
            return await self._handle_participant_input(state, event)
        elif isinstance(event, HandRaiseInput):
            logger.info(
                "Hand raise received after timeout nudge, returning to phase"
            )
            return self._return_node
        else:  # TimeoutInput
            if config.hand_raise_enabled and state.inputs.raised_hands:
                logger.info(
                    "No text input after timeout nudge but hand(s) raised, returning to phase"
                )
                return self._return_node
            logger.info("No input after timeout nudge, sending another nudge")
            return self.name

    async def _update_notes_after_input(self, state: DebateState) -> None:
        if self._phase not in {
            Phase.scenario.value,
            Phase.scenario_vote.value,
            Phase.brainstorm_vote.value,
        }:
            return

        if self.io.notes_updater:
            self.io.notes_updater.enqueue_update(phase=self._phase)

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
                phase=self._phase,
                timestamp=datetime.now(),
                target=None,
            )
        )

        logger.info(
            f"Received input from {participant_name}: {participant_text[:50]}..."
        )
        await self._update_notes_after_input(state)

        return self._return_node

    def _build_eligible_speakers(
        self, participants: list[str]
    ) -> list[HandRaisePrompt]:
        return [HandRaisePrompt(participant_id=name) for name in participants]
