# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.intro_node import IntroNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService


class IntroEngine(BasePhaseEngine):
    """Orchestrates intro graph execution with shared phase scaffolding."""

    @property
    def phase_label(self) -> str:
        return "intro"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        return (
            MiniGraph()
            .add_node(IntroNode(io, self.agent_registry))
            .set_entry(IntroNode.__name__)
            .set_pre_node_hook(self._wait_for_connection)
        )
