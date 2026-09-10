# Camera Platform

Multi-camera room vision perception + zone-based scene orchestration service.

**This is not a dialog agent, and not a TTS/avatar service.**
Its job is: **see → judge → emit events → stream structured information to the downstream service** (over a hosted WebSocket).

---

## One-line summary

> **Camera Platform = real-time YOLO person tracking + zone detection + per-person state machine + event publisher**

---

## Local acceptance

The repository supports a hardware-free event-contract test, uploaded video,
and a Docker Desktop host-camera stream. See
[`Layer4_Talk_to_TR/LOCAL_DEVELOPMENT.md`](../../LOCAL_DEVELOPMENT.md) for the
current setup and verification paths. Camera hardware, model weights, VLM
services, and source media are operator-provided and are not included.

---

## How the downstream conversation system talks to this system

For a hardware-free contract check, the shared Layer 4 profile includes a
fictional JSONL fixture and authenticated replay support:

```bash
export LIA_API_KEY=<CLIENT_API_KEYS value from lia_agent_api/.env.local>
python scripts/replay_events.py \
  --dir fixtures \
  --file local-events.jsonl \
  --url http://127.0.0.1:8010
```

This verifies event intake without a camera, model weights, visitor imagery, or
biometric data. It does not represent acceptance of the real perception stack.

camera_platform is **pure perception** — it never speaks to visitors. It watches
the room, decides *what just happened*, and **pushes events** to a downstream
**Agent Server** (`lia_agent_api`), which owns the dialog state machine and
drives the avatar's voice/face. The "conversation" between the two systems is a
one-way **event stream** (camera → agent) plus a small **session lookup**
(agent → exposes which session to attach to).

### The handshake

```
camera_platform                              Agent Server (lia_agent_api)
───────────────                              ────────────────────────────
  boot ──► GET /api/admin/kiosk-session ────► { "session_id": "K-abc123" }
           (X-API-Key: AGENT_SERVER_API_KEY)      ▲ which live kiosk session
                                                    to attach events to

  scene event ──► POST /api/camera/events ──►  202/200  → agent decides what
   (per event)     { envelope, session_id }            the avatar should say
                                                 404    → session rotated;
                                              re-fetch kiosk-session, retry
```

### Event envelope (what we POST per event)

```jsonc
{
  "event_id":   "evt_8f1c2a",
  "timestamp":  "2026-06-09T09:27:31+00:00",   // ISO-8601 UTC
  "source":     "camera_service",
  "event_type": "MIC_ZONE_ENGAGED",
  "payload":    { "person_id": 3,
                  "appearance": { "top": "hi-vis vest", "bottom": "tan pants",
                                  "age_group": "adult" } },
  "session_id": "K-abc123"
}
```

### How each event becomes a conversation move

| camera_platform emits | …the avatar does (on the agent side) |
|-----------------------|--------------------------------------|
| `PERSON_ENTERED_ROOM` | notes a new visitor (state update) |
| `BATCH_INVITE` (N new arrivals + appearances) | greets the group ("welcome, folks in the hi-vis vests…") |
| `MIC_ZONE_ENGAGED` | transitions to the **welcome/dialog** phase and starts talking to that person |
| `HAND_RAISE_RESPONSE` | acknowledges and responds to the raised hand |
| `ENGAGEMENT_SNAPSHOT` | adjusts tone from posture/gaze (attentive vs. leaving) |
| `MIC_ZONE_LEFT` | wraps up and returns to idle / camera phase |
| `VISITOR_HESITATING` / `VISITOR_IDLE_IN_ROOM` | optional nudges ("come on in") |

So a real exchange reads: *person walks in* → `PERSON_ENTERED_ROOM` →
*VLM describes them* → `BATCH_INVITE` (avatar greets) → *they step to the mic* →
`MIC_ZONE_ENGAGED` (avatar starts the conversation) → *they raise a hand* →
`HAND_RAISE_RESPONSE` → *they walk away* → `MIC_ZONE_LEFT` (avatar resets).

### Enabling forwarding

Forwarding is **off** in the verified run above (`--no-agent`) because no
matching Agent Server is running on this host. To turn it on, point at a real
`lia_agent_api` (which exposes `/api/camera/events` + `/api/admin/kiosk-session`)
and drop the flag:

