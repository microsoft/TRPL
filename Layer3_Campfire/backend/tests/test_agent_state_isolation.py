"""Regression tests for EndToEndAgent failure modes (REPORT.md #5 and #6)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.orchestrated_agent.agent import FACT_CHECK_FALLBACK_MESSAGE, EndToEndAgent
from chatbot.orchestrated_agent.models import (
    FactCheckIssue,
    FactCheckResult,
    FollowUpQuestions,
    RAGAnswer,
    ScopeDetectionResult,
    SearchQueries,
)


def _fact_check(flagged: bool = False, issues: list[FactCheckIssue] | None = None) -> FactCheckResult:
    return FactCheckResult(flagged=flagged, issues=issues or [])


def _clean_fact_check() -> AsyncMock:
    """An AsyncMock fact-checker that always passes (no flagged issues)."""
    return AsyncMock(return_value=_fact_check(flagged=False))


@pytest.fixture
def history_manager():
    mgr = MagicMock()
    mgr.get_chat_messages = AsyncMock(return_value=[])
    mgr.add_messages = AsyncMock(return_value=None)
    return mgr


def _scope(in_scope: bool = True) -> ScopeDetectionResult:
    return ScopeDetectionResult(in_scope=in_scope, reason="test")


def _queries() -> SearchQueries:
    return SearchQueries(historical_query="tr", book_query=None)


def _rag_answer(text: str = "fresh answer") -> RAGAnswer:
    return RAGAnswer(text=text, selected_sources=[])


def _follow_up() -> FollowUpQuestions:
    return FollowUpQuestions(questions=["Q1", "Q2"])


@pytest.mark.asyncio
async def test_failed_turn_does_not_serve_previous_turn_state(history_manager):
    """REPORT.md #5: state must reset at the start of every ask_stream call.

    Reproduces the bug: agent is reused across messages in a WebSocket session.
    Turn 1 set _last_rag_response. Turn 2's RAG stream truncates before
    response.completed. Without the reset, the assert at the end of ask_stream
    would pass against turn 1's value and serve it as turn 2's answer.
    """
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    # Pre-populate as if turn 1 had succeeded.
    agent._last_rag_response = _rag_answer("STALE TURN 1 ANSWER")
    agent._sources = [{"source": "letter", "index": 1, "id": "stale", "title": "Stale"}]
    agent._diagnostics = {"stale": "data"}

    async def rag_truncated(_messages):
        # Yields a delta but never flips _rag_stream_completed (no response.completed).
        yield "partial..."

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_truncated(m)), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        chunks = [c async for c in agent.ask_stream("turn 2 query")]

    final_chunks = [c for c in chunks if c.get("type") == "final"]
    assert not any("STALE TURN 1 ANSWER" in c.get("text", "") for c in final_chunks), \
        f"Stale answer leaked into turn 2: {final_chunks}"
    # Truncated stream + missing response.completed should surface as an error.
    assert any(c.get("type") == "error" for c in chunks), \
        f"Expected an error chunk for truncated stream, got: {[c['type'] for c in chunks]}"


@pytest.mark.asyncio
async def test_follow_up_failure_does_not_poison_final(history_manager):
    """REPORT.md #6: a follow-up agent crash must not emit an error after final."""
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    async def rag_ok(_messages):
        agent._rag_stream_completed = True
        yield "real answer"

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_ok(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        chunks = [c async for c in agent.ask_stream("hi")]

    types = [c.get("type") for c in chunks]
    assert "final" in types
    assert "error" not in types, f"Spurious error chunk after final: {chunks}"
    # Persist must have happened despite follow-up failing.
    history_manager.add_messages.assert_called_once()


@pytest.mark.asyncio
async def test_persist_failure_does_not_poison_final(history_manager):
    """REPORT.md #6: a Redis hiccup persisting history must not emit an error after final."""
    history_manager.add_messages = AsyncMock(side_effect=RuntimeError("redis down"))
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    async def rag_ok(_messages):
        agent._rag_stream_completed = True
        yield "real answer"

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_ok(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        chunks = [c async for c in agent.ask_stream("hi")]

    types = [c.get("type") for c in chunks]
    assert "final" in types
    assert "error" not in types, f"Spurious error chunk after final: {chunks}"
    # Follow-up should still have run since persist failure is isolated.
    assert "extra" in types


@pytest.mark.asyncio
async def test_all_searches_failing_yields_error_chunk(history_manager):
    """REPORT.md #20: when every scheduled search raises, abort with an error.

    Without this, asyncio.gather(..., return_exceptions=True) swallows the
    failures and the RAG model answers with no context — hallucinating or
    refusing — and the user gets no signal that retrieval failed.
    """
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    # Both queries scheduled so two tasks run; both blow up.
    both_queries = SearchQueries(historical_query="tr", book_query="rough riders")

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=both_queries)), \
         patch("chatbot.orchestrated_agent.agent.letter_search",
               AsyncMock(side_effect=RuntimeError("letter index down"))), \
         patch("chatbot.orchestrated_agent.agent.book_search",
               AsyncMock(side_effect=RuntimeError("book index down"))):
        chunks = [c async for c in agent.ask_stream("question")]

    types = [c.get("type") for c in chunks]
    assert "error" in types, f"Expected error chunk for total search failure, got: {types}"
    # No final answer should leak through when retrieval failed completely.
    assert "final" not in types, f"Final answer must not be served with zero context: {chunks}"
    # And the RAG model should not have been invoked.
    history_manager.add_messages.assert_not_called()


