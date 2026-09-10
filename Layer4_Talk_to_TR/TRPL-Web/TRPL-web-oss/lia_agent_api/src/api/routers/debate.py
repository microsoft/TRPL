# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
    BackgroundTasks,
)
from fastapi.responses import JSONResponse, FileResponse

from api.logs import logging_session_id
from api.version import __version__
from debate.models.state import (
    Participant,
    DebateState,
    CampDefinition,
    KeyPoint,
    InputState,
    WelcomeState,
)
from debate.models.constants import Phase
from debate.models.requests import StartDebateRequest, get_default_camp_definitions
from debate.models.requests import JoinSessionRequest, ReconnectSessionRequest
from debate.models.responses import (
    JoinSessionResponse,
    ReconnectSessionResponse,
    StartDebateResponse,
)
from debate.services.session_store import session_store
from debate.services.ws_ingress import process_inbound_message
from api.auth import require_auth
from api.config import config

logger = logging.getLogger(f"lia.{__name__}")

router = APIRouter(prefix="/api/debate", tags=["debate"])
_AUDIO_GATED_JSON_EVENT_TYPES = {"caption_timing", "caption_timing_end"}


def _should_skip_outbound_event(event: dict[str, Any], audio_enabled: bool) -> bool:
    event_type = event.get("type")
    if (
        event_type == "debate_output"
        and event.get("streaming") is True
        and event.get("stream_to_websocket") is False
    ):
        return True
    if event_type in _AUDIO_GATED_JSON_EVENT_TYPES and not audio_enabled:
        return True
    return False


