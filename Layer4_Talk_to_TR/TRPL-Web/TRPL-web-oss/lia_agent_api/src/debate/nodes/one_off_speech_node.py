# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from debate.models.constants import TR_SPEAKER
from debate.models.state import DebateState, HistoryEntry
from debate.nodes.base import BaseNode

logger = logging.getLogger(f"lia.{__name__}")


class OneOffSpeechNode(BaseNode):
    """Base node for one-off TR speech output in a single phase."""

    phase: str = ""
    done: bool = False

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        text = self._get_text(state)
        if not text:
            logger.warning("No text provided for %s; emitting empty output", self.phase)
            text = ""

        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=text,
                audience="all",
                phase=self.phase,
                timestamp=datetime.now(),
            )
        )

        await self.io.output.send_output(
            text=text,
            waiting_for_input=False,
            done=self.done,
            phase=self.phase,
        )

        logger.info("%s completed", self.phase)
        return None

    def _get_text(self, state: DebateState) -> str:
        raise NotImplementedError