```bash
export AGENT_SERVER_URL=http://<agent-host>:8010
export AGENT_SERVER_API_KEY=<same key the kiosk worker uses>
python main.py --api --port 5001            # no --no-agent
```

> Note: the bundled `agent_server/` in this repo is an **older reference** that
> exposes `POST /events` — it does **not** match the `/api/camera/events`
> contract above and is not the production sink. The real target is the external
> `lia_agent_api`.

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Camera Platform                              │
│                                                                     │
│  ┌──────────┐   frame   ┌────────────────────────────────────────┐  │
│  │ Camera   │──queue──▶│        RoomMonitor (main thread)        │  │
│  │ Source   │           │                                        │  │
│  │ (thread) │           │  YOLO Track ─▶ ReID ─▶ HandDetector   │  │
│  └──────────┘           │       │                    │           │  │
│                         │       ▼                    ▼           │  │
│                         │  EventManager      SceneOrchestrator   │  │
│                         │  (person states)   (zone + invite)     │  │
│                         │       │                    │           │  │
│                         └───────┼────────────────────┼───────────┘  │
│                                 │                    │              │
│                    ┌────────────┼────────────────────┼──────┐      │
│                    │            ▼                    ▼      │      │
│                    │     EventPublisher    VLMObserverWorker │      │
│                    │   (WS server + stack) (appearance VLM) │      │
│                    └────────────┬────────────────────────────┘      │
│                                 │  ws://:8765  (consume-once stack)  │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │ downstream connects IN and drains
                                  ▼
                        ┌──────────────────┐
                        │  Downstream      │
                        │  (WS client,     │
                        │   e.g. lia_agent)│
                        │  greeting/dialog │
                        └──────────────────┘
```

> **Transport:** by default camera_platform *hosts* a WebSocket server and
> pushes every event onto a consume-once FIFO stack; the downstream service
> connects in and drains it. A legacy HTTP-POST mode is still available
> (`EVENT_TRANSPORT=http`). See [Event system](#event-system).

---

## Data flow in detail

```
Camera Frame (per-camera thread, ~30fps)
    │
    ▼
┌─ RoomMonitor.process_frame() ─────────────────────────────────┐
│                                                                │
│  1. YOLO.track()         → person detection + BoT-SORT tracking│
│  2. PersonReID           → cross-camera matching (OSNet + pos) │
│  3. HandDetector         → body-frame hand raise + temporal vote│
│  4. EventManager         → record low-level events             │
│                             (person_entered, etc.)             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
    │
    ▼ every 200ms
┌─ SceneOrchestrator.tick() ────────────────────────────────────┐
│                                                                │
│  5. ZoneDetector         → check if person is in entry/mic zone│
│  6. PersonCache          → manage per-person cache             │
│                             (appearance, invite, 30s TTL)     │
│  7. State machine        → publish semantic events             │
│     - Entry zone (dwell+frames gate) → PERSON_ENTERED_ROOM     │
│     - Debounce → BATCH_INVITE (welcome; solo/multi)           │
│     - 30s dead air → COLD_ROOM_INVITE ("don't be shy")        │
│     - Mic zone → MIC_ZONE_ENGAGED / MIC_ZONE_LEFT             │
│     - Someone waiting behind mic → MIC_ZONE_WAITING (VLM)      │
│     - Hand raise → HAND_RAISE_RESPONSE (anywhere)              │
│                                                                │
└────────────────────────────────────────────────────────────────┘
    │
    ▼ EventPublisher → consume-once stack → WS server (:8765)
┌─ Downstream (WS client) ──────────────────────────────────────┐
│                                                                │
│  8. Connect in, drain the stack, dispatch by event_type        │
│  9. BATCH_INVITE → generate a welcome greeting                 │
│  10. MIC_ZONE_ENGAGED → load person description, prep dialog   │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## Core concepts

### Two zones

Two fixed polygon regions are defined on the camera frame:

