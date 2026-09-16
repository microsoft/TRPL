# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Camera Events Router — receives events from the camera platform.

Auto-creates a camera session on first event if none exists,
so you only need to open the web page and upload a video.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.config import config
from api.version import __version__
from debate.models.constants import Phase
from debate.models.state import (
    CameraState,
    DebateState,
    InputState,
    WelcomeState,
)
from debate.models.requests import get_default_camp_definitions
from debate.models.sockets import CameraEvent as CameraEventMessage
from debate.scenarios.ids import ScenarioId
from debate.services.session_store import session_store

logger = logging.getLogger(f"lia.{__name__}")

router = APIRouter(prefix="/api/camera", tags=["camera"])

# Lock to prevent concurrent auto-creation of sessions
_auto_create_lock = asyncio.Lock()


class CameraEventRequest(BaseModel):
    """Envelope for camera platform events."""

    event_id: str
    timestamp: str
    source: str = "camera_service"
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # Optional explicit session routing. When set, the event is delivered to
    # exactly this session and the legacy "find any active session" heuristic
    # is bypassed. When None (the default), the heuristic in
    # _find_or_create_session is used — kept for backward-compat with the
    # camera_welcome_test debug page.
    session_id: str | None = None


class CameraEventResponse(BaseModel):
    status: str = "accepted"
    event_id: str = ""
    session_id: str | None = None


async def _route_camera_event(target_session, event: CameraEventRequest) -> None:
    if config.runtime_mode == "deterministic":
        await target_session.output_queue.put_if_websocket_subscribed(
            {
                "type": "camera_event_received",
                "timestamp": datetime.now().isoformat(),
                "event_id": event.event_id,
                "event_type": event.event_type,
                "session_id": target_session.session_id,
            }
        )
        return

    await target_session.input_queue.put(
        CameraEventMessage(
            event_type=event.event_type,
            event_id=event.event_id,
            payload=event.payload,
        )
    )


async def _auto_create_camera_session(include_welcome: bool = False) -> str:
    """Auto-create a camera-phase session and start its orchestrator.

    Args:
        include_welcome: if True, add Phase.welcome after camera so the
            orchestrator will automatically transition when CameraNode ends.
    """
    scenario_id = ScenarioId.MIDNIGHT_RESERVES
    camp_definitions = get_default_camp_definitions()
    phases = [Phase.camera, Phase.welcome] if include_welcome else [Phase.camera]

    state = DebateState(
        camp_definitions=camp_definitions,
        current_camp=None,
        points_made=[],
        roster={},
        meeting_notes=None,
        history=[],
        next_speaker=None,
        eligible_speakers=[],
        inputs=InputState(),
        phase=Phase.camera,
        scenario_id=scenario_id,
    )
    state.camera_state = CameraState()
    state.welcome_state = WelcomeState()

    session_id = await session_store.create_session(
        state,
        audio_enabled=False,
        websocket_streaming=False,
        tts_voice=config.tts_voice,
    )
    logger.info(f"[camera] Auto-created session: {session_id}")

    session = await session_store.get_session(session_id)
    await session.output_queue.put({
        "type": "debug_message",
        "timestamp": datetime.now().isoformat(),
        "debug": True,
        "agent": "System",
        "content": f"Auto-created camera session: {session_id} (version: {__version__})",
        "phase": "camera",
    })

    if config.runtime_mode == "cloud":
        from debate.services.agent_registry import get_agent_registry
        from debate.services.session_orchestrator import SessionOrchestrator

        agents = await get_agent_registry(scenario_id=scenario_id)
        orchestrator = SessionOrchestrator(session, agents, phases)
        session.orchestrator_task = asyncio.create_task(orchestrator.run_session())
        session.graph_engine_task = session.orchestrator_task

    # Auto-set connection event so the engine doesn't wait for WebSocket
    session.connection_event.set()

    return session_id


