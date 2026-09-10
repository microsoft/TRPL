# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from debate.scenarios.coal_strike import COAL_STRIKE_INTRO, COAL_STRIKE_OUTRO, COAL_STRIKE_SCENARIO_SYS
from debate.scenarios.coal_strike_army_threat import (
    COAL_STRIKE_ARMY_THREAT_INTRO,
    COAL_STRIKE_ARMY_THREAT_OUTRO,
    COAL_STRIKE_ARMY_THREAT_SCENARIO_SYS,
)
from debate.scenarios.coal_strike_brainstorm import (
    COAL_STRIKE_BRAINSTORM_INTRO,
    COAL_STRIKE_BRAINSTORM_OUTRO,
    COAL_STRIKE_BRAINSTORM_SCENARIO_SYS,
)
from debate.scenarios.midnight_reserves import MIDNIGHT_RESERVES_INTRO, MIDNIGHT_RESERVES_OUTRO, MIDNIGHT_RESERVES_SCENARIO_SYS
from debate.scenarios.panama_canal import PANAMA_CANAL_INTRO, PANAMA_CANAL_OUTRO, PANAMA_CANAL_SCENARIO_SYS
from debate.scenarios.storys import STORYS_INTRO, STORYS_OUTRO, STORYS_SCENARIO_SYS
from debate.models.constants import Phase
from debate.scenarios.ids import ScenarioId


class ScenarioVoteRecap(BaseModel):
    recap_prefix: str
    outcome_for: str
    outcome_against: str
    outcome_tie: str
    for_values: list[str]
    against_values: list[str]
    dynamic_recap: bool = False
    append_outro: bool = False


class ScenarioVotePrompt(BaseModel):
    question: str
    options: list[dict]


def _default_scenario_vote_prompt() -> ScenarioVotePrompt:
    return ScenarioVotePrompt(
        question="Should we proceed with the proposed action?",
        options=[
            {"value": "yes", "label": "Yes"},
            {"value": "no", "label": "No"},
        ],
    )


def _default_scenario_vote_recap() -> ScenarioVoteRecap:
    return ScenarioVoteRecap(
        recap_prefix=(
            "We have weighed the urgency of protection and the cost of delay "
            "against the risks of overreach and the impact on Western communities. "
        ),
        outcome_for="The vote favors taking action now, and I hear that resolve.",
        outcome_against="The vote favors holding back for now, and I hear that caution.",
        outcome_tie="The room is evenly split, which speaks to the gravity of this choice.",
        for_values=["take_action"],
        against_values=["wait"],
    )


class ScenarioDefinition(BaseModel):
    scenario_id: ScenarioId
    intro_text: str
    outro_text: str
    scenario_agent_system_message: str
    phases: list[Phase]
    scenario_vote_prompt: ScenarioVotePrompt = Field(
        default_factory=_default_scenario_vote_prompt
    )
    scenario_vote_recap: ScenarioVoteRecap = Field(
        default_factory=_default_scenario_vote_recap
    )
    settings: dict = Field(default_factory=dict)


