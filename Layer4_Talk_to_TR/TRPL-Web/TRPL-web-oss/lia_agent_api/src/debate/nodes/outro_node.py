# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from debate.models.state import DebateState
from debate.nodes.one_off_speech_node import OneOffSpeechNode
from debate.scenarios import get_scenario

logger = logging.getLogger(f"lia.{__name__}")


class OutroNode(OneOffSpeechNode):
    """Outro node - outputs static TR speech from session initialization"""

    phase = "outro"
    done = True

    def _get_text(self, state: DebateState) -> str:
        if state.outro_text:
            return state.outro_text

        scenario = get_scenario(state.scenario_id)
        if scenario and scenario.outro_text:
            return scenario.outro_text

        logger.warning("No outro_text provided, using placeholder")
        return "This is galvanizing outro text."
