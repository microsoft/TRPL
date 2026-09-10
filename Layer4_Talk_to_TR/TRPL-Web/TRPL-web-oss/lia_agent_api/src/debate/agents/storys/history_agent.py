# -*- coding: utf-8 -*-
"""
History Summary Agent — summarizes each visitor conversation.

Called when a visitor leaves (hand-off). Generates a compact summary
that gets pushed to the visitor stack. TR can reference previous
visitors naturally: "Earlier today, someone asked me the same thing!"
"""
import json
import logging

from autogen import AssistantAgent
from debate.services.llm_streaming import stream_json_field

logger = logging.getLogger(f"lia.{__name__}")


_HISTORY_SYS = """\
You summarize a conversation between Theodore Roosevelt and a museum visitor.
The summary will be shown to TR in future conversations so he can reference
previous visitors naturally.

Return JSON:
{
  "name": "visitor's name, or a brief description if unknown ('a young girl', 'someone in a red jacket')",
  "topics": ["hiking", "conservation"],
  "stories_heard": ["The Day of the Seal", "Yosemite with Muir"],
  "highlight": "one sentence capturing the most memorable moment",
  "mood": "shy at first, then enthusiastic"
}

Keep it concise. The highlight should be something TR could naturally
bring up: "Earlier today, someone laughed so hard at the pony story!"
"""


class HistorySummaryAgent(AssistantAgent):
    """Summarizes a visitor conversation for the visitor stack."""

    def __init__(self, llm_config: dict):
        super().__init__(
            name="HistorySummaryAgent",
            system_message=_HISTORY_SYS,
            llm_config=llm_config,
        )
        self._llm_config = llm_config

    async def summarize(
        self,
        *,
        history: list[dict],
        memory: dict,
    ) -> dict:
        """Returns {name, topics, stories_heard, highlight, mood}."""
        payload = {
            "conversation": history[-20:],  # last 20 exchanges
            "agent_memory": memory,
        }
        try:
            result = await stream_json_field(
                user_messages=[
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
                ],
                llm_config=self._llm_config,
                field_name="name",
                on_field_chunk=None,
                system_message=self.system_message,
            )
            return result or {}
        except Exception as e:
            logger.error("HistorySummary error: %s", e, exc_info=True)
            return {}
