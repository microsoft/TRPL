# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
LiveKit worker that wires lia_agent_api → Azure Speech and an optional avatar.

Architecture (see README.md):

    Browser ── WebRTC ──► LiveKit Cloud
                              │  auto-dispatches this worker when a room
                              │  is created
                              ▼
                       AgentSession(stt=Azure, tts=Azure, llm=None-ish)
                              │
                              │  on user transcript → bridge.send_user_text()
                              │  bridge.utterances() → session.say(async iter)
                              │
                       AvatarSession(lemonslice)   pulls audio from AgentSession,
                                                   publishes lip-synced video.

Streaming: user_text → lia_agent_api (WS, streams back text deltas) →
           session.say(iter) → TTS (Azure text-stream mode) → LemonSlice →
           browser. No full-utterance buffering.

Run with:  python livekit_agent.py dev
"""
import asyncio
import json
import logging
import os
from urllib.parse import parse_qs, urlparse

import dotenv
from env_config import env_bool, required_deployment_value
from livekit import agents, rtc
from livekit.agents import Agent, AgentSession, AutoSubscribe, ErrorEvent, RoomInputOptions
from livekit.agents.llm import LLMError
from livekit.agents.stt import STTError
from livekit.agents.tts import TTSError
from livekit.plugins import azure, lemonslice

# Optional debug overlay: when SHOW_PHASE_DEBUG=True in .env, the worker
# publishes a JSON packet on LiveKit data channel topic "lia-phase" every
# time lia transitions phase. The browser (index.html) listens for this
# and shows a phase badge. When False, nothing is published and the badge
# stays hidden client-side.
SHOW_PHASE_DEBUG = os.getenv("SHOW_PHASE_DEBUG", "False").lower() == "true"

# Inline `<pose:NAME/>` markers in the LLM stream → POSTed to LemonSlice
# pose-trigger control endpoint. Off by default; the LLM prompt must also
# be told about the tag format and pose names for this to do anything.
POSE_TRIGGER_ENABLED = os.getenv("POSE_TRIGGER_ENABLED", "False").lower() == "true"

# Barge-in: when True, the user can interrupt the avatar by starting to
# speak. livekit-agents' built-in VAD detects this and we forward it to
# the brain so the in-flight LLM stream is cancelled (saves tokens) and
# the bridge drops queued deltas. Off by default — barge-in alters the
# turn-based flow some lia phases assume.
BARGE_IN_ENABLED = os.getenv("BARGE_IN_ENABLED", "False").lower() == "true"
AVATAR_ENABLED = env_bool("AVATAR_ENABLED", True)


def _extract_deployment_id(raw: str | None) -> str | None:
    """Azure SDK wants a bare deployment UUID, but lia_agent_api stores the
    whole cognitive-services endpoint URL. Accept either.
    """
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("http"):
        qs = parse_qs(urlparse(raw).query)
        vals = qs.get("deploymentId") or qs.get("deploymentid")
        return vals[0] if vals else None
    return raw

from lia_agent_api_bridge import bridge_from_env
from pose_control import trigger_pose

dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO)
# Silence noisy 'ignore event log' debug spam from the bridge.
logging.getLogger("lia_bridge").setLevel(logging.INFO)
# Keep Azure TTS / LemonSlice at DEBUG so we can see synth/publish activity.
logging.getLogger("livekit.plugins.azure").setLevel(logging.DEBUG)
logging.getLogger("livekit.plugins.lemonslice").setLevel(logging.DEBUG)
logger = logging.getLogger("livekit_agent")


# ─── Minimal "dumb" agent — the brain lives elsewhere ────────────────
#
# LiveKit's Agent class expects an LLM in most templates, but AgentSession
# allows running without one if we drive replies via `session.say()`.
# We supply an empty instructions string and do not pass `llm=`; speech
# generation is done by the bridge pump below.

async def entrypoint(ctx: agents.JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # LemonSlice's avatar joins the room as a participant whose identity
    # contains "lemonslice" and publishes events (notably
    # {"type":"bot_ready","session_id":...}) on the data channel. They
    # never surface in the backend log unless we subscribe here.
    #
    # `avatar.start()` only awaits LemonSlice's HTTP API ack — the avatar
    # process itself joins the LiveKit room asynchronously and announces
    # readiness via the bot_ready event. If we start `pump_replies` before
    # that, the greeting is generated into a room with no avatar yet, the
    # avatar buffers/drops audio while cold-starting, and our session.say
    # hits its 60s timeout for nothing. Gate the reply pump on this event.
    avatar_ready = asyncio.Event()

    def _on_data_received(packet: rtc.DataPacket):
        identity = packet.participant.identity if packet.participant else "(server)"
        topic = packet.topic or "(none)"
        text = packet.data.decode("utf-8", errors="replace")
        snippet = text if len(text) <= 500 else text[:500] + "…"
        if "lemonslice" in identity.lower():
            logger.info(
                "LemonSlice data event from=%s topic=%s payload=%s",
                identity, topic, snippet,
            )
            try:
                if json.loads(text).get("type") == "bot_ready" and not avatar_ready.is_set():
                    logger.info("LemonSlice bot_ready — avatar joined room")
                    avatar_ready.set()
            except Exception:  # noqa: BLE001
                pass
        else:
            logger.debug(
                "room data event from=%s topic=%s payload=%s",
                identity, topic, snippet,
            )

    ctx.room.on("data_received", _on_data_received)

    # Kiosk-room branch: when LiveKit dispatches us into a room named
    # "kiosk-…", swap in the full camera→welcome→storys phase chain and
    # publish the session_id to lia_agent_api so camera_platform can attach
    # to it. Any other room name (e.g. the default "tr-avatar-…" issued by
    # webapp-tokenserver) falls through to env-driven phases unchanged.
    KIOSK_PHASES = [
        "camera", "welcome", "intro", "storys",
        "scenario", "scenario_vote", "brainstorm_vote", "outro",
    ]
    is_kiosk = ctx.room.name.startswith("kiosk-")
    # Jailbreak rooms: same phase as web flow (storys), but skip LemonSlice
    # entirely so safety probing doesn't burn avatar Interactive Call minutes.
    # STT/TTS/LLM stay on so the experience matches production for testers.
    is_jailbreak = ctx.room.name.startswith("jailbreak-")
    # VIP rooms: honored-guest storymode. Boots straight into the "vip" phase
    # (VipEngine + VipNode + the warmer storys.vip agent) and, like jailbreak,
    # skips LemonSlice — the /vip page is audio-only (no avatar) by design.
    is_vip = ctx.room.name.startswith("vip-")
    avatar_enabled_for_room = AVATAR_ENABLED and not (is_jailbreak or is_vip)
    lemonslice_model = None
    if avatar_enabled_for_room:
        lemonslice_model = required_deployment_value("LEMONSLICE_MODEL")
    VIP_PHASES = ["vip"]
    if is_kiosk:
        phases_override = KIOSK_PHASES
    elif is_vip:
        phases_override = VIP_PHASES
    else:
        phases_override = None
    bridge = bridge_from_env(phases_override=phases_override)
    try:
        session_id = await bridge.start()
        logger.info("lia_agent_api session_id=%s (kiosk=%s)", session_id, is_kiosk)
    except Exception:
        logger.exception("Failed to open lia_agent_api session; aborting job")
        return

    if is_kiosk:
        try:
            await bridge.register_as_kiosk_session()
            logger.info("Registered kiosk session: %s", session_id)
        except Exception:
            logger.exception(
                "Failed to register kiosk session — camera_platform won't find this session_id"
            )

    agent = Agent(
        instructions="",  # unused — replies come from lia_agent_api
        stt=azure.STT(
            speech_key=os.getenv("AZURE_TTS_API_KEY"),
            speech_region=os.getenv("AZURE_TTS_REGION", "westus"),
        ),
        tts=azure.TTS(
            speech_key=os.getenv("AZURE_TTS_API_KEY"),
            speech_region=os.getenv("AZURE_TTS_REGION", "westus"),
            voice=os.getenv("AZURE_TTS_VOICE", "joe_2"),
            deployment_id=_extract_deployment_id(os.getenv("AUDIO_DEPLOYMENT")),
        ),
    )

    session = AgentSession()

    # ── LemonSlice avatar ─────────────────────────────────────────
    # Skipped entirely for jailbreak rooms — the page there shows a static
    # portrait, agent audio still publishes directly to the room so the
    # browser can play TR's voice, but no Interactive Call minutes are spent.
    if avatar_enabled_for_room:
        avatar_kwargs = {}
        if os.getenv("LEMONSLICE_AVATAR_IMAGE_URL"):
            avatar_kwargs["agent_image_url"] = os.getenv("LEMONSLICE_AVATAR_IMAGE_URL")
        if os.getenv("LEMONSLICE_AGENT_ID"):
            avatar_kwargs["agent_id"] = os.getenv("LEMONSLICE_AGENT_ID")

        if not avatar_kwargs:
            logger.warning(
                "No LEMONSLICE_AVATAR_IMAGE_URL or LEMONSLICE_AGENT_ID set — "
                "LemonSlice will fail to initialise."
            )
        logger.info(
            "LemonSlice dispatch: agent_id=%r agent_image_url=%r",
            os.getenv("LEMONSLICE_AGENT_ID"),
            os.getenv("LEMONSLICE_AVATAR_IMAGE_URL"),
        )

        avatar = lemonslice.AvatarSession(
            agent_prompt=(
                "speaking warmly and enthusiastically. Happy."
            ),
            agent_idle_prompt=(
                "happily looking around. smiling."
            ),
            # 3600s (1 hour). LemonSlice historically had a 30-min hard cap on
            # Interactive Call sessions — if that's still in force the session
            # will end at 30 min regardless of this value. Setting higher means
            # leaked sessions (subprocess hang, stuck room) burn more Interactive
            # Call minutes before idle auto-cleanup fires; webapp pagehide listener
            # is the primary defence against leaks.
            idle_timeout=3600,
            model=lemonslice_model,
            aspect_ratio="1x1",
            **avatar_kwargs,
        )

        lemonslice_session_id = await avatar.start(session, room=ctx.room)
        logger.info("LemonSlice avatar session_id=%s", lemonslice_session_id)

        if POSE_TRIGGER_ENABLED:
            async def _on_pose(name: str) -> None:
                await trigger_pose(lemonslice_session_id, name)
            bridge.set_pose_callback(_on_pose)
            logger.info("pose-trigger enabled; LLM may emit <pose:NAME/> markers")
    else:
        reason = (
            "AVATAR_ENABLED=false"
            if not AVATAR_ENABLED
            else ("vip" if is_vip else "jailbreak")
        )
        logger.info("LemonSlice avatar skipped (%s) for room %s", reason, ctx.room.name)
    # close_on_disconnect=False: don't tear down the whole session just
    # because LemonSlice's avatar-agent blips. We had cases where a cancelled
    # interrupt or brief RPC timeout on the LemonSlice side killed the whole
    # AgentSession and subsequent `session.say` calls silently produced no
    # audio. Keeping the session alive lets us recover.
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(close_on_disconnect=False),
    )

    # ── Pump 1: STT → bridge ──────────────────────────────────────
    # Every final transcript from the user is shipped to lia_agent_api
    # as a participant_input message. The brain runs safety moderation
    # server-side and emits safety_interrupt events back over WS;
    # see pump_safety_events below.
    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev):  # noqa: ANN001
        text = getattr(ev, "transcript", None) or getattr(ev, "text", None)
        if not text:
            return
        # ev may have `is_final` in some SDK versions; be permissive.
        is_final = getattr(ev, "is_final", True)
        if not is_final:
            return
        asyncio.create_task(bridge.send_user_text(text))
    
    # TODO: more test on that - on unrecoverable error we want to restart the pipeline
    # session should die and be recreated. avatar will leave and rejoin as a new one
    @session.on("error")
    def on_session_error(ev: ErrorEvent) -> None:
        err = ev.error
        if isinstance(err, TTSError):
            logger.error("AgentSession TTS error", exc_info=err.error)
        elif isinstance(err, STTError):
            logger.error("AgentSession STT error", exc_info=err.error)
        else:
            logger.error(
                "AgentSession error",
                exc_info=getattr(err, "error", None),
            )

    # ── Pump 2: bridge → TTS (one utterance at a time) ────────────
    SAY_TIMEOUT_SECS = float(os.getenv("SAY_TIMEOUT_SECS", "60"))

    # Tracks the utterance currently being played, so user_state_changed
    # can target it when forwarding the barge-in signal to the brain.
    current_say_utterance: dict[str, str | None] = {"id": None}

    async def pump_replies():
        try:
            async for utterance_id, text_iter in bridge.utterances():
                logger.info("→ session.say utterance=%s", utterance_id)
                current_say_utterance["id"] = utterance_id
                try:
                    await asyncio.wait_for(
                        session.say(
                            text_iter,
                            allow_interruptions=BARGE_IN_ENABLED,
                        ),
                        timeout=SAY_TIMEOUT_SECS,
                    )
                    logger.info("✓ session.say done utterance=%s", utterance_id)
                except asyncio.TimeoutError:
                    logger.error(
                        "session.say timed out after %.1fs utterance=%s — "
                        "continuing with next utterance",
                        SAY_TIMEOUT_SECS, utterance_id,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("session.say failed utterance=%s", utterance_id)
                finally:
                    if current_say_utterance["id"] == utterance_id:
                        current_say_utterance["id"] = None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("reply pump crashed")

    # Don't start consuming utterances until the LemonSlice avatar has
    # actually joined the room (see avatar_ready / bot_ready setup near
    # the top of entrypoint). On a warm pool this is ~ms; on a cold start
    # we've seen 2.5 minutes — generating audio before this point causes
    # session.say to time out for no real reason.
    if avatar_enabled_for_room:
        AVATAR_READY_TIMEOUT_SECS = float(os.getenv("AVATAR_READY_TIMEOUT_SECS", "180"))
        logger.info(
            "waiting for LemonSlice bot_ready (timeout=%.0fs)",
            AVATAR_READY_TIMEOUT_SECS,
        )
        _avatar_ready_t0 = asyncio.get_event_loop().time()
        try:
            await asyncio.wait_for(avatar_ready.wait(), timeout=AVATAR_READY_TIMEOUT_SECS)
            logger.info(
                "avatar ready, starting reply pump (waited %.2fs)",
                asyncio.get_event_loop().time() - _avatar_ready_t0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "avatar bot_ready not received in %.0fs — starting reply pump anyway "
                "(greeting may be lost)",
                AVATAR_READY_TIMEOUT_SECS,
            )
    else:
        reason = (
            "AVATAR_ENABLED=false"
            if not AVATAR_ENABLED
            else ("vip" if is_vip else "jailbreak")
        )
        logger.info(
            "skipping bot_ready wait (%s), starting reply pump immediately",
            reason,
        )

    pump_task = asyncio.create_task(pump_replies())

    # ── Barge-in: user_state_changed → speaking → tell the brain ──
    # livekit-agents' VAD already calls session.interrupt() when
    # allow_interruptions=True; this hook adds the brain-side cancel
    # so the LLM stream stops generating (saves tokens). The bridge
    # also receives the user_interrupt echo and drops queued deltas.
    if BARGE_IN_ENABLED:
        @session.on("user_state_changed")
        def _on_user_state(ev):  # noqa: ANN001
            new_state = getattr(ev, "new_state", None)
            old_state = getattr(ev, "old_state", None)
            if new_state != "speaking" or old_state == "speaking":
                return
            target_uid = current_say_utterance["id"]
            if target_uid is None:
                return  # avatar not currently speaking — nothing to barge in on
            logger.info("barge-in detected during utterance=%s", target_uid)
            asyncio.create_task(bridge.send_user_interrupt(target_uid))

    # ── Pump 3: phase changes → browser data channel (debug overlay) ──
    async def pump_phase_debug():
        if not SHOW_PHASE_DEBUG:
            return
        try:
            async for phase in bridge.phase_changes():
                payload = json.dumps({"phase": phase}).encode()
                try:
                    await ctx.room.local_participant.publish_data(
                        payload, reliable=True, topic="lia-phase"
                    )
                    logger.info("→ lia-phase debug broadcast: %s", phase)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to publish phase data")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("phase debug pump crashed")

    phase_debug_task = asyncio.create_task(pump_phase_debug())

    # ── Pump 4: camera events → browser data channel (debug overlay) ──
    async def pump_camera_events_debug():
        if not SHOW_PHASE_DEBUG:
            return
        try:
            async for ev in bridge.camera_events():
                payload = json.dumps(ev).encode()
                try:
                    await ctx.room.local_participant.publish_data(
                        payload, reliable=True, topic="lia-camera-event"
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("failed to publish camera-event data")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("camera-event debug pump crashed")

    camera_event_debug_task = asyncio.create_task(pump_camera_events_debug())

    # ── Pump 5: brain interrupt events → TTS cut + browser ────────
    # Handles both safety_interrupt (server moderation) and
    # user_interrupt (barge-in echo). Calls session.interrupt to stop
    # in-flight TTS playback, and publishes to LiveKit data channel
    # under topic "lia-interrupt" so the browser can show a UI cue.
    async def pump_safety_events():
        try:
            async for ev in bridge.safety_events():
                etype = ev.get("type")
                logger.warning(
                    "brain %s utterance=%s role=%s reason=%s",
                    etype,
                    ev.get("utterance_id"),
                    ev.get("role"),
                    ev.get("reason"),
                )
                try:
                    session.interrupt(force=True)
                except Exception:  # noqa: BLE001
                    logger.exception("session.interrupt failed")
                payload = json.dumps({
                    "type": etype,
                    "utterance_id": ev.get("utterance_id"),
                    "role": ev.get("role"),
                    "reason": ev.get("reason"),
                }).encode()
                try:
                    await ctx.room.local_participant.publish_data(
                        payload, reliable=True, topic="lia-interrupt"
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("failed to publish interrupt data")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("safety event pump crashed")

    safety_event_task = asyncio.create_task(pump_safety_events())

    # ── Text-chat fallback (data channel) ─────────────────────────
    # The browser may also send text via LiveKit data channel under the
    # "lk-chat-topic" topic. Route it into the bridge exactly like STT.
    @ctx.room.on("data_received")
    def _on_data(packet: rtc.DataPacket):
        try:
            import json as _json
            body = _json.loads(packet.data.decode())
            text = body.get("text", "")
            if text:
                asyncio.create_task(bridge.send_user_text(text))
        except Exception:  # noqa: BLE001
            logger.exception("bad data packet")

    # Keep the job alive until the room disconnects.
    try:
        await asyncio.Event().wait()
    finally:
        pump_task.cancel()
        phase_debug_task.cancel()
        camera_event_debug_task.cancel()
        safety_event_task.cancel()
        await bridge.close()


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint),
    )