```
┌──────────────────────────────────────────┐
│ Camera Frame                             │
│                                          │
│ ┌─────────┐                              │
│ │ ENTRY   │     (main room area)         │
│ │ ZONE    │                              │
│ │ (door)  │        ┌──────────┐          │
│ │         │        │ MIC ZONE │          │
│ │         │        │ (podium) │          │
│ └─────────┘        └──────────┘          │
│                                          │
└──────────────────────────────────────────┘
```

| Zone | Purpose | Notes |
|------|---------|-------|
| **Entry Zone** | Entry detection | Only people who first appear in this region are registered |
| **Mic Zone** | Interaction trigger | When a person enters this region, the system loads their cache and triggers dialog |

Zones use **normalized polygon coordinates** (0.0–1.0), so they work at any resolution.

### Per-person state machine

Each person is tracked independently — no group aggregation:

```
                    ┌──────────────────┐
                    │ Entry Zone hit   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │    ENTERED       │ → snapshot + VLM appearance
                    │  (added to cache)│ → added to pending invite
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │     IDLE         │ ← Avatar ignores this person
                    │  (inside room)   │
                    └───┬─────────┬────┘
                        │         │
           ┌────────────┘         └──────────────┐
           ▼                                     ▼
  ┌──────────────────┐                  ┌──────────────────┐
  │  IN_MIC_ZONE     │                  │  HAND_RAISED     │
  │  (entered podium)│                  │  (raised anywhere)│
  │                  │                  │                  │
  │ → load cache     │                  │ → system responds│
  │ → trigger dialog │                  │                  │
  └──────────────────┘                  └──────────────────┘
```

**Interaction rule:** Unless a person is in the mic zone or has their hand raised, the avatar completely ignores everyone in the room.

### Debounced batch invite

When multiple people walk in one after another, they are not invited one by one:

```
  T=0s   Person A enters → added to queue, starts 5s countdown
  T=2s   Person B enters → added to queue, resets countdown
  T=3s   Person C enters → added to queue, resets countdown
  T=8s   5s with no new arrivals → send BATCH_INVITE
                    payload: {person_ids: [A,B,C], total: 3, model: "multi"}
```

- `model_choice: "solo"` — exactly 1 person in the room
- `model_choice: "multi"` — more than 1 person in the room

### Per-person cache

Every person who passes the entry zone gets a cache entry (cleared automatically 30s after they disappear):

```json
{
  "person_id": 3,
  "appearance": {"top": "white shirt", "bottom": "black pants", "notable": "glasses"},
  "invited": true,
  "invite_batch_id": "inv_0001",
  "in_mic_zone": false,
  "scene_state": "idle",
  "mic_zone_visits": 0,
  "hand_raise_count": 0
}
```

---

## Module architecture

### Dependency graph

```
main.py
  └─▶ RoomMonitor ─────────────────────────────────────────────┐
       ├─▶ CameraManager          multi-camera capture + queues │
       ├─▶ YOLO                   person detection + tracking   │
       ├─▶ PersonReID             cross-camera ReID (OSNet)     │
       ├─▶ HandDetector           body-frame hand raise detect  │
       ├─▶ EventManager           low-level events + state      │
       ├─▶ ZoneDetector           polygon zone detection        │
       ├─▶ PersonCache            per-person cache (TTL)        │
       ├─▶ SceneOrchestrator      state machine + debounce      │
       ├─▶ VLMObserverWorker      async VLM appearance captions │
       ├─▶ EventPublisher         WS server + consume-once stack│
       └─▶ SnapshotManager        frame buffer access           │
  └─▶ RoomMonitorAPI (Flask)
       └─▶ WebDashboard           embedded web dashboard        │
```

### Module responsibilities

