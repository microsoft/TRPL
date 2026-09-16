# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter

from debate.models.constants import AgentType


class TouchscreenPrompt(BaseModel):
    participant_id: str
    question: str
    options: list[str]


class HandRaisePrompt(BaseModel):
    participant_id: str
    hint: str = ""


class SpokenInput(BaseModel):
    participant_id: str
    text: str
    utterance_id: str | None = None


class ParticipantInput(BaseModel):
    type: Literal["participant_input"] = "participant_input"
    spoken: SpokenInput | None = None
    raised_hands: list[str] | None = None
    touchscreen_responses: dict[str, str] | None = None


class ParticipantJoined(BaseModel):
    type: Literal["participant_joined"]
    participant_id: str = Field(alias=AliasChoices("participant_id", "name"))
    agent_type: AgentType | None = None


class ParticipantInfo(BaseModel):
    type: Literal["participant_info"]
    participant_id: str
    is_speaker: bool | None = None


class ParticipantReaction(BaseModel):
    type: Literal["participant_reaction"]
    participant_id: str
    utterance_id: str
    reaction: Literal["upvote", "downvote"]


class StartDebate(BaseModel):
    type: Literal["start_debate"]


class StartIntro(BaseModel):
    type: Literal["start_intro"]


class Ping(BaseModel):
    type: Literal["ping"]


class AudioControl(BaseModel):
    type: Literal["audio_control"]
    enabled: bool


class AudioIdle(BaseModel):
    type: Literal["audio_idle"]


class CameraEvent(BaseModel):
    """Inbound camera platform event (injected via /api/camera/events)."""

    type: Literal["camera_event"] = "camera_event"
    event_type: str  # BATCH_INVITE, MIC_ZONE_ENGAGED, etc.
    event_id: str = ""
    payload: dict = Field(default_factory=dict)


class UserInterrupt(BaseModel):
    """Barge-in signal from a front-end (livekit_worker / browser).

    Sent when VAD detects the user starting to speak while the avatar
    is mid-utterance. utterance_id is the in-flight TR utterance to
    kill; if omitted, the brain pre-flags the next-upcoming one.
    """

    type: Literal["user_interrupt"] = "user_interrupt"
    utterance_id: str | None = None
    reason: str = "barge_in"


InboundSocketMessage = Annotated[
    ParticipantInput
    | ParticipantJoined
    | ParticipantInfo
    | ParticipantReaction
    | StartDebate
    | StartIntro
    | Ping
    | AudioControl
    | AudioIdle
    | CameraEvent
    | UserInterrupt,
    Field(discriminator="type"),
]

inbound_socket_message_adapter = TypeAdapter(InboundSocketMessage)


class SpeakerPrompt(BaseModel):
    participant_id: str


class DebateOutputEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["debate_output"]
    waiting_for_input: bool = False
    text: str | None = None
    speaker_prompt: SpeakerPrompt | None = None
    touchscreen_prompts: list[TouchscreenPrompt] = Field(default_factory=list)
    streaming: bool = False
    stream_end: bool = False
    utterance_id: str | None = None


class SessionCompleteEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["session_complete"]


class CaptionTimingEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["caption_timing"]
    utterance_id: str
    tts_utterance_id: int
    audio_utterance_id: int
    boundary_type: Literal["word", "punctuation", "sentence"]
    text: str
    text_offset: int
    word_length: int
    audio_offset_ticks: int
    audio_offset_ms: float
    duration_ms: float
    phase: str | None = None


class CaptionTimingEndEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["caption_timing_end"]
    utterance_id: str
    tts_utterance_id: int
    audio_utterance_id: int
    reason: Literal["completed", "canceled", "force_closed"]


DebugAgentOutputEvent = Annotated[
    DebateOutputEvent
    | SessionCompleteEvent
    | CaptionTimingEvent
    | CaptionTimingEndEvent,
    Field(discriminator="type"),
]

debug_agent_output_event_adapter = TypeAdapter(DebugAgentOutputEvent)