@router.post("/start", response_model=StartDebateResponse)
async def start_debate(
    request: StartDebateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_auth()),
):
    """Start a new debate session"""
    logger.info(
        "Starting debate session: scenario_id=%s players=%s phases=%s wants_audio=%s websocket_streaming=%s",
        request.scenario_id,
        len(request.players),
        len(request.phases) if request.phases else "default",
        bool(request.wants_audio),
        bool(request.websocket_streaming),
    )
    try:
        # Use hard-coded camp definitions
        camp_definitions = get_default_camp_definitions()
        scenario_id = request.scenario_id

        # Initialize empty roster - participants will be added via participant_joined messages
        roster = {
            name: Participant(
                name=name,
                camp=player.camp,
                reason=player.reason,
            ) for name, player in request.players.items()
        }

        if config.runtime_mode == "cloud":
            from debate.scenarios.registry import get_scenario

            scenario = get_scenario(scenario_id)
            if not scenario:
                raise HTTPException(status_code=400, detail="Unknown scenario_id")
            phases = request.phases or list(scenario.phases)
        else:
            phases = request.phases or [Phase.camera]

        # Initialize state - start in the first configured phase
        state = DebateState(
            camp_definitions=camp_definitions,
            current_camp=None,
            points_made=[],
            roster=roster,
            meeting_notes=None,
            history=[],
            next_speaker=None,
            eligible_speakers=[],
            inputs=InputState(),
            phase=phases[0],
            scenario_id=scenario_id,
        )

        state.welcome_state = WelcomeState(
            max_rounds_per_visitor=config.welcome_max_rounds_per_visitor
        )

        # Create session
        session_id = await session_store.create_session(
            state,
            audio_enabled=bool(request.wants_audio),
            websocket_streaming=bool(request.websocket_streaming),
            audio_format=request.audio_format,
            audio_chunk_size_bytes=request.audio_chunk_size_bytes,
            tts_voice=request.tts_voice or config.tts_voice,
        )
        logger.info(f"[session={session_id}] Session created (version: {__version__})")

        # Get session
        session = await session_store.get_session(session_id)

        # Send debug message with version to output queue
        await session.output_queue.put({
            "type": "debug_message",
            "timestamp": datetime.now().isoformat(),
            "debug": True,
            "agent": "System",
            "content": f"Created new session: {session_id} (version: {__version__})",
            "phase": "session_init"
        })

        # Start initialization in background - don't block HTTP response
        async def initialize_session():
            """Background task to initialize agents, debug manager, and engine"""
            with logging_session_id(session_id):
                try:
                    from debate.services.agent_registry import get_agent_registry
                    from debate.services.session_orchestrator import SessionOrchestrator

                    # Build agents per session so scenario prompts are request-specific.
                    agents = await get_agent_registry(
                        scenario_id=scenario_id,
                        visitor_mode=request.visitor_mode,
                    )

                    # Start session orchestrator to manage phase transitions
                    orchestrator = SessionOrchestrator(session, agents, phases)
                    session.orchestrator_task = asyncio.create_task(
                        orchestrator.run_session()
                    )
                    session.graph_engine_task = session.orchestrator_task
                except Exception as e:
                    logger.error(
                        f"Error initializing session {session_id}: {e}", exc_info=True
                    )
                    await session_store.update_session_status(
                        session_id,
                        "error",
                        error_message="Session initialization failed",
                    )

        if config.runtime_mode == "cloud":
            background_tasks.add_task(initialize_session)
        else:
            await session.output_queue.put(
                {
                    "type": "runtime_status",
                    "timestamp": datetime.now().isoformat(),
                    "runtime_mode": "deterministic",
                    "providers": config.provider_status,
                    "content": (
                        "Session transport is ready; AI, search, speech, and avatar "
                        "generation are unavailable."
                    ),
                }
            )

        # Return immediately - session is ready for websocket connections
        ws_auth = None
        if config.ws_auth_enabled:
            ws_auth = await session_store.initialize_ws_auth(session_id)
        return StartDebateResponse(
            session_id=session_id,
            ws_url=f"/api/debate/ws/{session_id}",
            controller_token=(ws_auth or {}).get("controller_token"),
            reconnect_token=(ws_auth or {}).get("reconnect_token"),
            observer_join_code=(ws_auth or {}).get("observer_join_code"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting debate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start debate session")


@router.post("/join/{session_id}", response_model=JoinSessionResponse)
async def join_session(
    session_id: str,
    request: JoinSessionRequest,
    user: dict = Depends(require_auth()),
):
    if not config.ws_auth_enabled:
        raise HTTPException(
            status_code=400,
            detail="WebSocket auth workflow is disabled",
        )

    session = await session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    observer_token = await session_store.exchange_join_code_for_observer_token(
        session_id, request.join_code
    )
    if observer_token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired join code")

    return JoinSessionResponse(
        session_id=session_id,
        ws_url=f"/api/debate/ws/{session_id}",
        observer_token=observer_token,
    )


@router.post("/reconnect/{session_id}", response_model=ReconnectSessionResponse)
async def reconnect_session(
    session_id: str,
    request: ReconnectSessionRequest,
    user: dict = Depends(require_auth()),
):
    if not config.ws_auth_enabled:
        raise HTTPException(
            status_code=400,
            detail="WebSocket auth workflow is disabled",
        )

    session = await session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    rotated = await session_store.rotate_controller_tokens(
        session_id, request.reconnect_token
    )
    if rotated is None:
        raise HTTPException(status_code=401, detail="Invalid or expired reconnect token")

    return ReconnectSessionResponse(
        session_id=session_id,
        ws_url=f"/api/debate/ws/{session_id}",
        controller_token=rotated["controller_token"],
        reconnect_token=rotated["reconnect_token"],
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str, user: dict = Depends(require_auth())):
    """Get debate history"""
    history_file = Path.home() / "debate_histories" / f"{session_id}.json"

    if history_file.exists():
        return FileResponse(path=str(history_file), filename=f"{session_id}.json")

    session = await session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session history not found")

    # Fallback to in-memory history for active sessions before persistence.
    return JSONResponse(
        content=[entry.model_dump(mode="json") for entry in session.state.history]
    )


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for debate interaction"""
    session = await session_store.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    token_role = None
    if config.ws_auth_enabled:
        token = websocket.query_params.get("token") or websocket.headers.get(
            "x-ws-token"
        )
        token_role = await session_store.consume_websocket_token(session_id, token or "")
        if token_role is None:
            await websocket.close(code=1008, reason="Invalid websocket token")
            return

    await websocket.accept()

    connection_id = str(uuid.uuid4())
    is_controller = False
    audio_enabled = False

    # Track connection and assign controller if needed
    async with session._connection_lock:
        session.active_connection_count += 1
        if config.ws_auth_enabled:
            if token_role == "controller":
                if session.controller_connection_id is not None:
                    session.active_connection_count = max(
                        0, session.active_connection_count - 1
                    )
                    await websocket.close(
                        code=1008,
                        reason="Controller already connected",
                    )
                    return
                session.controller_connection_id = connection_id
                session.websocket_connected = True
                session.disconnected_at = None
                session.connection_event.set()
                is_controller = True
                audio_enabled = bool(session.default_audio_enabled)
                logger.info(
                    f"WebSocket controller connected for session {session_id} "
                    f"(connection_id={connection_id})"
                )
            else:
                logger.info(
                    f"WebSocket observer connected for session {session_id} "
                    f"(connection_id={connection_id})"
                )
        else:
            if session.controller_connection_id is None:
                session.controller_connection_id = connection_id
                session.websocket_connected = True
                session.disconnected_at = None
                session.connection_event.set()
                is_controller = True
                audio_enabled = bool(session.default_audio_enabled)
                logger.info(
                    f"WebSocket controller connected for session {session_id} "
                    f"(connection_id={connection_id})"
                )
            else:
                logger.info(
                    f"WebSocket observer connected for session {session_id} "
                    f"(connection_id={connection_id})"
                )

    subscription_queue = None

    # Task to forward output events to client
    async def forward_outputs():
        nonlocal subscription_queue
        dropped_audio_gated_json_events = 0
        try:
            # Controller connection gets the buffered replay; observers are live-only.
            if is_controller and not session.output_queue.websocket_subscribed:
                subscription_queue = await session.output_queue.subscribe_websocket(
                    audio_enabled=audio_enabled
                )
            else:
                subscription_queue = await session.output_queue.subscribe(
                    audio_enabled=audio_enabled
                )

            while True:
                # Get event from subscription queue
                event = await subscription_queue.get()

                if isinstance(event, dict) and event.get("type") == "audio_chunk":
                    if not audio_enabled:
                        continue
                    packet = event.get("data", b"")
                    try:
                        await websocket.send_bytes(packet)
                    except Exception as send_error:
                        logger.error(
                            f"Error sending audio to WebSocket: {send_error}",
                            exc_info=True,
                        )
                        break
                    continue

                # Log outbound message
                if event.get("type") not in ["log", "audio_chunk"]:
                    logger.debug(f"OUTBOUND: {json.dumps(event, indent=2)}")

                if _should_skip_outbound_event(event, audio_enabled):
                    if (
                        event.get("type") in _AUDIO_GATED_JSON_EVENT_TYPES
                        and not audio_enabled
                    ):
                        dropped_audio_gated_json_events += 1
                    continue

                # Send to client immediately (no sleep needed - websocket.send_json is async)
                try:
                    await websocket.send_json(event)
                except Exception as send_error:
                    logger.error(
                        f"Error sending message to WebSocket: {send_error}",
                        exc_info=True,
                    )
                    break

        except asyncio.CancelledError:
            logger.info(f"Output forwarding cancelled")
        except Exception as e:
            logger.error(f"Error forwarding outputs: {e}", exc_info=True)
        finally:
            if dropped_audio_gated_json_events:
                logger.info(
                    "Dropped %d audio-gated caption events for connection %s (audio_enabled=%s)",
                    dropped_audio_gated_json_events,
                    connection_id,
                    audio_enabled,
                )

    # Task to handle client input
    async def handle_inputs():
        nonlocal audio_enabled

        async def handle_audio_control(enabled: bool, role: str):
            nonlocal audio_enabled
            audio_enabled = enabled
            if subscription_queue:
                await session.output_queue.set_audio_enabled(subscription_queue, enabled)
            if (
                not enabled
                and session.state
                and not session.output_queue.any_audio_subscribers
            ):
                session.state.inputs.audio_idle = True

            if role == "observer":
                logger.info(
                    "Observer audio %s via websocket control",
                    "enabled" if enabled else "disabled",
                )
            else:
                logger.info(
                    "Session audio %s via websocket control",
                    "enabled" if enabled else "disabled",
                )

        async def handle_audio_idle():
            if session.state:
                session.state.inputs.audio_idle = True

        try:
            while True:
                # Receive message from client
                message = await websocket.receive_json()

                # Log inbound message
                logger.debug(f"INBOUND: {json.dumps(message, indent=2)}")

                role = "controller" if is_controller else "observer"
                ingress_result = process_inbound_message(
                    message,
                    role=role,
                    session_id=session_id,
                )
                if ingress_result.error:
                    await websocket.send_json(ingress_result.error)
                    continue

                if ingress_result.pong:
                    await websocket.send_json({"type": "pong"})
                    continue

                if ingress_result.audio_enabled is not None:
                    await handle_audio_control(ingress_result.audio_enabled, role)
                    continue

                if ingress_result.audio_idle:
                    await handle_audio_idle()
                    continue

                if ingress_result.enqueue_message is None:
                    continue

                msg = ingress_result.enqueue_message
                msg_type = getattr(msg, "type", None)

                # Barge-in: user_interrupt is handled inline (don't
                # forward to the engine). Kill the in-flight utterance
                # so the LLM stream gets cancelled and an event fires
                # for all WS subscribers.
                if msg_type == "user_interrupt":
                    if session.safety is not None:
                        await session.safety.kill_utterance(
                            getattr(msg, "utterance_id", None),
                            event_type="user_interrupt",
                            role="user",
                            reason=getattr(msg, "reason", "barge_in"),
                        )
                    continue

                # Out-of-band safety check on user-spoken text. Runs in
                # parallel with the engine — does not delay forwarding.
                if (
                    session.safety is not None
                    and msg_type == "participant_input"
                ):
                    spoken = getattr(msg, "spoken", None)
                    spoken_text = getattr(spoken, "text", None) if spoken else None
                    if spoken_text:
                        session.safety.check_user(spoken_text)

                await session.input_queue.put(ingress_result.enqueue_message)

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected by client")
        except Exception as e:
            logger.error(f"Error handling inputs: {e}")

    # Set session_id in logging context for all logs in this websocket connection
    with logging_session_id(session_id):

        # Start both tasks immediately (don't wait for them)
        output_task = asyncio.create_task(forward_outputs())
        input_task = asyncio.create_task(handle_inputs())

        # Log that tasks are starting
        logger.info(f"WebSocket tasks started - output_task and input_task")

        try:
            # Wait for either task to complete
            done, pending = await asyncio.wait(
                [output_task, input_task], return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        finally:
            # Handle disconnect: update connection state and signal pause
            async with session._connection_lock:
                session.active_connection_count = max(
                    0, session.active_connection_count - 1
                )

                if session.controller_connection_id == connection_id:
                    session.controller_connection_id = None
                    session.websocket_connected = False
                    session.connection_event.clear()

                if session.active_connection_count == 0:
                    session.disconnected_at = datetime.now()

                logger.info(
                    f"WebSocket disconnected for session {session_id} "
                    f"(connection_id={connection_id}, controller={is_controller})"
                )

            # Unsubscribe the websocket queue if it exists
            if subscription_queue:
                try:
                    await session.output_queue.unsubscribe(subscription_queue)
                    if session.state and not session.output_queue.any_audio_subscribers:
                        session.state.inputs.audio_idle = True
                except Exception as e:
                    logger.error(
                        f"Error unsubscribing websocket queue: {e}", exc_info=True
                    )

            try:
                await websocket.close()
            except Exception:
                pass
            logger.info(f"WebSocket closed")

            if session.status == "completed" and session.active_connection_count == 0:
                await session_store.cleanup_session(session.session_id)


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "runtime_mode": config.runtime_mode,
        "providers": config.provider_status,
    }
