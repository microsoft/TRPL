# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.outro_node import OutroNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService


class OutroEngine(BasePhaseEngine):
    """Orchestrates outro graph execution with shared phase scaffolding."""

    @property
    def phase_label(self) -> str:
        return "outro"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        return (
            MiniGraph()
            .add_node(OutroNode(io, self.agent_registry))
            .set_entry(OutroNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )
