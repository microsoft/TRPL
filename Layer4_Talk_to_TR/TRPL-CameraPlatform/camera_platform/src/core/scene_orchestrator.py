"""
Scene Orchestrator — person-level state machine (v2).

Architecture changes from v1 (group-based):
  - No more group tracking. Each person is tracked individually.
  - Two named zones: entry_zone (door) and mic_zone (interaction point).
  - Person must first appear in entry_zone to be registered.
  - Avatar only engages when person is in mic_zone OR raises hand anywhere.
  - Invitation is debounced: multiple people entering within a window
    trigger a single batch invite based on total room occupancy.

State machine per person:
  DETECTED_IN_ENTRY  → snapshot taken, VLM processes appearance
  ENTERED            → person confirmed in room, pending invite
  IDLE               → in room but not in mic zone (avatar ignores)
  IN_MIC_ZONE        → person in mic zone, system loads cache & engages
  HAND_RAISED        → person raised hand anywhere, system responds

Events published:
  PERSON_ENTERED_ROOM         — new person confirmed via entry zone
  BATCH_INVITE                — debounced welcome for N new arrivals (with model choice)
  COLD_ROOM_INVITE            — one "don't be shy" nudge after 30s of dead air
                                (room has un-engaged people, nobody at the mic);
                                re-armed when a new visitor arrives
  MIC_ZONE_ENGAGED            — person entered mic zone, cache loaded
  MIC_ZONE_LEFT               — person left mic zone
  MIC_ZONE_WAITING            — someone is waiting behind the person at the mic
                                (geometric trigger + VLM confirmation; one-shot;
                                 payload: queue_behind + crowd_size, no ids)
  SCENE_OBSERVATION           — fixed-interval (default 5 min) coarse environment
                                read: crowd_size + queue_behind + VLM description
  HAND_RAISE_RESPONSE         — person raised hand, system responds
  PERSON_SEATED               — person entered a bench zone
  PERSON_STOOD_UP             — person left their bench zone
  ROOM_DEMOGRAPHICS           — child/adult/elderly counts changed
"""
import logging
import random
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .person_cache import PersonCache, PersonCacheEntry
from .zone_detector import ZoneDetector
from ..utils.config import SCENE_CONFIG

logger = logging.getLogger(__name__)

# publish_cb(event_type: str, payload: dict)
PublishCallback = Callable[[str, dict], None]
# snapshot_cb(person_id: int, crop_b64: str | None)  — requests VLM appearance
SnapshotCallback = Callable[[int, Optional[str]], None]

# Spoken when the VLM produced no greeting AND we have no appearance to fall
# back on (breaker open / fully degraded — no clothing info at all).
_DEFAULT_GREETING = "Welcome! Please come closer to the microphone."

# Varied welcome templates used as a SAFETY NET: when the VLM greeting is bland
# (doesn't mention the appearance) but we DO have a descriptor, we synthesize a
# clothing-referencing welcome from one of these, picked at random so repeated
# welcomes don't sound identical. `{who}` = the merged descriptor(s), e.g.
# "the person wearing a cap" / "the person in the red jacket and the kid in blue".
_WELCOME_TEMPLATES = [
    "Welcome! I see {who} — please step a little closer to the microphone.",
    "Hi {who}! Come on up to the microphone, we would love to hear from you.",
    "Great to see {who}! Step a little closer to the mic whenever you are ready.",
    "Welcome, {who}! Please come a bit closer to the microphone.",
    "Hello {who} — please step right up to the microphone.",
    "Lovely to see {who}! Please move a little closer to the mic.",
]

# Standalone welcomes for the NO-LLM path (welcome_use_llm = False). These do
# NOT reference appearance (the VLM is bypassed), so they carry no `{who}`
# placeholder. One is chosen at random per arrival so repeated welcomes still
# sound varied without any LLM/VLM call.
WELCOME_FALLBACKS = [
    "Welcome! Please come a little closer to the microphone.",
    "Hi there! Step right up to the microphone whenever you are ready.",
    "Welcome in! Come on over to the mic and say hello.",
    "Hello and welcome! Please move a bit closer to the microphone.",
    "Great to see you! Come closer to the mic so we can chat.",
    "Welcome! Step up to the microphone and let us hear from you.",
]


def _natural_join(parts: List[str]) -> str:
    """Join phrases as 'a', 'a and b', or 'a, b and c'."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _appearance_words(entry: "PersonCacheEntry") -> List[str]:
    """Tokens describing what the person is wearing (from top/bottom/notable).

    Used to decide whether a VLM greeting actually references appearance.
    Short / filler tokens ("none", "unknown", "the", colors-only) are kept
    permissively — any overlap with the greeting counts as a reference.
    """
    ap = getattr(entry, "appearance", None) or {}
    words: List[str] = []
    for key in ("notable", "top", "bottom"):
        val = str(ap.get(key) or "").strip().lower()
        if val and val not in ("none", "unknown", "n/a"):
            words.extend(w for w in val.split() if len(w) > 2)
    return words


def _greeting_references_appearance(greeting: str, entry: "PersonCacheEntry") -> bool:
    """True if `greeting` mentions any of the person's appearance tokens."""
    g = (greeting or "").lower()
    return any(w in g for w in _appearance_words(entry))


