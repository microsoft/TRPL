# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pydantic import BaseModel, ConfigDict, computed_field, Field, field_validator, model_validator
from datetime import datetime
from typing import Literal
from debate.models.constants import Phase, TR_SPEAKER
from debate.scenarios.ids import ScenarioId


class HistoryEntry(BaseModel):
    speaker: str
    text: str
    audience: Literal["all", TR_SPEAKER]  # TODO: remove audience, or make an enum
    phase: Phase
    timestamp: datetime
    target: str | None = None  # new added for target field


class ParticipantReaction(BaseModel):
    participant_id: str
    utterance_id: str
    reaction: Literal["upvote", "downvote"]
    timestamp: datetime = Field(default_factory=datetime.now)


class Participant(BaseModel):
    name: str
    camp: str | None = None  # None during welcome phase, required for debate
    reason: str | None = None
    can_speak: bool = False
    # Welcome phase fields
    from_location: str | None = None  # visitor's location
    occupation: str | None = None  # visitor's occupation
    other_info: str | None = None  # additional info learned during welcome
    conversation_history: str | None = None  # new add conversation history with TR


class KeyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    point: str
    type: Literal["benefit", "risk"]
    camp: str


class PointMade(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_point: KeyPoint
    participant: str
    text: str


class CampDefinition(BaseModel):
    """Definition of a debate camp/position"""

    camp_id: str
    label: str
    position: str
    key_points: list[KeyPoint]


class BrainstormIdea(BaseModel):
    model_config = ConfigDict(frozen=True)

    idea_id: str
    text: str
    source_participant: str | None = None


class InputState(BaseModel):
    """Tracks the latest received inputs from participants"""

    last_spoken_text: str | None = None
    last_spoken_participant: str | None = None
    last_spoken_utterance_id: str | None = None
    raised_hands: list[str] = []
    touchscreen_responses: dict[str, str] = {}
    audio_idle: bool = True


class NoteCategory(BaseModel):
    header: str
    items: list[str] = Field(default_factory=list)

    @field_validator("header", mode="before")
    @classmethod
    def _normalize_header(cls, value) -> str:
        return str(value or "").strip()

    @field_validator("items", mode="before")
    @classmethod
    def _normalize_items(cls, value) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        cleaned: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                cleaned.append(text)
        return cleaned


class SupplementalNotes(BaseModel):
    title: str
    categories: list[NoteCategory] = Field(default_factory=list)

    @staticmethod
    def _canonical_header(header: str) -> str | None:
        lower = header.strip().lower()
        if "act now" in lower:
            return "Act Now"
        if "wait to act" in lower or lower == "wait" or lower.startswith("wait"):
            return "Wait to Act"
        return None

    @model_validator(mode="after")
    def _normalize_categories(self) -> "SupplementalNotes":
        self.title = str(self.title or "").strip() or "Discussion question"

        grouped: dict[str, list[str]] = {"Act Now": [], "Wait to Act": []}
        for category in self.categories:
            canonical = self._canonical_header(category.header)
            if canonical is None:
                continue
            grouped[canonical].extend(category.items)

        self.categories = [
            NoteCategory(header="Act Now", items=grouped["Act Now"]),
            NoteCategory(header="Wait to Act", items=grouped["Wait to Act"]),
        ]
        return self


class CameraPersonEntry(BaseModel):
    """A person detected by the camera platform."""

    person_id: int
    appearance: dict = Field(default_factory=dict)  # {top, bottom, notable}
    appearance_ready: bool = False
    in_mic_zone: bool = False
    mic_zone_visits: int = 0
    hand_raise_count: int = 0
    invited: bool = False
    invite_batch_id: str | None = None
    # Optional age-cohort label from camera ("child" / "adult"). When absent
    # the node falls back to the session default. Only "child" and "adult"
    # are recognised; anything else is treated as None.
    audience_label: str | None = None


class CameraState(BaseModel):
    """Phase-specific state for camera phase — tracks camera-detected visitors."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    persons: dict[int, CameraPersonEntry] = Field(default_factory=dict)
    event_queue: asyncio.Queue = Field(default_factory=asyncio.Queue)
    total_greeted: int = 0
    active_mic_person: int | None = None  # person currently at mic zone
    last_batch_id: str | None = None
    # Short-term memory: recent event/response pairs for LLM context
    recent_exchanges: list[dict] = Field(default_factory=list)

    # Latest VLM engagement observation (updated by ENGAGEMENT_SNAPSHOT events)
    # e.g. {"engagement": "attentive", "posture": "leaning forward", "gaze": "at screen"}
    latest_engagement: dict | None = None

    # Set to True when welcome phase should return to camera (e.g. person left mic zone)
    return_to_camera: bool = False


class WelcomeState(BaseModel):
    """Phase-specific state for welcome phase"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    current_visitor: str | None = None  # who TR is currently greeting
    current_round: int = 0  # current welcome conversation step
    max_rounds_per_visitor: int = 2  # max small talk rounds per visitor
    visitor_queue: asyncio.Queue = Field(default_factory=asyncio.Queue)
    relations: list[dict] = Field(
        default_factory=list
    )  # relationships between visitors
    visit_counts: dict[str, int] = Field(
        default_factory=dict
    )  # visitor -> number of completed visits
    
    # Async KB search: cached result from background query
    cached_kb_context: str = ""
    # The query that triggered the cached result (for logging/debugging)
    cached_kb_query: str | None = None

    # Camera-aware turn management (used by WelcomeNewNode)
    current_turn_start: float | None = None  # time.time() when current mic person started


class DebateState(BaseModel):
    # camp definitions keyed by camp_id
    camp_definitions: dict[str, CampDefinition]

    # all key points
    @computed_field
    def key_points(self) -> list[KeyPoint]:
        return list(
            set(
                key_point
                for camp_def in self.camp_definitions.values()
                for key_point in camp_def.key_points
            )
        )

    # points made by participants, in order of occurrence
    points_made: list[PointMade]

    @computed_field
    def uncovered_key_points(self) -> dict[str, list[str]]:
        return {
            camp: self.uncovered_camp_points(camp) for camp in self.camp_definitions
        }

    def uncovered_camp_points(self, camp: str) -> list[KeyPoint]:
        points = set(self.camp_definitions[camp].key_points)
        # a point for this camp may have been made by TR or by an opponent, don't filter by camp
        made = set(point.key_point for point in self.points_made)
        return list(points - made)

    def get_point(self, point: str) -> KeyPoint | None:
        for camp_def in self.camp_definitions.values():
            for key_point in camp_def.key_points:
                if key_point.point == point:
                    return key_point
        return None

    # participant name -> participant
    roster: dict[str, Participant]

    def speaking_participants(self) -> list[str]:
        return [
            participant.name
            for participant in self.roster.values()
            if participant.can_speak
        ]

    # meeting minutes as structured whiteboard notes
    meeting_notes: SupplementalNotes | None

    history: list[HistoryEntry]

    # current camp
    current_camp: str | None

    # next speaker to speak
    next_speaker: str | None

    # eligible to raise hands
    eligible_speakers: list[str]

    # scenario: pending hint to use for next hand-raise speaker prompt
    pending_hand_raise_hint: str | None = None
    pending_tr_question_summary: str | None = None

    # input state (tracks latest received inputs)
    inputs: InputState

    # participant reactions (chronological)
    reactions: list[ParticipantReaction] = Field(default_factory=list)

    # track follow-ups per participant to limit excessive follow-ups
    participant_follow_ups: dict[str, int] = Field(default_factory=dict)

    # round tracking
    current_round: int = 0
    max_rounds: int = 20

    # phase tracking
    phase: Phase = Phase.welcome

    # track one-time prompts per phase
    initial_hand_raise_prompted: set[str] = Field(default_factory=set)
    hand_raise_retry_counts: dict[str, int] = Field(default_factory=dict)

    # welcome phase state (None outside welcome phase)
    welcome_state: WelcomeState | None = None

    # camera phase state (None outside camera phase)
    camera_state: CameraState | None = None

    # intro/outro text (configurable per session)
    intro_text: str | None = None
    outro_text: str | None = None

    # per-phase agent memory (used by ScenarioNode)
    phase_memory: dict[str, dict] = Field(default_factory=dict)

    # scenario selection
    scenario_id: ScenarioId = ScenarioId.MIDNIGHT_RESERVES

    # brainstorm vote results
    brainstorm_votes: dict[str, str] = Field(
        default_factory=dict
    )  # participant -> vote

    # tracked brainstorm ideas for scenario vote
    brainstorm_ideas: list[BrainstormIdea] = Field(default_factory=list)

    # scenario decision vote results
    scenario_votes: dict[str, str] = Field(default_factory=dict)  # participant -> vote

    # track which response index (0 or 1) has been used for each question/statement ID
    # key: question/statement ID, value: index of last used response (0 for first, 1 for backup)
    canned_responses_used: dict[str, int] = Field(default_factory=dict)

    @computed_field
    def last_speaker(self) -> str | None:
        try:
            return next(
                entry.speaker
                for entry in reversed(self.history)
                if entry.speaker in self.roster
            )
        except StopIteration:
            return None
