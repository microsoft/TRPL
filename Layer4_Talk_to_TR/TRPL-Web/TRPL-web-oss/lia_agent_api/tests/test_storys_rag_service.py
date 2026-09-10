import asyncio
import os
from types import SimpleNamespace

os.environ["LIA_RUNTIME_MODE"] = "deterministic"
os.environ["LIA_ROOSEVELT_GUARDRAILS"] = "Test-only private guardrail."

from debate.models.constants import TR_SPEAKER
from debate.scenarios.story_rag import StoryIndex
from debate.services.storys_rag_service import StorysRAGService


def test_empty_story_index_falls_back_to_latest_visitor_input():
    query = "What happened in the fictional Blue Heron Compact?"
    state = SimpleNamespace(
        history=[
            SimpleNamespace(speaker=TR_SPEAKER, text="Welcome.", phase="storys"),
            SimpleNamespace(speaker="visitor", text=query, phase="storys"),
        ],
        phase_memory={"storys": {}},
    )

    class Picker:
        async def pick(self, **_kwargs):
            return {"title": None, "reason": "No story index.", "kb_query": None}

    empty_index = StoryIndex.__new__(StoryIndex)
    empty_index.entries = []
    empty_index._by_title = {}

    service = StorysRAGService(
        picker=Picker(),
        state=state,
        index=empty_index,
        history_watermark_fn=lambda: 0,
    )
    searched: list[str] = []

    async def search_kb(search_query: str) -> str:
        searched.append(search_query)
        return "retained context"

    service._search_kb = search_kb
    asyncio.run(service._retrieve(1))

    assert searched == [query]
    assert state.phase_memory["storys_rag"]["knowledge_context"] == "retained context"
