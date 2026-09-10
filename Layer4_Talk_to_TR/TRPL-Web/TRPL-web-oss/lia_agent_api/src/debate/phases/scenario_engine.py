# -*- coding: utf-8 -*-
from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.engagement.timeout_nudge_node import TimeoutNudgeNode
from debate.nodes.scenario_node import ScenarioNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService
from debate.utils import format_usage


class ScenarioEngine(BasePhaseEngine):
    """Orchestrates scenario graph execution with shared phase scaffolding."""

    @property
    def phase_label(self) -> str:
        return "scenario"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        return (
            MiniGraph()
            .add_node(
                ScenarioNode(
                    io,
                    self.agent_registry,
                    phase="scenario",
                    agent_attr="scenario",
                )
            )
            .add_node(
                TimeoutNudgeNode(
                    io,
                    self.agent_registry,
                    phase="scenario",
                    return_node="ScenarioNode",
                )
            )
            .set_entry(ScenarioNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )

    async def after_run(self, io: IOService, state: DebateState) -> None:
        summary_text = format_usage(self.agent_registry.all, include_per_agent=True)
        await io.output.send_debug(
            agent="System",
            content=summary_text,
            phase="scenario",
        )
        self.logger.info("LLM Usage Summary:\n%s", summary_text)
