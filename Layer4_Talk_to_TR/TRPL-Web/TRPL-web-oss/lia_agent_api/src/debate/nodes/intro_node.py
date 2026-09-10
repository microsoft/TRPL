# -*- coding: utf-8 -*-
import logging

from debate.models.state import DebateState
from debate.nodes.one_off_speech_node import OneOffSpeechNode
from debate.scenarios import get_scenario

logger = logging.getLogger(f"lia.{__name__}")


class IntroNode(OneOffSpeechNode):
    """Intro node - outputs static TR speech from session initialization"""

    phase = "intro"

    def _get_text(self, state: DebateState) -> str:
        intro_text = "This is inspiring intro text."
        if state.intro_text:
            intro_text = state.intro_text

        scenario = get_scenario(state.scenario_id)
        if scenario and scenario.intro_text:
            intro_text = scenario.intro_text

        # TODO: refactor to align better with video content
        intro_text = """
Alright, enough preamble — now to the matter at hand.

""" + intro_text

        return intro_text