| Layer | Module | File | Role |
|-------|--------|------|------|
| **Entry** | CLI | `main.py` | Argument parsing, logging, startup |
| **Infrastructure** | CameraManager | `src/core/camera_manager.py` | Multi-source camera registry, threaded reading, frame queues |
| | SnapshotManager | `src/core/snapshot_manager.py` | Ring buffer access for frames |
| **Detection** | YOLO + BoT-SORT | external library | Person detection + tracking |
| | PersonReID | `src/core/person_reid.py` | OSNet feature extraction, quality-weighted matching |
| | HandDetector | `src/core/hand_detector.py` | Body-frame multi-criteria scoring + temporal voting |
| **State** | EventManager | `src/core/event_manager.py` | Low-level event log, PersonState, webhook dispatch |
| | PersonCache | `src/core/person_cache.py` | Per-person cache (appearance, invite, TTL) |
| **Orchestration** | ZoneDetector | `src/core/zone_detector.py` | Normalized polygon point-in-polygon check |
| | SceneOrchestrator | `src/core/scene_orchestrator.py` | Per-person state machine, debounced invite, zone triggers |
| **Coordination** | RoomMonitor | `src/core/room_monitor.py` | Top-level coordinator: fast path (YOLO) + slow path (orchestrator) |
| **External** | EventPublisher | `src/services/event_publisher.py` | Hosts WS server + consume-once event stack (legacy HTTP POST fallback) |
| | VLMObserverWorker | `src/services/vlm_observer.py` | Async VLM appearance captions (semaphore-bounded concurrency) |
| | RoomMonitorAPI | `src/services/api_server.py` | Flask REST API + CORS |
| **Downstream** | WS consumer | (e.g. `lia_agent_api`) | Connects to the WS server, drains events, drives dialog |
| **Downstream (dev)** | Agent Server | `agent_server/main.py` | Legacy HTTP-mode sandbox: event intake + VLM greeting generation |
| **Tools** | Zone Labeler | `tools/zone_labeler.py` | Mouse-based tool to label zone polygons |
| **Config** | Config | `src/utils/config.py` | Centralized parameter config |
| | Image Utils | `src/utils/image.py` | Image encoding helpers (crop → base64) |

---

## Thread model

```
┌─ Main Thread ─────────────────────────────────────────────┐
│  RoomMonitor.run()                                        │
│    ├─ read_all_frames()                                   │
│    ├─ process_frame() × N cameras                         │
│    │   └─ YOLO → ReID → HandDetect → EventManager         │
│    └─ SceneOrchestrator.tick() (every 200ms)              │
│        └─ Zone check → state transition → publish event   │
└───────────────────────────────────────────────────────────┘

┌─ Per-Camera Thread (daemon) ──────────────────────────────┐
│  CameraSource._read_loop()                                │
│    └─ continuously read frames → frame_ring_buffer (queue)│
└───────────────────────────────────────────────────────────┘

┌─ VLM Worker Thread (daemon) ─────────────────────────────┐
│  VLMObserverWorker._run()                                │
│    └─ consume queue → Semaphore(2) → OpenAI API          │
└───────────────────────────────────────────────────────────┘

┌─ Event Publisher / WS server (daemon thread) ─────────────┐
│  EventPublisher                                           │
│    └─ push event → consume-once stack → WS clients drain   │
│       (or legacy HTTP POST when EVENT_TRANSPORT=http)      │
└───────────────────────────────────────────────────────────┘

┌─ Flask API Thread (daemon, optional) ────────────────────┐
│  RoomMonitorAPI.run_threaded()                            │
│    └─ REST API + Web Dashboard                            │
└───────────────────────────────────────────────────────────┘
```

**Thread safety:**
- `CameraManager` — RLock around the camera dict
- `CameraSource` — Queue-based frame handoff (inherently thread-safe)
- `EventManager` — Lock around webhook/callback registries
- `VLMObserverWorker` — Queue + semaphore for concurrency control
- `RoomMonitor` — Lock around the MJPEG stream buffer

---

## Event system

### Transport — how downstream consumes our output

By default (`EVENT_TRANSPORT=ws`) camera_platform **hosts a WebSocket server**
and the downstream service connects in to read events:

- camera_platform listens on `ws://<host>:${EVENT_WS_PORT:-8765}`.
- Every event is pushed onto a **consume-once FIFO stack**. A connected client
  receives one JSON envelope per text frame; each event is removed once sent.
- **Consume-once, single reader** — built for ONE downstream consumer (events
  are drained, not broadcast/fanned-out).
- While no client is connected, events buffer in the stack (bounded by
  `EVENT_STACK_MAXLEN`, default 1000; oldest dropped past that). A reconnecting
  client resumes from the front of the backlog. Every event is also appended to
  a daily JSONL audit log (`event_log/<date>.jsonl`) regardless.

