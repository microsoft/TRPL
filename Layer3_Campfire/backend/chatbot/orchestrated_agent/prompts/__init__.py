"""Per-mode, per-stage system prompts for the orchestrated agent.

Layout: one module per mode (discovery/research/teachers/students) composes
its four agent-stage prompts (SCOPE_PROMPT, QUERY_PROMPT, RAG_PROMPT,
FOLLOWUP_PROMPT) from `base.py` plus a short persona blurb. The registry
below references those constants by name.

The fact-checker prompt is mode-agnostic and lives in fact_checker.py.
"""

from typing import Literal, get_args

from . import discovery, research, students, teachers
from .fact_checker import FACT_CHECKER_PROMPT as fact_checker_system_prompt

ChatMode = Literal["discovery", "research", "teachers", "students"]
Stage = Literal["scope", "query", "rag", "followup"]

DEFAULT_MODE: ChatMode = "discovery"

STAGE_PROMPTS: dict[ChatMode, dict[Stage, str]] = {
    "discovery": {
        "scope": discovery.SCOPE_PROMPT,
        "query": discovery.QUERY_PROMPT,
        "rag": discovery.RAG_PROMPT,
        "followup": discovery.FOLLOWUP_PROMPT,
    },
    "research": {
        "scope": research.SCOPE_PROMPT,
        "query": research.QUERY_PROMPT,
        "rag": research.RAG_PROMPT,
        "followup": research.FOLLOWUP_PROMPT,
    },
    "teachers": {
        "scope": teachers.SCOPE_PROMPT,
        "query": teachers.QUERY_PROMPT,
        "rag": teachers.RAG_PROMPT,
        "followup": teachers.FOLLOWUP_PROMPT,
    },
    "students": {
        "scope": students.SCOPE_PROMPT,
        "query": students.QUERY_PROMPT,
        "rag": students.RAG_PROMPT,
        "followup": students.FOLLOWUP_PROMPT,
    },
}

# Loud import-time check: every (mode, stage) cell must be present and
# non-empty. Catches a typo'd persona file before any request hits it (App
# Service won't start, instead of failing on a live user query).
_missing = [
    (mode, stage)
    for mode in get_args(ChatMode)
    for stage in get_args(Stage)
    if not STAGE_PROMPTS.get(mode, {}).get(stage, "").strip()
]
assert not _missing, f"Prompt registry is missing cells: {_missing}"


def get_prompt(mode: ChatMode, stage: Stage) -> str:
    return STAGE_PROMPTS[mode][stage]


__all__ = [
    "ChatMode",
    "Stage",
    "DEFAULT_MODE",
    "STAGE_PROMPTS",
    "get_prompt",
    "fact_checker_system_prompt",
]
