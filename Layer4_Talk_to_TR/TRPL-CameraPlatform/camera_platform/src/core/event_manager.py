# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Room event management system
"""
import logging
import time
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import Any, List, Dict, Optional, Callable
from collections import deque
from enum import Enum
import json

from ..utils.config import TRACKING_CONFIG, REID_MATCHING_CONFIG

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types"""
    # --- Low-level person events (from YOLO tracking) ---
    HAND_RAISED = "hand_raised"
    HAND_LOWERED = "hand_lowered"
    PERSON_ENTERED = "person_entered"
    PERSON_LEFT = "person_left"
    PERSON_MOVED = "person_moved"
    POSE_CHANGED = "pose_changed"

    # --- High-level scene events (from SceneOrchestrator v2) ---
    PERSON_ENTERED_ROOM = "PERSON_ENTERED_ROOM"                 # Person confirmed via entry zone
    BATCH_INVITE = "BATCH_INVITE"                                 # Debounced welcome for N new arrivals
    COLD_ROOM_INVITE = "COLD_ROOM_INVITE"                         # "Don't be shy" nudge after 30s of dead air
    MIC_ZONE_ENGAGED = "MIC_ZONE_ENGAGED"                         # Person entered mic zone, cache loaded
    MIC_ZONE_LEFT = "MIC_ZONE_LEFT"                               # Person left mic zone
    MIC_ZONE_WAITING = "MIC_ZONE_WAITING"                         # Someone is waiting behind the mic person
    HAND_RAISE_RESPONSE = "HAND_RAISE_RESPONSE"                   # Person raised hand, system responds
    PERSON_SEATED = "PERSON_SEATED"                               # Person entered a bench zone
    PERSON_STOOD_UP = "PERSON_STOOD_UP"                           # Person left a bench zone
    ROOM_DEMOGRAPHICS = "ROOM_DEMOGRAPHICS"                       # Child/adult/elderly counts changed
    SCENE_OBSERVATION = "SCENE_OBSERVATION"                       # Periodic VLM environment snapshot


@dataclass
class Event:
    """Event data structure"""
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    camera_id: str = ""
    person_id: int = -1
    data: Dict[str, Any] = field(default_factory=dict)  # Extended data

    def to_dict(self):
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "camera_id": self.camera_id,
            "person_id": self.person_id,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class PersonState:
    """Tracked person state"""
    person_id: int
    first_seen_time: float
    last_seen_time: float
    cameras_seen: set = field(default_factory=set)  # Which cameras have seen this person
    entered_room: bool = False
    left_room: bool = False
    left_room_time: Optional[float] = None

    # Current state
    is_raising_hand: bool = False
    raised_hand_side: Optional[str] = None  # "left", "right", None
    last_raise_time: Dict[str, float] = field(default_factory=dict)  # side -> time

    # Position tracking
    last_position: Dict[str, tuple] = field(default_factory=dict)  # camera_id -> (x, y)
    trajectory: List[tuple] = field(default_factory=list)  # Trajectory records

    def to_dict(self):
        return {
            "person_id": self.person_id,
            "first_seen_time": self.first_seen_time,
            "last_seen_time": self.last_seen_time,
            "cameras_seen": list(self.cameras_seen),
            "entered_room": self.entered_room,
            "left_room": self.left_room,
            "is_raising_hand": self.is_raising_hand,
            "raised_hand_side": self.raised_hand_side,
            "duration_sec": self.last_seen_time - self.first_seen_time,
            "trajectory_length": len(self.trajectory),
        }


