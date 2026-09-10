import asyncio
import logging
import time
from itertools import zip_longest
from typing import Any, AsyncIterator, cast

from chatbot.chat_history import ChatHistoryManager
from chatbot.utils.citation_streamer import CitationStreamer
from chatbot.VectorSearch.AzureAISearch import book_search_client, letter_search_client
from common_config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_BASE_URL,
    FACT_CHECK_LOG_PROMPT_CHARS,
    FACT_CHECK_MAX_ATTEMPTS,
    GPT_CHAT_MODEL,
    GPT_FOLLOWUP_MODEL,
    GPT_SCOPE_MODEL,
    GPT_SEARCH_QUERY_MODEL,
    SEARCH_TOP_K,
    reasoning_kwargs,
)
from models import ChatMessageItem
from observability import get_tracer
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.responses import ResponseInputParam
from pricing import CostAccumulator

from .fact_checker import run_fact_checker
from .models import FactCheckIssue, FollowUpQuestions, RAGAnswer, ScopeDetectionResult, SearchQueries
from .prompts import DEFAULT_MODE, ChatMode, get_prompt
from .safety import SAFE_REFUSAL_MESSAGE, is_safety_filter_error

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

# Shown when an answer cannot be made to pass the fact-check within the retry
# cap. We never surface an unverified answer to the user.
FACT_CHECK_FALLBACK_MESSAGE = "I'm sorry, I can't find a good answer for that right now."

# Progress messages shown over the WebSocket while we verify (and regenerate)
# the answer. Index 0 is the first fact-check pass; later indices escalate as we
# retry, reassuring the user that a slower turn is doing extra accuracy work.
# Clamped to the last entry if FACT_CHECK_MAX_ATTEMPTS is raised beyond the list.
_VERIFY_PROGRESS_MESSAGES = [
    "Verifying the accuracy of my research",
    "Double-checking a few details to make sure this is accurate",
    "Taking extra care to get this right — almost there",
]


# Module-level singleton OpenAI client
_openai_client = AsyncOpenAI(base_url=AZURE_OPENAI_BASE_URL, api_key=AZURE_OPENAI_API_KEY)


def _chat_usage(response) -> tuple[int, int, int]:
    """(prompt_tokens, completion_tokens, cached_tokens) from a chat.completions response."""
    u = response.usage
    if u is None:
        return 0, 0, 0
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) or 0
    return u.prompt_tokens, u.completion_tokens, cached


def _record_search_score_stats(span, results: list[dict]) -> None:
    """Set search.score.{mean,min} span attrs if any hit carried a similarity_score.

    Azure exposes `@search.score` and `@search.reranker_score` on each hit; the
    mapper in AzureAISearch.py forwards them as `similarity_score` /
    `reranker_score`. Cached results (L1/L2 LRU hits in base.py) preserve the
    score that was recorded the first time the query ran.
    """
    scores = [d["similarity_score"] for d in results if d.get("similarity_score") is not None]
    if not scores:
        return
    span.set_attribute("search.score.mean", sum(scores) / len(scores))
    span.set_attribute("search.score.min", min(scores))


# Module-level search functions (from rag_agent.py:34-49)
async def letter_search(query: str, top_k: int, cost: CostAccumulator | None = None) -> list[tuple[dict, str]]:
    with tracer.start_as_current_span("agent.search") as span:
        span.set_attribute("search.index", "letter")
        span.set_attribute("search.top_k", top_k)
        t0 = time.perf_counter()
        results = await letter_search_client.search(query=query, top_k=top_k, cost=cost)
        span.set_attribute("search.duration_ms", round((time.perf_counter() - t0) * 1000, 2))
        srcs = []
        for d in results:
            llm_text = letter_search_client.format_source_for_llm(d)
            srcs.append((d, llm_text))
        span.set_attribute("search.results_count", len(srcs))
        _record_search_score_stats(span, results)
        return srcs


