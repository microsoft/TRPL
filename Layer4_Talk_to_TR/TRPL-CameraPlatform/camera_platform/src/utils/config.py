# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Multi-camera room monitoring system configuration file
"""
import os


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment.

    Truthy: 1/true/yes/on. Falsy: 0/false/no/off (case-insensitive). Anything
    unset or unrecognized falls back to `default`.
    """
    val = os.environ.get(name)
    if val is None:
        return default
    v = val.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _camera_source() -> int | str:
    """Use a local device by default; deployments may supply a path or URL."""
    source = os.getenv("CAMERA_SOURCE", "0").strip()
    return int(source) if source.isdigit() else source


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


# ==================== Camera Configuration ====================
CAMERAS = {
    # Format: (name, input source)
    # Supported:
    #   - Local device: 0, 1, 2 (USB camera device number) or "/dev/video0"
    #   - IP camera: "rtsp://<camera-host>:554/stream"
    #   - Video file: "/path/to/video.mp4"

    # view_type: "frontal" (front/side view) or "overhead" (top-down view)
    "camera_0": {
        "source": _camera_source(),
        "resolution": (
            _env_int("CAMERA_WIDTH", 1280),
            _env_int("CAMERA_HEIGHT", 720),
        ),
        "view_type": "frontal",
    },
}

# ==================== YOLO Model Configuration ====================
YOLO_CONFIG = {
    "model_path": os.getenv("YOLO_MODEL_PATH", "yolov8n-pose.pt"),
    "imgsz": _env_int("YOLO_IMAGE_SIZE", 960),
    "conf_threshold": float(os.getenv("YOLO_CONFIDENCE", "0.5")),
    "iou_threshold": float(os.getenv("YOLO_IOU_THRESHOLD", "0.45")),
    "device": _env_int("YOLO_DEVICE", 0),  # GPU index; -1 for CPU
}

# ==================== Hand Raise Detection Parameters ====================
# Hand detector uses body-local scoring with sensible defaults (see hand_detector.py).
# Only cooldown is configured here; scoring params can be passed to HandDetector() directly.
HAND_RAISE_CONFIG = {
    "cooldown_sec": 30.0,            # Once triggered, ignore this person's hand raise for 30s
}

# ==================== Overhead View Hand Raise Detection Parameters ====================
OVERHEAD_HAND_RAISE_CONFIG = {
    "conf_threshold": 0.20,              # Keypoint confidence (slightly higher for overhead to reduce noise)
    "min_arm_extension": 0.60,           # Min arm extension ratio (||wrist-shoulder|| / body_scale)
    "min_wrist_distance_ratio": 1.3,     # Min wrist distance / torso radius ratio
    "head_alignment_min_cos": 0.15,      # Min cosine between wrist direction and head direction
    "min_forearm_extension": 0.50,       # Min forearm extension ratio
    "score_threshold": 0.45,             # Overall score threshold
    "temporal_window": 5,                # Temporal window frame count
    "enter_frames": 3,                   # Hit frames to trigger hand raise
    "exit_frames": 2,                    # Consecutive miss frames to exit hand raise
}

# ==================== Person Detection Parameters ====================
PERSON_CONFIG = {
    "person_conf_threshold": 0.5,    # Person detection confidence
    "person_min_area": 2000,         # Min person bbox area (avoid detection noise)
    "person_timeout_sec": 30,        # Seconds unseen by any camera before marking as left
    "cleanup_delay_sec": 5,          # Seconds after marking left before complete memory cleanup
    "max_trajectory_length": 500,    # Max trajectory records per person
}

# ==================== Multi-Camera Sync Parameters ====================
MULTI_CAM_CONFIG = {
    "iou_threshold": 0.3,            # IOU threshold for same person's bbox across cameras
    "temporal_window": 5,            # Temporal window frames (for cross-camera sync)
    "position_tolerance": 100,       # Pixel tolerance (for determining same person)
    "reid_threshold": 0.6,           # ReID match threshold (higher = stricter, 0.6~0.8)
}

# ==================== Webhook Configuration ====================
# Webhooks auto-registered on system startup (downstream action endpoints)
# POST JSON to specified URL when events occur
WEBHOOKS = {
    # "example": {
    #     "url": "http://localhost:8080/on_event",
    #     "events": ["hand_raised", "person_left"],  # None means receive all events
    #     "secret": None,                             # Optional signing key
    # },
}