class EventManager:
    """
    Room event management system
    - Records all detected events
    - Maintains person states
    - Supports event querying and summarization
    """

    def __init__(self, max_history: int = 1000, history_window: int = 3600,
                 max_trajectory_length: int = 500):
        """
        Args:
            max_history: Max number of historical events to keep
            history_window: History time window (seconds)
            max_trajectory_length: Max trajectory records per person
        """
        self.events = deque(maxlen=max_history)
        self.history_window = history_window
        self.max_trajectory_length = max_trajectory_length

        # Person states: "global_person_id" -> PersonState
        self.person_states: Dict[int, PersonState] = {}
        self.next_global_person_id = 0

        # Mapping from camera-local track_id to global person_id
        # {camera_id: {local_track_id: global_person_id}}
        self.camera_track_mappings: Dict[str, Dict[int, int]] = {}

        # Track last-seen time for each (camera_id, local_track_id)
        # Used to detect stale tracks and allow ReID re-matching
        self._track_last_seen: Dict[str, Dict[int, float]] = {}

        # Webhook dispatch: proactively push events to downstream systems
        # HTTP webhooks: {name: {"url": str, "events": list[str] or None, "secret": str or None}}
        self._webhooks: Dict[str, Dict] = {}
        # In-process callbacks: {name: {"callback": Callable, "events": list[str] or None}}
        self._callbacks: Dict[str, Dict] = {}
        self._webhook_lock = threading.Lock()

    def register_camera(self, camera_id: str):
        """Register a camera"""
        if camera_id not in self.camera_track_mappings:
            self.camera_track_mappings[camera_id] = {}
        if camera_id not in self._track_last_seen:
            self._track_last_seen[camera_id] = {}

    def get_or_create_person(self, camera_id: str, local_track_id: int,
                            reid_model=None, reid_feature=None,
                            allow_create: bool = True,
                            position: tuple = None) -> Optional[int]:
        """
        Get or create a global person_id.

        Args:
            camera_id: Camera ID
            local_track_id: Local track ID within this camera
            reid_model: PersonReID instance (for feature matching)
            reid_feature: Current person's ReID feature vector
            allow_create: If False and no match found, return None (deferred creation)
            position: Current person center (x, y) for position-assisted matching

        Returns:
            Global person_id, or None if allow_create=False and no match
        """
        # Update last-seen time for this track
        if camera_id not in self._track_last_seen:
            self._track_last_seen[camera_id] = {}
        self._track_last_seen[camera_id][local_track_id] = time.time()

        # Already mapped — return immediately
        if local_track_id in self.camera_track_mappings.get(camera_id, {}):
            return self.camera_track_mappings[camera_id][local_track_id]

        matched_id = None

        # Compute the global IDs that are CONCURRENTLY present and therefore
        # ineligible for re-matching. A global ID is excluded ONLY if one of its
        # OTHER local tracks is still actively updating (seen within
        # concurrent_track_threshold_sec) — i.e. that identity is genuinely on
        # screen right now via a live track, so this new track must be a
        # different person.
        #
        # Crucially we do NOT exclude a global ID whose tracks have all gone
        # silent. BotSORT churns local track IDs constantly with the live feed
        # (see max_pending_frames note in config), so when the track that kept a
        # global ID alive dies, that ID is "orphaned": the freshly-appearing
        # track is almost always the SAME person fragmenting. It must be allowed
        # to RECLAIM its old global ID. The previous logic excluded every ID
        # seen in the last 5s, which forced these orphans to mint a brand-new
        # global ID — one physical body counted as two people, triggering a
        # phantom PERSON_ENTERED_ROOM / BATCH_INVITE and an inflated room count.
        now = time.time()
        concurrent_threshold = TRACKING_CONFIG.get("concurrent_track_threshold_sec", 1.5)
        concurrent_global_ids = set()
        track_map = self.camera_track_mappings.get(camera_id, {})
        track_times = self._track_last_seen.get(camera_id, {})
        for tid, gid in track_map.items():
            if tid == local_track_id:
                continue  # never exclude our own (not-yet-mapped) track
            last_seen = track_times.get(tid, 0)
            if (now - last_seen) < concurrent_threshold:
                concurrent_global_ids.add(gid)

        # Stage 1: Pure ReID matching
        best_sim = 0.0
        if reid_model is not None and reid_feature is not None:
            matched_id, best_sim = reid_model.find_match(reid_feature, exclude_ids=concurrent_global_ids)
            if matched_id is not None:
                logger.info(
                    f"ReID match: camera {camera_id} track {local_track_id} "
                    f"-> person {matched_id} (sim={best_sim:.3f})"
                )

        # Stage 2: Position-assisted matching (when pure ReID fails)
        if matched_id is None and position is not None and reid_model is not None:
            matched_id = self._position_assisted_match(
                camera_id, position, reid_model, reid_feature,
                concurrent_global_ids, best_sim
            )

        if matched_id is not None:
            self.camera_track_mappings[camera_id][local_track_id] = matched_id
            return matched_id

        # No match found
        if not allow_create:
            gallery_size = len(reid_model._feature_gallery) if reid_model else 0
            logger.debug(
                f"Deferred: cam={camera_id} track={local_track_id} "
                f"gallery={gallery_size} best_sim={best_sim:.3f}"
            )
            return None

        # Create new person
        gallery_size = len(reid_model._feature_gallery) if reid_model else 0
        logger.info(
            f"New person created: cam={camera_id} track={local_track_id} "
            f"gallery={gallery_size} best_sim={best_sim:.3f}"
        )
        global_id = self.next_global_person_id
        self.next_global_person_id += 1
        self.person_states[global_id] = PersonState(
            person_id=global_id,
            first_seen_time=time.time(),
            last_seen_time=time.time(),
            entered_room=True,
        )
        self.camera_track_mappings[camera_id][local_track_id] = global_id
        return global_id

    def _position_assisted_match(self, camera_id: str, position: tuple,
                                  reid_model, reid_feature,
                                  exclude_ids: set, best_reid_sim: float) -> Optional[int]:
        """
        Secondary matching using position proximity + ReID similarity.
        Used when pure ReID fails (below threshold) but might be close.

        Combines:
          - ReID similarity (weight 0.6)
          - Position proximity (weight 0.4)
        Match if combined score >= 0.5 and ReID sim >= 0.35 (safety floor)
        """
        if reid_feature is None:
            return None

        REID_WEIGHT = REID_MATCHING_CONFIG.get("reid_weight", 0.6)
        POS_WEIGHT = REID_MATCHING_CONFIG.get("position_weight", 0.4)
        COMBINED_THRESHOLD = REID_MATCHING_CONFIG.get("combined_threshold", 0.5)
        MIN_REID_SIM = REID_MATCHING_CONFIG.get("min_reid_similarity", 0.35)
        MAX_DISTANCE = REID_MATCHING_CONFIG.get("max_position_distance", 300)

        best_combined = 0.0
        best_id = None

        for gid, ps in self.person_states.items():
            if gid in exclude_ids:
                continue
            if ps.left_room:
                continue

            # ReID similarity
            reid_sim = 0.0
            if reid_model is not None and reid_feature is not None:
                mean_feat = reid_model.get_mean_feature(gid)
                if mean_feat is not None:
                    reid_sim = float(np.dot(reid_feature, mean_feat))

            if reid_sim < MIN_REID_SIM:
                continue

            # Position proximity (same camera only)
            pos_score = 0.0
            last_pos = ps.last_position.get(camera_id)
            if last_pos is not None:
                dx = position[0] - last_pos[0]
                dy = position[1] - last_pos[1]
                dist = (dx * dx + dy * dy) ** 0.5
                pos_score = max(0.0, 1.0 - dist / MAX_DISTANCE)

            combined = reid_sim * REID_WEIGHT + pos_score * POS_WEIGHT

            if combined > best_combined:
                best_combined = combined
                best_id = gid
                best_reid = reid_sim
                best_pos = pos_score

        if best_id is not None and best_combined >= COMBINED_THRESHOLD:
            logger.info(
                f"Position-assisted match: cam={camera_id} -> person {best_id} "
                f"(combined={best_combined:.3f}, reid={best_reid:.3f}, pos={best_pos:.3f})"
            )
            return best_id

        return None

    def update_person_seen(self, global_person_id: int, camera_id: str, position: tuple = None):
        """
        Update the last seen time and position for a person

        Args:
            global_person_id: Global person_id
            camera_id: Camera that saw the person
            position: Position (x, y), not updated if None
        """
        if global_person_id in self.person_states:
            ps = self.person_states[global_person_id]
            ps.last_seen_time = time.time()
            ps.cameras_seen.add(camera_id)

            # Revive a person who was marked "left" but has now been re-matched
            # to this same global_id (e.g. ReID recognised them after a brief
            # drop-out). Without this, cleanup phase 2 hard-deletes them at
            # left_room_time + cleanup_delay even though they are actively
            # present — the next frame then mints a NEW global_id and the
            # orchestrator re-invites them. Mirrors the revive in merge_persons.
            if ps.left_room:
                gone_for = time.time() - (ps.left_room_time or ps.last_seen_time)
                ps.left_room = False
                ps.left_room_time = None
                logger.info(
                    f"Person {global_person_id} revived (re-matched after "
                    f"{gone_for:.0f}s marked-left) on {camera_id}"
                )

            if position is not None:
                ps.last_position[camera_id] = position
                ps.trajectory.append((time.time(), camera_id, position))
                # Prevent trajectory from growing indefinitely
                if len(ps.trajectory) > self.max_trajectory_length:
                    ps.trajectory = ps.trajectory[-self.max_trajectory_length:]

    def merge_persons(self, from_id: int, to_id: int, camera_id: str, local_track_id: int):
        """
        Merge a newly-created person into an existing person (late ReID correction).
        Reassigns the track mapping and removes the duplicate PersonState.
        """
        # Reassign track mapping
        self.camera_track_mappings[camera_id][local_track_id] = to_id

        # Merge state: update the target person's last_seen, cameras_seen
        if to_id in self.person_states:
            target = self.person_states[to_id]
            target.last_seen_time = time.time()
            if from_id in self.person_states:
                source = self.person_states[from_id]
                target.cameras_seen.update(source.cameras_seen)
                # If from was marked as left, undo that since person is back
                target.left_room = False
                target.left_room_time = None

        # Remove the duplicate person state
        self.person_states.pop(from_id, None)
        logger.info(f"Merged person {from_id} -> {to_id}")

    # ---- Webhook / Callback Management ----

    def register_webhook(self, name: str, url: str, events: List[str] = None,
                         secret: str = None):
        """
        Register HTTP webhook, POST JSON to specified URL when events occur.

        Args:
            name: Webhook name (unique identifier for management)
            url: Target URL, e.g. http://localhost:8080/on_event
            events: Which event types to receive, e.g. ["hand_raised", "person_left"]; None for all
            secret: Optional signing key, placed in X-Webhook-Secret header
        """
        with self._webhook_lock:
            self._webhooks[name] = {"url": url, "events": events, "secret": secret}
        logger.info(f"Webhook registered: {name} -> {url} (events={events or 'all'})")

    def unregister_webhook(self, name: str) -> bool:
        with self._webhook_lock:
            removed = self._webhooks.pop(name, None)
        if removed:
            logger.info(f"Webhook unregistered: {name}")
        return removed is not None

    def list_webhooks(self) -> Dict[str, Dict]:
        with self._webhook_lock:
            return {
                name: {"url": wh["url"], "events": wh["events"]}
                for name, wh in self._webhooks.items()
            }

    def register_callback(self, name: str, callback: Callable, events: List[str] = None):
        """
        Register in-process callback, directly calls callback(event_dict) when events occur.
        Suitable for same-process integration (e.g., controlling robotic arms, playing alert sounds, etc.).
        """
        with self._webhook_lock:
            self._callbacks[name] = {"callback": callback, "events": events}
        logger.info(f"Callback registered: {name} (events={events or 'all'})")

    def unregister_callback(self, name: str) -> bool:
        with self._webhook_lock:
            removed = self._callbacks.pop(name, None)
        return removed is not None

    def _dispatch_event(self, event: Event):
        """Dispatch event to all matching webhooks and callbacks (async, non-blocking)"""
        event_dict = event.to_dict()
        event_type_str = event.event_type.value

        with self._webhook_lock:
            webhooks = list(self._webhooks.items())
            callbacks = list(self._callbacks.items())

        # In-process callbacks (synchronous, but with try-catch protection)
        for name, cb in callbacks:
            if cb["events"] and event_type_str not in cb["events"]:
                continue
            try:
                cb["callback"](event_dict)
            except Exception as e:
                logger.error(f"Callback {name} error: {e}")

        # HTTP webhooks (sent in background threads to avoid blocking the main detection loop)
        for name, wh in webhooks:
            if wh["events"] and event_type_str not in wh["events"]:
                continue
            threading.Thread(
                target=self._send_webhook,
                args=(name, wh, event_dict),
                daemon=True,
            ).start()

    def _send_webhook(self, name: str, wh: Dict, event_dict: Dict,
                      max_retries: int = 2):
        """Send a single HTTP webhook with retry."""
        import urllib.request
        payload = json.dumps(event_dict, ensure_ascii=False).encode("utf-8")

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    wh["url"],
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                if wh.get("secret"):
                    req.add_header("X-Webhook-Secret", wh["secret"])
                with urllib.request.urlopen(req, timeout=5) as resp:
                    logger.debug(f"Webhook {name} delivered: {resp.status}")
                    return
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    logger.debug(f"Webhook {name} retry {attempt + 1} after error: {e}")
                else:
                    logger.warning(f"Webhook {name} failed after {max_retries} attempts ({wh['url']}): {e}")

    # ---- Event Recording ----

    def record_event(self, event: Event):
        """Record an event and dispatch to webhooks/callbacks"""
        self.events.append(event)

        # Dispatch to downstream
        self._dispatch_event(event)

        # Update person_state
        if event.person_id >= 0:
            if event.person_id not in self.person_states:
                self.person_states[event.person_id] = PersonState(
                    person_id=event.person_id,
                    first_seen_time=event.timestamp,
                    last_seen_time=event.timestamp,
                )

            ps = self.person_states[event.person_id]
            ps.last_seen_time = event.timestamp

            if event.event_type == EventType.PERSON_ENTERED:
                ps.entered_room = True
                ps.left_room = False
            elif event.event_type == EventType.PERSON_LEFT:
                ps.left_room = True
                ps.left_room_time = event.timestamp
            elif event.event_type == EventType.HAND_RAISED:
                ps.is_raising_hand = True
                side = event.data.get("side", "unknown")
                ps.raised_hand_side = side
                ps.last_raise_time[side] = event.timestamp
            elif event.event_type == EventType.HAND_LOWERED:
                ps.is_raising_hand = False

    def get_events(self,
                   event_type: Optional[EventType] = None,
                   person_id: Optional[int] = None,
                   camera_id: Optional[str] = None,
                   time_window: Optional[float] = None) -> List[Event]:
        """
        Query events

        Args:
            event_type: Filter by event type
            person_id: Filter by person ID
            camera_id: Filter by camera ID
            time_window: Time window (seconds)

        Returns:
            List of matching events
        """
        cutoff_time = time.time() - (time_window if time_window else self.history_window)
        result = []

        for event in self.events:
            if event.timestamp < cutoff_time:
                continue

            if event_type and event.event_type != event_type:
                continue
            if person_id is not None and event.person_id != person_id:
                continue
            if camera_id and event.camera_id != camera_id:
                continue

            result.append(event)

        return result

    def get_person_state(self, global_person_id: int) -> Optional[PersonState]:
        """Get person state"""
        return self.person_states.get(global_person_id)

    def get_persons_in_room(self) -> List[PersonState]:
        """Get all persons currently in the room (entered and not left)"""
        return [ps for ps in self.person_states.values() if ps.entered_room and not ps.left_room]

    def get_room_summary(self) -> Dict:
        """Get summary of current room status"""
        persons_in_room = self.get_persons_in_room()
        raising_hands = [p for p in persons_in_room if p.is_raising_hand]

        recent_events = self.get_events(time_window=10)  # Last 10 seconds

        return {
            "timestamp": time.time(),
            "total_persons_in_room": len(persons_in_room),
            "persons_raising_hands": len(raising_hands),
            "persons_raising_details": [
                {
                    "person_id": p.person_id,
                    "side": p.raised_hand_side,
                    "cameras_seen": list(p.cameras_seen),
                }
                for p in raising_hands
            ],
            "all_persons_in_room": [p.to_dict() for p in persons_in_room],
            "recent_events_count": len(recent_events),
        }

    def cleanup_old_person_states(self, person_timeout_sec: float = 30,
                                   cleanup_delay_sec: float = 60) -> List[int]:
        """
        Two-phase expiration cleanup based on last_seen_time:
        1) Unseen by any camera for > person_timeout_sec -> mark left_room, generate PERSON_LEFT event
        2) After marking left_room, wait cleanup_delay_sec -> completely remove from memory

        Args:
            person_timeout_sec: Seconds unseen before considered left
            cleanup_delay_sec: Seconds after marking left before complete cleanup

        Returns:
            List of person_ids completely cleaned up (for caller to sync-clean ReID and other external data)
        """
        current_time = time.time()
        removed_ids = []

        for person_id, ps in list(self.person_states.items()):
            time_since_seen = current_time - ps.last_seen_time

            if not ps.left_room and time_since_seen > person_timeout_sec:
                # Phase 1: Mark as left, generate event
                ps.left_room = True
                ps.left_room_time = current_time
                ps.is_raising_hand = False
                ps.raised_hand_side = None
                self.record_event(Event(
                    event_type=EventType.PERSON_LEFT,
                    person_id=person_id,
                    data={"reason": "timeout", "last_seen_ago_sec": round(time_since_seen, 1)},
                ))
                logger.info(f"Person {person_id} marked as left (unseen for {time_since_seen:.0f}s)")

            elif ps.left_room and ps.left_room_time:
                if current_time - ps.left_room_time > cleanup_delay_sec:
                    # Phase 2: Complete cleanup
                    removed_ids.append(person_id)

        # Delete person_states
        for pid in removed_ids:
            del self.person_states[pid]

        # Clean up camera_track_mappings entries pointing to deleted persons
        if removed_ids:
            removed_set = set(removed_ids)
            for cam_id in self.camera_track_mappings:
                self.camera_track_mappings[cam_id] = {
                    tid: gid for tid, gid in self.camera_track_mappings[cam_id].items()
                    if gid not in removed_set
                }
            logger.info(f"Cleaned up {len(removed_ids)} expired persons: {removed_ids}")

        return removed_ids

    def export_all_events_json(self) -> str:
        """Export all events as JSON"""
        events = [e.to_dict() for e in self.events]
        return json.dumps(events, ensure_ascii=False, indent=2)
