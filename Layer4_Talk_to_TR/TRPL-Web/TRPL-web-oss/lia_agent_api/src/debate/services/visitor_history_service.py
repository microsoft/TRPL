# -*- coding: utf-8 -*-
"""
Visitor History Service — maintains a stack of recent visitor summaries.

Called on each visitor hand-off (when conversation ends).
Stores summaries in state so TR can reference previous visitors:
  "Earlier today, a girl asked me the same thing!"
"""
import logging
from datetime import datetime

from debate.agents.storys.history_agent import HistorySummaryAgent
from debate.models.state import DebateState

logger = logging.getLogger(f"lia.{__name__}")

MAX_STACK_SIZE = 8


class VisitorHistoryService:
    """Summarizes conversations and maintains visitor stack."""

    def __init__(self, *, agent: HistorySummaryAgent, state: DebateState):
        self._agent = agent
        self._state = state

    def get_stack(self) -> list[dict]:
        """Get the current visitor stack from state."""
        if not self._state.phase_memory:
            return []
        return self._state.phase_memory.get("visitor_stack", [])

    async def on_visitor_leave(self) -> None:
        """Called when a visitor leaves. Summarizes and pushes to stack."""
        state = self._state
        history = [
            {"speaker": e.speaker, "text": e.text}
            for e in state.history
            if e.phase == "storys"
        ]

        if len(history) < 2:
            logger.info("VisitorHistory: too short to summarize (%d entries)", len(history))
            return

        memory = state.phase_memory.get("storys", {}) if state.phase_memory else {}

        summary = await self._agent.summarize(
            history=history,
            memory=memory,
        )

        if not summary:
            logger.warning("VisitorHistory: agent returned empty summary")
            return

        # Add timestamp
        summary["time"] = datetime.now().strftime("%H:%M")

        # Push to stack
        if state.phase_memory is None:
            state.phase_memory = {}
        stack = state.phase_memory.get("visitor_stack", [])
        stack.append(summary)

        # Keep only the most recent entries
        if len(stack) > MAX_STACK_SIZE:
            stack = stack[-MAX_STACK_SIZE:]

        state.phase_memory["visitor_stack"] = stack

        logger.info(
            "VisitorHistory: added '%s' (stack size: %d)",
            summary.get("name", "?"),
            len(stack),
        )