# ==================== Event Management Configuration ====================
EVENT_MANAGER_CONFIG = {
    "max_history_events": 1000,      # Max number of historical events to keep
    "history_window_sec": 3600,      # History window (seconds)
    "aggregate_interval": 5,         # Interval for aggregating info to LLM (seconds)
}

# ==================== Visualization Configuration ====================
VISUALIZATION = {
    "show_annotated_frames": True,   # Whether to show processed video
    "draw_keypoints": True,          # Whether to draw keypoints (shoulder/elbow/wrist)
    "draw_tracks": True,             # Whether to draw person tracking trails
    "fps_window": 30,                # FPS calculation window
}

# ==================== Agent Server Configuration ====================
# The agent server receives ALL events from the camera system and decides what to do.
#
# Env overrides (read at import time):
#   AGENT_SERVER_URL       — full base URL of lia_agent_api, e.g. https://<vm>:8010
#                            Default: http://localhost:8000 (local dev)
#   AGENT_SERVER_API_KEY   — X-API-Key header value used for admin endpoints
#                            (e.g. GET /api/admin/kiosk-session). Default: empty
#                            Both admin calls and event POSTs require this key.
#
# Transport (env EVENT_TRANSPORT):
#   "ws"   — (default) camera HOSTS a WebSocket server; downstream connects in
#            and drains a consume-once FIFO event stack. Nothing is delivered
#            until/unless a client reads it; events buffer in the stack (bounded)
#            and the JSONL audit log backs everything up.
#   "http" — legacy fallback: camera POSTs each event to AGENT_SERVER_URL
#            /api/camera/events with retry (the original behaviour).
AGENT_SERVER_CONFIG = {
    "url": os.environ.get("AGENT_SERVER_URL", "http://localhost:8000"),
    "api_key": os.environ.get("AGENT_SERVER_API_KEY", ""),
    "timeout": 5.0,                  # HTTP request timeout (seconds)
    "enabled": True,                 # Set False to disable forwarding entirely

    # --- Transport selection ---
    "transport": os.environ.get("EVENT_TRANSPORT", "ws"),   # "ws" | "http"

    # --- WebSocket server (transport="ws") ---
    "ws_host": os.environ.get("EVENT_WS_HOST", "0.0.0.0"),  # bind address
    "ws_port": int(os.environ.get("EVENT_WS_PORT", "8765")), # listen port
    # Max events buffered in the consume-once stack while no client is draining.
    # Oldest are dropped past this (still recorded in the JSONL audit log).
    "stack_maxlen": int(os.environ.get("EVENT_STACK_MAXLEN", "1000")),

    # Event types to NOT output downstream (still tracked internally for the
    # dashboard / state). We only care about people leaving the MIC ZONE
    # (MIC_ZONE_LEFT), not people leaving the ROOM — so the room-leave signal
    # (person_left timeout) is suppressed.
    "suppress_event_types": [
        "person_left",
        # Low-level "new track ID" event. ReID fragments under occlusion / when
        # several people wear similar clothing, re-firing this for people already
        # in the room. The real, debounced "someone entered" signal downstream
        # should key on is PERSON_ENTERED_ROOM (entry-zone gated), not this.
        "person_entered",
    ],

    "include_person_crop": True,     # Include cropped person image in PERSON_ENTERED events
    "include_frame_thumbnail": False, # Include downscaled full frame (large payload, off by default)
    "crop_jpeg_quality": 75,         # JPEG quality for person crops (0-100)
    "frame_thumbnail_width": 320,    # Downscale full frame to this width if include_frame_thumbnail=True
}