SCENARIOS: dict[ScenarioId, ScenarioDefinition] = {
    ScenarioId.COAL_STRIKE: ScenarioDefinition(
        scenario_id=ScenarioId.COAL_STRIKE,
        intro_text=COAL_STRIKE_INTRO,
        outro_text=COAL_STRIKE_OUTRO,
        scenario_agent_system_message=COAL_STRIKE_SCENARIO_SYS,
        phases=[
            Phase.welcome,
            Phase.intro,
            Phase.scenario,
            Phase.outro,
        ],
        settings={},
    ),
    ScenarioId.COAL_STRIKE_BRAINSTORM: ScenarioDefinition(
        scenario_id=ScenarioId.COAL_STRIKE_BRAINSTORM,
        intro_text=COAL_STRIKE_BRAINSTORM_INTRO,
        outro_text=COAL_STRIKE_BRAINSTORM_OUTRO,
        scenario_agent_system_message=COAL_STRIKE_BRAINSTORM_SCENARIO_SYS,
        phases=[
            Phase.welcome,
            Phase.intro,
            Phase.scenario,
            Phase.brainstorm_vote,
            Phase.outro,
        ],
        settings={},
    ),
    ScenarioId.MIDNIGHT_RESERVES: ScenarioDefinition(
        scenario_id=ScenarioId.MIDNIGHT_RESERVES,
        intro_text=MIDNIGHT_RESERVES_INTRO,
        outro_text=MIDNIGHT_RESERVES_OUTRO,
        scenario_agent_system_message=MIDNIGHT_RESERVES_SCENARIO_SYS,
        phases=[
            Phase.welcome,
            Phase.intro,
            Phase.scenario,
            Phase.scenario_vote,
        ],
        scenario_vote_prompt=ScenarioVotePrompt(
            question="Should I issue executive orders to create forest reserves now?",
            options=[
                {"value": "take_action", "label": "Create the reserves"},
                {"value": "wait", "label": "Wait and let the bill pass"},
            ],
        ),
        scenario_vote_recap=ScenarioVoteRecap(
            recap_prefix=(
                "We have weighed the urgency of protection and the cost of delay "
                "against the risks of overreach and the impact on Western communities. "
            ),
            outcome_for="The vote favors taking action now, and I hear that resolve.",
            outcome_against="The vote favors holding back for now, and I hear that caution.",
            outcome_tie=(
                "The room is evenly split, which speaks to the gravity of this choice."
            ),
            for_values=["take_action"],
            against_values=["wait"],
            dynamic_recap=True,
            append_outro=True,
        ),
        settings={},
    ),
    ScenarioId.PANAMA_CANAL: ScenarioDefinition(
        scenario_id=ScenarioId.PANAMA_CANAL,
        intro_text=PANAMA_CANAL_INTRO,
        outro_text=PANAMA_CANAL_OUTRO,
        scenario_agent_system_message=PANAMA_CANAL_SCENARIO_SYS,
        phases=[
            Phase.welcome,
            Phase.intro,
            Phase.scenario,
            Phase.scenario_vote,
        ],
        scenario_vote_prompt=ScenarioVotePrompt(
            question="Should I send in the military to Panama?",
            options=[
                {"value": "take_action", "label": "Send the military"},
                {"value": "wait", "label": "Hold back"},
            ],
        ),
        scenario_vote_recap=ScenarioVoteRecap(
            recap_prefix=(
                "We have weighed the strategic value of the canal and the cost of "
                "instability against the risks of intervention and the precedent it sets. "
            ),
            outcome_for="The vote favors sending the military, and I hear that resolve.",
            outcome_against="The vote favors holding back for now, and I hear that caution.",
            outcome_tie=(
                "The room is evenly split, which speaks to the gravity of this choice."
            ),
            for_values=["take_action"],
            against_values=["wait"],
            dynamic_recap=True,
            append_outro=True,
        ),
        settings={},
    ),
    ScenarioId.STORYS: ScenarioDefinition(
        scenario_id=ScenarioId.STORYS,
        intro_text=STORYS_INTRO,
        outro_text=STORYS_OUTRO,
        scenario_agent_system_message=STORYS_SCENARIO_SYS,
        phases=[
            Phase.storys,
        ],
        settings={},
        # To enable camera: client passes phases=["camera","storys"] in StartDebateRequest
    ),
    ScenarioId.COAL_STRIKE_ARMY_THREAT: ScenarioDefinition(
        scenario_id=ScenarioId.COAL_STRIKE_ARMY_THREAT,
        intro_text=COAL_STRIKE_ARMY_THREAT_INTRO,
        outro_text=COAL_STRIKE_ARMY_THREAT_OUTRO,
        scenario_agent_system_message=COAL_STRIKE_ARMY_THREAT_SCENARIO_SYS,
        phases=[
            Phase.welcome,
            Phase.intro,
            Phase.scenario,
            Phase.scenario_vote,
        ],
        scenario_vote_prompt=ScenarioVotePrompt(
            question="Should we threaten to use the Army to take over the mines?",
            options=[
                {"value": "take_action", "label": "Issue the threat"},
                {"value": "wait", "label": "Hold back"},
            ],
        ),
        scenario_vote_recap=ScenarioVoteRecap(
            recap_prefix=(
                "We have weighed the urgency of heat and the cost of delay "
                "against the risks of escalation and executive overreach. "
            ),
            outcome_for=(
                "The vote favors issuing the army threat, and I hear that resolve."
            ),
            outcome_against=(
                "The vote favors holding back for now, and I hear that caution."
            ),
            outcome_tie=(
                "The room is evenly split, which speaks to the gravity of this choice."
            ),
            for_values=["take_action"],
            against_values=["wait"],
            dynamic_recap=True,
            append_outro=True,
        ),
        settings={},
    ),
}


def get_scenario(scenario_id: ScenarioId) -> ScenarioDefinition | None:
    return SCENARIOS.get(scenario_id)


def get_scenario_context(scenario_id: ScenarioId) -> dict[str, str]:
    scenario = get_scenario(scenario_id)
    display_name = scenario_id.name.replace("_", " ").title()
    if not scenario:
        return {"id": scenario_id.value, "name": display_name, "intro": ""}

    intro_text = (scenario.intro_text or "").strip()
    if not intro_text:
        return {"id": scenario_id.value, "name": display_name, "intro": ""}

    first_sentence = intro_text.split(".", 1)[0].strip()
    if first_sentence and not first_sentence.endswith("."):
        first_sentence += "."

    return {
        "id": scenario_id.value,
        "name": display_name,
        "intro": first_sentence,
    }
