# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging

from debate.graph import MiniGraph
from debate.models.state import DebateState
from debate.nodes.engagement.timeout_nudge_node import TimeoutNudgeNode
from debate.nodes.storys import StorysNode
from debate.phases.base_engine import BasePhaseEngine
from debate.services.io import IOService
from debate.utils import format_usage

logger = logging.getLogger(f"lia.{__name__}")


class StorysEngine(BasePhaseEngine):
    """Orchestrates storytelling with RAG + visitor history services."""

    # The conversation node class this engine builds. VipEngine swaps this
    # for VipNode; everything else (RAG, history, cleanup) is identical.
    _node_class = StorysNode

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rag_service = None
        self._history_service = None

    @property
    def phase_label(self) -> str:
        return "storys"

    def build_graph(self, io: IOService, state: DebateState) -> MiniGraph:
        node_name = self._node_class.__name__
        storys_node = self._node_class(io, self.agent_registry)
        self._storys_node = storys_node  # for after_run person_memory save

        # Attach RAG service (story picker + KB search)
        if self.agent_registry.story_picker is not None:
            from debate.scenarios.story_rag import get_index
            from debate.services.storys_rag_service import StorysRAGService

            rag_svc = StorysRAGService(
                picker=self.agent_registry.story_picker,
                state=state,
                index=get_index(),
                history_watermark_fn=lambda: storys_node._history_watermark,
            )
            rag_svc.start()
            storys_node._rag_service = rag_svc
            self._rag_service = rag_svc

        # Attach visitor history service
        if self.agent_registry.history_summary is not None:
            from debate.services.visitor_history_service import VisitorHistoryService

            history_svc = VisitorHistoryService(
                agent=self.agent_registry.history_summary,
                state=state,
            )
            storys_node._history_service = history_svc
            self._history_service = history_svc

        return (
            MiniGraph()
            .add_node(storys_node)
            .add_node(
                TimeoutNudgeNode(
                    io,
                    self.agent_registry,
                    phase="storys",
                    return_node=node_name,
                )
            )
            .set_entry(node_name)
            .set_pre_node_hook(self._wait_for_connection)
        )

    async def after_run(self, io: IOService, state: DebateState) -> None:
        # Save person_memory for the departing visitor
        if hasattr(self, '_storys_node') and self._storys_node:
            self._storys_node._save_person_memory(state)

        # Summarize visitor conversation before leaving
        if self._history_service:
            try:
                await self._history_service.on_visitor_leave()
            except Exception as e:
                logger.warning("Failed to summarize visitor on phase end: %s", e)

        summary_text = format_usage(self.agent_registry.all, include_per_agent=True)
        await io.output.send_debug(
            agent="System",
            content=summary_text,
            phase="storys",
        )
        self.logger.info("LLM Usage Summary:\n%s", summary_text)

    async def _cleanup_services(self) -> None:
        """Stop background services. Called on both normal and cancel paths."""
        if self._rag_service:
            try:
                await self._rag_service.stop()
            except Exception as e:
                logger.warning("Failed to stop RAG service: %s", e)
            self._rag_service = None

    async def run(self) -> None:
        """Override to ensure cleanup on cancellation."""
        try:
            await super().run()
        finally:
            await self._cleanup_services()