# ==================== Tracking & Pipeline Configuration ====================
TRACKING_CONFIG = {
    "stale_track_threshold_sec": 5.0,   # (legacy) Seconds before a camera track is considered stale. No longer used for ReID exclusion — see concurrent_track_threshold_sec.
    # ReID re-match exclusion window. A global ID is excluded from re-matching
    # ONLY if one of its other local tracks updated within this many seconds
    # (i.e. it is genuinely on screen via a live track right now). When the
    # track that kept an ID alive dies (BotSORT churns IDs constantly), the ID
    # is left reclaimable so a fragmented track merges back instead of minting
    # a duplicate person. Keep this SHORT (a few frames) — too long re-creates
    # the old duplicate-identity bug; too short risks two live tracks briefly
    # sharing one ID. 1.5s ≈ several frames of grace at the live frame rate.
    "concurrent_track_threshold_sec": 1.5,
    "max_pending_frames": 2,            # Consecutive frames a SINGLE track ID must persist before a person is created. MUST stay low: BotSORT reassigns track IDs constantly with this live feed, so a high value means no track ever reaches it and persons are NEVER created (count stays 0). Do NOT raise to "reduce ID churn" — it breaks detection. (Originally 30 → caused exactly that → lowered to 2.)
    # Which Y-fraction of the bbox to test against zones. 0.0 = top (head),
    # 0.5 = center (torso), 1.0 = bottom (feet).
    # Set to 0.5 (body center): with the current camera angle the feet are
    # often occluded / out of frame, and the center is the more stable point
    # to test against zones. (Previously biased toward the feet at 0.85.)
    "zone_point_y_fraction": 0.5,
    "orchestrator_interval": 0.2,       # Seconds between orchestrator ticks
    "annotated_buffer_size": 120,       # Ring buffer size for annotated frames (~4s at 30fps)
}

# ==================== ReID Matching Configuration ====================
REID_MATCHING_CONFIG = {
    "reid_weight": 0.6,                 # Weight for ReID similarity in combined matching
    "position_weight": 0.4,             # Weight for position proximity in combined matching
    "combined_threshold": 0.5,          # Min combined score for position-assisted match
    "min_reid_similarity": 0.35,        # Safety floor: don't match on position alone
    "max_position_distance": 300,       # Pixels: beyond this, position score = 0
}

# ==================== Logging Configuration ====================
LOGGING = {
    "level": "INFO",                 # DEBUG, INFO, WARNING, ERROR
    "log_file": "room_monitor.log",
    "max_file_size": 10485760,       # 10MB
    "backup_count": 5,
}

# ==================== Named Zone Configuration ====================
# Zones are defined as polygons in NORMALIZED coordinates (0.0–1.0).
# Each polygon is a list of (x, y) vertices.
# The system uses two key zones:
#   - entry_zone: new person tracks must first appear here to be counted as "entered the room"
#   - mic_zone:   when a tracked person is inside this zone, the system engages in dialog

ZONE_CONFIG = {
    # Entry zone — door/entrance area
    # Labelled from snapshot_20260418_145659.jpg (RealSense, 1280x720).
    "entry_zone": [
        (0.6734, 0.525),
        (0.6531, 0.8472),
        (0.7203, 0.9139),
        (0.7656, 0.5083),
        (0.6945, 0.4375),
        (0.6852, 0.4431),
    ],
    # Microphone zone — front-center area (a.k.a. "podium")
    # Expanded from the original thin band (y 0.89-0.95) to a more
    # forgiving patch so the computed test point (body center,
    # y_fraction=0.5 of bbox) reliably lands inside.
    # NOTE: this polygon was tuned for the feet point (y_fraction=0.85);
    # after switching to body center it may need re-labeling so a person
    # standing at the mic still registers inside.
    "mic_zone": [
        (0.40, 0.80),
        (0.40, 1.00),
        (0.56, 1.00),
        (0.56, 0.80),
    ],
    # Bench / seating zones — list of polygons in normalized coords.
    # Leave empty until physical layout is annotated. Each polygon emits
    # PERSON_SEATED / PERSON_STOOD_UP events independently.
    "bench_zones": [
        # Example: [(0.05, 0.60), (0.05, 0.85), (0.30, 0.85), (0.30, 0.60)],
    ],
    # Per-zone exit-hysteresis overrides (normalized margin). Once a person is
    # inside a zone, their test point must move this far past the boundary
    # before they count as left; a larger value makes the zone "stickier".
    # The mic zone gets a bigger margin (vs the 0.02 default) so a person
    # standing at the mic doesn't flicker out and fire a false MIC_ZONE_LEFT
    # when the body-center test point jitters near the edge. Zones not listed
    # fall back to the global default. NOTE: this hardens the boundary behavior
    # but does NOT replace proper mic_zone re-labeling against the real camera
    # view — if a person standing at the mic still reads "out", widen the
    # polygon in the web zone labeler.
    "zone_hysteresis_margins": {
        "mic_zone": 0.05,
    },
}

