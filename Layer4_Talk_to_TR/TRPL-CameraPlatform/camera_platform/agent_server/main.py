"""
Agent Server — FastAPI (v2)
──────────────────────────
Receives events from the camera platform's new zone-based architecture.

Event types handled:
  - person_entered        → low-level YOLO detection, buffered
  - PERSON_ENTERED_ROOM   → person confirmed via entry zone
  - BATCH_INVITE          → debounced invite for N new people
  - MIC_ZONE_ENGAGED      → person in mic zone, load cache
  - MIC_ZONE_LEFT         → person left mic zone
  - HAND_RAISE_RESPONSE   → person raised hand anywhere
  - hand_raised           → low-level hand raise event

Run:
    cd camera_platform/agent_server
    uvicorn agent_server.main:app --port 8000 --reload
"""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.prompts import VLM_PERSON_CAPTION, PROMPT_GREETING, PROMPT_GREETING_COLD
from .agent_config import FAR_THRESHOLD, GROUP_WINDOW_SEC, MAX_GROUP_CROPS, MIC_POSITION
from .models import CameraEvent, PersonDescription

# ── load .env ────────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# ── setup ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
)
logger = logging.getLogger("agent_server")

app = FastAPI(title="Camera Agent Server", version="0.3.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ── in-memory state ──────────────────────────────────────────────────────────
_event_log: deque = deque(maxlen=2000)
_person_descriptions: Dict[int, PersonDescription] = {}
_hand_raises: Dict[int, List[Dict[str, Any]]] = {}
_greetings: deque = deque(maxlen=200)
_mic_zone_sessions: deque = deque(maxlen=200)  # Active mic zone engagements
_batch_invites: deque = deque(maxlen=100)       # Batch invite history
_bench_events: deque = deque(maxlen=200)       # PERSON_SEATED / PERSON_STOOD_UP
_latest_demographics: Dict[str, Any] = {}      # Last ROOM_DEMOGRAPHICS snapshot
_latest_scene: Dict[str, Any] = {}             # Last SCENE_OBSERVATION snapshot

# ── TTL sweeper config ───────────────────────────────────────────────────────
# Stale entries in per-person dicts are purged every N seconds; any entry
# whose last timestamp is older than the TTL is removed. Prevents unbounded
# growth during 10h+ operation when persons come and go.
HAND_RAISE_TTL_SEC = int(os.environ.get("HAND_RAISE_TTL_SEC", "1800"))      # 30 min
PERSON_DESC_TTL_SEC = int(os.environ.get("PERSON_DESC_TTL_SEC", "1800"))    # 30 min
SWEEP_INTERVAL_SEC = int(os.environ.get("SWEEP_INTERVAL_SEC", "300"))        # 5 min
_sweep_stats: Dict[str, int] = {
    "hand_raises_pruned": 0,
    "person_descriptions_pruned": 0,
    "entry_buffers_pruned": 0,
    "last_sweep_ts": 0,
}
_start_time = time.time()

# Entry buffering (for low-level person_entered events — kept for compat)
_entry_buffer: Dict[str, List[CameraEvent]] = defaultdict(list)
_buffer_lock = threading.Lock()
_flush_timers: Dict[str, threading.Timer] = {}

# ── OpenAI client ────────────────────────────────────────────────────────────
_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1")


def _get_client() -> Optional[OpenAI]:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")
    if not api_key or not base_url:
        logger.warning("LLM_API_KEY / LLM_BASE_URL not set — VLM will be skipped")
        return None
    return OpenAI(api_key=api_key, base_url=base_url)


# ── VLM calls ────────────────────────────────────────────────────────────────

def _chat(client: OpenAI, messages: list, max_tokens: int = 300) -> str:
    resp = client.chat.completions.create(
        model=_LLM_MODEL, messages=messages, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _img_content(crop_b64: str) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{crop_b64}", "detail": "low"},
    }


def _vlm_caption_single(crop_b64: str, client: OpenAI) -> str:
    return _chat(client, [{
        "role": "user",
        "content": [
            _img_content(crop_b64),
            {"type": "text", "text": VLM_PERSON_CAPTION},
        ],
    }], max_tokens=300)


def _vlm_greeting(caption: str, person_count: int, model_choice: str,
                  client: OpenAI) -> str:
    """Generate a plain English welcome adapted to solo vs multi-person mode."""
    subject = "this visitor" if model_choice == "solo" else f"these {person_count} visitors"
    addr = "you" if model_choice == "solo" else "all of you"
    prompt = PROMPT_GREETING.format(subject=subject, addr=addr, caption=caption)
    return _chat(client, [{"role": "user", "content": prompt}], max_tokens=150)


def _vlm_cold_nudge(caption: str, person_count: int, client: OpenAI) -> str:
    """Generate a standalone 'don't be shy' nudge for a cold room (COLD_ROOM_INVITE)."""
    subject = "this visitor" if person_count <= 1 else f"these {person_count} visitors"
    addr = "you" if person_count <= 1 else "all of you"
    prompt = PROMPT_GREETING_COLD.format(subject=subject, addr=addr, caption=caption)
    return _chat(client, [{"role": "user", "content": prompt}], max_tokens=150)


def _store_description(person_id: int, camera_id: str, timestamp: float,
                       description: str, bbox: Optional[list] = None):
    _person_descriptions[person_id] = PersonDescription(
        person_id=person_id, camera_id=camera_id,
        timestamp=timestamp, description=description, bbox=bbox,
    )


def _record_greeting(text: str, person_ids: List[int], camera_id: str,
                     batch_id: str = ""):
    _greetings.append({
        "timestamp": time.time(),
        "camera_id": camera_id,
        "person_ids": person_ids,
        "batch_id": batch_id,
        "greeting": text,
    })
    print("\n" + "▶" * 60)
    print(f"  GREETING  →  {text}")
    print("▶" * 60 + "\n")
    logger.info(f"Greeting generated for persons {person_ids}: {text}")


# ── Event handlers ───────────────────────────────────────────────────────────

def _on_person_entered(event: CameraEvent):
    """Low-level person_entered — buffer for group window (backward compat)."""
    with _buffer_lock:
        _entry_buffer[event.camera_id].append(event)
    _schedule_flush(event.camera_id)


def _on_hand_raised(event: CameraEvent):
    side = event.data.get("side", "unknown")
    logger.info(f"Hand raised — person {event.person_id} [{side}] on {event.camera_id}")
    _hand_raises.setdefault(event.person_id, []).append({
        "timestamp": event.timestamp,
        "side": side,
        "camera_id": event.camera_id,
        "confidence": event.data.get("confidence"),
    })


def _on_batch_invite(event: CameraEvent):
    """
    BATCH_INVITE — debounced invite for N new people.
    Generate a greeting if VLM is available.
    """
    payload = event.data
    batch_id = payload.get("batch_id", "")
    person_ids = payload.get("person_ids", [])
    total_count = payload.get("total_room_count", len(person_ids))
    model_choice = payload.get("model_choice", "solo")
    appearances = payload.get("appearances", {})

    logger.info(
        f"BATCH_INVITE batch={batch_id} persons={person_ids} "
        f"total={total_count} model={model_choice}"
    )

    _batch_invites.append({
        "timestamp": time.time(),
        "batch_id": batch_id,
        "person_ids": person_ids,
        "total_room_count": total_count,
        "model_choice": model_choice,
    })

    # Build a combined description from appearances
    descriptions = []
    for pid in person_ids:
        app = appearances.get(str(pid)) or appearances.get(pid)
        if app:
            parts = []
            if app.get("top"):
                parts.append(app["top"])
            if app.get("bottom"):
                parts.append(app["bottom"])
            if app.get("notable") and app["notable"].lower() != "none":
                parts.append(app["notable"])
            if parts:
                descriptions.append(f"Person {pid}: {', '.join(parts)}")

    caption = "; ".join(descriptions) if descriptions else "(appearance not yet available)"

    client = _get_client()
    if client and descriptions:
        try:
            greeting = _vlm_greeting(caption, total_count, model_choice, client)
            _record_greeting(greeting, person_ids, event.camera_id, batch_id)
        except Exception as e:
            logger.error(f"Batch greeting failed: {e}")


def _on_cold_room_invite(event: CameraEvent):
    """COLD_ROOM_INVITE — room has gone quiet (un-engaged people, nobody at the
    mic) for a while. Generate one standalone 'don't be shy' nudge."""
    payload = event.data
    idle_ids = payload.get("idle_person_ids", [])
    appearances = payload.get("appearances", {})
    cold_for = payload.get("cold_duration_sec")

    logger.info(
        f"COLD_ROOM_INVITE idle={idle_ids} room={payload.get('room_person_count')} "
        f"cold_for={cold_for}s"
    )

    descriptions = []
    for pid in idle_ids:
        app = appearances.get(str(pid)) or appearances.get(pid)
        if app:
            parts = [app.get("top"), app.get("bottom")]
            if app.get("notable") and app["notable"].lower() != "none":
                parts.append(app["notable"])
            parts = [p for p in parts if p]
            if parts:
                descriptions.append(f"Person {pid}: {', '.join(parts)}")

    caption = "; ".join(descriptions) if descriptions else "(appearance not available)"

    client = _get_client()
    if client:
        try:
            nudge = _vlm_cold_nudge(caption, len(idle_ids), client)
            _record_greeting(nudge, idle_ids, event.camera_id, batch_id="cold")
        except Exception as e:
            logger.error(f"Cold-room nudge failed: {e}")


def _on_mic_zone_engaged(event: CameraEvent):
    """MIC_ZONE_ENGAGED — person entered mic zone, load their cached info."""
    payload = event.data
    person_id = payload.get("person_id")
    appearance = payload.get("appearance")
    logger.info(
        f"MIC_ZONE_ENGAGED — person {person_id} "
        f"(appearance_ready={payload.get('appearance_ready')}, "
        f"visits={payload.get('mic_zone_visits')})"
    )
    _mic_zone_sessions.append({
        "timestamp": time.time(),
        "event": "engaged",
        "person_id": person_id,
        "appearance": appearance,
        "room_person_count": payload.get("room_person_count"),
    })


def _on_mic_zone_left(event: CameraEvent):
    payload = event.data
    person_id = payload.get("person_id")
    logger.info(f"MIC_ZONE_LEFT — person {person_id}")
    _mic_zone_sessions.append({
        "timestamp": time.time(),
        "event": "left",
        "person_id": person_id,
    })


def _on_mic_zone_waiting(event: CameraEvent):
    """MIC_ZONE_WAITING — someone is waiting behind the person at the mic."""
    payload = event.data
    logger.info(
        f"MIC_ZONE_WAITING — queue_behind={payload.get('queue_behind')} "
        f"crowd={payload.get('crowd_size')}"
    )
    _mic_zone_sessions.append({
        "timestamp": time.time(),
        "event": "waiting",
        "queue_behind": payload.get("queue_behind"),
        "crowd_size": payload.get("crowd_size"),
    })


def _on_scene_observation(event: CameraEvent):
    """SCENE_OBSERVATION — periodic coarse environment read (VLM reality sync)."""
    payload = event.data
    logger.info(
        f"SCENE_OBSERVATION — crowd={payload.get('crowd_size')} "
        f"queue_behind={payload.get('queue_behind')} "
        f"desc={payload.get('description')!r}"
    )
    _latest_scene.clear()
    _latest_scene.update({"timestamp": time.time(), **payload})


def _on_hand_raise_response(event: CameraEvent):
    """HAND_RAISE_RESPONSE — person raised hand, system should respond."""
    payload = event.data
    person_id = payload.get("person_id")
    appearance = payload.get("appearance")
    in_mic = payload.get("in_mic_zone", False)
    logger.info(
        f"HAND_RAISE_RESPONSE — person {person_id} "
        f"(in_mic={in_mic}, appearance={appearance})"
    )


def _on_person_seated(event: CameraEvent):
    """PERSON_SEATED — person entered a bench/seating zone."""
    payload = event.data
    person_id = payload.get("person_id")
    zone = payload.get("zone")
    logger.info(f"PERSON_SEATED — person {person_id} in {zone}")
    _bench_events.append({
        "timestamp": time.time(),
        "event": "seated",
        "person_id": person_id,
        "zone": zone,
        "age_group": payload.get("age_group"),
    })


def _on_person_stood_up(event: CameraEvent):
    """PERSON_STOOD_UP — person left a bench/seating zone."""
    payload = event.data
    person_id = payload.get("person_id")
    zone = payload.get("from_zone")
    dwell = payload.get("dwell_sec")
    logger.info(f"PERSON_STOOD_UP — person {person_id} left {zone} (dwell={dwell}s)")
    _bench_events.append({
        "timestamp": time.time(),
        "event": "stood_up",
        "person_id": person_id,
        "from_zone": zone,
        "dwell_sec": dwell,
    })


def _on_room_demographics(event: CameraEvent):
    """ROOM_DEMOGRAPHICS — child/adult/elderly counts changed."""
    payload = event.data
    counts = payload.get("counts", {})
    logger.info(
        f"ROOM_DEMOGRAPHICS — total={payload.get('total')} {counts} "
        f"children={payload.get('has_children')}"
    )
    _latest_demographics.clear()
    _latest_demographics.update({
        "timestamp": time.time(),
        **payload,
    })


# ── Legacy buffer flush (backward compat for low-level person_entered) ───────

def _schedule_flush(camera_id: str):
    with _buffer_lock:
        existing = _flush_timers.get(camera_id)
        if existing:
            existing.cancel()
        t = threading.Timer(GROUP_WINDOW_SEC, _flush_camera_buffer, args=[camera_id])
        t.daemon = True
        t.start()
        _flush_timers[camera_id] = t


def _flush_camera_buffer(camera_id: str):
    with _buffer_lock:
        persons = list(_entry_buffer.pop(camera_id, []))
        _flush_timers.pop(camera_id, None)
    if not persons:
        return
    client = _get_client()
    for p in persons:
        crop_b64 = p.data.get("person_crop_b64")
        if crop_b64 and client:
            try:
                caption = _vlm_caption_single(crop_b64, client)
                _store_description(p.person_id, camera_id, p.timestamp, caption, p.data.get("bbox"))
            except Exception as e:
                logger.error(f"Caption failed for person {p.person_id}: {e}")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.post("/events", status_code=202)
async def receive_event(event: CameraEvent, background_tasks: BackgroundTasks):
    _event_log.append({**event.model_dump(), "_received_at": time.time()})
    et = event.event_type

    if et == "person_entered":
        background_tasks.add_task(_on_person_entered, event)
    elif et == "hand_raised":
        background_tasks.add_task(_on_hand_raised, event)
    elif et == "BATCH_INVITE":
        background_tasks.add_task(_on_batch_invite, event)
    elif et == "COLD_ROOM_INVITE":
        background_tasks.add_task(_on_cold_room_invite, event)
    elif et == "MIC_ZONE_ENGAGED":
        background_tasks.add_task(_on_mic_zone_engaged, event)
    elif et == "MIC_ZONE_LEFT":
        background_tasks.add_task(_on_mic_zone_left, event)
    elif et == "MIC_ZONE_WAITING":
        background_tasks.add_task(_on_mic_zone_waiting, event)
    elif et == "SCENE_OBSERVATION":
        background_tasks.add_task(_on_scene_observation, event)
    elif et == "HAND_RAISE_RESPONSE":
        background_tasks.add_task(_on_hand_raise_response, event)
    elif et == "PERSON_SEATED":
        background_tasks.add_task(_on_person_seated, event)
    elif et == "PERSON_STOOD_UP":
        background_tasks.add_task(_on_person_stood_up, event)
    elif et == "ROOM_DEMOGRAPHICS":
        background_tasks.add_task(_on_room_demographics, event)

    return {"status": "accepted", "event_type": et}


@app.get("/persons")
def list_persons():
    return list(_person_descriptions.values())


@app.get("/persons/{person_id}")
def get_person(person_id: int):
    from fastapi import HTTPException
    desc = _person_descriptions.get(person_id)
    if desc is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return desc


@app.get("/greetings")
def list_greetings():
    return list(reversed(list(_greetings)))


@app.get("/hand_raises")
def list_hand_raises():
    return _hand_raises


@app.get("/mic_zone_sessions")
def list_mic_zone_sessions(limit: int = 50):
    return list(reversed(list(_mic_zone_sessions)[-limit:]))


@app.get("/batch_invites")
def list_batch_invites():
    return list(reversed(list(_batch_invites)))


@app.get("/events")
def list_events(limit: int = 100):
    return list(reversed(list(_event_log)[-limit:]))


# NOTE: /podium_events, /bench_events, /demographics removed — no consumer.
# The real downstream is lia_agent_api, not this dev-sandbox agent_server.


@app.get("/health")
def health():
    # Minimal — this server is a dev sandbox, not the real consumer.
    return {"status": "ok", "version": "0.3.0"}


# ── TTL sweeper ──────────────────────────────────────────────────────────────

def _sweep_stale_entries() -> None:
    """Prune per-person dicts whose newest entry is older than its TTL.

    Called periodically from the background asyncio task. Safe to run
    concurrently with FastAPI handlers since Python dicts are thread-safe
    for single get/set, and we iterate over a list copy.
    """
    now = time.time()

    # _hand_raises: prune persons whose most-recent raise is older than TTL
    pruned_hand = 0
    for pid in list(_hand_raises.keys()):
        records = _hand_raises.get(pid) or []
        if not records:
            _hand_raises.pop(pid, None)
            pruned_hand += 1
            continue
        newest = max(r.get("timestamp", 0) for r in records)
        if now - newest > HAND_RAISE_TTL_SEC:
            _hand_raises.pop(pid, None)
            pruned_hand += 1

    # _person_descriptions: prune by stored timestamp
    pruned_desc = 0
    for pid in list(_person_descriptions.keys()):
        desc = _person_descriptions.get(pid)
        if desc is None:
            continue
        ts = getattr(desc, "timestamp", None)
        if ts is None or now - ts > PERSON_DESC_TTL_SEC:
            _person_descriptions.pop(pid, None)
            pruned_desc += 1

    # _entry_buffer: prune camera buffers whose newest event is older than TTL
    pruned_entry = 0
    with _buffer_lock:
        for cam_id in list(_entry_buffer.keys()):
            buf = _entry_buffer.get(cam_id) or []
            if not buf:
                _entry_buffer.pop(cam_id, None)
                pruned_entry += 1
                continue
            newest = max(getattr(ev, "timestamp", 0) for ev in buf)
            if now - newest > HAND_RAISE_TTL_SEC:
                _entry_buffer.pop(cam_id, None)
                pruned_entry += 1

    _sweep_stats["hand_raises_pruned"] += pruned_hand
    _sweep_stats["person_descriptions_pruned"] += pruned_desc
    _sweep_stats["entry_buffers_pruned"] += pruned_entry
    _sweep_stats["last_sweep_ts"] = int(now)

    if pruned_hand or pruned_desc or pruned_entry:
        logger.info(
            f"TTL sweep pruned: hand_raises={pruned_hand} "
            f"descriptions={pruned_desc} entry_buffers={pruned_entry}"
        )


async def _sweep_loop() -> None:
    """Background task: runs _sweep_stale_entries every SWEEP_INTERVAL_SEC."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SEC)
            _sweep_stale_entries()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"TTL sweep failed: {e}")


@app.on_event("startup")
async def _start_sweeper():
    import asyncio
    asyncio.create_task(_sweep_loop())
    logger.info(
        f"TTL sweeper started: interval={SWEEP_INTERVAL_SEC}s "
        f"hand_raise_ttl={HAND_RAISE_TTL_SEC}s desc_ttl={PERSON_DESC_TTL_SEC}s"
    )