def _build_welcome(entries: List["PersonCacheEntry"], use_llm: bool = True) -> str:
    """Build a spoken welcome that is varied AND references appearance.

    When `use_llm` is False the LLM/VLM welcome is disabled: skip all
    appearance-aware logic and return one of WELCOME_FALLBACKS at random.

    Otherwise, priority:
      1. Single person whose VLM greeting already references their appearance →
         speak it verbatim (most natural, and the VLM varies it per person).
      2. Otherwise, if we have descriptor(s) → synthesize from a random
         template so the welcome still names what they are wearing and does not
         sound canned.
      3. Fully degraded (no greeting, no descriptor) → generic default.
    """
    if not use_llm:
        return random.choice(WELCOME_FALLBACKS)

    entries = [e for e in entries if e]
    descriptors = [e.descriptor for e in entries if e and e.descriptor]

    # 1. Trust a single rich VLM greeting (already appearance-aware + varied).
    if len(entries) == 1:
        e = entries[0]
        if e.greeting and _greeting_references_appearance(e.greeting, e):
            return e.greeting

    # 2. Guaranteed appearance reference, with variety, from descriptor(s).
    if descriptors:
        return random.choice(_WELCOME_TEMPLATES).format(who=_natural_join(descriptors))

    # 3. Nothing to reference — fall back to whatever greeting exists, else default.
    greetings = [e.greeting for e in entries if e and e.greeting]
    return greetings[0] if greetings else _DEFAULT_GREETING


