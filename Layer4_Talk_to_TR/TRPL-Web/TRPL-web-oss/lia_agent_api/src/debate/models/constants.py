from enum import StrEnum


class AgentType(StrEnum):
    human = "human"
    informed = "informed"
    curious_museum_visitor = "curious_museum_visitor"
    uninformed = "uninformed"
    malicious_disruptive = "malicious_disruptive"
    disengaged = "disengaged"
    struggling = "struggling"
    off_topic = "off_topic"
    on_track_needs_more = "on_track_needs_more"
    eight_year_old = "8_year_old"


class Phase(StrEnum):
    camera = "camera"
    welcome = "welcome"
    intro = "intro"
    scenario = "scenario"
    storys = "storys"
    vip = "vip"  # honored-guest variant of storys (VipNode/VipEngine)
    scenario_vote = "scenario_vote"
    brainstorm_vote = "brainstorm_vote"
    outro = "outro"


TR_SPEAKER = "[TR]"
