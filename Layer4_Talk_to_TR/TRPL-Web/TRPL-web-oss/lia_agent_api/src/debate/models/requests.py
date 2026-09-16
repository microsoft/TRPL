# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import AgentType, Phase
from debate.scenarios.ids import ScenarioId
from .state import CampDefinition, KeyPoint

logger = logging.getLogger(f"lia.{__name__}")


class PlayerRequest(BaseModel):
    """Player configuration for a debate participant"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "camp": "camp1",
                "reason": "Congress won't act swiftly enough.",
                "agent_type": None,
            },
            "example_optional_reason": {
                "camp": "camp1",
                "agent_type": None,
            },
        }
    )

    camp: str
    reason: str | None = None
    agent_type: AgentType | None = None


class CampRequest(BaseModel):
    """Camp configuration for a debate camp"""

    camp_id: str
    label: str
    position: str
    benefits: list[str]
    risks: list[str]


def get_default_camp_definitions() -> dict[str, CampDefinition]:
    """Get default hard-coded camp definitions"""
    return {
        "pressure_miners": CampDefinition(
            camp_id="pressure_miners",
            label="Pressure miners",
            position="Pressure the striking miners to return to work",
            key_points=[
                KeyPoint(point="Swift return to operations", type="benefit", camp="pressure_miners"),
                KeyPoint(point="In line with historical precedent", type="benefit", camp="pressure_miners"),
                KeyPoint(point="Shows strength and commitment to law and order", type="benefit", camp="pressure_miners"),
                KeyPoint(point="Well-understood approach", type="benefit", camp="pressure_miners"),
                KeyPoint(
                    point="let me remind you of two important facts. 1. the workers have the people on their side. If we are seen to go against them, we will lose public support, and my political opponents will seize upon this opportunity to see that we lose the next election. 2. Any action against the workers may spur violent retribution! Let us not forget the Pullman strike. The carnage and chaos that resulted from President Cleveland using force against the strikers must be avoided at all costs!",
                    type="risk",
                    camp="pressure_miners",
                ),
            ],
        ),
        "pressure_owners": CampDefinition(
            camp_id="pressure_owners",
            label="Pressure owners",
            position="Pressure the mine owners to concede to worker demands",
            key_points=[
                KeyPoint(point="In line with public opinion", type="benefit", camp="pressure_owners"),
                KeyPoint(point="In line with TR's personal opinion", type="benefit", camp="pressure_owners"),
                KeyPoint(point="Owners carry ethical responsibility for worker wellbeing", type="benefit", camp="pressure_owners"),
                KeyPoint(point="Owners have power to resolve the strike", type="benefit", camp="pressure_owners"),
                KeyPoint(point="Improved working conditions for miners", type="benefit", camp="pressure_owners"),
                KeyPoint(
                    point="We must grapple with the reality that they control the purse strings of the republican party. If we are going to achieve our goals of serving the working man and protecting our nations natural resources, we must stay in power! And that means not making enemies of these men.",
                    type="risk",
                    camp="pressure_owners",
                ),
            ],
        ),
    }


class StartDebateRequest(BaseModel):
    """Request to start a new debate session"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "wants_audio": True,
                "websocket_streaming": True,
                "audio_format": "Raw24Khz16BitMonoPcm",
                "audio_chunk_size_bytes": 2048,
                "tts_voice": "davis_dragon_hd",
                "scenario_id": "midnight_reserves",
                "players": {},
            }
        }
    )

    wants_audio: bool = Field(
        default=False,
        description="Enable TTS audio generation for this session.",
    )
    websocket_streaming: bool = Field(
        default=False,
        description="Stream partial text updates over WebSocket while the agent speaks.",
    )
    audio_format: Literal["Raw24Khz16BitMonoPcm", "Raw16Khz16BitMonoPcm"] = (
        "Raw24Khz16BitMonoPcm"
    )
    audio_chunk_size_bytes: int | None = Field(default=None, ge=2)
    tts_voice: str | None = None
    scenario_id: ScenarioId = Field(
        default=ScenarioId.MIDNIGHT_RESERVES,
        description=(
            "Scenario template id. "
            "Available values: coal_strike_guided, coal_strike_brainstorm, "
            "coal_strike, midnight_reserves, panama_canal."
        ),
    )
    players: dict[str, PlayerRequest] = Field(
        default_factory=dict,
        description=(
            "Optional initial participant roster keyed by participant name. "
            "Most clients start with an empty roster and add participants over WebSocket."
        ),
    )
    phases: list[Phase] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional explicit phase sequence. If omitted, phases default to the selected scenario."
        ),
    )
    visitor_mode: Literal["adult", "child"] | None = Field(
        default=None,
        description=(
            "Session-level default audience mode for storytelling scenarios. "
            "Per-visitor camera labels override this at runtime. "
            "Omit to inherit the scenario default (typically 'adult')."
        ),
    )

    @field_validator("audio_chunk_size_bytes")
    @classmethod
    def _validate_audio_chunk_size_bytes(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value % 2 != 0:
            raise ValueError("audio_chunk_size_bytes must be even (16-bit PCM samples)")
        return value


class JoinSessionRequest(BaseModel):
    join_code: str


class ReconnectSessionRequest(BaseModel):
    reconnect_token: str
