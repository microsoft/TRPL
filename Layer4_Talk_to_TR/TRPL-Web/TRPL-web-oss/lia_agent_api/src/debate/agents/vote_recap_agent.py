# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from autogen import AssistantAgent

from debate.prompts import ROOSEVELT_TRANSCRIPTS, ROOSEVELT_VOICE_SNIPPET, SITUATIONAL_CONTEXT, VOTE_RECAP_RULES
from debate.utils import _a_generate_reply_threaded, LLMError

logger = logging.getLogger(f"lia.{__name__}")


class VoteRecapAgent(AssistantAgent):
    """Agent that writes the final vote recap in Roosevelt's voice."""

    def __init__(self, llm_config: dict):
        system_message = "\n\n".join(
            [
                SITUATIONAL_CONTEXT,
                ROOSEVELT_VOICE_SNIPPET,
                ROOSEVELT_TRANSCRIPTS,
                VOTE_RECAP_RULES,
            ]
        )
        super().__init__(
            name="VoteRecap",
            system_message=system_message,
            llm_config=llm_config,
        )

    async def generate_recap(
        self,
        *,
        scenario_context: str,
        history: list[dict],
        vote_question: str,
        vote_options: list[dict],
        vote_counts: dict[str, int],
        outro_text: str | None,
        append_outro: bool,
    ) -> str:
        payload = {
            "scenario_context": scenario_context,
            "history": history,
            "vote_question": vote_question,
            "vote_options": vote_options,
            "vote_counts": vote_counts,
            "append_outro": append_outro,
            "outro_text": outro_text or "",
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
                text = str(reply).strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
                    text = text.strip()
                return text

            logger.warning("VoteRecap agent returned empty response")
            return ""
        except LLMError as e:
            logger.error(f"LLM error generating vote recap: {e.message}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error generating vote recap: {e}", exc_info=True)
            return ""