async def book_search(query: str, top_k: int, cost: CostAccumulator | None = None) -> list[tuple[dict, str]]:
    with tracer.start_as_current_span("agent.search") as span:
        span.set_attribute("search.index", "book")
        span.set_attribute("search.top_k", top_k)
        t0 = time.perf_counter()
        results = await book_search_client.search(query=query, top_k=top_k, cost=cost)
        span.set_attribute("search.duration_ms", round((time.perf_counter() - t0) * 1000, 2))
        srcs = []
        for d in results:
            llm_text = book_search_client.format_source_for_llm(d)
            srcs.append((d, llm_text))
        span.set_attribute("search.results_count", len(srcs))
        _record_search_score_stats(span, results)
        return srcs


class EndToEndAgent:
    def __init__(
        self,
        chat_id: str,
        user_id: str,
        chat_history_manager: ChatHistoryManager,
        mode: ChatMode = DEFAULT_MODE,
    ):
        self.chat_id = chat_id
        self.user_id = user_id
        self.mode: ChatMode = mode
        self.chat_history = chat_history_manager
        self._sources = []  # Store search results for citations
        self._last_rag_response: RAGAnswer | None = None  # Final RAG answer (text + indices)
        self._rag_stream_completed: bool = False  # Did `response.completed` fire?
        # Diagnostics: intermediate pipeline state (read after ask_stream completes)
        self._diagnostics: dict[str, Any] = {}
        # Per-turn token + cost totals across every LLM call in ask_stream.
        self._cost: CostAccumulator = CostAccumulator()

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Pipeline diagnostics from the last ask_stream call.

        Available after ask_stream completes. Contains:
        - scope_result: {in_scope, reason}
        - search_queries: {historical_query, book_query}
        - sources_retrieved: total docs from search
        - sources_by_index: {letter: N, book: N}
        - retrieved_titles: [{index, source, title}, ...]
        - rag_selected_indices: [1, 3, ...] from RAG JSON
        - sources_selected: count of final cited sources
        """
        return self._diagnostics

    async def _run_scope_agent(self, user_messages: list[dict]) -> ScopeDetectionResult:
        """Determine if query is in-scope."""
        with tracer.start_as_current_span("agent.scope") as span:
            span.set_attribute("agent.stage", "scope")
            span.set_attribute("agent.mode", self.mode)
            span.set_attribute("agent.model", GPT_SCOPE_MODEL)
            span.set_attribute("chat.id", self.chat_id)
            messages = [{"role": "system", "content": get_prompt(self.mode, "scope")}] + user_messages

            response = await _openai_client.chat.completions.parse(
                model=GPT_SCOPE_MODEL,
                messages=cast(list[ChatCompletionMessageParam], messages),
                response_format=ScopeDetectionResult,
                **reasoning_kwargs(GPT_SCOPE_MODEL),
            )
            prompt_tokens, completion_tokens, cached_tokens = _chat_usage(response)
            self._cost.add(GPT_SCOPE_MODEL, prompt_tokens, completion_tokens, cached_tokens)
            span.set_attribute("llm.usage.prompt_tokens", prompt_tokens)
            span.set_attribute("llm.usage.completion_tokens", completion_tokens)
            span.set_attribute("llm.usage.cached_tokens", cached_tokens)

            json_content = response.choices[0].message.content
            assert json_content is not None, "Scope agent did not return any content"
            result = ScopeDetectionResult.model_validate_json(json_content)

            # Logged here (not at the caller) so abstention rate stays accurate:
            # ask_stream has an optimistic early-return path that checks
            # scope_task.done() before awaiting, and would otherwise skip the
            # caller-side log.
            logger.info(
                "Agent: scope detection completed",
                extra={
                    "microsoft.custom_event.name": "scope_detection_completed",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "in_scope": result.in_scope,
                },
            )
            return result

    async def _run_query_agent(self, user_messages: list[dict]) -> SearchQueries:
        """Generate search queries for vector databases."""
        with tracer.start_as_current_span("agent.query") as span:
            span.set_attribute("agent.stage", "query")
            span.set_attribute("agent.mode", self.mode)
            span.set_attribute("agent.model", GPT_SEARCH_QUERY_MODEL)
            span.set_attribute("chat.id", self.chat_id)
            messages = [{"role": "system", "content": get_prompt(self.mode, "query")}] + user_messages

            response = await _openai_client.chat.completions.parse(
                model=GPT_SEARCH_QUERY_MODEL,
                messages=cast(list[ChatCompletionMessageParam], messages),
                response_format=SearchQueries,
                seed=42,
                **reasoning_kwargs(GPT_SEARCH_QUERY_MODEL),
            )
            prompt_tokens, completion_tokens, cached_tokens = _chat_usage(response)
            self._cost.add(GPT_SEARCH_QUERY_MODEL, prompt_tokens, completion_tokens, cached_tokens)
            span.set_attribute("llm.usage.prompt_tokens", prompt_tokens)
            span.set_attribute("llm.usage.completion_tokens", completion_tokens)
            span.set_attribute("llm.usage.cached_tokens", cached_tokens)

            json_content = response.choices[0].message.content
            assert json_content is not None, "Query agent did not return any content"
            return SearchQueries.model_validate_json(json_content)

    async def _run_rag_agent_stream(self, user_messages: list[dict]) -> AsyncIterator[str]:
        """Generate streaming plain-text answer with RAG context.

        Yields raw model deltas (including inline [N] citation markers). Sets
        `_rag_stream_completed` on `response.completed` so the caller can tell a
        truncated stream from a clean finish.
        """
        messages = [{"role": "system", "content": get_prompt(self.mode, "rag")}] + user_messages

        with tracer.start_as_current_span("agent.rag") as span:
            span.set_attribute("agent.stage", "rag")
            span.set_attribute("agent.mode", self.mode)
            span.set_attribute("agent.model", GPT_CHAT_MODEL)
            span.set_attribute("chat.id", self.chat_id)
            async with _openai_client.responses.stream(
                model=GPT_CHAT_MODEL,
                input=cast(ResponseInputParam, messages),
                **reasoning_kwargs(GPT_CHAT_MODEL, api="responses"),
            ) as stream:
                async for event in stream:
                    if event.type == "response.refusal.delta":
                        pass
                    elif event.type == "response.output_text.delta":
                        yield event.delta  # ty:ignore[possibly-missing-attribute]
                    elif event.type == "response.error":
                        raise RuntimeError(event.error.message)
                    elif event.type == "response.failed":
                        raise RuntimeError(event.response.error.message if event.response.error else "response failed")
                    elif event.type == "response.incomplete":
                        reason = event.response.incomplete_details.reason if event.response.incomplete_details else None
                        if reason == "content_filter":
                            raise RuntimeError("content_filter triggered: response incomplete")
                        raise RuntimeError(f"response incomplete: {reason or 'unknown reason'}")
                    elif event.type == "response.completed":
                        self._rag_stream_completed = True
                        usage = getattr(event.response, "usage", None)
                        if usage is not None:
                            details = getattr(usage, "input_tokens_details", None)
                            cached = getattr(details, "cached_tokens", None) or 0
                            self._cost.add(
                                GPT_CHAT_MODEL,
                                prompt_tokens=usage.input_tokens,
                                completion_tokens=usage.output_tokens,
                                cached_tokens=cached,
                            )
                            span.set_attribute("llm.usage.prompt_tokens", usage.input_tokens)
                            span.set_attribute("llm.usage.completion_tokens", usage.output_tokens)
                            span.set_attribute("llm.usage.cached_tokens", cached)

    async def _run_follow_up_agent(self, messages: list[dict]) -> FollowUpQuestions:
        """Generate follow-up questions based on the conversation."""
        with tracer.start_as_current_span("agent.followup") as span:
            span.set_attribute("agent.stage", "followup")
            span.set_attribute("agent.mode", self.mode)
            span.set_attribute("agent.model", GPT_FOLLOWUP_MODEL)
            span.set_attribute("chat.id", self.chat_id)
            messages = [{"role": "system", "content": get_prompt(self.mode, "followup")}] + messages

            response = await _openai_client.chat.completions.parse(
                model=GPT_FOLLOWUP_MODEL,
                messages=cast(list[ChatCompletionMessageParam], messages),
                response_format=FollowUpQuestions,
                **reasoning_kwargs(GPT_FOLLOWUP_MODEL),
            )
            prompt_tokens, completion_tokens, cached_tokens = _chat_usage(response)
            self._cost.add(GPT_FOLLOWUP_MODEL, prompt_tokens, completion_tokens, cached_tokens)
            span.set_attribute("llm.usage.prompt_tokens", prompt_tokens)
            span.set_attribute("llm.usage.completion_tokens", completion_tokens)
            span.set_attribute("llm.usage.cached_tokens", cached_tokens)

            json_content = response.choices[0].message.content
            assert json_content is not None, "Follow-up agent did not return any content"
            return FollowUpQuestions.model_validate_json(json_content)

    def _format_search_progress(self, queries: SearchQueries) -> str:
        """Format progress message based on search queries (from workflow.py:143-149)."""
        search_progress = "Searching relevant"
        search_progress += " historical letters" if queries.historical_query else ""
        search_progress += " and" if queries.historical_query and queries.book_query else ""
        search_progress += " books" if queries.book_query else ""
        search_progress += "..."
        return search_progress

    async def _generate_candidate(self, messages: list[dict]) -> tuple[RAGAnswer, list[dict]]:
        """Generate one buffered RAG answer (no live deltas) and resolve its cited
        sources against `self._sources`.

        Called once per fact-check attempt, so `_rag_stream_completed` is reset on
        entry. Search has already run — every attempt reuses the same retrieved
        sources and only the answer text is regenerated.
        """
        self._rag_stream_completed = False
        citation_streamer = CitationStreamer()
        full_text_parts: list[str] = []

        async for chunk in self._run_rag_agent_stream(messages):
            delta_text = citation_streamer.feed(chunk)
            if delta_text:
                full_text_parts.append(delta_text)
        trailing = citation_streamer.flush()
        if trailing:
            full_text_parts.append(trailing)

        if not self._rag_stream_completed:
            raise RuntimeError("RAG agent did not return a final response")

        self._last_rag_response = RAGAnswer(
            text="".join(full_text_parts),
            selected_sources=citation_streamer.indices,
        )
        parsed_response = self._last_rag_response

        # Validate the LLM's source indices against what we actually retrieved.
        # Out-of-range/negative indices were dropping silently, which produced
        # citation references in the answer text with no matching citation entry.
        valid_indices = {s["index"] for s in self._sources if "index" in s}
        requested_indices = set(parsed_response.selected_sources)
        invalid_indices = requested_indices - valid_indices
        if invalid_indices:
            logger.warning(
                "Agent: RAG referenced unknown source indices",
                extra={
                    "microsoft.custom_event.name": "rag_invalid_source_indices",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "invalid_indices": sorted(invalid_indices),
                    "valid_indices": sorted(valid_indices),
                },
            )
        selected_indices = requested_indices & valid_indices
        final_sources = [s for s in self._sources if s.get("index") in selected_indices]

        # Capture diagnostics: RAG selection
        self._diagnostics["rag_selected_indices"] = parsed_response.selected_sources
        self._diagnostics["rag_invalid_indices"] = sorted(invalid_indices)
        self._diagnostics["sources_selected"] = len(final_sources)

        logger.info(
            "Agent: RAG generation completed",
            extra={
                "microsoft.custom_event.name": "rag_generation_completed",
                "chat_id": self.chat_id,
                "user_id": self.user_id,
                "response_length": len(parsed_response.text),
                "sources_selected": len(parsed_response.selected_sources),
            },
        )
        return parsed_response, final_sources

    def _format_fact_check_correction(self, issues: list[FactCheckIssue]) -> str:
        """Build a corrective user message for a regeneration attempt, listing the
        groundedness issues the fact-checker flagged in the previous draft."""
        lines = "\n".join(f"- Claim: {issue.claim}\n  Problem: {issue.explanation}" for issue in issues)
        return (
            "A reviewer found the previous draft made claims that are not grounded in the "
            "cited sources. Rewrite the answer to fix the following issues, using ONLY "
            f"information supported by the cited sources:\n{lines}"
        )

    async def ask_stream(self, latest_user_message: str) -> AsyncIterator[dict[str, Any]]:
        """Main entry point — yields a stream of typed dict chunks (delta/final/progress/error/extra).

        Wraps the whole turn in a single root span ("agent.flow") so that every
        event emitted during the turn — agent_flow_started/completed,
        search_queries_generated, scope_detection_completed, fact_check_completed,
        and the per-stage sub-spans — shares one operation_Id. Without this, events
        logged directly from the flow body have no active span and Application
        Insights stamps them with the all-zeros trace ID, which breaks any KQL
        correlation across events.
        """
        with tracer.start_as_current_span("agent.flow"):
            async for chunk in self._ask_stream_impl(latest_user_message):
                yield chunk

    async def _ask_stream_impl(self, latest_user_message: str) -> AsyncIterator[dict[str, Any]]:
        """Turn implementation. Runs inside the ``agent.flow`` root span opened by ``ask_stream``."""

        # Reset per-turn state. Agent is reused across messages in a WebSocket
        # session, so without this a turn that fails before `response.completed`
        # would inherit the previous turn's RAGAnswer and serve it as the answer
        # to the current question.
        self._sources.clear()
        self._last_rag_response = None
        self._rag_stream_completed = False
        self._diagnostics = {}
        self._cost = CostAccumulator()

        try:
            latest_user_message_timestamp = time.time()

            logger.info(
                "Agent: flow started",
                extra={
                    "microsoft.custom_event.name": "agent_flow_started",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "mode": self.mode,
                    "message_length": len(latest_user_message),
                },
            )

            # Build full message history
            history = await self.chat_history.get_chat_messages(self.chat_id, self.user_id)
            recent_history = history[-20:]  # Last 20 exchanges

            user_messages = [{"role": msg.role, "content": msg.text} for msg in recent_history] + [
                {"role": "user", "content": latest_user_message}
            ]

            yield {"type": "progress", "progress": "Analyzing your question..."}

            # Start scope check in parallel
            scope_task = asyncio.create_task(self._run_scope_agent(user_messages))

            # Generate search queries
            search_queries = await self._run_query_agent(user_messages)

            # Capture diagnostics: search queries
            self._diagnostics["search_queries"] = {
                "historical_query": search_queries.historical_query,
                "book_query": search_queries.book_query,
            }

            logger.info(
                "Agent: search queries generated",
                extra={
                    "microsoft.custom_event.name": "search_queries_generated",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "historical_query": search_queries.historical_query,
                    "book_query": search_queries.book_query,
                },
            )

            # check if scope task is completed, if not still dont await yet, proceed with search
            if scope_task.done():
                if not scope_task.result().in_scope:
                    yield {
                        "type": "final",
                        "text": "I can't help with that request. Please ask something related to Theodore Roosevelt or his era.",
                        "citations": [],
                    }
                    return

            # Execute vector search and build context (inline from rag_agent.py:68-125)
            context_messages = []

            if search_queries.historical_query or search_queries.book_query:
                # Execute searches in parallel. Each chosen index is asked for
                # up to SEARCH_TOP_K hits; the combined collection is then
                # round-robin interleaved (A1, B1, A2, B2, …) and capped at
                # SEARCH_TOP_K so both indexes get representation when both
                # were searched.
                tasks = []
                if search_queries.historical_query:
                    tasks.append(letter_search(search_queries.historical_query, SEARCH_TOP_K, self._cost))
                if search_queries.book_query:
                    tasks.append(book_search(search_queries.book_query, SEARCH_TOP_K, self._cost))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # If every scheduled search blew up, the RAG would answer with
                # no context — typically hallucinating or refusing with no
                # signal to the user that retrieval failed. Surface it instead.
                failures = [r for r in results if isinstance(r, Exception)]
                if failures and len(failures) == len(results):
                    for failure in failures:
                        logger.exception("Search failed: %s", failure)
                    logger.error(
                        "Agent: all retrieval searches failed",
                        extra={
                            "microsoft.custom_event.name": "search_all_failed",
                            "chat_id": self.chat_id,
                            "user_id": self.user_id,
                            "failure_count": len(failures),
                            "task_count": len(results),
                        },
                    )
                    yield {
                        "type": "error",
                        "error": "I couldn't retrieve any sources to answer your question. Please try again in a moment.",
                    }
                    return

                successful_results: list[list[tuple[dict, str]]] = []
                for result in results:
                    if isinstance(result, BaseException):
                        logger.exception("Search failed: %s", result)
                        continue
                    successful_results.append(result)  # ty:ignore[invalid-argument-type]

                interleaved: list[tuple[dict, str]] = []
                for group in zip_longest(*successful_results):
                    for item in group:
                        if item is None:
                            continue
                        interleaved.append(item)
                        if len(interleaved) >= SEARCH_TOP_K:
                            break
                    if len(interleaved) >= SEARCH_TOP_K:
                        break

                # Build context messages
                lines = []
                src_index = 1
                for src, llm_text in interleaved:
                    src["index"] = src_index
                    src_index += 1
                    self._sources.append(src)
                    lines.append(f"Source {src['index']}: {llm_text}")

                if lines:
                    context_messages = [
                        {"role": "user", "content": "Retrieved documents below:"},
                        *[{"role": "user", "content": line} for line in lines],
                    ]

                letter_count = sum(1 for s in self._sources if s.get("source") == "letter")
                book_count = sum(1 for s in self._sources if s.get("source") == "book")
                resource_types = sorted({s["resource_type"] for s in self._sources if s.get("resource_type")})
                collections = sorted({s["collection"] for s in self._sources if s.get("collection")})
                has_empty_index = (bool(search_queries.historical_query) and letter_count == 0) or (
                    bool(search_queries.book_query) and book_count == 0
                )

                logger.info(
                    "Agent: search execution completed",
                    extra={
                        "microsoft.custom_event.name": "search_execution_completed",
                        "chat_id": self.chat_id,
                        "user_id": self.user_id,
                        "sources_found": len(self._sources),
                        "has_context": len(lines) > 0,
                        "letter_count": letter_count,
                        "book_count": book_count,
                        "resource_types": resource_types,
                        "collections": collections,
                        "has_empty_index": has_empty_index,
                    },
                )

            # Yield progress update
            search_progress = self._format_search_progress(search_queries)
            yield {"type": "progress", "progress": search_progress}

            # Check scope result, here we await if not done yet, rag generation cannot proceed if out of scope.
            # The custom event for this is logged inside _run_scope_agent so the optimistic
            # early-return path above (when scope_task is already done before search) is also covered.
            scope_result = await scope_task

            # Capture diagnostics: scope result
            self._diagnostics["scope_result"] = {
                "in_scope": scope_result.in_scope,
                "reason": scope_result.reason,
            }

            if not scope_result.in_scope:
                yield {
                    "type": "final",
                    "text": "I can't help with that request. Please ask something related to Theodore Roosevelt or his era.",
                    "citations": [],
                }
                return

            # Capture diagnostics: all retrieved sources (before RAG selection)
            self._diagnostics["sources_retrieved"] = len(self._sources)
            self._diagnostics["sources_by_index"] = {
                "letter": sum(1 for s in self._sources if s.get("source") == "letter"),
                "book": sum(1 for s in self._sources if s.get("source") == "book"),
            }
            self._diagnostics["retrieved_titles"] = [
                {"index": s.get("index"), "source": s.get("source"), "title": (s.get("title") or "")[:100]}
                for s in self._sources
            ]

            yield {"type": "progress", "progress": f"Reviewing {len(self._sources)} documents..."}

            logger.info(
                "Agent: RAG generation started",
                extra={
                    "microsoft.custom_event.name": "rag_generation_started",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "available_sources": len(self._sources),
                },
            )

            # Generate the answer, fact-check it, and regenerate (feeding the
            # flagged issues back as corrective guidance) until it passes or we
            # hit the attempt cap. The answer is buffered, not streamed live, so
            # the user only ever sees a fact-checked response.
            base_messages = user_messages + context_messages
            correction: list[dict] = []
            clean: tuple[RAGAnswer, list[dict]] | None = None
            fact_check_result = None
            attempt_num = 0
            last_issue_count = 0

            for attempt_num in range(1, FACT_CHECK_MAX_ATTEMPTS + 1):
                parsed_response, final_sources = await self._generate_candidate(base_messages + correction)
                # Surface the verification step (it gates every answer, so the
                # user waits through it even without a retry).
                if attempt_num == 1:
                    yield {"type": "progress", "progress": _VERIFY_PROGRESS_MESSAGES[0]}
                fact_check_result = await run_fact_checker(
                    openai_client=_openai_client,
                    user_question=latest_user_message,
                    rag_answer=parsed_response.text,
                    cited_sources=final_sources,
                    cost_accumulator=self._cost,
                )
                if not fact_check_result.flagged:
                    clean = (parsed_response, final_sources)
                    break
                last_issue_count = len(fact_check_result.issues)
                will_retry = attempt_num < FACT_CHECK_MAX_ATTEMPTS
                issues_summary = (
                    "; ".join(f"{issue.claim} — {issue.explanation}" for issue in fact_check_result.issues)
                    or "(fact-checker flagged the answer but listed no specific issues)"
                )
                logger.warning(
                    "Agent: fact-check round %s/%s failed with %s issue(s): %s",
                    attempt_num,
                    FACT_CHECK_MAX_ATTEMPTS,
                    last_issue_count,
                    issues_summary,
                    extra={
                        "microsoft.custom_event.name": "fact_check_round_failed",
                        "chat_id": self.chat_id,
                        "user_id": self.user_id,
                        "attempt": attempt_num,
                        "max_attempts": FACT_CHECK_MAX_ATTEMPTS,
                        "issue_count": last_issue_count,
                        "fact_check_issues": [issue.model_dump() for issue in fact_check_result.issues],
                        "user_prompt_prefix": latest_user_message[:FACT_CHECK_LOG_PROMPT_CHARS],
                        "will_retry": will_retry,
                    },
                )
                correction = [
                    {"role": "assistant", "content": parsed_response.text},
                    {"role": "user", "content": self._format_fact_check_correction(fact_check_result.issues)},
                ]
                if will_retry:
                    # Escalate the message for the upcoming retry so a longer wait
                    # reads as deliberate care rather than a stall.
                    escalation_index = min(attempt_num, len(_VERIFY_PROGRESS_MESSAGES) - 1)
                    yield {"type": "progress", "progress": _VERIFY_PROGRESS_MESSAGES[escalation_index]}

            if clean is None:
                # Cap exhausted and still not grounded — never surface an
                # unverified answer. Send a safe message and log for review.
                logger.warning(
                    "Agent: fact-check unresolved",
                    extra={
                        "microsoft.custom_event.name": "fact_check_unresolved",
                        "chat_id": self.chat_id,
                        "user_id": self.user_id,
                        "attempts": FACT_CHECK_MAX_ATTEMPTS,
                        "issue_count": last_issue_count,
                        "user_prompt_prefix": latest_user_message[:FACT_CHECK_LOG_PROMPT_CHARS],
                    },
                )
                yield {"type": "final", "text": FACT_CHECK_FALLBACK_MESSAGE, "citations": []}
                yield self._cost.to_payload()
                return

            parsed_response, final_sources = clean
            assert fact_check_result is not None

            # yield final response
            yield {"type": "final", "text": parsed_response.text, "citations": final_sources}

            # Persist BEFORE follow-up so the answer survives a follow-up agent
            # failure. Wrap so a Redis hiccup doesn't fall through to the outer
            # except and yield an error chunk after the user already saw `final`.
            assistant_timestamp = time.time()
            search_results_persisted = [
                {
                    "id": s.get("id"),
                    "index_name": (
                        letter_search_client.index if s.get("source") == "letter" else book_search_client.index
                    ),
                }
                for s in self._sources
            ]
            try:
                await self.chat_history.add_messages(
                    self.chat_id,
                    self.user_id,
                    [
                        ChatMessageItem(role="user", text=latest_user_message, timestamp=latest_user_message_timestamp),
                        ChatMessageItem(
                            role="assistant",
                            text=parsed_response.text,
                            timestamp=assistant_timestamp,
                            citations=final_sources,
                            search_queries={
                                "historical_query": search_queries.historical_query,
                                "book_query": search_queries.book_query,
                            },
                            search_results=search_results_persisted,
                        ),
                    ],
                )
            except Exception as persist_error:
                logger.exception("Failed to persist chat history (non-fatal): %s", persist_error)

            # The answer already passed fact-check above. Generate follow-up
            # questions on the approved answer — a post-final nicety whose failure
            # must not surface as a top-level error after the user has the answer.
            follow_up_questions_payload: list[str] | None = None
            try:
                follow_up_result = await self._run_follow_up_agent(
                    user_messages + context_messages + [{"role": "assistant", "content": parsed_response.text}]
                )
            except Exception as follow_up_error:
                logger.exception("Follow-up generation failed (non-fatal): %s", follow_up_error)
            else:
                follow_up_questions_payload = follow_up_result.questions
                yield {"type": "extra", "follow_up_questions": follow_up_questions_payload}
                logger.info(
                    "Agent: follow-up generation completed",
                    extra={
                        "microsoft.custom_event.name": "follow_up_generation_completed",
                        "chat_id": self.chat_id,
                        "user_id": self.user_id,
                        "follow_up_count": len(follow_up_result.questions),
                    },
                )

            # Record the (passing) fact-check outcome. `attempts` shows how many
            # generations grounding took. No `fact_check` chunk is sent to the
            # client — the gated answer is always verified, so there is no badge.
            self._diagnostics["fact_check_result"] = {
                "flagged": fact_check_result.flagged,
                "issue_count": len(fact_check_result.issues),
            }
            logger.info(
                "Agent: fact-check completed",
                extra={
                    "microsoft.custom_event.name": "fact_check_completed",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "flagged": fact_check_result.flagged,
                    "issue_count": len(fact_check_result.issues),
                    "fact_check_issues": [issue.model_dump() for issue in fact_check_result.issues],
                    "user_prompt_prefix": latest_user_message[:FACT_CHECK_LOG_PROMPT_CHARS],
                    "attempts": attempt_num,
                },
            )

            # Persist follow-up questions. fact_check stays None — no badge on
            # reload, consistent with the gated, no-badge delivery.
            if follow_up_questions_payload is not None:
                try:
                    await self.chat_history.update_last_assistant_message(
                        self.chat_id,
                        self.user_id,
                        expected_timestamp=assistant_timestamp,
                        fact_check=None,
                        follow_up_questions=follow_up_questions_payload,
                    )
                except Exception as persist_error:
                    logger.exception(
                        "Failed to persist follow-up (non-fatal): %s",
                        persist_error,
                    )

            yield self._cost.to_payload()

            # Roll up content-trust dimensions from the final citation set.
            # Kept on agent_flow_completed (one event per query) so the Trust
            # tab in the observability workbook can compute mixes/rates in a
            # single KQL pass without joining across events.
            citation_letter_count = sum(1 for s in final_sources if s.get("source") == "letter")
            citation_book_count = sum(1 for s in final_sources if s.get("source") == "book")
            citation_resource_types = sorted({s["resource_type"] for s in final_sources if s.get("resource_type")})
            citation_collections_distinct = len({s["collection"] for s in final_sources if s.get("collection")})
            citation_with_copyright_notes = sum(1 for s in final_sources if (s.get("copyright_notes") or "").strip())
            fact_check_flagged = fact_check_result.flagged if fact_check_result is not None else None

            logger.info(
                "Agent: flow completed successfully",
                extra={
                    "microsoft.custom_event.name": "agent_flow_completed",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "mode": self.mode,
                    "duration_seconds": time.time() - latest_user_message_timestamp,
                    "final_citations": len(final_sources),
                    "total_cost_usd": self._cost.total_usd,
                    "prompt_tokens": self._cost.prompt_tokens,
                    "cached_tokens": self._cost.cached_tokens,
                    "completion_tokens": self._cost.completion_tokens,
                    "embedding_tokens": self._cost.embedding_tokens,
                    "embedding_cost_usd": self._cost.embedding_cost_usd,
                    "citation_letter_count": citation_letter_count,
                    "citation_book_count": citation_book_count,
                    "citation_resource_types": citation_resource_types,
                    "citation_collections_distinct": citation_collections_distinct,
                    "citation_with_copyright_notes": citation_with_copyright_notes,
                    "fact_check_flagged": fact_check_flagged,
                },
            )

        except Exception as e:
            logger.exception("ask_stream() failed: %s", e)
            logger.info(
                "Agent: flow failed",
                extra={
                    "microsoft.custom_event.name": "agent_flow_failed",
                    "chat_id": self.chat_id,
                    "user_id": self.user_id,
                    "mode": self.mode,
                    "error": str(e),
                },
            )
            if is_safety_filter_error(e):
                yield {"type": "final", "text": SAFE_REFUSAL_MESSAGE, "citations": []}
                return
            yield {"type": "error", "error": str(e)}