async def _find_or_create_session(
    include_welcome: bool = False,
) -> "DebateSession | None":
    """Find an active camera session, or auto-create one.

    Preference order for matching an existing session:
      1. WebSocket-connected session currently in camera phase
         — this is the one the LiveKit bridge (or debug UI) is actively
         driving, so its avatar is on screen and will hear any LLM reply.
      2. Any WebSocket-connected session (even in another phase) — events
         will still update the shared camera_state, and phase logic decides
         whether to emit a response.
      3. Any session in camera phase (possibly orphaned, no WS).
      4. Any active session.
      5. Auto-create a fresh camera-phase session.
    """
    candidates = []
    for sid in session_store.active_sessions():
        session = await session_store.get_session(sid)
        if not session or not session.state:
            continue
        candidates.append(session)

    def _ws_connected(s) -> bool:
        # websocket_connected is set True when a controller WS is attached.
        return bool(getattr(s, "websocket_connected", False))

    # 1. WS-connected + camera phase
    for s in candidates:
        if _ws_connected(s) and s.state.phase == "camera":
            return s
    # 2. WS-connected in any phase
    for s in candidates:
        if _ws_connected(s):
            return s
    # 3. camera phase, no WS
    for s in candidates:
        if s.state.phase == "camera":
            return s
    # 4. any
    if candidates:
        return candidates[0]

    # 5. auto-create
    async with _auto_create_lock:
        for sid in session_store.active_sessions():
            session = await session_store.get_session(sid)
            if session and session.state:
                return session
        session_id = await _auto_create_camera_session(
            include_welcome=include_welcome,
        )
        return await session_store.get_session(session_id)


@router.post("/events", response_model=CameraEventResponse)
async def receive_camera_event(
    event: CameraEventRequest,
    user: dict = Depends(require_auth()),
):
    """
    Receive a camera platform event and route it to a session.

    Auth-gated: the camera client must send a valid X-Api-Key (see
    CLIENT_API_KEYS) — these events drive the live avatar's behavior.

    Routing:
      - If event.session_id is set, deliver to exactly that session
        (returns 404 if the session does not exist).
      - Otherwise, fall back to the find-or-create heuristic.
    """
    logger.info(
        f"Received camera event: type={event.event_type} id={event.event_id} "
        f"session_id={event.session_id or '<heuristic>'}"
    )

    if event.session_id:
        target_session = await session_store.get_session(event.session_id)
        if target_session is None:
            raise HTTPException(
                status_code=404,
                detail=f"session_id {event.session_id} not found",
            )
    else:
        target_session = await _find_or_create_session()
        if target_session is None:
            logger.error("Failed to find or create session for camera event")
            return CameraEventResponse(
                status="error",
                event_id=event.event_id,
            )

    await _route_camera_event(target_session, event)

    logger.info(
        f"Camera event {event.event_id} ({event.event_type}) "
        f"routed to session {target_session.session_id}"
    )

    return CameraEventResponse(
        status="accepted",
        event_id=event.event_id,
        session_id=target_session.session_id,
    )


@router.get("/health")
async def camera_health():
    """Health check for camera integration."""
    return {
        "status": "ok",
        "integration": "camera_platform",
        "runtime_mode": config.runtime_mode,
        "event_source": (
            "fictional_fixture"
            if config.runtime_mode == "deterministic"
            else "external_camera_platform"
        ),
    }


@router.get("/session")
async def get_camera_session(user: dict = Depends(require_auth())):
    """Get the current camera session ID (for WebSocket connection).

    Auth-gated: the session id gates the debate WebSocket, so it must not
    be readable anonymously."""
    for sid in session_store.active_sessions():
        session = await session_store.get_session(sid)
        if session and session.state:
            return {
                "session_id": session.session_id,
                "phase": session.state.phase,
                "ws_url": f"/api/debate/ws/{session.session_id}",
            }
    return {"session_id": None, "phase": None, "ws_url": None}


# ── Test-only: camera → welcome flow ──


@router.post("/test/create-session", response_model=CameraEventResponse)
async def create_test_camera_welcome_session(user: dict = Depends(require_auth())):
    """Create a camera+welcome session for testing the full transition flow.

    Auth-gated debug endpoint — creates background orchestrator tasks.

    This does NOT affect the normal /events endpoint — existing camera-only
    sessions continue to work as before.
    """
    async with _auto_create_lock:
        session_id = await _auto_create_camera_session(include_welcome=True)
    session = await session_store.get_session(session_id)
    return CameraEventResponse(
        status="created",
        event_id="",
        session_id=session_id,
    )


@router.post("/test/events", response_model=CameraEventResponse)
async def receive_test_camera_event(
    event: CameraEventRequest,
    user: dict = Depends(require_auth()),
):
    """Like /events but auto-creates a camera+welcome session (for testing).

    Auth-gated debug endpoint."""
    logger.info(
        f"[test] Received camera event: type={event.event_type} id={event.event_id}"
    )

    target_session = await _find_or_create_session(include_welcome=True)

    if target_session is None:
        return CameraEventResponse(status="error", event_id=event.event_id)

    await _route_camera_event(target_session, event)

    return CameraEventResponse(
        status="accepted",
        event_id=event.event_id,
        session_id=target_session.session_id,
    )
