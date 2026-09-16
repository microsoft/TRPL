# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from autogen import AssistantAgent

from debate.utils import _a_generate_reply_threaded, LLMError
from debate.models.state import (
    CampDefinition,
    HistoryEntry,
    Participant,
    SupplementalNotes,
)

logger = logging.getLogger(f"lia.{__name__}")


class NoteTakerAgent(AssistantAgent):
    """Agent that maintains structured meeting notes."""

    def __init__(self, llm_config: dict):
        system_message = """You are summarizing a live debate in order to maintain a very concise shared whiteboard.

The whiteboard should help keep participants on track with only the top few points.
Update it based on the existing whiteboard content and the latest discussion.
Do not include points made in the introduction or framing. Include only:
- points made by non-TR participants
- points made by TR in response to non-TR participants

Guidelines:
Return JSON only with this schema:
{
  "title": "<a very short summary of the main question under discussion>",
  "categories": [
    {
      "header": "Act Now",
      "items": ["Emergency justifies intervention"]
    },
    {
      "header": "Wait to Act",
      "items": ["Risk of federal overreach"]
    }
  ]
}

Rules:
- No markdown, no code fences, no preamble.
- The title is a short question, for example:
    - Should President Roosevelt send U.S. forces to Panama?
    - Should President Roosevelt threaten to take the mines with the army?
    - Should President Roosevelt create new forest reserves?
- Always include exactly two categories in this order:
  1) Act Now
  2) Wait to Act
- Keep each item very short (max 6-8 words).
- Max 4 items per category.
- Do not invent points; use empty items when nothing relevant has been said.
- Never include names or quotes.
"""

        super().__init__(
            name="NoteTaker",
            system_message=system_message,
            llm_config=llm_config,
        )

    @staticmethod
    def _default_notes() -> SupplementalNotes:
        return SupplementalNotes(
            title="Discussion question",
            categories=[
                {"header": "Act Now", "items": []},
                {"header": "Wait to Act", "items": []},
            ],
        )

    @staticmethod
    def _parse_notes_reply(reply_text: str) -> SupplementalNotes:
        try:
            return SupplementalNotes.model_validate(json.loads(reply_text))
        except json.JSONDecodeError:
            start = reply_text.find("{")
            end = reply_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return SupplementalNotes.model_validate(
                    json.loads(reply_text[start : end + 1])
                )
            raise

    async def update_notes(
        self,
        history: list[HistoryEntry],
        existing_notes: SupplementalNotes | None,
        camp_definitions: dict[str, CampDefinition],
        roster: dict[str, Participant],
        phase: str | None,
        brainstorm_votes: dict[str, str],
        brainstorm_ideas: list[dict] | None,
        scenario_votes: dict[str, str],
    ) -> SupplementalNotes:
        """
        Update holistic meeting notes.

        Args:
            history: List of all history entries from the debate
            existing_notes: Current notes (or None if starting fresh)
            camp_definitions: Dictionary of camp definitions
            roster: Dictionary of participants
            phase: Current phase
            brainstorm_votes: Brainstorm vote results
            brainstorm_ideas: Tracked brainstorm ideas
            scenario_votes: Scenario vote results

        Returns:
            Structured notes with updated categories
        """
        history_dict = [
            {
                "speaker": entry.speaker,
                "text": entry.text,
                "audience": entry.audience,
                "phase": entry.phase,
            }
            for entry in history
            if entry.audience == "all"
        ]

        roster_dict = {
            name: {
                "name": p.name,
                "camp": p.camp,
                "reason": p.reason,
            }
            for name, p in roster.items()
        }

        payload = {
            "history": history_dict,
            "existing_notes": (existing_notes.model_dump() if existing_notes else None),
            "roster": roster_dict,
            "phase": phase,
            "brainstorm_votes": brainstorm_votes,
            "brainstorm_ideas": brainstorm_ideas or [],
            "scenario_votes": scenario_votes,
        }

        try:
            reply = await _a_generate_reply_threaded(
                self,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload),
                    }
                ],
            )

            if reply:
                reply_text = str(reply).strip()
                notes = self._parse_notes_reply(reply_text)
                logger.debug(
                    "NoteTaker updated notes: title=%s categories=%s",
                    notes.title,
                    len(notes.categories),
                )
                return notes

            logger.warning("NoteTaker returned empty response, keeping existing notes")
            return existing_notes or self._default_notes()

        except LLMError as e:
            logger.error(f"LLM error updating notes: {e.message}", exc_info=True)
            return existing_notes or self._default_notes()
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from NoteTaker: %s", e, exc_info=True)
            return existing_notes or self._default_notes()
        except Exception as e:
            logger.error(f"Error updating notes: {e}", exc_info=True)
            return existing_notes or self._default_notes()

