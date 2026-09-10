# -*- coding: utf-8 -*-
import json
import logging
from autogen import AssistantAgent

from debate.prompts import timeout_nudge_system_prompt_for_phase
from debate.utils import _a_generate_reply_threaded

logger = logging.getLogger(f"lia.{__name__}")


class TimeoutNudgeAgent(AssistantAgent):
    """Agent that nudges participants after a quiet timeout."""

    def __init__(self, llm_config: dict, *, phase: str):
        self._phase = phase
        super().__init__(
            name=f"TimeoutNudgeAgent_{phase}",
            system_message=timeout_nudge_system_prompt_for_phase(phase),
            llm_config=llm_config,
        )
        self._llm_config = llm_config

    async def generate_response(
        self,
        *,
        last_prompt: str | None,
        participants: list[str],
        speakers: list[str],
        recent_history: list[dict],
        retry_stage: str | None = None,
        retry_index: int | None = None,
        guidance: str | None = None,
    ) -> dict:
        payload = {
            "last_prompt": last_prompt,
            "participants": participants,
            "speakers": speakers,
            "recent_history": recent_history,
            "retry_stage": retry_stage,
            "retry_index": retry_index,
            "guidance": guidance,
        }

        try:
            reply = await _a_generate_reply_threaded(
                self,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                ],
            )

            if reply:
                reply_str = str(reply).strip()
                start, end = reply_str.find("{"), reply_str.rfind("}")
                if start != -1 and end != -1 and end > start:
                    parsed = json.loads(reply_str[start : end + 1])
                    logger.debug(f"TimeoutNudgeAgent parsed response: {parsed}")
                    return parsed

            logger.warning("TimeoutNudgeAgent returned empty or invalid response")
            logger.debug(f"TimeoutNudgeAgent reply: {reply}")
            return {
                "response": "",
                "reasoning": "Failed to generate response",
            }

        except Exception as e:
            logger.error(f"Error generating timeout nudge: {e}", exc_info=True)
            return {
                "response": "",
                "reasoning": f"Error: {str(e)}",
            }