Minimal downstream consumer (see [`scripts/ws_consumer.py`](scripts/ws_consumer.py)):

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8765") as ws:
        async for frame in ws:
            event = json.loads(frame)
            dispatch(event["event_type"], event["payload"])

asyncio.run(main())
```

Legacy mode: `EVENT_TRANSPORT=http` makes camera_platform POST each event to
`{AGENT_SERVER_URL}/api/camera/events` instead (3× retry).

### Envelope format

All events are wrapped in a uniform envelope:

```json
{
  "event_id": "evt_a1b2c3d4",
  "timestamp": "2026-03-18T10:30:00+00:00",
  "source": "camera_service",
  "event_type": "BATCH_INVITE",
  "payload": { ... },
  "session_id": null
}
```

`session_id` is `null` over WS; it carries the kiosk session only in HTTP mode.

### Event types

#### High-level semantic events (emitted by SceneOrchestrator)

| Event | Trigger | Key payload fields |
|-------|---------|--------------------|
| `PERSON_ENTERED_ROOM` | Confirmed in entry zone (dwell + frame-count gate) | `person_id`, `room_person_count` |
| `BATCH_INVITE` | Welcome for new arrivals after debounce | `batch_id`, `person_ids`, `model_choice`, `appearances` |
| `COLD_ROOM_INVITE` | Room cold (un-engaged people, nobody at mic) ≥30s; one-shot, re-armed by new arrivals | `idle_person_ids`, `appearances`, `cold_duration_sec` |
| `MIC_ZONE_ENGAGED` | Person confirmed in mic zone | `person_id`, `appearance`, `mic_zone_visits` |
| `MIC_ZONE_LEFT` | Person continuously out of mic zone > 2s | `person_id` |
| `MIC_ZONE_WAITING` | Someone waiting behind the mic person (geometric + VLM-confirmed; one-shot) | `queue_behind`, `crowd_size` |
| `SCENE_OBSERVATION` | Fixed-interval (5 min) VLM environment snapshot, regardless of occupancy | `crowd_size`, `queue_behind`, `description` |
| `HAND_RAISE_RESPONSE` | Person raised a hand anywhere | `person_id`, `appearance`, `in_mic_zone` |

Additional situational events: `APPROACHING_OCCUPIED_PODIUM`,
`GROUP_AROUND_PODIUM`, `PERSON_SEATED` / `PERSON_STOOD_UP`, `ROOM_DEMOGRAPHICS`,
`ENGAGEMENT_SNAPSHOT`, `VISITOR_DEPARTING`.

#### Low-level detection events (logged by EventManager)

| Event | Trigger |
|-------|---------|
| `person_entered` | YOLO first confirms a global person ID |
| `person_left` | Not seen for 30s |
| `hand_raised` | Hand raise detected (temporal confirmation) |
| `hand_lowered` | Hand lowered |

---

## Quick start

### 1. Environment setup

```bash
cd camera_platform
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Label the zones (required the first time you deploy)

Label the entry and mic regions on a screenshot of the camera view:

```bash
python tools/zone_labeler.py <screenshot_path>
```

| Action | Description |
|--------|-------------|
| Left click | Add a polygon vertex |
| Right click | Undo the last vertex |
| Enter | Confirm the current zone (entry_zone first, then mic_zone) |
| R | Redo the current zone |
| Q / ESC | Quit |

When finished, copy the printed coordinates into `ZONE_CONFIG` in `src/utils/config.py`:

```python
ZONE_CONFIG = {
    "entry_zone": [
        (0.0, 0.02),
        (0.23, 0.0),
        (0.23, 0.99),
        (0.0, 1.0),
    ],
    "mic_zone": [
        (0.38, 0.57),
        (0.62, 0.57),
        (0.62, 0.93),
        (0.38, 0.93),
    ],
}
```

### 3. Start a downstream consumer

By default camera_platform hosts a WebSocket server and the downstream connects
in. For local testing, use the bundled consumer:

```bash
python scripts/ws_consumer.py ws://127.0.0.1:8765   # prints every event
```

