# -*- coding: utf-8 -*-
"""
CameraEngine — phase engine for camera-driven interaction.

This phase runs indefinitely, processing camera platform events
(BATCH_INVITE, MIC_ZONE_ENGAGED, HAND_RAISE_RESPONSE, etc.)
and generating LLM responses for each event.

The phase does not auto-transition; it must be manually advanced
or cancelled to move to the next phase.
"""
from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.camera_node import CameraNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService


class CameraEngine(BasePhaseEngine):
    """Orchestrates camera event processing graph."""

    @property
    def phase_label(self) -> str:
        return "camera"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        return (
            MiniGraph()
            .add_node(CameraNode(io, self.agent_registry))
            .set_entry(CameraNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )
