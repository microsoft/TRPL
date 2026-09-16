# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Per-1M-token USD pricing for Azure OpenAI models in use.

Verify against current Azure pricing before relying on these figures.
"""

from dataclasses import dataclass

# Keep in sync with model defaults in `common_config.py`. KeyError on a missing
# model is intentional — adding a new model to production forces adding its
# price here. Embedding models only bill on input — set "cached" and "output"
# to 0 so estimate_cost_usd() can stay model-agnostic.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-chat-latest":        {"input": 5.00, "cached": 0.50,  "output": 30.00},
    "gpt-5.4-mini":           {"input": 0.75, "cached": 0.075, "output": 4.50},
    "text-embedding-3-large": {"input": 0.13, "cached": 0.0,   "output": 0.0},
}


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Cost in USD. `cached_tokens` is a subset of `prompt_tokens` priced at
    the cached rate; the remainder is priced at the input rate."""
    if model not in MODEL_PRICING:
        raise KeyError(f"No pricing for model {model!r}; add it to MODEL_PRICING")
    p = MODEL_PRICING[model]
    uncached = prompt_tokens - cached_tokens
    return (
        uncached / 1_000_000 * p["input"]
        + cached_tokens / 1_000_000 * p["cached"]
        + completion_tokens / 1_000_000 * p["output"]
    )


@dataclass
class CostAccumulator:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0
    embedding_cost_usd: float = 0.0
    total_usd: float = 0.0

    def add(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.cached_tokens += cached_tokens
        self.completion_tokens += completion_tokens
        self.total_usd += estimate_cost_usd(
            model, prompt_tokens, completion_tokens, cached_tokens
        )

    def add_embedding(self, model: str, tokens: int) -> None:
        cost = estimate_cost_usd(model, prompt_tokens=tokens, completion_tokens=0)
        self.embedding_tokens += tokens
        self.embedding_cost_usd += cost
        self.total_usd += cost

    def to_payload(self) -> dict:
        return {
            "type": "cost",
            "total_usd": round(self.total_usd, 6),
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "embedding_tokens": self.embedding_tokens,
            "embedding_cost_usd": round(self.embedding_cost_usd, 6),
        }