# Override the defaults above with zones labeled in the web zone labeler
# (config/zones.json, written by POST /api/zones). This makes browser-labeled
# zones the source of truth across restarts without hand-editing this file.
try:
    from .zone_store import load_persisted_zones as _load_persisted_zones
    _persisted_zones = _load_persisted_zones()
    if _persisted_zones:
        ZONE_CONFIG.update(_persisted_zones)
except Exception:  # never let a bad zones.json block startup
    pass

# ==================== Scene Orchestrator Configuration ====================
SCENE_CONFIG = {
    # ---------- Entry commitment gate (anti-YOLO-glitch) ----------
    # A newly-seen track is NOT registered as a real arrival the instant its
    # center touches the entry zone. It must stay continuously inside the zone
    # for entry_dwell_sec first. A 1-2 frame phantom (tracker fragment,
    # reflection, ID switch) never survives that, so it never triggers a
    # (false) welcome.
    "entry_dwell_sec": 0.4,         # continuous time inside entry zone before registering

    # ---------- Entry direction gate (inbound vs outbound at the door) ----------
    # The entry zone sits on the physical doorway, so a person crosses it both
    # when ARRIVING and when LEAVING — position alone cannot tell the two apart,
    # which is why a departure used to re-trigger a welcome. We additionally
    # check the person's direction of travel: only an inbound crossing (moving
    # "into the room") earns a BATCH_INVITE welcome; an outbound crossing
    # (heading for the door) is a departure and is never welcomed.
    #
    # The "into the room" direction comes from the entry-direction arrow drawn
    # in the web zone labeler (/label), falling back to the entry_zone→mic_zone
    # centroid when no arrow is annotated. See ZoneDetector.get_entry_inward_unit.
    "entry_direction_gate_enabled": True,
    # Trajectory window (sec) for estimating direction of travel: the net
    # displacement from the oldest sample within this window to the latest.
    "entry_direction_window_sec": 1.2,
    # Minimum net displacement (normalized, fraction of frame) before the
    # direction estimate is trusted. Below this the motion is "ambiguous"
    # (person standing still in the doorway) and the fallback below applies.
    "entry_direction_min_disp": 0.03,
    # Behavior when direction is ambiguous (too little motion to call it):
    #   "welcome"  → admit + welcome (preserves legacy behavior; lowest
    #                regression risk — a clear OUTBOUND exit is still suppressed)
    #   "suppress" → do not welcome until the person shows clear inbound motion
    "entry_direction_fallback": "welcome",

    # ---------- Cold-room nudge (standalone "don't be shy") ----------
    # When the room has un-engaged visitors and nobody is at the mic, it's
    # "cold". After cold_room_threshold_sec of continuous cold the orchestrator
    # fires ONE COLD_ROOM_INVITE and then stays quiet — no repeat. The timer is
    # re-armed (reset) when a new visitor enters, so a fresh arrival earns a
    # fresh 30s wait before the next cold nudge. Population unchanged → silent.
    "cold_room_threshold_sec": 30.0,

    # Whether the entry welcome (BATCH_INVITE greeting) goes through the LLM/VLM.
    #   True  → appearance-aware greeting: wait for the per-person VLM appearance
    #           and speak a varied, clothing-referencing line (legacy behavior).
    #   False → no LLM: skip the VLM wait entirely and speak one of a handful of
    #           canned fallbacks (scene_orchestrator.WELCOME_FALLBACKS) at random.
    # Override per-deployment with the WELCOME_USE_LLM env var (1/0/true/false).
    "welcome_use_llm": _env_bool("WELCOME_USE_LLM", False),

    "invite_debounce_sec": 1.5,     # Wait this long after last new entry before sending batch invite (BATCH_INVITE)
    "invite_vlm_wait_max_sec": 2.5, # After debounce, wait at most this long for VLM appearances before flushing BATCH_INVITE anyway

    # Welcome rate limit — calm, host-like cadence instead of greeting every
    # arrival. At most `welcome_max_per_window` spoken welcomes are emitted in
    # any rolling `welcome_window_sec` window; extras are silently suppressed
    # (the person is still tracked/marked, just not re-welcomed). Shared budget
    # across BATCH_INVITE and COLD_ROOM_INVITE so they don't stack.
    "welcome_window_sec": 30.0,     # rolling window (seconds)
    "welcome_max_per_window": 1,    # max welcomes per window
    "mic_zone_confirm_sec": 0.7,    # Continuous dwell (sec) in mic zone before MIC_ZONE_ENGAGED. Tune to room size.
    # Seconds continuously OUT of mic zone before firing MIC_ZONE_LEFT.
    # Debounces brief steps-out (bent down to tie a shoe, YOLO jitter) AND the
    # body-center test point flickering across a tight mic-zone boundary while
    # the person stands still. Raised 2.0 → 3.0 after observing false LEFTs
    # (followed by a second MIC_ZONE_ENGAGED) for a person who never left.
    # Works together with the larger mic-zone exit hysteresis below.
    # Set to 0 to restore the old immediate behavior.
    "mic_zone_leave_sec": 3.0,
    # Remove the orchestrator's per-person cache (which holds the "invited" /
    # "greeted" dedup flags) this many seconds after last seen.
    #
    # MUST stay aligned with the identity lifetime in event_manager —
    # person_timeout_sec + cleanup_delay_sec — i.e. how long ReID keeps a
    # person's gallery before forgetting them. If this is SHORTER than that,
    # the dedup state expires while identity (global_id + ReID gallery) is
    # still alive: a person who briefly drops out and is re-matched to their
    # old global_id would be treated as new and RE-INVITED. Computing it from
    # PERSON_CONFIG keeps the two from drifting apart.
    "cache_ttl_sec": float(
        PERSON_CONFIG["person_timeout_sec"] + PERSON_CONFIG["cleanup_delay_sec"]
    ),  # = 35.0s, matches identity lifetime

    # How often to recompute and emit ROOM_DEMOGRAPHICS (only if counts changed).
    "demographics_publish_interval_sec": 10.0,
    # Fixed interval for the periodic SCENE_OBSERVATION (VLM full-frame reality
    # sync: crowd_size + queue_behind + one-line description). Fires regardless
    # of occupancy. Default 5 minutes.
    "scene_observation_interval_sec": 300.0,
}

