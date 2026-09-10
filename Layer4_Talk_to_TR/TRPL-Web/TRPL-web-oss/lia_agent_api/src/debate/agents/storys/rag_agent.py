# -*- coding: utf-8 -*-
"""
RAG Agent — retrieves relevant content from 3 sources:
  1. Story files (85 stories with full narratives)
  2. Letters DB (Azure Search — TR's letters, archives)
  3. Books DB (Azure Search — biographies, historical books)

Runs async after each visitor input. Stores results for the main
storytelling agent to use on the next turn.
"""
import asyncio
import json
import logging

from autogen import AssistantAgent
from debate.services.llm_streaming import stream_json_field

logger = logging.getLogger(f"lia.{__name__}")


_PICKER_SYS = """\
You are a Story Selector for a Theodore Roosevelt museum exhibit.
You do NOT speak to visitors. Your job is to pick which story TR
should draw on for the next conversational turn.

You receive:
- recent_history: last few exchanges between TR and the visitor
- stories_told: stories already shared (avoid repeats)
- visitor_interests: what the visitor seems interested in
- story_index: all available stories with one-line hooks (in system prompt)

Pick ONE story that:
1. Connects naturally to what the visitor just said
2. Is NOT in stories_told (this is a HARD EXCLUSION — never pick a title
   that appears in stories_told, even loosely. If every on-topic story
   is already told, pick any untold story rather than repeat.)
3. Would feel like a natural continuation

If the visitor hasn't given any hooks yet, rotate across diverse
themes turn by turn — do NOT default to pets/animals. Use the round
count or recent_history length to shift between: adventure (Rough
Riders, African safari, Amazon), grit (Milwaukee shooting, Badlands
flight), leadership (coal strike, trust-busting, Square Deal),
conservation (Yosemite, bison, Antiquities Act), family/loss (double
loss of mother and wife), and character (San Juan Hill, "daring
mighty things"). Pet/animal stories (pony, menagerie, teddy bear)
should be picked ONLY when the visitor explicitly mentions animals,
pets, children, or the "Teddy" nickname.

If no untold story fits, return title: null.

THEMATIC BRIDGING — CRITICAL:
When the visitor asks about something TR cannot discuss directly (modern
politics, current presidents, modern parties, internet culture, post-1909
events, or anything TR should refuse), DO NOT default to a pet/adventure
story. That makes TR sound evasive and dismissive.

Instead, pick a TR-era story that shares the UNDERLYING THEME:

  visitor asks about ...          →  pick a story about ...
  ──────────────────────────────────────────────────────────────────────
  a modern president              →  TR's own presidential dilemmas,
                                     trust-busting, the coal strike,
                                     the Square Deal, or Lincoln's ghost
  modern politics / parties       →  TR's fights with party bosses,
                                     leaving the Republicans, Bull Moose
  corporations / billionaires     →  trust-busting, Northern Securities,
                                     the coal strike of 1902
  wars / foreign policy           →  Rough Riders, San Juan Hill, the
                                     Panama Canal, the Great White Fleet
  celebrities / fame / media      →  TR's relationship with the press,
                                     muckrakers, getting shot in Milwaukee
  protest / unrest                →  labor tensions, the coal strike,
                                     regulating robber barons
  violence / threats              →  the Milwaukee assassination attempt,
                                     or the "big stick" principle
  anything off-topic or vague     →  something universally fun, as before

The point is the visitor feels HEARD — TR pivots from modern to
TR-era via the shared theme, not by dodging to pets.

Return JSON:
{
  "title": "exact story title from the index",
  "reason": "why this fits (1 sentence)",
  "kb_query": "search query for letters/books DB (or null if not needed)"
}

"kb_query" should be a focused search query to find relevant letters,
book excerpts, or historical details that could enrich the conversation.
Set to null if the story alone is sufficient.
"""


def get_picker_default_prompt() -> str:
    """Default picker system prompt (without the story index, which is
    appended at runtime). Used by prompt_overrides to register the
    'storys.picker' slot."""
    return _PICKER_SYS


class StoryPickerAgent(AssistantAgent):
    """Picks a story title + optionally generates a KB search query."""

    def __init__(
        self,
        llm_config: dict,
        story_index_text: str,
        system_prompt: str | None = None,
    ):
        base = system_prompt if system_prompt is not None else _PICKER_SYS
        system_message = base + "\n\n" + story_index_text
        super().__init__(
            name="StoryPickerAgent",
            system_message=system_message,
            llm_config=llm_config,
        )
        self._llm_config = llm_config

    async def pick(
        self,
        *,
        history: list[dict],
        stories_told: list[str],
        visitor_interests: list[str],
    ) -> dict:
        """Returns {title, reason, kb_query}."""
        payload = {
            "recent_history": history[-8:],
            "stories_told": stories_told,
            "visitor_interests": visitor_interests,
        }
        try:
            result = await stream_json_field(
                user_messages=[
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
                ],
                llm_config=self._llm_config,
                field_name="title",
                on_field_chunk=None,
                system_message=self.system_message,
            )
            return result or {"title": None, "reason": "", "kb_query": None}
        except Exception as e:
            logger.error("StoryPicker error: %s", e, exc_info=True)
            return {"title": None, "reason": f"error: {e}", "kb_query": None}
