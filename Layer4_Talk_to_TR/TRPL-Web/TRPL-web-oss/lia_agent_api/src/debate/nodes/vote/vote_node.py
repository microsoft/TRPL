# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from datetime import datetime
from debate.models.inputs import TimeoutInput, TouchscreenVoteInput
from debate.models.state import DebateState, HistoryEntry
from debate.nodes.base import BaseNode
from debate.models.constants import TR_SPEAKER
from debate.services.agent_registry import AgentRegistry
from debate.services.io import IOService

logger = logging.getLogger(f"lia.{__name__}")

SCENARIO_TOUCHSCREEN_QUESTION = "What should Roosevelt do?"


class VoteNode(BaseNode):
    """Generic vote collection node - collects votes via touchscreen prompts"""

    def __init__(
        self,
        io: IOService,
        agent_registry: AgentRegistry,
        vote_type: str,
        question: str,
        options: list[dict],
    ):
        super().__init__(io, agent_registry)
        self.vote_type = vote_type  # "camp", "brainstorm", or "scenario"
        self.question = question
        self.options = options  # List of {"value": str, "label": str}

    async def run(self, state: DebateState) -> str:
        """Collect votes from all participants"""
        self.log_start(state)

        # Get all participant IDs
        participant_ids = list(state.roster.keys())

        if not participant_ids:
            logger.warning("No participants in roster, skipping vote")
            return "VoteRecapNode"

        # Create touchscreen prompts for each participant
        touchscreen_question = (
            SCENARIO_TOUCHSCREEN_QUESTION
            if self.vote_type == "scenario"
            else self.question
        )
        touchscreen_prompts = []
        for participant_id in participant_ids:
            prompt = {
                "participant_id": participant_id,
                "question": touchscreen_question,
                "options": [opt["label"] for opt in self.options],
            }
            touchscreen_prompts.append(prompt)

        # Send vote prompt
        prompt_text = f"{self.question} Please vote."
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=prompt_text,
                audience="all",
                phase=state.phase,
                timestamp=datetime.now(),
            )
        )

        await self.io.output.send_output(
            text=prompt_text,
            touchscreen_prompts=touchscreen_prompts,
            waiting_for_input=True,
        )

        # Wait for all participants to vote
        logger.info(f"Waiting for votes from {len(participant_ids)} participants")
        event = await self.io.input.wait_for_input(TouchscreenVoteInput, TimeoutInput)
        if isinstance(event, TimeoutInput):
            logger.info("Vote input timed out; processing any received responses")

        # Process votes from touchscreen responses
        responses = state.inputs.touchscreen_responses.copy()
        logger.info(f"Received {len(responses)} vote responses")

        # Map label responses to values
        label_to_value = {opt["label"]: opt["value"] for opt in self.options}

        # Store votes
        if self.vote_type == "camp":
            # Update participant camps
            for participant_id, response_label in responses.items():
                if participant_id in state.roster:
                    vote_value = label_to_value.get(response_label)
                    if vote_value:
                        state.roster[participant_id].camp = vote_value
                        logger.info(
                            f"Participant {participant_id} voted for camp: {vote_value}"
                        )
        elif self.vote_type == "brainstorm":
            # Store in brainstorm_votes
            for participant_id, response_label in responses.items():
                vote_value = label_to_value.get(response_label)
                if vote_value:
                    state.brainstorm_votes[participant_id] = vote_value
                    logger.info(
                        f"Participant {participant_id} brainstorm vote: {vote_value}"
                    )
        elif self.vote_type == "scenario":
            # Store in scenario_votes
            for participant_id, response_label in responses.items():
                vote_value = label_to_value.get(response_label)
                if vote_value:
                    state.scenario_votes[participant_id] = vote_value
                    logger.info(
                        f"Participant {participant_id} scenario vote: {vote_value}"
                    )

        # Clear touchscreen responses after processing
        state.inputs.touchscreen_responses.clear()

        return "VoteRecapNode"
