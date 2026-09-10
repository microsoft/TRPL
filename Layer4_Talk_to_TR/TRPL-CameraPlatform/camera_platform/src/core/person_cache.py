"""
Person Cache — per-person appearance and state cache with TTL-based cleanup.

Each person who enters via the entry zone gets a cache entry containing:
- Snapshot frame and VLM-generated appearance description
- Invite state (whether they've been invited, which batch)
- Mic zone visit tracking
- Hand raise count

Cache entries are removed when a person has not been detected for cache_ttl_sec
(default 35s — kept aligned with the identity lifetime person_timeout_sec +
cleanup_delay_sec so dedup state never expires before ReID forgets the person).

Persistence: save_to_disk() / load_from_disk() pickle the cache so that a
watchdog-triggered restart doesn't lose in-flight visitors (which would cause
double-greetings and incorrect room counts). TTL still applies on load, so
stale entries are auto-pruned on read.
"""
import logging
import os
import pickle
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from ..utils.config import SCENE_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PersonCacheEntry:
    """Cached state for a single person."""
    person_id: int
    entry_time: float = field(default_factory=time.time)
    last_seen_time: float = field(default_factory=time.time)

    # Appearance (filled async by VLM)
    snapshot_crop_b64: Optional[str] = None
    appearance: Optional[Dict[str, str]] = None  # {"top": "...", "bottom": "...", "notable": "..."}
    appearance_ready: bool = False
    # VLM-generated welcome line + short descriptor (folded into the appearance
    # call). Used to build the BATCH_INVITE greeting that the avatar speaks
    # verbatim — no second downstream LLM hop.
    greeting: str = ""
    descriptor: str = ""

    # Multi-frame crops for richer VLM analysis: deque of (timestamp, crop_b64)
    max_crops: int = 5
    crop_history: Deque[Tuple[float, str]] = field(default_factory=lambda: deque(maxlen=5))

    # Invite state
    invited: bool = False
    invite_batch_id: Optional[str] = None

    # Interaction tracking
    mic_zone_visits: int = 0
    in_mic_zone: bool = False
    mic_zone_entered_at: Optional[float] = None
    hand_raise_count: int = 0

    # Demographics (extracted from VLM appearance response once available)
    # age_group ∈ {"child", "adult", "elderly", "unknown"}
    age_group: str = "unknown"
    age_confidence: float = 0.0


    # Bench zone occupancy (name of the bench zone, e.g. "bench_0", or None)
    bench_zone_name: Optional[str] = None
    bench_zone_entered_at: Optional[float] = None

    # ---------- Trajectory + UX interaction state ----------
    # Short sliding window of (timestamp, (cx, cy)) — used for motion/velocity.
    # Kept small (10 samples ≈ 2s) so it's cheap to pickle and doesn't blow up memory.
    trajectory: Deque[Tuple[float, Tuple[int, int]]] = field(
        default_factory=lambda: deque(maxlen=10)
    )

    # Per-person scene state
    # "idle" — in room, avatar ignores unless hand raised
    # "in_mic_zone" — in mic zone, system engages
    # "hand_raised" — anywhere in room, system responds
    scene_state: str = "idle"

    # Richer UX state label used for dashboard badges + downstream routing.
    # Distinct from scene_state because it includes transitional UX signals
    # (approaching, seated) that don't affect the core state machine.
    # Recomputed every tick from the underlying flags.
    ux_state: str = "noticed"

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "entry_time": self.entry_time,
            "last_seen_time": self.last_seen_time,
            "appearance": self.appearance,
            "appearance_ready": self.appearance_ready,
            "invited": self.invited,
            "invite_batch_id": self.invite_batch_id,
            "mic_zone_visits": self.mic_zone_visits,
            "in_mic_zone": self.in_mic_zone,
            "hand_raise_count": self.hand_raise_count,
            "age_group": self.age_group,
            "age_confidence": self.age_confidence,
            "bench_zone_name": self.bench_zone_name,
            "scene_state": self.scene_state,
        }


