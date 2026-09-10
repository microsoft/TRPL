# -*- coding: utf-8 -*-
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from debate.models.constants import Phase
from debate.models.state import DebateState, SupplementalNotes
from debate.services.agent_registry import AgentRegistry
from debate.utils import BroadcastQueue, LLMError

logger = logging.getLogger(f"lia.{__name__}")


@dataclass(frozen=True)
class NotesUpdateRequest:
    request_id: int
    history_len: int
    last_speaker: str | None
    phase: str | None
    queued_at: str


class NotesUpdater:
    """Background worker that updates meeting notes and emits them."""
    _allowed_phases = {
        Phase.scenario.value,
        Phase.scenario_vote.value,
        Phase.brainstorm_vote.value,
    }

    def __init__(
        self,
        *,
        state: DebateState,
        output_queue: BroadcastQueue,
        agent_registry: AgentRegistry,
        max_queue_size: int = 1,
    ):
        self._state = state
        self._output_queue = output_queue
        self._agent_registry = agent_registry
        self._queue: asyncio.Queue[NotesUpdateRequest] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._task: asyncio.Task | None = None
        self._latest_enqueued_id = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("NotesUpdater: started background worker")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("NotesUpdater: stopped background worker")

    def enqueue_update(self, *, phase: str | None = None) -> int:
        """Enqueue a notes update without blocking the caller."""
        resolved_phase = phase or self._state.phase
        if resolved_phase not in self._allowed_phases:
            logger.debug(
                "NotesUpdater: skipping notes update for phase %s",
                resolved_phase,
            )
            return 0

        self._latest_enqueued_id += 1
        request = NotesUpdateRequest(
            request_id=self._latest_enqueued_id,
            history_len=len(self._state.history),
            last_speaker=self._state.last_speaker,
            phase=resolved_phase,
            queued_at=datetime.now().isoformat(),
        )

        try:
            self._queue.put_nowait(request)
        except asyncio.QueueFull:
            # Drop the oldest request and keep the latest.
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(request)
            except asyncio.QueueFull:
                logger.warning(
                    "NotesUpdater: queue still full, dropping request %s",
                    request.request_id,
                )
        return request.request_id

    async def _run(self) -> None:
        while True:
            request = await self._queue.get()

            # Coalesce to the latest queued request.
            while True:
                try:
                    request = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Skip immediately if a newer request exists.
            if request.request_id < self._latest_enqueued_id:
                logger.debug(
                    "NotesUpdater: skipping stale request %s (latest=%s)",
                    request.request_id,
                    self._latest_enqueued_id,
                )
                continue

            try:
                await self._process_request(request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "NotesUpdater: error processing request %s: %s",
                    request.request_id,
                    exc,
                    exc_info=True,
                )

    async def _process_request(self, request: NotesUpdateRequest) -> None:
        updated_notes = await self._update_notes(request)

        if request.request_id != self._latest_enqueued_id:
            logger.debug(
                "NotesUpdater: skipping apply for stale request %s (latest=%s)",
                request.request_id,
                self._latest_enqueued_id,
            )
            return

        if not updated_notes:
            return

        self._state.meeting_notes = updated_notes

        await self._send_supplemental_notes(updated_notes)

        logger.debug(
            "NotesUpdater: applied notes update %s (%s categories)",
            request.request_id,
            len(updated_notes.categories),
        )

    async def _update_notes(self, request: NotesUpdateRequest) -> SupplementalNotes | None:
        note_taker = self._agent_registry.note_taker
        existing_notes = self._state.meeting_notes
        filtered_history = [
            entry
            for entry in self._state.history
            if entry.phase != Phase.welcome
        ]

        try:
            updated_notes = await note_taker.update_notes(
                history=filtered_history,
                existing_notes=existing_notes,
                camp_definitions=self._state.camp_definitions,
                roster=self._state.roster,
                phase=request.phase,
                brainstorm_votes=self._state.brainstorm_votes,
                brainstorm_ideas=[
                    idea.model_dump() for idea in self._state.brainstorm_ideas
                ],
                scenario_votes=self._state.scenario_votes,
            )
        except LLMError as exc:
            logger.error(
                "NotesUpdater: LLM error updating notes: %s",
                exc.message,
                exc_info=True,
            )
            return existing_notes
        except Exception as exc:
            logger.error(
                "NotesUpdater: error updating notes: %s",
                exc,
                exc_info=True,
            )
            return existing_notes

        logger.info(
            "NoteTaker updated notes (request_id=%s, categories=%s)",
            request.request_id,
            len(updated_notes.categories),
        )

        return updated_notes

    async def _send_supplemental_notes(self, notes: SupplementalNotes) -> None:
        event = {
            "type": "supplemental_notes",
            "timestamp": datetime.now().isoformat(),
            "notes": notes.model_dump(),
        }
        await self._output_queue.put(event)
