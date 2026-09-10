# -*- coding: utf-8 -*-
"""
WelcomeNewEngine — camera-aware welcome phase engine.

Same structure as WelcomeEngine but uses WelcomeNewNode.
"""
from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.engagement.timeout_nudge_node import TimeoutNudgeNode
from debate.nodes.welcome.welcome_new_node import WelcomeNewNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService
from debate.utils import format_usage


class WelcomeNewEngine(BasePhaseEngine):
    """Orchestrates camera-aware welcome graph execution."""

    @property
    def phase_label(self) -> str:
        return "welcome"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        return (
            MiniGraph()
            .add_node(WelcomeNewNode(io, self.agent_registry))
            .add_node(
                TimeoutNudgeNode(
                    io,
                    self.agent_registry,
                    phase="welcome",
                    return_node="WelcomeNewNode",
                )
            )
            .set_entry(WelcomeNewNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )

    async def after_run(self, io: IOService, state: DebateState) -> None:
        summary_text = format_usage(self.agent_registry.all, include_per_agent=True)
        await io.output.send_debug(
            agent="System",
            content=summary_text,
            phase="welcome",
        )
        self.logger.info("LLM Usage Summary:\n%s", summary_text)