@pytest.mark.asyncio
async def test_partial_search_failure_still_answers(history_manager):
    """Partial failure (one search ok, one raises) must continue to the RAG."""
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)
    both_queries = SearchQueries(historical_query="tr", book_query="rough riders")

    async def rag_ok(_messages):
        agent._rag_stream_completed = True
        yield "partial answer"

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=both_queries)), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_ok(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])), \
         patch("chatbot.orchestrated_agent.agent.book_search",
               AsyncMock(side_effect=RuntimeError("book index down"))):
        chunks = [c async for c in agent.ask_stream("question")]

    types = [c.get("type") for c in chunks]
    assert "final" in types, f"Partial failure should still answer, got: {types}"


@pytest.mark.asyncio
async def test_invalid_source_indices_are_filtered_and_logged(history_manager, caplog):
    """REPORT.md #25: indices the LLM names but didn't retrieve must be dropped
    explicitly and logged. Otherwise phantom indices silently disappear from
    citations, leaving dangling [n] references in the answer text and no
    operator signal that the model hallucinated.
    """
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    # Two real sources retrieved; RAG claims to have used indices 1, 5, 7.
    async def rag_with_phantom_indices(_messages):
        agent._rag_stream_completed = True
        yield "answer [1] [5] [7]"

    fake_sources = [
        ({"source": "letter", "id": "a", "title": "A"}, "src1 llm text"),
        ({"source": "letter", "id": "b", "title": "B"}, "src2 llm text"),
    ]

    with caplog.at_level("WARNING"), \
         patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_with_phantom_indices(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.run_fact_checker", _clean_fact_check()), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=fake_sources)):
        chunks = [c async for c in agent.ask_stream("q")]

    final = next(c for c in chunks if c.get("type") == "final")
    # Only the one valid index survives.
    assert [c["index"] for c in final["citations"]] == [1]
    assert any(
        "unknown source indices" in rec.message.lower() for rec in caplog.records
    ), f"Expected a warning about unknown indices, got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_duplicate_and_non_positive_source_indices_dropped(history_manager):
    """REPORT.md #25: duplicate indices dedupe, zero/negative are dropped."""
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    async def rag_with_dupes_and_negs(_messages):
        agent._rag_stream_completed = True
        # Streamer dedupes by first appearance; index 0 will be filtered by the
        # agent's valid_indices intersection (only 1 and 2 exist).
        yield "answer [1] [1] [0] [2]"

    fake_sources = [
        ({"source": "letter", "id": "a", "title": "A"}, "src1"),
        ({"source": "letter", "id": "b", "title": "B"}, "src2"),
    ]

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_with_dupes_and_negs(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.run_fact_checker", _clean_fact_check()), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=fake_sources)):
        chunks = [c async for c in agent.ask_stream("q")]

    final = next(c for c in chunks if c.get("type") == "final")
    assert [c["index"] for c in final["citations"]] == [1, 2]


@pytest.mark.asyncio
async def test_persist_happens_before_follow_up(history_manager):
    """REPORT.md #6: persisting the answer is more important than follow-up.

    Order matters: if follow-up takes seconds, a user refreshing during that
    window must see their answer in history.
    """
    call_order: list[str] = []

    async def record_add_messages(*_args, **_kwargs):
        call_order.append("persist")

    async def record_follow_up(*_args, **_kwargs):
        call_order.append("follow_up")
        return _follow_up()

    history_manager.add_messages = AsyncMock(side_effect=record_add_messages)
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    async def rag_ok(_messages):
        agent._rag_stream_completed = True
        yield "answer"

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=lambda m: rag_ok(m)), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(side_effect=record_follow_up)), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        async for _ in agent.ask_stream("hi"):
            pass

    assert call_order == ["persist", "follow_up"], \
        f"Persist must run before follow-up, got order: {call_order}"


