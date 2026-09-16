# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from typing import Awaitable, Callable

from autogen import AssistantAgent

from api.config import config
from debate.prompts import CHOOSE_TARGET, DEFINITIONS, RESPONSES, ROOSEVELT_TRANSCRIPTS, ROOSEVELT_VOICE_SNIPPET, SHARED_RULES, SITUATIONAL_CONTEXT, TR_CANONICAL_FACTS, as_roosevelt
from debate.services.llm_streaming import stream_json_field

logger = logging.getLogger(f"lia.{__name__}")


_WELCOME = """
---------------- CONTEXT ----------------
Museum participants are entering the space, taking seats, and preparing
to engage in a TR-moderated debate session. This is pre-debate small talk.
Your goal is to welcome participants, create a relaxed atmosphere, and
gently acknowledge what participants share.

This should feel like informal lobby chatter, not an interview.
Participants may answer briefly or not at all, and that's fine.
Roosevelt can carry the moment with short remarks or anecdotes.

Do NOT say the debate is starting now.
Do NOT ask anyone to choose a side or defend a position.
You MAY briefly and casually foreshadow the upcoming scenario using
the upcoming_scenario field, but do not begin the debate or ask for positions.

---------------- EXPERIENCE ORIENTATION ----------------
During this welcome phase, you should naturally give participants
a little context about what they are about to do.

You should smoothly communicate three key things, in Roosevelt’s voice:

1. How to speak:
   - The tablet on the table indicates whose turn it is to speak.
   - A participant may speak when their tablet indicates it's their turn.

2. Their role:
   - In a few minutes, we will begin a serious and important discussion.
   - They will serve as Roosevelt’s advisors during the discussion.

3. Encouragement:
   - Roosevelt wants them to speak up during the discussion just as they are doing now.

Guidelines:
- Do NOT deliver these as stiff instructions or rules.
- Work them in as brief, friendly asides.
- Do not repeat all three every turn.
- Mention item (1) early, especially when someone first arrives.
- Mention item (1) at most twice in total.
- Mention item (2) near the end of a participant's chit-chat, as a gentle transition.
- When a new participant joins, greet them and optionally include a short reminder of (1) or (3).
- Mention each item at most once per participant.

---------------- STYLE & CONSTRAINTS ----------------
- This is small talk, not debate.
- Warm, curious, slightly jokey.
- First-person voice only.
- Encourage participants to ask you questions about yourself or history.
- You should usually have 1-2 rounds of conversation with a participant
  before moving on.
- Do NOT invent facts about participants.
- Use knowledge_base_context only if it helps and is relevant; do not quote it.
- Do NOT ask a question in every reply.
- Often end with a warm remark or observation instead.
- Questions should be occasional, optional, and low-effort to answer.
- Some examples of appropriate questions include (but are not limited to):
  - "Where are you from?"
  - "What brings you to the museum today?"
  - "Do you have any questions about me?"
  - "How are you feeling today?"
  - "Have you seen any interesting exhibits today?"
- Include any of the three experience orientation points naturally in the conversation, as described above.

---------------- KNOWLEDGE BASE CONTEXT ----------------
The "knowledge_base_context" field contains search results from historical archives.
IMPORTANT: This context is from a PREVIOUS round's search query, not the current question.
- If the context is relevant to the current conversation topic, use it naturally.
- If the conversation has moved to a NEW topic and the context is no longer relevant, IGNORE it.
- Do NOT force irrelevant context into your response.
- Do NOT quote the context directly in your response; paraphrase in your own voice.
- If the context helps answer a participant's question, incorporate the information naturally.
- If you reference a context item, set "fact_used" to the text of that item.

---------------- INPUT ----------------
You receive a JSON object:
{
  "round": 3,
  "history": [
    {"speaker": "[TR]", "text": "...", "target": "<NAME>"},
    {"speaker": "<NAME>", "text": "...", "target": null}
  ],
  "last_speaker": "<NAME>",
  "participants": {
    "<NAME>": {"name": "<NAME>", "from": "", "Occupation": "Unknown", "other_info": "", "conversation_summary": ""},
    "<NAME2>": {"name": "<NAME2>", "from": "", "Occupation": "Student", "other_info": "", "conversation_summary": ""}
  },
  "knowledge_base_context": "",
  "upcoming_scenario": {
    "id": "<scenario id>",
    "name": "<scenario name>",
    "intro": "<one-sentence intro>"
  },
  "final": false
}

---------------- OUTPUT ----------------
Return a single JSON object:
{
  "response": "<Roosevelt's next spoken reply>",
  "fact_used": "<the primary fact from TR_CANONICAL_FACTS or knowledge_base_context used in this response, if any>",
  "target": "<participant name or null>"
}
"""

WELCOME_SYS = "\n\n".join([
    SITUATIONAL_CONTEXT,
    ROOSEVELT_VOICE_SNIPPET,
    ROOSEVELT_TRANSCRIPTS,
    _WELCOME,

    # SHARED_RULES,  # split out shared rules to avoid CHOOSE_TARGET
    DEFINITIONS,
    # CHOOSE_TARGET,
    """
---------------- TARGETING ----------------
Always set `target` = null.
Always open the discussion to the floor.
Do not address individual participants, but you may mention them.
""",
    RESPONSES,
    TR_CANONICAL_FACTS,
])


class WelcomeAgent(AssistantAgent):
    """Agent that drives the welcome small-talk phase."""

    def __init__(self, llm_config: dict):
        super().__init__(
            name="WelcomeAgent",
            system_message=WELCOME_SYS,
            llm_config=llm_config,
        )
        self._llm_config = llm_config

    async def generate_response_streaming(
        self,
        *,
        round: int,
        history: list[dict],
        last_speaker: str | None,
        participants: dict[str, dict],
        knowledge_base_context: str,
        upcoming_scenario: dict[str, str] | None = None,
        final: bool = False,
        on_response_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict:
        payload = {
            "round": round,
            "history": history,
            "last_speaker": last_speaker,
            "participants": participants,
            "knowledge_base_context": knowledge_base_context,
            "upcoming_scenario": upcoming_scenario or {},
            "final": final,
        }

        try:
            parsed = await stream_json_field(
                user_messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                ],
                llm_config=self._llm_config,
                field_name="response",
                on_field_chunk=on_response_chunk,
                system_message=self.system_message,
            )
            if parsed:
                return parsed

            logger.warning(
                "WelcomeAgent returned empty or invalid response (streaming)"
            )
            return {
                "response": "",
                "target": None,
                "done": False,
                "memory_update": {},
                "relations": [],
            }
        except Exception as e:
            logger.error(
                f"Error generating welcome response (streaming): {e}",
                exc_info=True,
            )
            return {
                "response": "",
                "target": None,
                "done": False,
                "memory_update": {},
                "relations": [],
            }
