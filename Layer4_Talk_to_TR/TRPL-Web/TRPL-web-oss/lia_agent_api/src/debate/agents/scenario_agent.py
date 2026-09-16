# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import os
from typing import Awaitable, Callable

from autogen import AssistantAgent

from api.config import config
from debate.prompts import KIDS_MODE_SYS
from debate.services.llm_streaming import stream_json_field

logger = logging.getLogger(f"lia.{__name__}")

# How many times to re-issue the generation when the model returns no usable
# JSON "response" field (bare-prose output). 0 disables retries.
_SCENARIO_JSON_RETRIES = int(os.getenv("SCENARIO_JSON_RETRIES", "2"))


class ScenarioAgent(AssistantAgent):
    """Generic scenario agent that uses (response, done, memory) JSON protocol."""

    def __init__(
        self,
        llm_config: dict,
        system_message: str,
        post_protocol_suffix: str = "",
    ):
        system_message += SCENARIO_MEMORY_PROTOCOL
        if config.audience == "kids":
            system_message += KIDS_MODE_SYS
        # Anything in post_protocol_suffix lands AFTER the canonical
        # output schema, so it can override or extend the schema (e.g.
        # the storys self-routing block adds a `needs_more_kb` field).
        if post_protocol_suffix:
            system_message += post_protocol_suffix
        super().__init__(
            name="ScenarioAgent",
            system_message=system_message,
            llm_config=llm_config,
        )
        self._llm_config = llm_config

    async def generate_response_streaming(
        self,
        *,
        round: int,
        max_rounds: int,
        history: list[dict],
        participants: list[str],
        last_speaker: str | None,
        memory: dict | None,
        final: bool = False,
        on_response_chunk: Callable[[str], Awaitable[None]] | None = None,
        **extra,
    ) -> dict:
        payload = {
            "round": round,
            "max_rounds": max_rounds,
            "history": history,
            "participants": participants,
            "last_speaker": last_speaker,
            "memory": memory or {},
            "final": final,
        }
        # Allow scenario-specific extensions (storys: camera context, etc.)
        for key, value in extra.items():
            if value is not None:
                payload[key] = value

        user_messages = [
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]

        # The model occasionally ignores the JSON envelope and streams bare
        # prose (no "response" field) → parse yields {} → a silent dead turn.
        # In that case the extractor never matched "response", so nothing has
        # been emitted to on_response_chunk and we can safely re-issue the
        # request. We only retry while NOTHING has streamed yet (a partial
        # JSON stream can't be cleanly re-sent). SCENARIO_JSON_RETRIES caps it
        # so a bad model window can't hang a turn.
        emitted = {"any": False}

        async def _tracked_chunk(text: str) -> None:
            emitted["any"] = True
            if on_response_chunk is not None:
                await on_response_chunk(text)

        try:
            parsed: dict = {}
            for attempt in range(1 + _SCENARIO_JSON_RETRIES):
                emitted["any"] = False
                parsed = await stream_json_field(
                    user_messages=user_messages,
                    llm_config=self._llm_config,
                    field_name="response",
                    on_field_chunk=_tracked_chunk,
                    system_message=self.system_message,
                )
                if parsed.get("response"):
                    return parsed
                if emitted["any"]:
                    # Partial content already reached the consumer; retrying
                    # would double it up. Stop and let the caller fall back.
                    break
                logger.warning(
                    "ScenarioAgent: no JSON 'response' (attempt %d/%d)%s",
                    attempt + 1,
                    1 + _SCENARIO_JSON_RETRIES,
                    "; retrying" if attempt < _SCENARIO_JSON_RETRIES else "; giving up",
                )

            return {
                "response": "",
                "done": False,
                "target": None,
                "participant_hint": None,
                "question_summary": None,
                "tracked_idea": None,
                "memory": payload["memory"],
            }
        except Exception as e:
            logger.error(
                f"Error generating scenario response (streaming): {e}",
                exc_info=True,
            )
            return {
                "response": "",
                "done": False,
                "target": None,
                "participant_hint": None,
                "question_summary": None,
                "tracked_idea": None,
                "memory": payload["memory"],
            }


SCENARIO_MEMORY_PROTOCOL = """

---------------- MEMORY PROTOCOL ----------------

You receive a JSON object:
{
  "round": 3,
  "max_rounds": 12,
  "history": [
    {"speaker": "[TR]", "text": "...", "target": "<NAME>"},
    {"speaker": "<NAME>", "text": "...", "target": null}
  ],
  "participants": ["<NAME1>", "<NAME2>"],
  "last_speaker": "<NAME>",
  "memory": {},
  "final": false
}

Return a single JSON object:
{
  "response": "<Roosevelt's next spoken reply>",
  "target": "<participant name or null>",
  "participant_hint": "<short hint about what the participant might say next, or null>",
  "question_summary": "<concise summary of TR's question/request to participants, or null>",
  "tracked_idea": "<short idea to add to brainstorming list, or null>",
  "done": false,
  "memory": { ... }
}

Rules:
- Always include "memory" in the output (even if unchanged).
- "memory" must be a JSON object (use {} if empty).
- If "final" is true or you set "done": true, wrap up and do not ask for more input.
- If "target" is null, set "participant_hint" to null.
- "question_summary" should be a short phrase (3-8 words) capturing what TR is asking.
- If TR is not asking for participant input this turn, set "question_summary" to null.
- "tracked_idea" must be either a short string or null.
- Keep "participant_hint" short and concrete (1 short phrase, second person, 8 words max).
  Examples of participant_hint: 
    - "Ask about environmental policies"
    - "Say something about the impact on miners"
"""
