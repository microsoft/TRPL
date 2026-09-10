# -*- coding: utf-8 -*-
"""
Storys RAG Service — orchestrates story retrieval + KB search.

After each visitor input:
  1. StoryPickerAgent picks a story title + generates KB query
  2. Service loads full story narrative from disk
  3. Service searches letters + books DB (if kb_query provided)
  4. Stores all results in state for main agent to use

Replaces the old StoryCuratorService with a unified RAG pipeline.
"""
import asyncio
import logging
from datetime import datetime

from debate.agents.storys.rag_agent import StoryPickerAgent
from debate.models.constants import TR_SPEAKER
from debate.models.state import DebateState
from debate.scenarios.story_rag import StoryIndex, load_full_story

logger = logging.getLogger(f"lia.{__name__}")


class StorysRAGService:
    """Background worker: picks story + searches KB."""

    def __init__(
        self,
        *,
        picker: StoryPickerAgent,
        state: DebateState,
        index: StoryIndex,
        history_watermark_fn=None,
    ):
        self._picker = picker
        self._state = state
        self._index = index
        self._history_watermark_fn = history_watermark_fn or (lambda: 0)
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        self._counter = 0
        # Used by retrieve_now() to detect that the background fetch for
        # the latest enqueue has already finished, so it can reuse the
        # existing phase_memory["storys_rag"] instead of re-running the
        # picker + KB pipeline.
        self._last_completed_counter: int = 0
        # Set while a background _retrieve is actively running. retrieve_now
        # awaits this so the sync path doesn't race with the in-flight one.
        self._inflight_done: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("StorysRAGService started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("StorysRAGService stopped")

    def enqueue(self) -> None:
        self._counter += 1
        try:
            self._queue.put_nowait(self._counter)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(self._counter)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _rag_has_content(rag_data: dict | None) -> bool:
        """True only if the stored RAG actually carries useful context.

        The picker can return None/empty when nothing matched, in which
        case phase_memory["storys_rag"] holds a record like
        {"title": None, "narrative": None, "knowledge_context": ""}.
        Treating that as "fresh" would waste a second LLM call on
        empty context.
        """
        if not rag_data:
            return False
        return bool(rag_data.get("narrative") or rag_data.get("knowledge_context"))

    async def retrieve_now(self) -> bool:
        """Ensure phase_memory["storys_rag"] holds USEFUL RAG for the
        latest turn. Returns True iff useful RAG is now present.

        Three cases:
          (a) The background _retrieve for the latest enqueue is still
              running — await it (no duplicate work).
          (b) That background _retrieve has already completed AND has
              useful content — reuse it (skip the picker + KB pipeline).
          (c) Otherwise — run a fresh synchronous fetch. If the fetch
              raises or still returns empty, return False so the caller
              can degrade gracefully.
        """
        # (a) Wait for any in-flight background fetch to settle. If the
        # latest counter is still inflight, the event will be set when it
        # finishes and we'll fall into (b) below.
        inflight = self._inflight_done
        if inflight is not None and not inflight.is_set():
            try:
                await asyncio.wait_for(inflight.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "retrieve_now: background fetch exceeded 10s, falling through"
                )

        rag_data = (
            self._state.phase_memory.get("storys_rag")
            if self._state.phase_memory else None
        )

        # (b) Background fetch for the latest enqueue is done and produced
        # something useful — reuse it.
        if (
            self._counter > 0
            and self._last_completed_counter >= self._counter
            and self._rag_has_content(rag_data)
        ):
            logger.info(
                "retrieve_now: reusing background RAG (counter=%d)",
                self._counter,
            )
            return True

        # (c) Fall back to a fresh synchronous fetch.
        self._counter += 1
        logger.info(
            "retrieve_now: synchronous fetch (counter=%d)", self._counter
        )
        try:
            await self._retrieve(self._counter)
        except Exception as e:
            logger.warning("retrieve_now: synchronous fetch failed: %s", e)
            return False
        if self._counter > self._last_completed_counter:
            self._last_completed_counter = self._counter

        rag_data = (
            self._state.phase_memory.get("storys_rag")
            if self._state.phase_memory else None
        )
        return self._rag_has_content(rag_data)

    async def _run(self) -> None:
        while True:
            req = await self._queue.get()
            while True:
                try:
                    req = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            if req < self._counter:
                continue
            done_event = asyncio.Event()
            self._inflight_done = done_event
            try:
                await self._retrieve(req)
                if req > self._last_completed_counter:
                    self._last_completed_counter = req
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("StorysRAG retrieval error: %s", e, exc_info=True)
            finally:
                done_event.set()
                if self._inflight_done is done_event:
                    self._inflight_done = None

    async def _retrieve(self, req_id: int) -> None:
        state = self._state
        watermark = self._history_watermark_fn()
        history = [
            {"speaker": e.speaker, "text": e.text}
            for e in state.history[watermark:]
            if e.phase == "storys"
        ]

        memory = state.phase_memory.get("storys", {}) if state.phase_memory else {}
        stories_told = list(memory.get("stories_told", []) or [])
        visitor_interests = memory.get("visitor_interests", [])

        logger.info(
            "RAG picker input: %d stories_told so far — %s",
            len(stories_told), stories_told,
        )

        # 1. Picker selects story + generates KB query
        pick = await self._picker.pick(
            history=history,
            stories_told=stories_told,
            visitor_interests=visitor_interests,
        )

        title = pick.get("title")
        reason = pick.get("reason", "")
        kb_query = pick.get("kb_query")
        if not kb_query and not self._index.entries:
            kb_query = next(
                (
                    turn["text"]
                    for turn in reversed(history)
                    if turn.get("speaker") != TR_SPEAKER
                    and str(turn.get("text", "")).strip()
                ),
                None,
            )
            if kb_query:
                logger.info(
                    "RAG picker returned no query with an empty story index; "
                    "using the latest visitor input for KB search"
                )

        # 2. Load full story narrative (if title provided)
        narrative = None
        if title:
            narrative = load_full_story(title, self._index)
            if not narrative:
                logger.warning("RAG: picked '%s' but file not found", title)

        # 3. Search KB (letters + books) if query provided
        knowledge_context = ""
        if kb_query:
            knowledge_context = await self._search_kb(kb_query)

        # 4. Track pick to stories_told (don't rely on main LLM to write it).
        #    Match by fuzzy title equality — keep list shape identical to
        #    whatever the main agent might also push in.
        if title and narrative:
            if state.phase_memory is None:
                state.phase_memory = {}
            phase_storys = state.phase_memory.setdefault("storys", {})
            told_list: list[str] = list(phase_storys.get("stories_told", []) or [])
            already = any(
                title.lower() in t.lower() or t.lower() in title.lower()
                for t in told_list
            )
            if not already:
                told_list.append(title)
                phase_storys["stories_told"] = told_list

        # 5. Store results
        if state.phase_memory is None:
            state.phase_memory = {}
        state.phase_memory["storys_rag"] = {
            "title": title,
            "reason": reason,
            "narrative": narrative,
            "knowledge_context": knowledge_context,
            "_active_turns": 0,
        }

        logger.info(
            "RAG retrieved: story='%s' (%d chars), kb=%d chars — %s",
            title,
            len(narrative) if narrative else 0,
            len(knowledge_context),
            reason,
        )

    async def _search_kb(self, query: str) -> str:
        """Search letters + books DB, format results for LLM context."""
        try:
            from debate.services.shared import get_knowledge_base_service
            kb = await get_knowledge_base_service()
            results = await kb.search(
                query=query,
                top_k=6,
                search_letters=True,
                search_books=True,
            )
            if not results:
                return ""
            return kb.format_results_for_llm(results, max_results=6)
        except Exception as e:
            logger.warning("RAG KB search failed: %s", e)
            return ""
