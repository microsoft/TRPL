# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import logging
from abc import ABC, abstractmethod

from api.logs import AddContextFilter, QueueHandler
from debate.graph import MiniGraph
from debate.services.io import IOService
from debate.services.session_store import DebateSession
from debate.services.agent_registry import AgentRegistry
from debate.models.state import DebateState


class BasePhaseEngine(ABC):
    """Shared scaffolding for phase engines."""

    def __init__(self, session: DebateSession, agent_registry: AgentRegistry):
        self.session = session
        self.agent_registry = agent_registry
        self.logger = logging.getLogger(f"lia.{__name__}.{self.phase_label}")

    @property
    @abstractmethod
    def phase_label(self) -> str:
        """Human-readable phase label for logs."""

    @abstractmethod
    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        """Build and return the phase graph."""

    async def after_run(self, io: IOService, state: DebateState) -> None:
        """Optional hook executed after graph completion."""
        return

    async def _wait_for_connection(self) -> bool:
        await self.session.connection_event.wait()
        return True

    @contextmanager
    def log_to_socket(self):
        handler = QueueHandler(self.session.output_queue)
        handler.addFilter(AddContextFilter())
        handler.addFilter(
            lambda r: getattr(r, "session_id", None) == self.session.session_id
        )
        logging.getLogger("lia.debate").addHandler(handler)
        logging.getLogger("lia.services").addHandler(handler)
        try:
            yield
        finally:
            logging.getLogger("lia.debate").removeHandler(handler)
            logging.getLogger("lia.services").removeHandler(handler)

    async def run(self) -> None:
        with self.log_to_socket():
            try:
                state = self.session.state
                io = self.session.io_service
                if io is None:
                    raise RuntimeError("Session IO service is not initialized")

                graph = self.build_graph(io, state)

                self.logger.info("Starting %s graph", self.phase_label)
                await graph.run(state)
                await self.after_run(io, state)
                self.logger.info("%s phase completed", self.phase_label)
            except Exception as e:
                self.logger.error(
                    "Error running %s: %s",
                    self.phase_label,
                    e,
                    exc_info=True,
                )
                await self.session.output_queue.put(
                    {
                        "type": "debug_message",
                        "timestamp": datetime.now().isoformat(),
                        "debug": True,
                        "agent": "System",
                        "content": f"Execution error: {str(e)}",
                        "phase": "error",
                    }
                )