# ==================== RealSense Configuration ====================
# Applied to any CAMERAS entry whose source starts with "realsense://".
# Depth is aligned to the color stream; values outside [min, max] are treated
# as invalid when sampling per-person depth. Depth stream resolution follows
# D455 native profiles — 848x480 @ 30Hz is the manufacturer-recommended default.
REALSENSE_CONFIG = {
    "depth_width": 848,          # D455 native depth resolution
    "depth_height": 480,
    "depth_fps": 30,
    "color_fps": 30,             # color stream FPS (color resolution follows CAMERAS[*].resolution)
    "depth_min_m": 0.3,          # samples below this are discarded (too close = noisy)
    "depth_max_m": 6.0,          # samples above this are discarded (D455 reliable range)
    "emitter_enabled": True,     # IR projector — leave on for indoor use
    "align_to_color": True,      # align depth → color intrinsics so bbox pixels map 1:1
    "bbox_depth_sample_ratio": 0.33,  # central fraction of bbox to median-sample (torso, not edges)
}

# ==================== VLM Observer Configuration ====================
from .prompts import VLM_APPEARANCE

VLM_CONFIG = {
    "enabled": _env_bool("VLM_ENABLED", True),
    # Vision-capable model. For Azure OpenAI the deployment name in
    # LLM_BASE_URL (…/deployments/<name>/…) takes precedence over this.
    # gpt-4.1-mini is ~2x faster and ~5x cheaper than gpt-4.1 — plenty for
    # clothing/age description and queue checks. Override via LLM_MODEL env.
    "model": "gpt-4.1-mini",
    "timeout_sec": 15.0,
    "max_concurrent": 4,            # Max in-flight VLM requests (parallelism for groups)
    # Image detail for the appearance call. "low" = flat ~85 image tokens (fast);
    # "high" = tiled, ~10x more tokens but far better colour/age accuracy.
    "appearance_detail": "high",
    # Downscale person crops so the longest side <= this before encoding (0 =
    # off). With detail="high" the model tiles in 512px, so 768 gives it real
    # pixels to read colour/age from (a 512 crop would waste "high").
    "crop_max_side": 768,
    "appearance_prompt": VLM_APPEARANCE,
}
