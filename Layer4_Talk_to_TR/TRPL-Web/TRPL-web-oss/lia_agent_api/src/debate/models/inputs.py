# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

from debate.models.state import Participant


class InputType(str, Enum):
    """Types of input messages in a debate session."""

    SPOKEN_TEXT = "spoken_text"
    TOUCHSCREEN_VOTE = "touchscreen_vote"
    HAND_RAISE = "hand_raise"
    TIMEOUT = "timeout"
    PARTICIPANT_JOINED = "participant_joined"
    PARTICIPANT_REACTION = "participant_reaction"
    CAMERA_EVENT = "camera_event"


class ParticipantJoinedInput(BaseModel):
    """Input from a participant joining the debate."""

    type: InputType = InputType.PARTICIPANT_JOINED
    participant: Participant
    timestamp: Optional[float] = None


_MAX_INPUT_LENGTH = 500  # characters — speech transcription shouldn't exceed this


class SpokenTextInput(BaseModel):
    """Input from a participant speaking."""

    type: InputType = InputType.SPOKEN_TEXT
    participant_id: str
    text: str
    utterance_id: str
    timestamp: Optional[float] = None

    @model_validator(mode="after")
    def _truncate_text(self):
        if self.text and len(self.text) > _MAX_INPUT_LENGTH:
            self.text = self.text[:_MAX_INPUT_LENGTH]
        return self


class TouchscreenVoteInput(BaseModel):
    """Input from a touchscreen vote selection."""

    type: InputType = InputType.TOUCHSCREEN_VOTE
    data: dict[str, str] = Field(
        ..., description="Mapping of participant IDs to selected options"
    )
    timestamp: Optional[float] = None


class HandRaiseInput(BaseModel):
    """Input from a participant raising their hand."""

    type: InputType = InputType.HAND_RAISE
    participant_ids: list[str]
    timestamp: Optional[float] = None


class TimeoutInput(BaseModel):
    """Input representing a phase timeout or no input received."""

    type: InputType = InputType.TIMEOUT
    phase: str = Field(..., description="Phase name that timed out")
    timestamp: Optional[float] = None


class ParticipantReactionInput(BaseModel):
    """Input from a participant reaction."""

    type: InputType = InputType.PARTICIPANT_REACTION
    participant_id: str
    utterance_id: str
    reaction: Literal["upvote", "downvote"]
    timestamp: Optional[float] = None


class CameraEventInput(BaseModel):
    """Input from the camera platform (scene orchestrator events)."""

    type: InputType = InputType.CAMERA_EVENT
    event_type: str  # BATCH_INVITE, MIC_ZONE_ENGAGED, HAND_RAISE_RESPONSE, etc.
    event_id: str
    payload: dict = Field(default_factory=dict)
    timestamp: Optional[float] = None


# Union type for all input messages
DebateInput = (
    ParticipantJoinedInput
    | SpokenTextInput
    | TouchscreenVoteInput
    | HandRaiseInput
    | TimeoutInput
    | ParticipantReactionInput
    | CameraEventInput
)
