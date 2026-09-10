# -*- coding: utf-8 -*-
from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.vote import VoteNode, VoteRecapNode
from debate.phases.base_engine import BasePhaseEngine
from debate.scenarios.registry import get_scenario
from debate.services.io import IOService


class VoteEngine(BasePhaseEngine):
    """Orchestrates vote graph execution with shared phase scaffolding."""

    def __init__(
        self,
        session,
        agent_registry,
        vote_type: str,
    ):
        self.vote_type = vote_type  # "camp" or "brainstorm" or "scenario"
        super().__init__(session, agent_registry)

    @property
    def phase_label(self) -> str:
        return f"{self.vote_type} vote"

    def _get_vote_config(self):
        """Get vote configuration based on vote type."""
        state = self.session.state

        if self.vote_type == "camp":
            options = []
            for camp_id, camp_def in state.camp_definitions.items():
                options.append({"value": camp_id, "label": camp_def.label})
            return {
                "question": "Should we pressure the operators to meet worker demands, or pressure the workers to end the strike?",
                "options": options,
            }

        if self.vote_type == "brainstorm":
            if state.brainstorm_ideas:
                options = [
                    {"value": idea.idea_id, "label": idea.text}
                    for idea in state.brainstorm_ideas
                ]
                return {
                    "question": "Which idea should we pursue?",
                    "options": options,
                }
            return {
                "question": "What say you all? Should we threaten to seize control of the mines?",
                "options": [
                    {"value": "yes", "label": "Yes"},
                    {"value": "no", "label": "No"},
                ],
            }

        if self.vote_type == "scenario":
            scenario = get_scenario(state.scenario_id)
            if not scenario:
                raise ValueError(f"Unknown scenario_id: {state.scenario_id}")
            return {
                "question": scenario.scenario_vote_prompt.question,
                "options": scenario.scenario_vote_prompt.options,
            }

        raise ValueError(f"Unknown vote type: {self.vote_type}")

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        vote_config = self._get_vote_config()
        vote_node = VoteNode(
            io,
            self.agent_registry,
            self.vote_type,
            vote_config["question"],
            vote_config["options"],
        )
        vote_recap_node = VoteRecapNode(
            io,
            self.agent_registry,
            self.vote_type,
            state.camp_definitions if self.vote_type == "camp" else None,
        )
        return (
            MiniGraph()
            .add_node(vote_node)
            .add_node(vote_recap_node)
            .set_entry(VoteNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )
