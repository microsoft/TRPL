# -*- coding: utf-8 -*-
"""
Shared camera utilities used by CameraNode, WelcomeNewNode, and StorysNode.

Extracted to avoid code duplication across nodes that interact with
camera_platform events.
"""
import logging

from debate.models.inputs import CameraEventInput
from debate.models.state import CameraPersonEntry, CameraState, DebateState

logger = logging.getLogger(f"lia.{__name__}")


# ------------------------------------------------------------------
# Camera state update
# ------------------------------------------------------------------

def normalize_audience_label(raw) -> str | None:
    """Accept a camera-provided label and normalize to 'child'/'adult'/None."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("child", "kid", "kids", "children"):
        return "child"
    if s in ("adult", "adults"):
        return "adult"
    return None


def apply_camera_event(camera_state: CameraState, event: CameraEventInput) -> None:
    """Update camera_state from a CameraEventInput.

    Shared by StorysNode and WelcomeNewNode for passthrough camera event
    processing during conversation phases.
    """
    et = event.event_type
    p = event.payload

    if et == "PERSON_ENTERED_ROOM":
        pid = p.get("person_id")
        if pid is not None and pid not in camera_state.persons:
            camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
        label = normalize_audience_label(p.get("audience_label"))
        if label and pid in camera_state.persons:
            camera_state.persons[pid].audience_label = label

    elif et == "BATCH_INVITE":
        batch_id = p.get("batch_id")
        camera_state.last_batch_id = batch_id
        person_ids = p.get("person_ids", [])
        appearances = p.get("appearances", {})
        audience_labels = p.get("audience_labels", {}) or {}
        for pid in person_ids:
            if pid not in camera_state.persons:
                camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
            entry = camera_state.persons[pid]
            entry.invited = True
            entry.invite_batch_id = batch_id
            app = appearances.get(pid) or appearances.get(str(pid)) or {}
            if app:
                entry.appearance = app
                entry.appearance_ready = True
            label = normalize_audience_label(
                audience_labels.get(pid) or audience_labels.get(str(pid))
            )
            if label:
                entry.audience_label = label
        camera_state.total_greeted += len(person_ids)

    elif et == "MIC_ZONE_ENGAGED":
        pid = p.get("person_id")
        if pid is not None:
            if pid not in camera_state.persons:
                camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
            entry = camera_state.persons[pid]
            entry.in_mic_zone = True
            entry.mic_zone_visits = p.get("mic_zone_visits", entry.mic_zone_visits + 1)
            app = p.get("appearance")
            if app:
                entry.appearance = app
                entry.appearance_ready = p.get("appearance_ready", True)
            label = normalize_audience_label(p.get("audience_label"))
            if label:
                entry.audience_label = label
            camera_state.active_mic_person = pid

    elif et == "MIC_ZONE_LEFT":
        pid = p.get("person_id")
        if pid is not None and pid in camera_state.persons:
            camera_state.persons[pid].in_mic_zone = False
        if camera_state.active_mic_person == pid:
            camera_state.active_mic_person = None

    elif et == "HAND_RAISE_RESPONSE":
        pid = p.get("person_id")
        if pid is not None:
            if pid not in camera_state.persons:
                camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
            entry = camera_state.persons[pid]
            entry.hand_raise_count = p.get("hand_raise_count", entry.hand_raise_count + 1)
            app = p.get("appearance")
            if app:
                entry.appearance = app
                entry.appearance_ready = p.get("appearance_ready", True)

    elif et == "ENGAGEMENT_SNAPSHOT":
        camera_state.latest_engagement = p


# ------------------------------------------------------------------
# Camera context helpers
# ------------------------------------------------------------------

def build_camera_context(state: DebateState) -> list[dict]:
    """Build a summary of camera-phase exchanges for agent context."""
    cs = state.camera_state
    if not cs or not cs.recent_exchanges:
        return []
    return [
        {
            "event": ex.get("event", {}).get("event_type", ""),
            "response": ex.get("reply", {}).get("response", ""),
        }
        for ex in cs.recent_exchanges
    ]


def get_active_visitor_appearance(state: DebateState) -> dict | None:
    """Get the appearance dict of the person at the mic."""
    cs = state.camera_state
    if not cs:
        return None
    active_pid = cs.active_mic_person
    if active_pid is not None and active_pid in cs.persons:
        entry = cs.persons[active_pid]
        return {
            "person_id": entry.person_id,
            **(entry.appearance if isinstance(entry.appearance, dict) else {}),
        }
    for entry in reversed(list(cs.persons.values())):
        if entry.appearance:
            return {
                "person_id": entry.person_id,
                **(entry.appearance if isinstance(entry.appearance, dict) else {}),
            }
    return None


def count_people_waiting(state: DebateState) -> int:
    """Count how many people are in the room but NOT at the mic."""
    cs = state.camera_state
    if not cs:
        return 0
    active = cs.active_mic_person
    return sum(1 for pid in cs.persons if pid != active)


def pick_next_visitor(state: DebateState) -> CameraPersonEntry | None:
    """Pick the next person to invite (first non-active with appearance)."""
    cs = state.camera_state
    if not cs:
        return None
    active = cs.active_mic_person
    for entry in cs.persons.values():
        if entry.person_id != active and entry.appearance:
            return entry
    for entry in cs.persons.values():
        if entry.person_id != active:
            return entry
    return None


def calc_time_pressure(turn_start: float, people_waiting: int,
                       soft_limit: float = 120, hard_limit: float = 180,
                       solo_soft_limit: float = 180,
                       solo_hard_limit: float = 240) -> str:
    """Determine time pressure level for current speaker.

    Two regimes:
      - people_waiting > 0  → use the shorter (soft=120, hard=180) limits
        so the current visitor wraps up promptly and the next visitor
        gets their turn.
      - people_waiting == 0 → use the longer (solo_soft=180, solo_hard=240)
        limits. Nobody is in line, so we can be more patient, but we still
        cap eventually to keep any single visitor from dominating the
        exhibit indefinitely.
    """
    import time
    elapsed = time.time() - turn_start
    if people_waiting > 0:
        soft, hard = soft_limit, hard_limit
    else:
        soft, hard = solo_soft_limit, solo_hard_limit
    if elapsed >= hard:
        return "hard_limit"
    if elapsed >= soft:
        return "soft_limit"
    return "none"