> The `agent_server/` FastAPI app is a **legacy HTTP-mode dev sandbox** (it
> generates VLM greetings and keeps history). Use it only with
> `EVENT_TRANSPORT=http`:
> ```bash
> cd agent_server && cp .env.example .env   # set LLM_API_KEY, LLM_BASE_URL
> uvicorn agent_server.main:app --port 8000 --reload
> ```

### 4. Start the Camera Platform

```bash
# Basic run (hosts the event WS server on :8765 by default)
python main.py

# With REST API + Web Dashboard
python main.py --api --port 5000

# With a visualization window (shows zone boundaries + person states)
python main.py --api --demo

# Disable event forwarding (pure local debug)
python main.py --no-agent
```

### 5. Start the remote camera server (if needed)

```bash
python camera/stream_server.py --port 9090
```

---

## Configuration

All parameters are centralized in `src/utils/config.py` — no magic numbers elsewhere.

### Cameras

```bash
export CAMERA_SOURCE=/path/to/video.mp4
export CAMERA_WIDTH=1280
export CAMERA_HEIGHT=720
```

### YOLO model

```python
YOLO_CONFIG = {
    "model_path": "yolov8n-pose.pt",   # nano (fast); swap for yolov8s-pose.pt (more accurate)
    "imgsz": 960,
    "conf_threshold": 0.25,
    "device": 0,                        # GPU; -1 = CPU
}
```

### Zones

```python
ZONE_CONFIG = {
    "entry_zone": [(x, y), ...],        # entry polygon (normalized 0–1)
    "mic_zone": [(x, y), ...],          # mic polygon (normalized 0–1)
}
```

### Scene orchestration

```python
SCENE_CONFIG = {
    "invite_debounce_sec": 5.0,         # how long to wait after new arrivals before sending the batch invite
    "mic_zone_confirm_sec": 1.0,        # how long a person must stay in the mic zone to trigger dialog
    "cache_ttl_sec": 30.0,              # how long after the person disappears before the cache is cleared
}
```

### Person detection

```python
PERSON_CONFIG = {
    "person_timeout_sec": 30,           # mark as left after this long without detection
    "cleanup_delay_sec": 5,             # delay between "left" and full cleanup
}
```

### Hand raise

```python
HAND_RAISE_CONFIG = {
    "cooldown_sec": 30.0,               # cooldown after a trigger (HandDetector's scoring parameters have their own defaults)
}
```

### VLM appearance description

```python
VLM_CONFIG = {
    "enabled": True,
    "model": "gpt-4.1-mini",
    "timeout_sec": 15.0,
    "max_concurrent": 2,
    "appearance_prompt": "JSON only: {top, bottom, notable}",
}
```

### Event delivery (downstream forwarding)

```python
AGENT_SERVER_CONFIG = {
    "enabled": True,
    "transport": "ws",          # "ws" (default) | "http"  (env EVENT_TRANSPORT)

    # WebSocket transport (camera hosts; downstream connects in)
    "ws_host": "0.0.0.0",       # env EVENT_WS_HOST
    "ws_port": 8765,            # env EVENT_WS_PORT
    "stack_maxlen": 1000,       # env EVENT_STACK_MAXLEN — consume-once stack bound

    # Legacy HTTP transport (camera POSTs out)
    "url": "http://localhost:8000",   # env AGENT_SERVER_URL
    "api_key": "",                    # env AGENT_SERVER_API_KEY (admin lookups)

    "include_person_crop": True,
    "crop_jpeg_quality": 75,
}
```

---

## Project layout

