# trpl-lemonslice-frountend

LiveKit + LemonSlice avatar frontend that uses **lia_agent_api** as the brain.

The worker bridges LiveKit sessions to `lia_agent_api`, Azure Speech, and an
optional avatar provider. Set `AVATAR_ENABLED=false` to publish Azure TTS audio
directly to LiveKit without LemonSlice, licensed persona assets, or a
`LEMONSLICE_MODEL`.

```
Browser ── WebRTC ──► LiveKit Cloud ──► livekit_agent.py (this repo)
                                            │
                                            │  STT text  ─► bridge ─► WS to
                                            │                        lia_agent_api
                                            │
                                            │                    streams text
                                            │                    deltas back
                                            ▼
                                      Azure TTS ─► LemonSlice avatar ─►
                                                   video track
                                                   (back to browser via WebRTC)
```

## Why streaming doesn't break

Every hop preserves streaming. The only chunking point is the 60-char /
`.!?\n` flush that already exists inside `lia_agent_api` — we don't add any
further buffering. Each `debate_output` delta that arrives over the WebSocket
is forwarded to `session.say(async_iterator)` immediately, which pushes it
into Azure TTS text-stream mode, which emits PCM chunks as they are
synthesized, which LemonSlice consumes in real time.

See `lia_agent_api_bridge.py` for the implementation.

## Files

| File | Purpose |
|---|---|
| `lia_agent_api_bridge.py` | WebSocket client to lia_agent_api; one class per room. Starts a `scenario=storys` session, ships user input in, yields streaming TR text out. |
| `livekit_agent.py` | LiveKit worker — STT + TTS + LemonSlice + bridge pumps. Auto-dispatched by LiveKit Cloud when a room opens. |
| `token_server.py` | FastAPI server — mints LiveKit JWTs and serves `index.html`. Independent from the agent worker. |
| `index.html` | Minimal browser SPA: video pane + chat + mic button. |
| `requirements.txt` | Runtime deps (livekit-agents, azure, lemonslice, aiohttp, fastapi). |
| `.env.example` | Template — copy to `.env` and fill in. |

## Prerequisites

1. **lia_agent_api** running and reachable (default `http://localhost:8000`).
   - In `lia_agent_api/.env`, set `CLIENT_API_KEYS=some-shared-secret`
     (otherwise `/api/debate/start` returns 401).
   - `TTS_ENABLED=False` is recommended here — LiveKit does TTS, so having
     lia_agent_api also produce audio is wasted Azure spend.
   - The `storys/` folder (at repo root) must be present so the story RAG
     can find `quiz_stories/` and `this_day_in_history.json`.
2. **LiveKit Cloud** project (or self-hosted LiveKit) — get API key + secret.
3. **LemonSlice** — agent_id (or a hosted avatar image URL) + API key.
4. **Azure Speech** — one subscription key works for both STT and TTS.

## Setup

```bash
cd trpl-lemonslice-frountend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in .env
```

## Run (three processes)

```bash
# Terminal 1 — the brain
cd ../lia_agent_api
./startup.sh                       # or: python run_server.py

# Terminal 2 — the LiveKit worker (connects to LiveKit Cloud)
cd trpl-lemonslice-frountend
python livekit_agent.py dev

# Terminal 3 — token server + HTML
cd trpl-lemonslice-frountend
python token_server.py             # http://localhost:8001
```

Open http://localhost:8001 in a browser, click "▶", allow mic when the
button lights up.

## Interaction model

- **First turn**: lia_agent_api's storys node runs round-1 pre-greeting
  immediately when the WebSocket connects (it's static, zero-LLM — see
  `src/debate/nodes/storys/greeting.py`). That text streams out and LemonSlice
  speaks it before the user says anything.
- **User speaks** (mic) → Azure STT final transcript → bridge posts
  `participant_input` → storys generates a response → streams back → TTS →
  avatar speaks.
- **Text input** → same path via LiveKit data channel (topic
  `lk-chat-topic`).

## Knobs

| Env var | Effect |
|---|---|
| `LIA_VISITOR_MODE=adult\|child` | Session-level audience mode. Overrides per-visitor. |
| `AZURE_TTS_VOICE=joe_2` | Custom voice name or deployment. `joe_2` matches the default LemonSlice avatar. |
| `AVATAR_ENABLED=true\|false` | Globally enable or skip LemonSlice. Audio remains available when disabled. |
| `LEMONSLICE_AGENT_ID` | Picks a specific avatar persona. |
| `LEMONSLICE_AVATAR_IMAGE_URL` | Alternative to `LEMONSLICE_AGENT_ID` — a public URL to a still image of TR. |

## Limits / known rough edges

- `person_memory` (returning-visitor recognition) depends on
  `camera_state.active_mic_person`, which is always `None` in this
  topology. Returning-visitor features effectively disabled.
- `today_in_history` still works (adult mode only, reads
  `storys/this_day_in_history.json` by calendar date).
- Graceful reconnect on lia_agent_api flake is **not** implemented yet —
  the worker will log and exit the job, LiveKit Cloud will dispatch a new
  worker when a new room opens.
- LiveKit plugin API shapes vary between minor versions of
  `livekit-agents`; see TODO comments in `livekit_agent.py` if the
  `user_input_transcribed` event or `session.say(async_iter)` signature
  changes.