class PersonCache:
    """
    Manages per-person cache entries.

    Lifecycle:
      1. Person detected in entry zone → create_entry()
      2. VLM returns appearance → set_appearance()
      3. Person enters mic zone → enter_mic_zone()
      4. Person leaves mic zone → leave_mic_zone()
      5. Person undetected for 30s → cleanup removes entry
    """

    def __init__(self):
        self._cache: Dict[int, PersonCacheEntry] = {}
        self._ttl = SCENE_CONFIG.get("cache_ttl_sec", 30.0)
        self._next_batch_id = 0

    def create_entry(self, person_id: int, crop_b64: Optional[str] = None) -> PersonCacheEntry:
        """Create a new cache entry when person enters via entry zone."""
        if person_id in self._cache:
            return self._cache[person_id]
        entry = PersonCacheEntry(
            person_id=person_id,
            snapshot_crop_b64=crop_b64,
        )
        self._cache[person_id] = entry
        logger.info(f"PersonCache: created entry for person {person_id}")
        return entry

    def get(self, person_id: int) -> Optional[PersonCacheEntry]:
        return self._cache.get(person_id)

    def has(self, person_id: int) -> bool:
        return person_id in self._cache

    def update_seen(self, person_id: int) -> None:
        """Update last_seen_time for a person."""
        entry = self._cache.get(person_id)
        if entry:
            entry.last_seen_time = time.time()

    def update_position(self, person_id: int, cx: int, cy: int,
                        timestamp: Optional[float] = None) -> None:
        """Append the person's center to their trajectory."""
        entry = self._cache.get(person_id)
        if entry is None:
            return
        ts = timestamp if timestamp is not None else time.time()
        entry.trajectory.append((ts, (cx, cy)))

    def add_crop(self, person_id: int, crop_b64: str, timestamp: Optional[float] = None) -> None:
        """Append a timestamped crop to the person's crop history."""
        entry = self._cache.get(person_id)
        if entry is None:
            return
        ts = timestamp or time.time()
        entry.crop_history.append((ts, crop_b64))

    def set_appearance(self, person_id: int, appearance: Dict[str, str]) -> None:
        """Set VLM-generated appearance description.

        Also extracts age_group / age_confidence if present in the response,
        so scene-level demographics can be computed without re-parsing.
        """
        entry = self._cache.get(person_id)
        if not entry:
            return
        entry.appearance = appearance
        entry.appearance_ready = True
        entry.greeting = (appearance.get("greeting") or "").strip()
        entry.descriptor = (appearance.get("descriptor") or "").strip()

        raw_group = (appearance.get("age_group") or "unknown").strip().lower()
        if raw_group in ("child", "adult", "elderly", "unknown"):
            entry.age_group = raw_group
        else:
            entry.age_group = "unknown"
        try:
            entry.age_confidence = float(appearance.get("age_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            entry.age_confidence = 0.0

        logger.info(
            f"PersonCache: appearance set for person {person_id}: "
            f"{appearance} (age={entry.age_group}, conf={entry.age_confidence:.2f})"
        )

    def mark_invited(self, person_id: int, batch_id: str) -> None:
        """Mark person as having been invited."""
        entry = self._cache.get(person_id)
        if entry:
            entry.invited = True
            entry.invite_batch_id = batch_id

    def enter_mic_zone(self, person_id: int) -> bool:
        """
        Mark person as entering mic zone.
        Returns True if this is a new entry (was not already in mic zone).
        """
        entry = self._cache.get(person_id)
        if entry is None:
            return False
        if entry.in_mic_zone:
            return False
        entry.in_mic_zone = True
        entry.mic_zone_entered_at = time.time()
        entry.mic_zone_visits += 1
        entry.scene_state = "in_mic_zone"
        logger.info(f"PersonCache: person {person_id} entered mic zone (visit #{entry.mic_zone_visits})")
        return True

    def leave_mic_zone(self, person_id: int) -> bool:
        """
        Mark person as leaving mic zone.
        Returns True if they were in the mic zone.
        """
        entry = self._cache.get(person_id)
        if entry is None:
            return False
        if not entry.in_mic_zone:
            return False
        entry.in_mic_zone = False
        entry.mic_zone_entered_at = None
        entry.scene_state = "idle"
        logger.info(f"PersonCache: person {person_id} left mic zone")
        return True

    def record_hand_raise(self, person_id: int) -> None:
        entry = self._cache.get(person_id)
        if entry:
            entry.hand_raise_count += 1
            entry.scene_state = "hand_raised"

    def clear_hand_raise(self, person_id: int) -> None:
        entry = self._cache.get(person_id)
        if entry and entry.scene_state == "hand_raised":
            entry.scene_state = "in_mic_zone" if entry.in_mic_zone else "idle"

    def enter_bench_zone(self, person_id: int, zone_name: str) -> bool:
        """Mark person as entering a specific bench zone. Returns True on fresh
        transition (was not in this bench zone before)."""
        entry = self._cache.get(person_id)
        if entry is None:
            return False
        if entry.bench_zone_name == zone_name:
            return False
        entry.bench_zone_name = zone_name
        entry.bench_zone_entered_at = time.time()
        logger.info(f"PersonCache: person {person_id} seated in {zone_name}")
        return True

    def leave_bench_zone(self, person_id: int) -> Optional[str]:
        """Mark person as no longer in any bench zone. Returns the name of the
        zone they were in, or None if they weren't seated."""
        entry = self._cache.get(person_id)
        if entry is None or entry.bench_zone_name is None:
            return None
        prev = entry.bench_zone_name
        entry.bench_zone_name = None
        entry.bench_zone_entered_at = None
        logger.info(f"PersonCache: person {person_id} left {prev}")
        return prev

    def get_all_in_room(self) -> List[PersonCacheEntry]:
        """Return all active cache entries."""
        return list(self._cache.values())

    def get_persons_in_mic_zone(self) -> List[PersonCacheEntry]:
        return [e for e in self._cache.values() if e.in_mic_zone]

    def get_persons_in_bench_zones(self) -> List[PersonCacheEntry]:
        return [e for e in self._cache.values() if e.bench_zone_name is not None]

    def get_uninvited_persons(self) -> List[PersonCacheEntry]:
        return [e for e in self._cache.values() if not e.invited]

    def room_person_count(self) -> int:
        return len(self._cache)

    def count_by_age_group(self) -> Dict[str, int]:
        """Return counts of cache entries by age_group.

        Keys: "child", "adult", "elderly", "unknown". Always all four present.
        """
        counts = {"child": 0, "adult": 0, "elderly": 0, "unknown": 0}
        for entry in self._cache.values():
            group = entry.age_group if entry.age_group in counts else "unknown"
            counts[group] += 1
        return counts

    def new_batch_id(self) -> str:
        self._next_batch_id += 1
        return f"inv_{self._next_batch_id:04d}"

    def cleanup(self) -> List[int]:
        """
        Remove cache entries for persons unseen for > cache_ttl_sec.
        Returns list of removed person_ids.
        """
        now = time.time()
        removed = []
        for pid, entry in list(self._cache.items()):
            if now - entry.last_seen_time > self._ttl:
                removed.append(pid)
                del self._cache[pid]
        if removed:
            logger.info(f"PersonCache: cleaned up {len(removed)} entries (TTL={self._ttl}s): {removed}")
        return removed

    def remove(self, person_id: int) -> None:
        """Explicitly remove a person from cache."""
        if person_id in self._cache:
            del self._cache[person_id]
            logger.info(f"PersonCache: removed person {person_id}")

    # ------------------------------------------------------------------
    # Persistence — survive watchdog-triggered restarts
    # ------------------------------------------------------------------

    def save_to_disk(self, path: str) -> bool:
        """Atomically pickle the cache dict to `path`.

        Uses a tmp-file + rename so a crash mid-write never leaves a corrupt
        file behind. Returns True on success.
        """
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # Temp file in the same directory so rename is atomic.
            fd, tmp_path = tempfile.mkstemp(
                prefix=".person_cache_", suffix=".pkl.tmp",
                dir=os.path.dirname(path) or ".",
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    pickle.dump(
                        {
                            "cache": self._cache,
                            "next_batch_id": self._next_batch_id,
                            "saved_at": time.time(),
                        },
                        f,
                        protocol=pickle.HIGHEST_PROTOCOL,
                    )
                os.replace(tmp_path, path)
                return True
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.warning(f"PersonCache.save_to_disk failed: {e}")
            return False

    def load_from_disk(self, path: str) -> int:
        """Load the cache from a previous save. Returns number of entries
        restored (after TTL pruning).

        Silently returns 0 if the file doesn't exist — that's just a cold
        start. Any entries whose last_seen_time is older than ttl are dropped
        immediately so restart doesn't resurrect stale presences.
        """
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "rb") as f:
                blob = pickle.load(f)
        except Exception as e:
            logger.warning(f"PersonCache.load_from_disk failed ({path}): {e}")
            return 0

        loaded_cache = blob.get("cache", {})
        self._next_batch_id = blob.get("next_batch_id", 0)

        now = time.time()
        kept = 0
        for pid, entry in loaded_cache.items():
            if now - entry.last_seen_time <= self._ttl:
                self._cache[pid] = entry
                kept += 1

        age = now - blob.get("saved_at", now)
        logger.info(
            f"PersonCache: restored {kept}/{len(loaded_cache)} entries "
            f"from {path} (snapshot age {age:.1f}s)"
        )
        return kept

    def fingerprint(self) -> int:
        """Cheap hash of the cache state — used to skip no-op saves."""
        return hash(tuple(sorted(
            (pid, entry.last_seen_time, entry.appearance_ready,
             entry.in_mic_zone, entry.hand_raise_count)
            for pid, entry in self._cache.items()
        )))