class SceneOrchestrator:
    """
    Person-level scene orchestrator with debounced invitation.

    Call `tick(tracked_persons)` from the main thread every ~0.2s.
    """

    # multi_crop_cb(person_id: int, crop_b64_list: list[str])  — multi-frame VLM
    MultiCropCallback = Callable[[int, List[str]], None]

    # queue_check_cb(result_cb: Callable[[bool], None]) — ask VLM, on a full
    # frame, whether someone is really waiting/queuing behind the mic person.
    # The result is delivered async as a single bool via result_cb.
    QueueCheckCallback = Callable[[Callable[[bool], None]], None]

    # scene_observe_cb(result_cb: Callable[[dict], None]) — ask VLM, on a full
    # frame, for a coarse environment read. result dict: {description, queue_behind}.
    SceneObserveCallback = Callable[[Callable[[dict], None]], None]

    def __init__(self,
                 publish_cb: PublishCallback,
                 person_cache: PersonCache,
                 zone_detector: ZoneDetector,
                 snapshot_cb: Optional[SnapshotCallback] = None,
                 multi_crop_cb: Optional["SceneOrchestrator.MultiCropCallback"] = None,
                 queue_check_cb: Optional["SceneOrchestrator.QueueCheckCallback"] = None,
                 scene_observe_cb: Optional["SceneOrchestrator.SceneObserveCallback"] = None):
        self._publish = publish_cb
        self._cache = person_cache
        self._zones = zone_detector
        self._snapshot_cb = snapshot_cb
        self._multi_crop_cb = multi_crop_cb
        self._queue_check_cb = queue_check_cb
        self._scene_observe_cb = scene_observe_cb
        self._cfg = SCENE_CONFIG
        self._lock = threading.Lock()

        # Mic-queue ("someone is waiting") state. Pure-VLM: while the mic is
        # occupied, poll the VLM on an interval asking whether anyone is queuing
        # behind; emit once on the first positive. One-shot per occupied period:
        # set when announced, cleared when the mic frees.
        self._mic_waiting_announced: bool = False
        self._mic_waiting_vlm_pending: bool = False
        self._mic_waiting_vlm_requested_at: float = 0.0
        self._last_mic_queue_check: float = 0.0
        # How often to poll the VLM while the mic is occupied and no queue has
        # been confirmed yet. Internal plumbing — not a per-room knob.
        self._mic_queue_check_interval: float = 5.0
        # Safety: if a VLM queue-check never calls back (dropped/disabled), drop
        # the pending flag after this long so detection can retry. Internal
        # plumbing — not a per-room knob.
        self._mic_waiting_vlm_timeout: float = 10.0

        # Periodic scene observation (fixed-interval VLM environment snapshot,
        # to keep the system loosely synced with reality). Fires regardless of
        # occupancy. 0.0 → first one happens on the next tick after startup.
        self._scene_obs_interval: float = self._cfg.get("scene_observation_interval_sec", 300.0)
        # Grace window: if a VLM scene read is still in flight this long past the
        # interval, don't fire another (avoids piling up on a slow/stuck call).
        # Internal plumbing — not a per-room knob.
        self._scene_obs_inflight_grace: float = 15.0
        self._last_scene_obs_at: float = 0.0
        self._scene_obs_pending: bool = False

        # Debounce invite state
        self._pending_invite_ids: List[int] = []
        self._last_entry_time: float = 0.0
        self._invite_timer_started: bool = False

        # Welcome greeting source: True → appearance-aware LLM/VLM greeting (wait
        # for VLM appearances); False → no LLM, speak a random WELCOME_FALLBACKS
        # line and skip the VLM wait entirely. Env: WELCOME_USE_LLM.
        self._welcome_use_llm: bool = self._cfg.get("welcome_use_llm", True)

        # Debounce: wait this long after the last new entry before flushing a
        # BATCH_INVITE, so people arriving together get one combined greeting.
        self._debounce_base: float = self._cfg.get("invite_debounce_sec", 1.5)

        # VLM wait: extra time to wait for appearances after debounce expires.
        # Internal plumbing — not a per-room knob.
        self._vlm_wait_max: float = self._cfg.get("invite_vlm_wait_max_sec", 2.5)   # max extra seconds to wait for VLM
        self._vlm_wait_started_at: float = 0.0
        self._waiting_for_vlm: bool = False

        # Track which person IDs have been seen in entry zone (to avoid re-entry)
        self._entered_via_entry_zone: set = set()

        # Entry commitment gate: candidate person_id → first_continuous_time
        # inside the entry zone. A candidate is only promoted to a real arrival
        # once it has dwelled continuously for entry_dwell_sec — rejecting
        # transient YOLO glitches / walk-pasts.
        self._entry_candidates: Dict[int, float] = {}
        self._entry_dwell_sec: float = self._cfg.get("entry_dwell_sec", 0.4)

        # --- Entry direction gate (inbound vs outbound at the door) ---
        # The entry zone lies on the doorway, so a person crosses it both when
        # arriving AND when leaving. We additionally check direction of travel:
        # only an inbound crossing (moving "into the room", per the inward unit
        # vector resolved by the ZoneDetector from the annotated arrow or the
        # entry→mic centroid) earns a welcome. _recent_pos holds a short
        # normalized-position trajectory PER tracked id (even before admission),
        # so the approach direction is available the instant they reach the door.
        self._recent_pos: Dict[int, Deque[Tuple[float, Tuple[float, float]]]] = {}
        self._dir_gate_enabled: bool = self._cfg.get("entry_direction_gate_enabled", True)
        self._dir_window_sec: float = self._cfg.get("entry_direction_window_sec", 1.2)
        self._dir_min_disp: float = self._cfg.get("entry_direction_min_disp", 0.03)
        self._dir_fallback_welcome: bool = (
            str(self._cfg.get("entry_direction_fallback", "welcome")).lower() != "suppress"
        )

        # Cold-room nudge: fire one COLD_ROOM_INVITE after the room has been
        # continuously "cold" (un-engaged people present, nobody at the mic) for
        # cold_room_threshold_sec, then stay quiet until the population changes.
        self._cold_threshold_sec: float = self._cfg.get("cold_room_threshold_sec", 30.0)
        self._cold_since: Optional[float] = None   # when the room first went cold
        self._cold_nudge_fired: bool = False       # one-shot guard for this cold period

        # Welcome rate limit — at most `_welcome_max` spoken welcomes per rolling
        # `_welcome_window` seconds (shared by BATCH_INVITE + COLD_ROOM_INVITE).
        self._welcome_window: float = self._cfg.get("welcome_window_sec", 30.0)
        self._welcome_max: int = self._cfg.get("welcome_max_per_window", 1)
        self._welcome_times: Deque[float] = deque()

        # Mic zone confirm: person_id → first_continuous_time_in_mic
        self._mic_zone_enter_time: Dict[int, float] = {}
        # Mic zone leave grace: person_id → first_continuous_time_out_of_mic.
        # Used to debounce MIC_ZONE_LEFT so a single stray frame (bent over,
        # tracker jitter) doesn't instantly end the conversation.
        self._mic_zone_leave_time: Dict[int, float] = {}
        # Person IDs detected in the PREVIOUS tick. Used so the mic-leave grace
        # measures CONTINUOUS detected-out time: if a person reappears after a
        # detection gap (YOLO miss while they stand still), restart the window
        # instead of counting the gap as "outside the mic".
        self._prev_tracked_ids: set = set()

        # Periodic crop collection: person_id → last crop timestamp.
        # Internal plumbing (multi-frame VLM cadence) — not a per-room knob.
        self._last_crop_time: Dict[int, float] = {}
        self._crop_interval_sec: float = 10.0  # collect a new crop every N seconds


        # Demographics emission state
        self._demographics_interval: float = self._cfg.get("demographics_publish_interval_sec", 10.0)
        self._last_demographics_emit_at: float = 0.0
        self._last_demographics_snapshot: Optional[dict] = None

        # Mic confirm: continuous dwell required inside mic_zone before we
        # declare MIC_ZONE_ENGAGED. A walkthrough won't accumulate this dwell,
        # so it's filtered without any velocity heuristics.
        self._mic_confirm_sec: float = self._cfg.get("mic_zone_confirm_sec", 0.7)
        # Debounce for MIC_ZONE_LEFT: the person must be continuously OUT of
        # the mic zone for this many seconds before we declare them gone.
        self._mic_leave_sec: float = self._cfg.get("mic_zone_leave_sec", 2.0)

    def tick(self, tracked_persons: Dict[int, dict]) -> None:
        """
        Drive the state machine for all currently tracked persons.

        Args:
            tracked_persons: {global_person_id: {
                "center": (cx, cy),
                "bbox": [x1, y1, x2, y2],
                "frame_w": int,
                "frame_h": int,
                "is_raising_hand": bool,
                "crop_b64": str | None,  (only on first detection)
            }}
        """
        with self._lock:
            self._tick_inner(tracked_persons)

    def _tick_inner(self, tracked_persons: Dict[int, dict]) -> None:
        now = time.time()

        for pid, info in tracked_persons.items():
            cx, cy = info["center"]
            fw, fh = info["frame_w"], info["frame_h"]
            is_raising_hand = info.get("is_raising_hand", False)
            crop_b64 = info.get("crop_b64")
            frame_count = info.get("frame_count", 0)

            # Record normalized position for the entry direction gate — every
            # tracked person, every tick, BEFORE the admission check, so a fresh
            # track already has approach history when it reaches the door.
            if fw > 0 and fh > 0:
                buf = self._recent_pos.get(pid)
                if buf is None:
                    buf = deque(maxlen=40)
                    self._recent_pos[pid] = buf
                buf.append((now, (cx / fw, cy / fh)))

            # Mic-zone membership — computed ONCE per tick and reused below.
            # is_in_zone() maintains per-person enter/exit hysteresis, so calling
            # it twice in a tick would corrupt that state.
            # Mic membership: test multiple points down the body (torso → feet)
            # so standing on a floor-level zone registers even though the torso
            # point sits higher in the image. Falls back to the single center
            # point if no bbox is available.
            bbox = info.get("bbox")
            if bbox:
                in_mic = self._zones.is_bbox_in_zone("mic_zone", bbox, fw, fh, person_id=pid)
            else:
                in_mic = self._zones.is_in_zone("mic_zone", cx, cy, fw, fh, person_id=pid)

            # --- Entry zone check (commitment gate vs YOLO glitches) ---
            # Touching the entry zone is not enough: the candidate must dwell
            # there continuously for entry_dwell_sec before we register a real
            # arrival.
            if pid not in self._entered_via_entry_zone:
                if self._zones.is_in_zone("entry_zone", cx, cy, fw, fh, person_id=pid):
                    first_in_zone = self._entry_candidates.setdefault(pid, now)
                    dwell = now - first_in_zone
                    if dwell >= self._entry_dwell_sec:
                        # Direction gate: the entry zone sits on the doorway, so a
                        # person crosses it both arriving AND leaving. Only an
                        # inbound crossing (moving into the room) earns a welcome;
                        # an outbound crossing (heading for the door) is a
                        # departure and must NOT re-trigger BATCH_INVITE.
                        if self._is_inbound_crossing(pid, now):
                            self._handle_new_entry(pid, crop_b64, now, via_entry_zone=True)
                            self._entry_candidates.pop(pid, None)
                        # else: outbound/ambiguous — keep the candidate in place.
                        # A genuine arrival that paused will be admitted on a
                        # later tick once it shows inbound motion; an outbound
                        # exit simply never passes the inbound test, so it is
                        # silently ignored (no welcome).
                else:
                    # Left the zone before committing — reset so a transient
                    # glitch / walk-past doesn't accumulate dwell across visits.
                    self._entry_candidates.pop(pid, None)

                # Fallback admission: a person can reach the mic zone (or raise a
                # hand) without ever passing through the entry zone — they were
                # already in the room, or the entry zone simply isn't on their
                # path. The engagement rule is "in mic zone OR hand raised", so
                # don't leave that unreachable: admit them on the spot (with a
                # small anti-phantom frame guard) so the mic/hand state machine runs.
                if (not self._cache.has(pid)
                        and frame_count >= 2
                        and (in_mic or is_raising_hand)):
                    self._handle_new_entry(pid, crop_b64, now, via_entry_zone=False)
                    self._entry_candidates.pop(pid, None)

            # Skip further processing if person hasn't entered the room yet
            if not self._cache.has(pid):
                continue

            # Update seen time + trajectory
            self._cache.update_seen(pid)
            self._cache.update_position(pid, cx, cy, now)

            # --- Periodic crop collection (for multi-frame VLM) ---
            crop_b64 = info.get("crop_b64")
            if crop_b64:
                last_crop = self._last_crop_time.get(pid, 0.0)
                if now - last_crop >= self._crop_interval_sec:
                    self._cache.add_crop(pid, crop_b64, now)
                    self._last_crop_time[pid] = now

            # --- Mic zone check (adaptive confirm based on velocity) ---
            # in_mic was computed once at the top of this person's tick.
            entry = self._cache.get(pid)

            if in_mic:
                # Came back inside (or never left) — cancel any pending
                # "leave" timer so a brief step-out doesn't end the chat.
                self._mic_zone_leave_time.pop(pid, None)

                if pid not in self._mic_zone_enter_time:
                    self._mic_zone_enter_time[pid] = now

                dwell = now - self._mic_zone_enter_time[pid]

                # Single dwell confirm: a person must linger in the mic zone for
                # `mic_zone_confirm_sec` before we declare MIC_ZONE_ENGAGED. A
                # walkthrough never accumulates enough dwell, so it's filtered
                # out naturally without any velocity heuristics.
                confirm_sec = self._mic_confirm_sec

                if dwell >= confirm_sec and not entry.in_mic_zone:
                    self._cache.enter_mic_zone(pid)

                    # Trigger multi-crop VLM re-analysis if we have >1 crop
                    if len(entry.crop_history) > 1 and self._multi_crop_cb is not None:
                        crops = [c for _, c in entry.crop_history]
                        logger.info(
                            f"Orchestrator: triggering multi-crop VLM for P{pid} "
                            f"({len(crops)} frames)"
                        )
                        self._multi_crop_cb(pid, crops)

                    self._publish("MIC_ZONE_ENGAGED", {
                        "person_id": pid,
                        "appearance": entry.appearance,
                        "appearance_ready": entry.appearance_ready,
                        "mic_zone_visits": entry.mic_zone_visits,
                        "hand_raise_count": entry.hand_raise_count,
                        "room_person_count": self._cache.room_person_count(),
                    })
            else:
                # Out of mic zone. Start / continue a debounce timer — only
                # declare MIC_ZONE_LEFT after they've been CONTINUOUSLY detected
                # outside for `mic_zone_leave_sec`. Keeps momentary dips (tie
                # shoe, tracker hiccup) from cutting the conversation.
                self._mic_zone_enter_time.pop(pid, None)
                if entry.in_mic_zone:
                    # Restart the grace window on (a) the first out-frame, or
                    # (b) reappearance after a detection gap — a YOLO miss while
                    # the person stands still must NOT count as time-outside,
                    # otherwise one stray out-frame + a gap fires a false LEFT.
                    if (pid not in self._mic_zone_leave_time
                            or pid not in self._prev_tracked_ids):
                        self._mic_zone_leave_time[pid] = now
                    out_dwell = now - self._mic_zone_leave_time[pid]
                    if out_dwell >= self._mic_leave_sec:
                        self._cache.leave_mic_zone(pid)
                        self._mic_zone_leave_time.pop(pid, None)
                        logger.info(
                            f"Orchestrator: MIC_ZONE_LEFT P{pid} after "
                            f"{out_dwell:.1f}s continuously detected outside mic"
                        )
                        self._publish("MIC_ZONE_LEFT", {
                            "person_id": pid,
                        })
                else:
                    # Not in mic and wasn't in mic — nothing to debounce.
                    self._mic_zone_leave_time.pop(pid, None)

            # --- Bench zone seating ---
            self._update_bench_zone(pid, cx, cy, fw, fh, now)

            # --- UX interaction signals ---
            self._refresh_ux_state(pid, in_mic)

            # --- Hand raise (anywhere in room) ---
            if is_raising_hand and entry.scene_state != "hand_raised":
                self._cache.record_hand_raise(pid)
                self._publish("HAND_RAISE_RESPONSE", {
                    "person_id": pid,
                    "appearance": entry.appearance,
                    "appearance_ready": entry.appearance_ready,
                    "in_mic_zone": entry.in_mic_zone,
                    "hand_raise_count": entry.hand_raise_count,
                    "room_person_count": self._cache.room_person_count(),
                })
            elif not is_raising_hand and entry.scene_state == "hand_raised":
                self._cache.clear_hand_raise(pid)

        # --- Debounced invite check ---
        self._check_invite_flush(now)

        # --- Periodic demographics emission ---
        self._check_demographics(now)

        # --- Cold-room nudge ("don't be shy" after 30s of dead air) ---
        self._check_cold_room(now)

        # --- Mic queue ("someone is waiting" behind the mic person) ---
        self._check_mic_queue(now)

        # --- Periodic scene observation (fixed-interval VLM reality sync) ---
        self._check_scene_observation(now)

        # Drop entry candidates that vanished before committing, so a phantom
        # that flickers out of tracking doesn't linger in the candidate map.
        if self._entry_candidates:
            self._entry_candidates = {
                p: t for p, t in self._entry_candidates.items() if p in tracked_persons
            }

        # Drop direction buffers for tracks that vanished a while ago. Admitted
        # persons are cleared in the cache-cleanup loop below; this catches door
        # hoverers that were never admitted (so the dict can't grow unbounded).
        if self._recent_pos:
            stale_cutoff = max(self._dir_window_sec * 2.0, 3.0)
            self._recent_pos = {
                p: b for p, b in self._recent_pos.items()
                if p in tracked_persons or (b and now - b[-1][0] <= stale_cutoff)
            }

        # --- Cache cleanup (35s TTL) ---
        removed = self._cache.cleanup()
        for pid in removed:
            self._entered_via_entry_zone.discard(pid)
            self._entry_candidates.pop(pid, None)
            self._mic_zone_enter_time.pop(pid, None)
            self._mic_zone_leave_time.pop(pid, None)
            self._last_crop_time.pop(pid, None)
            self._recent_pos.pop(pid, None)
            self._zones.clear_person(pid)

        # Remember who was detected this tick so the next tick can tell whether
        # a person reappeared after a detection gap (mic-leave continuity).
        self._prev_tracked_ids = set(tracked_persons.keys())

    def _is_inbound_crossing(self, person_id: int, now: float) -> bool:
        """True if the person is crossing the entry zone INTO the room (arrival).

        Estimates direction of travel from the recent-position buffer (net
        displacement over the configured window) and dots it against the inward
        unit vector resolved by the ZoneDetector (annotated arrow, else the
        entry→mic centroid). Returns the configured fallback when:
          - the gate is disabled,
          - no inward direction is available (→ legacy position-only behavior),
          - or the motion is too small to call (person standing in the doorway).

        Because direction is recomputed fresh every time, this is immune to the
        track-id churn / cache-TTL expiry that let departures re-trigger welcomes
        under the old position-only logic.
        """
        if not self._dir_gate_enabled:
            return True
        inward = self._zones.get_entry_inward_unit()
        if inward is None:
            return True  # nothing to gate on → behave like before (welcome)

        buf = self._recent_pos.get(person_id)
        if not buf or len(buf) < 2:
            return self._dir_fallback_welcome

        # Oldest sample still within the window → latest sample.
        old = None
        for ts, p in buf:
            if now - ts <= self._dir_window_sec:
                old = p
                break
        if old is None:
            old = buf[0][1]
        new = buf[-1][1]

        mx, my = new[0] - old[0], new[1] - old[1]
        disp = (mx * mx + my * my) ** 0.5
        if disp < self._dir_min_disp:
            # Too little net motion to trust a direction (loitering / just
            # appeared and stood still). Defer to the configured fallback.
            return self._dir_fallback_welcome

        score = mx * inward[0] + my * inward[1]
        inbound = score > 0.0
        if not inbound:
            logger.info(
                f"Orchestrator: suppressed entry welcome for person {person_id} "
                f"— outbound crossing (motion·inward={score:+.3f}, disp={disp:.3f})"
            )
        return inbound

    def _handle_new_entry(self, person_id: int, crop_b64: Optional[str], now: float,
                          via_entry_zone: bool = True) -> None:
        """Admit a newly-tracked person to the room and start their state machine.

        Args:
            via_entry_zone: True when the person was committed by dwelling in the
                entry zone (a genuine "walked in the door" arrival). False when
                they were admitted by the mic/hand fallback — already in the room
                (or a re-acquired track) and merely reached the mic / raised a
                hand without ever crossing the entry zone.

        DESIGN RULE: only entry-zone arrivals earn a BATCH_INVITE welcome.
        Fallback admissions still get a cache entry and run the full mic/hand
        state machine (so we greet them at the mic, count them, etc.), but they
        must NOT seed the pending-invite batch — otherwise a person who was
        already present, or a track that fragmented and got re-admitted via the
        mic fallback, would generate a spurious welcome.
        """
        self._entered_via_entry_zone.add(person_id)
        entry = self._cache.create_entry(person_id, crop_b64)

        # Store initial crop in history
        if crop_b64:
            self._cache.add_crop(person_id, crop_b64, now)
            self._last_crop_time[person_id] = now

        # Request VLM appearance snapshot
        if self._snapshot_cb is not None:
            self._snapshot_cb(person_id, crop_b64)

        self._publish("PERSON_ENTERED_ROOM", {
            "person_id": person_id,
            "room_person_count": self._cache.room_person_count(),
            "via_entry_zone": via_entry_zone,
        })

        if not via_entry_zone:
            logger.info(
                f"Orchestrator: person {person_id} admitted via mic/hand fallback "
                f"(already in room — no BATCH_INVITE; "
                f"room count: {self._cache.room_person_count()})"
            )
            return

        # --- Entry-zone arrival only: seed the debounced welcome batch ---
        self._pending_invite_ids.append(person_id)
        self._last_entry_time = now
        self._invite_timer_started = True
        self._waiting_for_vlm = False  # reset VLM wait on new entry

        # Re-arm the cold-room timer: a fresh arrival earns a fresh wait before
        # the next cold nudge (and clears the one-shot guard so it can fire again).
        self._cold_since = None
        self._cold_nudge_fired = False

        logger.info(
            f"Orchestrator: person {person_id} entered via entry zone "
            f"(room count: {self._cache.room_person_count()}, "
            f"pending invite: {len(self._pending_invite_ids)})"
        )

    def _all_appearances_ready(self, person_ids: List[int]) -> bool:
        """Check if VLM has returned appearance for all pending persons."""
        for pid in person_ids:
            entry = self._cache.get(pid)
            if entry and not entry.appearance_ready:
                return False
        return True

    def _welcome_allowed(self, now: float) -> bool:
        """True if a spoken welcome may fire now (rate limit not exceeded).

        At most `_welcome_max` welcomes per rolling `_welcome_window` seconds,
        shared across BATCH_INVITE and COLD_ROOM_INVITE. Does NOT record — call
        _record_welcome() when one actually fires.
        """
        cutoff = now - self._welcome_window
        while self._welcome_times and self._welcome_times[0] < cutoff:
            self._welcome_times.popleft()
        return len(self._welcome_times) < self._welcome_max

    def _record_welcome(self, now: float) -> None:
        self._welcome_times.append(now)

    def _check_invite_flush(self, now: float) -> None:
        """
        Flush pending invites after dynamic debounce window expires.

        Two-phase check:
        1. Wait for the debounce window after the last entry.
        2. If debounce expired but some VLM appearances missing, wait up to
           vlm_wait_max extra seconds for them to arrive.
        """
        if not self._invite_timer_started or not self._pending_invite_ids:
            return

        # Phase 1: debounce not yet expired
        if now - self._last_entry_time < self._debounce_base:
            return

        # Phase 2: debounce expired, check VLM readiness.
        # When the welcome does NOT go through the LLM, the greeting is a canned
        # fallback that needs no appearance — skip the wait and flush right away.
        person_ids = list(self._pending_invite_ids)
        if self._welcome_use_llm and not self._all_appearances_ready(person_ids):
            if not self._waiting_for_vlm:
                self._waiting_for_vlm = True
                self._vlm_wait_started_at = now
                logger.info(
                    f"Orchestrator: debounce expired, waiting for VLM appearances "
                    f"({sum(1 for pid in person_ids if self._cache.get(pid) and not self._cache.get(pid).appearance_ready)}"
                    f"/{len(person_ids)} pending)"
                )
                return

            # Still waiting — check if we've exceeded the max wait
            if now - self._vlm_wait_started_at < self._vlm_wait_max:
                return
            else:
                logger.info(
                    "Orchestrator: VLM wait timeout, flushing with available appearances"
                )

        # Flush
        self._pending_invite_ids.clear()
        self._invite_timer_started = False
        self._waiting_for_vlm = False

        batch_id = self._cache.new_batch_id()
        for pid in person_ids:
            self._cache.mark_invited(pid, batch_id)

        # Welcome rate limit: mark them invited (so they don't re-trigger) but
        # stay silent if we've already hit the cap for this window.
        if not self._welcome_allowed(now):
            logger.info(
                f"Orchestrator: BATCH_INVITE suppressed (welcome rate limit "
                f"{self._welcome_max}/{self._welcome_window:.0f}s) persons={person_ids}"
            )
            return
        self._record_welcome(now)

        total_in_room = self._cache.room_person_count()
        model_choice = "multi" if total_in_room > 1 else "solo"

        # Build the spoken welcome line. With the LLM on this is varied and
        # appearance-aware: the VLM folds a per-person `greeting` (+ short
        # `descriptor`) into the appearance call; `_build_welcome` speaks a rich
        # single greeting verbatim when it already references appearance, and
        # otherwise synthesizes a varied, clothing-referencing line from the
        # descriptor(s). With the LLM off (welcome_use_llm=False) it returns a
        # random WELCOME_FALLBACKS line and ignores appearance entirely.
        entries = [self._cache.get(pid) for pid in person_ids]
        greeting = _build_welcome(entries, use_llm=self._welcome_use_llm)

        # BATCH_INVITE is a plain welcome of the new arrivals. Cold-room
        # "don't be shy" nudging is handled separately by _check_cold_room
        # (the standalone COLD_ROOM_INVITE timer), so a new arrival here just
        # gets welcomed — and resets that cold timer via _handle_new_entry.
        self._publish("BATCH_INVITE", {
            "batch_id": batch_id,
            "person_ids": person_ids,
            "new_person_count": len(person_ids),
            "total_room_count": total_in_room,
            "model_choice": model_choice,
            "greeting": greeting,
            "appearances": {
                pid: self._cache.get(pid).appearance
                for pid in person_ids
                if self._cache.get(pid) is not None
            },
        })

        ready_count = sum(1 for pid in person_ids if self._cache.get(pid) and self._cache.get(pid).appearance_ready)
        logger.info(
            f"Orchestrator: BATCH_INVITE batch={batch_id} "
            f"persons={person_ids} total_room={total_in_room} model={model_choice} "
            f"debounce={self._debounce_base:.1f}s appearances={ready_count}/{len(person_ids)}"
        )

    # ------------------------------------------------------------------
    # Bench / demographics helpers
    # ------------------------------------------------------------------

    def _update_bench_zone(self, pid: int, cx: int, cy: int,
                            fw: int, fh: int, now: float) -> None:
        """Track bench zone occupancy and emit seated/stood events on transitions."""
        if not self._zones.get_bench_zone_names():
            return  # no benches configured — skip entirely

        entry = self._cache.get(pid)
        if entry is None:
            return

        new_zone = self._zones.which_bench_zone(cx, cy, fw, fh, person_id=pid)
        prev_zone = entry.bench_zone_name

        if new_zone == prev_zone:
            return  # no change

        if new_zone is not None:
            # Either fresh seating or a move between benches — in the move case
            # emit a leave event first, then a seated event.
            if prev_zone is not None:
                self._cache.leave_bench_zone(pid)
                self._publish("PERSON_STOOD_UP", {
                    "person_id": pid,
                    "from_zone": prev_zone,
                })
            self._cache.enter_bench_zone(pid, new_zone)
            self._publish("PERSON_SEATED", {
                "person_id": pid,
                "zone": new_zone,
                "appearance": entry.appearance,
                "age_group": entry.age_group,
                "room_person_count": self._cache.room_person_count(),
            })
        else:
            # Left bench zone entirely
            self._cache.leave_bench_zone(pid)
            dwell = now - (entry.bench_zone_entered_at or now)
            self._publish("PERSON_STOOD_UP", {
                "person_id": pid,
                "from_zone": prev_zone,
                "dwell_sec": round(dwell, 1),
            })

    def _check_demographics(self, now: float) -> None:
        """Periodically emit ROOM_DEMOGRAPHICS when counts have changed."""
        if now - self._last_demographics_emit_at < self._demographics_interval:
            return

        counts = self._cache.count_by_age_group()
        total = sum(counts.values())
        snapshot = {**counts, "total": total}

        if snapshot == self._last_demographics_snapshot:
            return  # nothing changed — don't spam

        self._last_demographics_emit_at = now
        self._last_demographics_snapshot = snapshot
        self._publish("ROOM_DEMOGRAPHICS", {
            "counts": counts,
            "total": total,
            "has_children": counts["child"] > 0,
            "has_elderly": counts["elderly"] > 0,
        })
        logger.info(f"Orchestrator: ROOM_DEMOGRAPHICS {snapshot}")

    def _check_cold_room(self, now: float) -> None:
        """Fire ONE COLD_ROOM_INVITE after the room has been continuously cold.

        "Cold" = nobody is at the mic AND there is at least one visitor in the
        room who has never engaged (never visited the mic zone, never raised a
        hand). The avatar nudges ("don't be shy, come on over") exactly once per
        cold period:

          - Room goes cold        → start the timer.
          - Cold for >= threshold → fire once, then stay quiet (no repeat).
          - Room stops being cold → reset only the timer; the one-shot guard
                                     STAYS set (an engagement that later goes
                                     cold again does NOT re-fire).
          - New visitor arrives   → the ONLY re-arm: _handle_new_entry clears
                                     the guard, so a fresh arrival earns a new nudge.

        So exactly one nudge is sent per arrival-armed cold stretch; engaging
        the mic and going cold again does not produce another.
        """
        mic_occupied = bool(self._cache.get_persons_in_mic_zone())
        idle = [
            e for e in self._cache.get_all_in_room()
            if e.mic_zone_visits == 0 and e.hand_raise_count == 0
        ]
        cold = (not mic_occupied) and bool(idle)

        if not cold:
            # Reset only the timer, NOT the one-shot guard: once we've nudged we
            # stay quiet until a NEW visitor arrives (the only re-arm, handled in
            # _handle_new_entry). An engagement that then goes cold again does
            # NOT earn a fresh nudge.
            self._cold_since = None
            return

        if self._cold_since is None:
            self._cold_since = now
            return

        if self._cold_nudge_fired:
            return
        if now - self._cold_since < self._cold_threshold_sec:
            return
        # Share the welcome rate budget — don't stack a nudge right after a
        # welcome. If rate-limited, retry on a later tick (don't burn the guard).
        if not self._welcome_allowed(now):
            return

        self._cold_nudge_fired = True
        self._record_welcome(now)
        idle_ids = [e.person_id for e in idle]
        self._publish("COLD_ROOM_INVITE", {
            "idle_person_ids": idle_ids,
            "appearances": {
                e.person_id: e.appearance for e in idle if e.appearance is not None
            },
            "room_person_count": self._cache.room_person_count(),
            "cold_duration_sec": round(now - self._cold_since, 1),
        })
        logger.info(
            f"Orchestrator: COLD_ROOM_INVITE idle={idle_ids} "
            f"cold_for={now - self._cold_since:.1f}s"
        )

    def _check_mic_queue(self, now: float) -> None:
        """Emit ONE MIC_ZONE_WAITING when the VLM confirms someone is queuing
        behind the mic person.

        Pure-VLM: while the mic is occupied, poll the VLM (on a full frame)
        every `_mic_queue_check_interval` seconds, asking whether anyone is
        waiting/queuing behind the person at the mic. On the first positive,
        emit once and stay quiet until the mic frees. Requires the VLM — with
        no queue_check_cb wired, there is no queue detection.
        """
        mic_occupants = self._cache.get_persons_in_mic_zone()

        # Mic free → reset the one-shot guard, nothing to do.
        if not mic_occupants:
            self._mic_waiting_announced = False
            return

        # Already announced for this occupied period, or no VLM to ask.
        if self._mic_waiting_announced or self._queue_check_cb is None:
            return

        # A VLM confirmation is in flight — wait for it (with a timeout so a
        # lost callback can't wedge detection forever).
        if self._mic_waiting_vlm_pending:
            if now - self._mic_waiting_vlm_requested_at < self._mic_waiting_vlm_timeout:
                return
            self._mic_waiting_vlm_pending = False  # gave up on the lost callback

        # Poll cadence: don't hammer the VLM every tick.
        if now - self._last_mic_queue_check < self._mic_queue_check_interval:
            return

        self._last_mic_queue_check = now
        self._mic_waiting_vlm_pending = True
        self._mic_waiting_vlm_requested_at = now

        def _on_vlm(confirmed: bool) -> None:
            self._mic_waiting_vlm_pending = False
            if confirmed and not self._mic_waiting_announced:
                self._mic_waiting_announced = True
                self._emit_mic_waiting()

        self._queue_check_cb(_on_vlm)

    def _emit_mic_waiting(self) -> None:
        """Publish MIC_ZONE_WAITING — coarse environment state, no person ids.

        No shared-state mutation here, so it is safe to call from the VLM worker
        thread."""
        self._publish("MIC_ZONE_WAITING", {
            "queue_behind": True,
            "crowd_size": self._crowd_size_label(),
        })
        logger.info("Orchestrator: MIC_ZONE_WAITING queue_behind=true")

    # ------------------------------------------------------------------
    # Environment state helpers + periodic scene observation
    # ------------------------------------------------------------------

    def _crowd_size_label(self) -> str:
        """Coarse crowd-size bucket from the current room occupancy count."""
        n = self._cache.room_person_count()
        if n == 0:
            return "empty"
        if n == 1:
            return "individual"
        if n <= 5:
            return "small_group"
        return "large_group"

    def _check_scene_observation(self, now: float) -> None:
        """Every scene_observation_interval_sec, emit a SCENE_OBSERVATION with a
        coarse environment read (crowd size + queue yes/no + optional VLM
        one-line description). Fires on a fixed interval regardless of occupancy
        so the system stays loosely synced with reality via a real photo."""
        if now - self._last_scene_obs_at < self._scene_obs_interval:
            return
        # A VLM observation is already in flight — don't pile up.
        if self._scene_obs_pending and \
                now - self._last_scene_obs_at < self._scene_obs_interval + self._scene_obs_inflight_grace:
            return

        self._last_scene_obs_at = now

        if self._scene_observe_cb is None:
            # No VLM → no queue read (pure-VLM); emit a bare environment state.
            self._emit_scene_observation(description=None, queue_behind=False)
            return

        self._scene_obs_pending = True

        def _on_obs(result: dict) -> None:
            self._scene_obs_pending = False
            self._emit_scene_observation(
                description=result.get("description"),
                queue_behind=bool(result.get("queue_behind") or False),
            )

        self._scene_observe_cb(_on_obs)

    def _emit_scene_observation(self, description: Optional[str],
                                queue_behind: bool) -> None:
        """Publish SCENE_OBSERVATION (safe to call from the VLM worker thread)."""
        self._publish("SCENE_OBSERVATION", {
            "crowd_size": self._crowd_size_label(),
            "queue_behind": queue_behind,
            "description": description,
        })
        logger.info(
            f"Orchestrator: SCENE_OBSERVATION crowd={self._crowd_size_label()} "
            f"queue_behind={queue_behind}"
        )

    def _refresh_ux_state(self, pid: int, in_mic: bool) -> None:
        """Recompute the person's ux_state label for the dashboard badge.

        Priority order (highest first):
          HAND_UP → AT_MIC → SEATED → NOTICED
        """
        entry = self._cache.get(pid)
        if entry is None:
            return

        if entry.scene_state == "hand_raised":
            entry.ux_state = "HAND_UP"
        elif in_mic or entry.in_mic_zone:
            entry.ux_state = "AT_MIC"
        elif entry.bench_zone_name is not None:
            entry.ux_state = "SEATED"
        else:
            entry.ux_state = "NOTICED"

    def get_person_state(self, person_id: int) -> Optional[PersonCacheEntry]:
        return self._cache.get(person_id)

    def get_all_states(self) -> List[dict]:
        return [e.to_dict() for e in self._cache.get_all_in_room()]
