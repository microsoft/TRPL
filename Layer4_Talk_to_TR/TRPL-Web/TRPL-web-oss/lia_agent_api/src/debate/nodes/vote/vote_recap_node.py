# -*- coding: utf-8 -*-
import logging
from datetime import datetime
from debate.models.state import DebateState, HistoryEntry, SupplementalNotes
from debate.nodes.base import BaseNode
from debate.models.constants import TR_SPEAKER, Phase
from debate.services.agent_registry import AgentRegistry
from debate.services.io import IOService
from debate.scenarios.registry import get_scenario

logger = logging.getLogger(f"lia.{__name__}")


class VoteRecapNode(BaseNode):
    """Generic vote recap node - summarizes vote results"""

    def __init__(
        self,
        io: IOService,
        agent_registry: AgentRegistry,
        vote_type: str,
        camp_definitions: dict | None = None,
    ):
        super().__init__(io, agent_registry)
        self.vote_type = vote_type  # "camp", "brainstorm", or "scenario"
        self.camp_definitions = camp_definitions

    async def run(self, state: DebateState) -> str:
        """Summarize vote results"""
        self.log_start(state)

        if self.vote_type == "camp":

            summary_text = "Alright, we have voted. "

            # determine majority vote
            pressure_owners_votes = []
            pressure_miners_votes = []
            for participant in state.roster.values():
                if participant.camp == "pressure_owners":
                    pressure_owners_votes.append(participant.name)
                elif participant.camp == "pressure_miners":
                    pressure_miners_votes.append(participant.name)

            if len(pressure_owners_votes) > len(pressure_miners_votes):
                summary_text += "The majority have voted to pressure the operators. My heart tells me to do the same, but I fear we must be more strategic to bend these men to our will."
            elif len(pressure_miners_votes) > len(pressure_owners_votes):
                summary_text += "The majority have voted to pressure the miners. I appreciate the pragmatism of this group, but I ran as a champion of the common man and must not be seen as going against the miners."
            else:  # tied
                summary_text += "The room is split! I can see why. This is indeed a dilemma."

        elif self.vote_type == "brainstorm":
            if state.brainstorm_ideas:
                vote_counts = {idea.idea_id: 0 for idea in state.brainstorm_ideas}
                for _participant_id, vote in state.brainstorm_votes.items():
                    if vote in vote_counts:
                        vote_counts[vote] += 1

                if vote_counts:
                    top_count = max(vote_counts.values())
                    top_ideas = [
                        idea
                        for idea in state.brainstorm_ideas
                        if vote_counts.get(idea.idea_id, 0) == top_count
                    ]
                else:
                    top_ideas = []

                if len(top_ideas) == 1:
                    summary_text = (
                        f"The room favors this course: {top_ideas[0].text}"
                    )
                elif len(top_ideas) > 1:
                    top_list = "; ".join(idea.text for idea in top_ideas)
                    summary_text = (
                        "The room is split between these leading ideas: "
                        f"{top_list}"
                    )
                else:
                    summary_text = "We have no clear favorite yet."
            else:
                # Count Yes and No votes
                yes_votes = []
                no_votes = []

                for participant_id, vote in state.brainstorm_votes.items():
                    if vote.lower() in ["yes", "y"]:
                        yes_votes.append(participant_id)
                    else:
                        no_votes.append(participant_id)

                # Determine majority (Yes wins on tie)
                yes_count = len(yes_votes)
                no_count = len(no_votes)

                if yes_count >= no_count:
                    # Majority is Yes (or tied) - Yes wins on tie
                    summary_text = "Bully! Most are in favor of this plan! Look at what we have accomplished today!"
                else:
                    # Majority is No
                    summary_text = "The majority voted no. Respectfully, I disagree."

        elif self.vote_type == "scenario":
            scenario = get_scenario(state.scenario_id)
            if not scenario:
                raise ValueError(f"Unknown scenario_id: {state.scenario_id}")

            recap_config = scenario.scenario_vote_recap
            for_values = set(recap_config.for_values)
            against_values = set(recap_config.against_values)
            for_votes = []
            against_votes = []

            for participant_id, vote in state.scenario_votes.items():
                if vote in for_values:
                    for_votes.append(participant_id)
                elif vote in against_values:
                    against_votes.append(participant_id)

            for_count = len(for_votes)
            against_count = len(against_votes)
            summary_text = await self._generate_scenario_recap_with_agent(
                state=state,
                scenario=scenario,
                recap_config=recap_config,
                for_count=for_count,
                against_count=against_count,
            )

            if not summary_text:
                recap_prefix = (
                    self._build_dynamic_recap_prefix(state, recap_config)
                    if recap_config.dynamic_recap
                    else (recap_config.recap_prefix or "")
                )

                if for_count > against_count:
                    summary_text = recap_prefix + recap_config.outcome_for
                elif against_count > for_count:
                    summary_text = recap_prefix + recap_config.outcome_against
                else:
                    summary_text = recap_prefix + recap_config.outcome_tie

                if recap_config.append_outro and scenario.outro_text:
                    summary_text = summary_text.rstrip() + " " + scenario.outro_text

        else:
            summary_text = "Vote completed."

        # Add to history
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=summary_text,
                audience="all",
                phase=state.phase,
                timestamp=datetime.now(),
            )
        )

        # Output summary
        await self.io.output.send_output(
            text=summary_text,
            waiting_for_input=False,
        )

        if self.io.notes_updater:
            self.io.notes_updater.enqueue_update(phase=state.phase)

        logger.info("Vote recap completed")
        return None  # Signal to engine to transition to next phase

    async def _generate_scenario_recap_with_agent(
        self,
        *,
        state: DebateState,
        scenario,
        recap_config,
        for_count: int,
        against_count: int,
    ) -> str:
        if not hasattr(self.agent_registry, "vote_recap"):
            return ""

        history = [
            {
                "speaker": entry.speaker,
                "text": entry.text,
                "phase": entry.phase,
            }
            for entry in state.history
            if entry.audience == "all" and entry.phase == Phase.scenario
        ]

        vote_counts: dict[str, int] = {}
        for vote in state.scenario_votes.values():
            vote_counts[vote] = vote_counts.get(vote, 0) + 1

        scenario_context = (scenario.intro_text or "").strip()

        try:
            return await self.agent_registry.vote_recap.generate_recap(
                scenario_context=scenario_context,
                history=history,
                vote_question=scenario.scenario_vote_prompt.question,
                vote_options=scenario.scenario_vote_prompt.options,
                vote_counts=vote_counts,
                outro_text=scenario.outro_text,
                append_outro=recap_config.append_outro,
            )
        except Exception:
            logger.exception("Vote recap agent failed; falling back to static recap")
            return ""

    def _build_dynamic_recap_prefix(self, state: DebateState, recap_config) -> str:
        notes = state.meeting_notes
        if not notes:
            return recap_config.recap_prefix or ""

        act_points, wait_points = self._extract_act_wait_points(notes)
        if not act_points and not wait_points:
            return recap_config.recap_prefix or ""

        act_phrase = self._join_phrases(act_points, max_items=2)
        wait_phrase = self._join_phrases(wait_points, max_items=2)

        if act_phrase and wait_phrase:
            return f"We have weighed {act_phrase} against {wait_phrase}. "
        if act_phrase:
            return f"We have weighed {act_phrase}. "
        if wait_phrase:
            return f"We have weighed the case for restraint: {wait_phrase}. "
        return recap_config.recap_prefix or ""

    @staticmethod
    def _extract_act_wait_points(notes: SupplementalNotes) -> tuple[list[str], list[str]]:
        act_points: list[str] = []
        wait_points: list[str] = []

        for category in notes.categories:
            header = category.header.strip().lower()
            items = [item.strip() for item in category.items if item.strip()]
            if "act now" in header:
                act_points.extend(items)
            elif "wait to act" in header or header == "wait":
                wait_points.extend(items)

        return act_points, wait_points

    @staticmethod
    def _join_phrases(phrases: list[str], max_items: int = 2) -> str:
        cleaned = [p.strip().rstrip(".") for p in phrases if p.strip()]
        if not cleaned:
            return ""
        items = cleaned[:max_items]
        if len(items) == 1:
            return items[0]
        return f"{items[0]} and {items[1]}"
