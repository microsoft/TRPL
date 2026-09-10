"""Verifies that the orchestrated agent picks the correct (mode, stage) prompt.

This is a safety net: if a future edit to prompts/__init__.py wires the wrong
constant for a mode/stage cell, the parametrized assertions below catch it
without needing a full end-to-end run.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.chat_history import ChatHistoryManager
from chatbot.orchestrated_agent.agent import EndToEndAgent
from chatbot.orchestrated_agent.prompts import STAGE_PROMPTS, get_prompt


MODES = ("discovery", "research", "teachers", "students")


def _mock_chat_history() -> ChatHistoryManager:
    """ChatHistoryManager whose get_chat_messages returns no prior history.

    The agent reads history before issuing the first LLM call; without this, the
    real (un-mocked) implementation would hit Redis during the test.
    """
    chm = MagicMock(spec=ChatHistoryManager)
    chm.get_chat_messages = AsyncMock(return_value=[])
    chm.add_messages = AsyncMock(return_value=None)
    return chm


def test_stage_prompts_registry_covers_all_modes_and_stages():
    """The registry must be square: every (mode, stage) is non-empty."""
    for mode in MODES:
        assert mode in STAGE_PROMPTS
        for stage in ("scope", "query", "rag", "followup"):
            prompt = get_prompt(mode, stage)
            assert prompt.strip(), f"{mode}/{stage} prompt is empty"


@pytest.mark.parametrize("mode", MODES)
def test_scope_agent_uses_per_mode_prompt(mode):
    """The scope agent's system message must be the (mode, scope) prompt."""
    import asyncio

    agent = EndToEndAgent(chat_id="c1", user_id="u1", chat_history_manager=_mock_chat_history(), mode=mode)

    captured_messages: list[list[dict]] = []

    class _FakeResponse:
        usage = None
        choices = [MagicMock(message=MagicMock(content='{"in_scope": true, "reason": "ok"}'))]

    async def _fake_parse(*, model, messages, response_format, **kwargs):
        captured_messages.append(messages)
        return _FakeResponse()

    with patch("chatbot.orchestrated_agent.agent._openai_client") as client:
        client.chat.completions.parse = _fake_parse

        async def _run():
            await agent._run_scope_agent([{"role": "user", "content": "test"}])

        asyncio.run(_run())

    assert captured_messages[0][0]["role"] == "system"
    assert captured_messages[0][0]["content"] == get_prompt(mode, "scope")


@pytest.mark.parametrize("mode", MODES)
def test_query_agent_uses_per_mode_prompt(mode):
    import asyncio

    agent = EndToEndAgent(chat_id="c1", user_id="u1", chat_history_manager=_mock_chat_history(), mode=mode)

    captured_messages: list[list[dict]] = []

    class _FakeResponse:
        usage = None
        choices = [MagicMock(message=MagicMock(content='{"historical_query": null, "book_query": null}'))]

    async def _fake_parse(*, model, messages, response_format, **kwargs):
        captured_messages.append(messages)
        return _FakeResponse()

    with patch("chatbot.orchestrated_agent.agent._openai_client") as client:
        client.chat.completions.parse = _fake_parse

        async def _run():
            await agent._run_query_agent([{"role": "user", "content": "test"}])

        asyncio.run(_run())

    assert captured_messages[0][0]["content"] == get_prompt(mode, "query")


@pytest.mark.parametrize("mode", MODES)
def test_followup_agent_uses_per_mode_prompt(mode):
    import asyncio

    agent = EndToEndAgent(chat_id="c1", user_id="u1", chat_history_manager=_mock_chat_history(), mode=mode)

    captured_messages: list[list[dict]] = []

    class _FakeResponse:
        usage = None
        choices = [MagicMock(message=MagicMock(content='{"questions": ["q1", "q2", "q3"]}'))]

    async def _fake_parse(*, model, messages, response_format, **kwargs):
        captured_messages.append(messages)
        return _FakeResponse()

    with patch("chatbot.orchestrated_agent.agent._openai_client") as client:
        client.chat.completions.parse = _fake_parse

        async def _run():
            await agent._run_follow_up_agent([{"role": "user", "content": "test"}])

        asyncio.run(_run())

    assert captured_messages[0][0]["content"] == get_prompt(mode, "followup")


@pytest.mark.parametrize("mode", MODES)
def test_rag_stream_uses_per_mode_prompt(mode):
    """The RAG stream is an async iterator. Drain it and capture the system
    prompt the stream call received."""
    import asyncio

    agent = EndToEndAgent(chat_id="c1", user_id="u1", chat_history_manager=_mock_chat_history(), mode=mode)

    captured_inputs: list[list[dict]] = []

    class _CompletedEvent:
        type = "response.completed"

        class response:
            usage = None

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        def __aiter__(self):
            async def _gen():
                yield _CompletedEvent()
            return _gen()

    def _fake_stream(*, model, input, **kwargs):
        captured_inputs.append(input)
        return _FakeStream()

    with patch("chatbot.orchestrated_agent.agent._openai_client") as client:
        client.responses.stream = _fake_stream

        async def _drain():
            async for _ in agent._run_rag_agent_stream([{"role": "user", "content": "test"}]):
                pass

        asyncio.run(_drain())

    assert captured_inputs[0][0]["role"] == "system"
    assert captured_inputs[0][0]["content"] == get_prompt(mode, "rag")
