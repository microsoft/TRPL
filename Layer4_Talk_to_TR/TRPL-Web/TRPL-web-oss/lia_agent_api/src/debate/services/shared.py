# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import threading

from api.config import config
from debate.services.book_search import BookSearchClient
from debate.services.knowledge_base import KnowledgeBaseService
from debate.services.search import SearchService, close_search_clients
from openai import OpenAI

logger = logging.getLogger(f"lia.{__name__}")

_search_service = None
_book_search_client = None
_knowledge_base_service = None

_search_lock = asyncio.Lock()
_book_lock = asyncio.Lock()
_kb_lock = asyncio.Lock()

_openai_client = None
_openai_lock = threading.Lock()


async def get_search_service() -> SearchService:
    global _search_service
    if _search_service is None:
        async with _search_lock:
            if _search_service is None:
                _search_service = SearchService()
    return _search_service


async def get_book_search_client() -> BookSearchClient:
    global _book_search_client
    if _book_search_client is None:
        async with _book_lock:
            if _book_search_client is None:
                _book_search_client = BookSearchClient()
    return _book_search_client


async def get_knowledge_base_service() -> KnowledgeBaseService:
    global _knowledge_base_service
    if _knowledge_base_service is None:
        async with _kb_lock:
            if _knowledge_base_service is None:
                search_service = await get_search_service()
                book_search = await get_book_search_client()
                _knowledge_base_service = KnowledgeBaseService(
                    letter_search=search_service,
                    book_search=book_search,
                )
    return _knowledge_base_service


async def warmup_knowledge_base_service() -> None:
    """
    Pre-initialize the KB service and run a warmup query.
    Call this during app startup to avoid cold start delays.
    """
    import time
    start = time.perf_counter()
    try:
        kb_service = await get_knowledge_base_service()
        # Run a simple warmup query to initialize embeddings client
        await kb_service.search(
            query="Theodore Roosevelt conservation",
            top_k=1,
            search_letters=True,
            search_books=True,
        )
        elapsed = time.perf_counter() - start
        logger.info(f"[KB Warmup] Completed in {elapsed:.3f}s")
    except Exception as e:
        elapsed = time.perf_counter() - start
        logger.warning(f"[KB Warmup] Failed after {elapsed:.3f}s: {e}")


