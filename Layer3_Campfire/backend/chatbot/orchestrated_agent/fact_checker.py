import logging
from typing import Any

from chatbot.VectorSearch.AzureAISearch import book_search_client, letter_search_client
from common_config import GPT_EVALUATION_MODEL, reasoning_kwargs
from openai import AsyncOpenAI
from pricing import CostAccumulator

from .models import FactCheckResult
from .prompts import fact_checker_system_prompt

logger = logging.getLogger(__name__)


def _format_cited_sources(cited_sources: list[dict[str, Any]]) -> str:
    """Render cited sources as 'Source N: ...' blocks, mirroring how the RAG
    agent built its context (agent.py:283). Uses the per-client formatter so
    the fact-checker sees the same text the answer agent saw."""
    lines: list[str] = []
    for src in cited_sources:
        index = src.get("index")
        if index is None:
            continue
        formatter = book_search_client if src.get("source") == "book" else letter_search_client
        lines.append(f"Source {index}: {formatter.format_source_for_llm(src)}")
    return "\n".join(lines)


async def run_fact_checker(
    openai_client: AsyncOpenAI,
    user_question: str,
    rag_answer: str,
    cited_sources: list[dict[str, Any]],
    cost_accumulator: CostAccumulator | None = None,
) -> FactCheckResult:
    """LLM-as-judge groundedness check.

    Returns FactCheckResult(flagged=False, issues=[]) without an LLM call when
    there are no cited sources to check against (e.g., out-of-scope refusal).
    """
    if not cited_sources:
        return FactCheckResult(flagged=False, issues=[])

    sources_block = _format_cited_sources(cited_sources)
    user_content = (
        f"Question:\n{user_question}\n\n"
        f"Answer:\n{rag_answer}\n\n"
        f"Cited sources:\n{sources_block}"
    )

    response = await openai_client.chat.completions.parse(
        model=GPT_EVALUATION_MODEL,
        messages=[
            {"role": "system", "content": fact_checker_system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=FactCheckResult,
        **reasoning_kwargs(GPT_EVALUATION_MODEL),
    )

    if cost_accumulator is not None and response.usage is not None:
        details = getattr(response.usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) or 0
        cost_accumulator.add(
            GPT_EVALUATION_MODEL,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cached_tokens=cached,
        )

    json_content = response.choices[0].message.content
    assert json_content is not None, "Fact-checker did not return any content"
    return FactCheckResult.model_validate_json(json_content)