```
camera_platform/
├── main.py                             entry point (argparse CLI)
│
├── src/
│   ├── core/
│   │   ├── room_monitor.py             top-level coordinator (613 lines)
│   │   ├── camera_manager.py           multi-camera management + queues (238 lines)
│   │   ├── event_manager.py            event log + person state (584 lines)
│   │   ├── hand_detector.py            body-frame hand raise detection (455 lines)
│   │   ├── overhead_hand_detector.py   overhead-view hand raise detection (266 lines)
│   │   ├── person_reid.py              cross-camera ReID (287 lines)
│   │   ├── zone_detector.py            polygon zone detection (82 lines)
│   │   ├── person_cache.py             per-person cache + TTL (202 lines)
│   │   ├── scene_orchestrator.py       state machine + debounce (231 lines)
│   │   └── snapshot_manager.py         frame buffer management (130 lines)
│   │
│   ├── services/
│   │   ├── event_publisher.py          WS server + consume-once event stack
│   │   ├── vlm_observer.py             async VLM appearance captions (165 lines)
│   │   ├── api_server.py               Flask REST API (290 lines)
│   │   ├── llm_agent.py                OpenAI GPT integration (233 lines)
│   │   └── web_dashboard.py            embedded web dashboard
│   │
│   └── utils/
│       ├── config.py                   global parameter config (159 lines)
│       └── image.py                    image encoding helpers (46 lines)
│
├── agent_server/
│   ├── main.py                         FastAPI event intake (374 lines)
│   ├── agent_config.py                 agent configuration (28 lines)
│   └── models.py                       Pydantic models (33 lines)
│
├── tools/
│   └── zone_labeler.py                 zone labeling tool (166 lines)
│
├── camera/
│   └── stream_server.py                MJPEG stream server
│
├── config/
│   └── botsort.yaml                    BoT-SORT tracker parameters
│
└── requirements.txt
```

**Total code: ~5,200 lines of Python**

---

## API endpoints

### Camera Service REST API (started with `--api`)

| Endpoint | Description |
|----------|-------------|
| `GET /` | Web dashboard |
| `GET /api/health` | Health check |
| `GET /api/room/summary` | Room status (count, hands up, event totals) |
| `GET /api/room/persons` | Current state of every person |
| `GET /api/events` | Event query (`?type=` `?person_id=` `?time_window=`) |
| `GET /api/cameras` | Camera status + FPS |
| `GET /video_feed` | MJPEG live video stream |

### Agent Server API (legacy HTTP-mode dev sandbox)

> Only used with `EVENT_TRANSPORT=http`. In the default WS mode the downstream
> connects to camera_platform's WS server instead (see [Event system](#event-system)).

| Endpoint | Description |
|----------|-------------|
| `POST /events` | Receive events from the camera service |
| `GET /persons` | Known people + VLM descriptions |
| `GET /persons/{id}` | Single-person detail |
| `GET /greetings` | Greeting history |
| `GET /hand_raises` | Hand raise log |
| `GET /mic_zone_sessions` | Mic zone enter/leave log |
| `GET /batch_invites` | Batch invite history |
| `GET /health` | Configuration + status |

---

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| **Zone-based rather than distance-based** | With a fixed camera angle, polygon regions are more precise and controllable than distance rings |
| **No group aggregation** | Real visitors don't always arrive as a group; per-person tracking is more flexible |
| **Debounced invite** | Avoids re-inviting the same cluster when 3–5 people enter one after another |
| **30s TTL cache** | No need to persist state for people who left; saves resources |
| **Dialog only triggers in mic zone** | The avatar should not speak to everyone walking by |
| **Hand raise responds anywhere** | Hand raising is an unambiguous interaction signal |
| **VLM only describes appearance** | Separates observation from decision-making; the VLM never makes business logic decisions |
| **Camera hosts the event stack** | Downstream connects in and drains a consume-once stack, so the detection loop never blocks on network I/O and events buffer safely when downstream is away |

---

## Requirements

- Python 3.11+
- GPU (CUDA) recommended; CPU also works (set `device: -1` in `config.py`)
- VLM features need the `LLM_API_KEY` environment variable (skipped automatically if unset)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port 5000 taken | macOS AirPlay grabs it; use `--port 8080` instead |
| Camera cannot open | System Settings → Privacy → Camera → allow the terminal |
| torchreid fails to load | `pip install gdown tensorboard`; falls back to ResNet50 automatically |
| VLM returns nothing | Check `LLM_API_KEY`; look for `VLM call failed` in the logs |
| Zones don't align | Re-run `tools/zone_labeler.py`; make sure the screenshot matches the deployed camera angle |
| Invites fire too often | Increase `SCENE_CONFIG["invite_debounce_sec"]` |
| Cache clears too fast | Increase `SCENE_CONFIG["cache_ttl_sec"]` |
| Hand raise is unresponsive | Lower `HandDetector`'s `score_threshold` (default 0.48) |