class BackgroundKBSearchManager:
    """
    Manages background KB search tasks per session.
    
    When async KB search is enabled:
    - User speaks → schedule_search() starts background query
    - Next round → get_cached_result() retrieves completed result
    - LLM sees KB context from previous round's query
    """

    def __init__(self):
        self._pending_tasks: dict[str, asyncio.Task] = {}  # session_id -> task
        self._cached_results: dict[str, str] = {}          # session_id -> kb_context
        self._cached_queries: dict[str, str] = {}          # session_id -> query
        self._lock = asyncio.Lock()

    async def schedule_search(
        self,
        session_id: str,
        query: str,
        should_query: bool = True,
    ) -> None:
        """
        Start a background KB search for the given query.
        Non-blocking - the search runs in background.
        
        Args:
            session_id: Unique session identifier
            query: The user's message to search for
            should_query: Whether this query warrants KB search
        """
        if not should_query:
            logger.debug(f"[KB Async] Skipping search for session {session_id} - not a question")
            return

        async with self._lock:
            # Cancel any existing pending task for this session
            if session_id in self._pending_tasks:
                old_task = self._pending_tasks[session_id]
                if not old_task.done():
                    old_task.cancel()
                    logger.debug(f"[KB Async] Cancelled old task for session {session_id}")

            # Start new background task
            task = asyncio.create_task(self._do_search(session_id, query))
            self._pending_tasks[session_id] = task
            self._cached_queries[session_id] = query
            logger.info(f"[KB Async] Scheduled background search for session {session_id}: '{query[:50]}...'")

    async def _do_search(self, session_id: str, query: str) -> None:
        """Execute the KB search in background."""
        import time
        start_time = time.perf_counter()
        
        try:
            kb_service = await get_knowledge_base_service()
            kb_results = await kb_service.search(
                query=query,
                top_k=6,
                search_letters=True,
                search_books=True,
            )

            result = ""
            if kb_results:
                result = kb_service.format_results_for_llm(kb_results, max_results=6)
            
            async with self._lock:
                self._cached_results[session_id] = result
            
            elapsed = time.perf_counter() - start_time
            logger.info(
                f"[KB Async] Background search completed for session {session_id} "
                f"in {elapsed:.3f}s, found {len(kb_results)} results"
            )
        except asyncio.CancelledError:
            logger.debug(f"[KB Async] Search cancelled for session {session_id}")
            raise
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.warning(f"[KB Async] Search failed for session {session_id} after {elapsed:.3f}s: {e}")
            async with self._lock:
                self._cached_results[session_id] = ""

    def get_cached_result(self, session_id: str) -> tuple[str, str | None]:
        """
        Get the cached KB result for a session (non-blocking).
        
        Returns:
            Tuple of (kb_context, query_that_produced_it)
            Returns ("", None) if no cached result available.
        """
        result = self._cached_results.get(session_id, "")
        query = self._cached_queries.get(session_id)
        
        if result:
            logger.info(f"[KB Async] Using cached result for session {session_id} (query: '{query[:30] if query else ''}...')")
        
        return result, query

    def clear_cache(self, session_id: str) -> None:
        """Clear cached result after it's been used."""
        self._cached_results.pop(session_id, None)
        # Keep the query for debugging purposes

    async def cancel_pending(self, session_id: str) -> None:
        """Cancel any pending search for a session."""
        async with self._lock:
            if session_id in self._pending_tasks:
                task = self._pending_tasks.pop(session_id)
                if not task.done():
                    task.cancel()
                    logger.debug(f"[KB Async] Cancelled pending task for session {session_id}")

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up all resources for a session."""
        await self.cancel_pending(session_id)
        self._cached_results.pop(session_id, None)
        self._cached_queries.pop(session_id, None)

    async def shutdown(self) -> None:
        """Cancel and await all pending tasks."""
        async with self._lock:
            pending_tasks = list(self._pending_tasks.values())
            self._pending_tasks.clear()
            self._cached_results.clear()
            self._cached_queries.clear()

        for task in pending_tasks:
            if not task.done():
                task.cancel()

        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            logger.info(
                "[KB Async] Shutdown cancelled %d pending task(s)",
                len(pending_tasks),
            )


# Singleton instance
_kb_search_manager: BackgroundKBSearchManager | None = None
_kb_manager_lock = asyncio.Lock()


async def get_kb_search_manager() -> BackgroundKBSearchManager:
    """Get the singleton BackgroundKBSearchManager instance."""
    global _kb_search_manager
    if _kb_search_manager is None:
        async with _kb_manager_lock:
            if _kb_search_manager is None:
                _kb_search_manager = BackgroundKBSearchManager()
                logger.info("[KB Async] BackgroundKBSearchManager initialized")
    return _kb_search_manager


async def shutdown_shared_services():
    global _knowledge_base_service, _book_search_client, _search_service
    global _openai_client, _kb_search_manager
    
    # Clean up KB search manager
    if _kb_search_manager is not None:
        try:
            await _kb_search_manager.shutdown()
        except Exception as e:
            logger.warning(f"Failed to shutdown KB search manager: {e}", exc_info=True)
        _kb_search_manager = None
    
    if _book_search_client is not None:
        try:
            await _book_search_client.close()
        except Exception as e:
            logger.warning(f"Failed to close book search client: {e}", exc_info=True)
    try:
        await close_search_clients()
    except Exception as e:
        logger.warning(f"Failed to close search clients: {e}", exc_info=True)

    _knowledge_base_service = None
    _book_search_client = None
    _search_service = None
    _openai_client = None


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    with _openai_lock:
        if _openai_client is None:
            _openai_client = OpenAI(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
            )
    return _openai_client