@pytest.mark.asyncio
async def test_regenerates_until_fact_check_passes(history_manager):
    """A flagged first draft triggers a regeneration; the clean second draft is
    what gets sent. The user never sees the flagged draft."""
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    drafts = ["flagged draft", "clean draft"]
    gen_count = 0

    def rag_stream(_messages):
        async def gen():
            nonlocal gen_count
            agent._rag_stream_completed = True
            yield drafts[gen_count]
            gen_count += 1
        return gen()

    fact_checker = AsyncMock(side_effect=[
        _fact_check(flagged=True, issues=[FactCheckIssue(claim="x", explanation="ungrounded")]),
        _fact_check(flagged=False),
    ])

    with patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=rag_stream), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.run_fact_checker", fact_checker), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        chunks = [c async for c in agent.ask_stream("q")]

    assert gen_count == 2, "Expected exactly one regeneration"
    finals = [c for c in chunks if c.get("type") == "final"]
    assert len(finals) == 1 and finals[0]["text"] == "clean draft"
    # No fact_check chunk is ever sent to the client on the gated path.
    assert not any(c.get("type") == "fact_check" for c in chunks)
    # The clean answer is persisted.
    history_manager.add_messages.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_message_when_never_clean(history_manager, caplog):
    """When every attempt stays flagged, send the safe fallback message, persist
    nothing, and log fact_check_unresolved for offline review."""
    agent = EndToEndAgent(chat_id="c", user_id="u", chat_history_manager=history_manager)

    gen_count = 0

    def rag_stream(_messages):
        async def gen():
            nonlocal gen_count
            agent._rag_stream_completed = True
            gen_count += 1
            yield "still ungrounded"
        return gen()

    always_flagged = AsyncMock(
        return_value=_fact_check(flagged=True, issues=[FactCheckIssue(claim="x", explanation="bad")])
    )

    with caplog.at_level("WARNING"), \
         patch.object(agent, "_run_scope_agent", AsyncMock(return_value=_scope(True))), \
         patch.object(agent, "_run_query_agent", AsyncMock(return_value=_queries())), \
         patch.object(agent, "_run_rag_agent_stream", side_effect=rag_stream), \
         patch.object(agent, "_run_follow_up_agent", AsyncMock(return_value=_follow_up())), \
         patch("chatbot.orchestrated_agent.agent.run_fact_checker", always_flagged), \
         patch("chatbot.orchestrated_agent.agent.letter_search", AsyncMock(return_value=[])):
        chunks = [c async for c in agent.ask_stream("q")]

    from common_config import FACT_CHECK_MAX_ATTEMPTS
    assert gen_count == FACT_CHECK_MAX_ATTEMPTS, "Should exhaust the attempt cap"
    finals = [c for c in chunks if c.get("type") == "final"]
    assert len(finals) == 1 and finals[0]["text"] == FACT_CHECK_FALLBACK_MESSAGE
    assert finals[0]["citations"] == []
    # No unverified answer persisted, no follow-up, no fact_check chunk.
    history_manager.add_messages.assert_not_called()
    assert not any(c.get("type") in ("extra", "fact_check") for c in chunks)
    assert any("fact-check unresolved" in r.message.lower() for r in caplog.records)
