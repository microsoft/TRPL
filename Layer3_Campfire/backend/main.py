# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
import os
import socket as _socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

# Enable TCP keepalive on every urllib3-based HTTPS connection (azure-core's
# RequestsTransport, plain `requests`, etc.) so Azure App Service's outbound
# Load Balancer — 4-min idle timeout, silently drops idle flows — can't
# evict our HTTPS sessions to the AppInsights ingestion endpoint and surface
# the loss as the exporter's "Envelopes could not be exported and are not
# retryable" log line. KEEPIDLE=60 + KEEPINTVL=20 × KEEPCNT=5 declares a
# dead peer by ~160 s, well inside the 4-min LB window — see Microsoft's
# Load Balancer TCP Reset doc, which recommends "TCP keep-alive with an
# interval less than the idle timeout setting".
#
# IMPORTANT: must be in-place mutation (`.extend()`), not reassignment.
# urllib3 v2 captures `HTTPConnection.default_socket_options` by reference
# as the default value of `HTTPConnection.__init__(socket_options=...)`,
# so a fresh list bound to the class attribute is invisible to new
# connections — they keep using the original list object. Mutating that
# original list in place means every future HTTPConnection sees the new
# options.
from urllib3.connection import HTTPConnection as _HTTPConnection

_HTTPConnection.default_socket_options.append((_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1))
# TCP_KEEPIDLE/INTVL/CNT are Linux/Windows-only — guarded so the module
# still imports on macOS dev boxes.
if hasattr(_socket, "TCP_KEEPIDLE"):
    _HTTPConnection.default_socket_options.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPIDLE, 60))
if hasattr(_socket, "TCP_KEEPINTVL"):
    _HTTPConnection.default_socket_options.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPINTVL, 20))
if hasattr(_socket, "TCP_KEEPCNT"):
    _HTTPConnection.default_socket_options.append((_socket.IPPROTO_TCP, _socket.TCP_KEEPCNT, 5))

from auth import verify_api_key, verify_websocket_auth
from azure.core.exceptions import ResourceNotFoundError
from azure.monitor.opentelemetry import configure_azure_monitor
from chatbot.chat_history import ChatHistoryManager
from common_config import CAMPFIRE_RAG_MODE, CLOUD_RAG_ENABLED, REDIS_URL_CHAT_STORE
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from models import (
    ArtifactRequest,
    ChatCostResponse,
    ChatDeltaResponse,
    ChatErrorResponse,
    ChatFactCheckResponse,
    ChatFinalResponse,
    ChatFollowUpResponse,
    ChatMessageItem,
    ChatMessageItemPublic,
    ChatMode,
    ChatProgressResponse,
    ChatRequest,
    ChatSummary,
)
from observability import OTEL_DETACH_FILTER, PII_FILTER, get_tracer, request_scope
from redis.asyncio import Redis

if TYPE_CHECKING:
    from chatbot.orchestrated_agent.agent import EndToEndAgent as EndToEndAgentType

if CLOUD_RAG_ENABLED:
    from chatbot.orchestrated_agent.agent import EndToEndAgent
    from chatbot.orchestrated_agent.prompts import DEFAULT_MODE
    from chatbot.orchestrated_agent.safety import (
        SAFE_REFUSAL_MESSAGE,
        is_safety_filter_error,
    )
    from chatbot.VectorSearch.AzureAISearch import (
        book_search_client,
        letter_search_client,
    )
else:
    EndToEndAgent = None
    DEFAULT_MODE: ChatMode = "discovery"
    SAFE_REFUSAL_MESSAGE = ""
    book_search_client = None
    letter_search_client = None

    def is_safety_filter_error(error: object) -> bool:
        return False


if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # Data Foundations' shared VNet fronts Azure Monitor with a Private Link
    # Scope (AMPLS): once this App Service is VNet-integrated, the ingestion and
    # live-metrics endpoints can resolve to private network addresses that
    # REJECT instrumentation-key auth and require Entra ID — key-based export
    # fails with `403 Forbidden ... Monitoring Metrics Publisher`. Authenticate
    # the exporter with a managed identity instead. The App Service's identity
    # needs the **Monitoring Metrics Publisher** role on the App Insights
    # resource.
    #
    # Local dev / CI normally leave APPLICATIONINSIGHTS_CONNECTION_STRING unset,
    # so this branch (and the credential) only runs in Azure. DefaultAzureCredential
    # mirrors the storage path in chatbot/utils/blob.py and resolves to the
    # App Service managed identity at runtime.
    from azure.identity import DefaultAzureCredential

    # --- TEMP DEBUG (debug/appinsights-entra-identity) ---------------------
    # The exporter is returning 403 Forbidden from the ingestion endpoint even
    # though the App Service's system-assigned identity holds Monitoring Metrics
    # Publisher on the AI resource. A 403 = authenticated-but-not-authorized, so
    # we need to see *which* principal DefaultAzureCredential actually resolves
    # to at runtime. These logs go to the console (stdout) on purpose — the
    # App Insights export path is exactly what's broken, so we can't rely on it.
    # Inspect via: az webapp log tail / the App Service Log Stream.
    # Remove this block once the identity is confirmed.
    import base64 as _b64

    _dbg = logging.getLogger("appinsights.identity.debug")

    def _decode_jwt_claims(token: str) -> dict:
        """Decode (WITHOUT verifying) a JWT's payload for diagnostic logging."""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)  # restore base64 padding
            return json.loads(_b64.urlsafe_b64decode(payload))
        except Exception as exc:  # pragma: no cover - diagnostic only
            return {"_decode_error": repr(exc)}

    # Turn up azure-identity's own logging so the credential-chain selection
    # (which credential in DefaultAzureCredential won) is visible on the console.
    logging.getLogger("azure.identity").setLevel(logging.DEBUG)

    _credential = DefaultAzureCredential()

    _dbg.warning(
        "appinsights identity debug: AZURE_CLIENT_ID=%r IDENTITY_ENDPOINT_set=%s "
        "MSI_ENDPOINT_set=%s conn_str_present=%s",
        os.getenv("AZURE_CLIENT_ID"),
        bool(os.getenv("IDENTITY_ENDPOINT")),
        bool(os.getenv("MSI_ENDPOINT")),
        bool(os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")),
    )
    try:
        # Same audience the Azure Monitor exporter requests for ingestion.
        _tok = _credential.get_token("https://monitor.azure.com/.default")
        _claims = _decode_jwt_claims(_tok.token)
        _dbg.warning(
            "appinsights identity debug: token acquired OK -> oid=%s appid=%s "
            "aud=%s tid=%s idtyp=%s xms_mirid=%s",
            _claims.get("oid"),
            _claims.get("appid"),
            _claims.get("aud"),
            _claims.get("tid"),
            _claims.get("idtyp"),
            _claims.get("xms_mirid"),
        )
    except Exception as exc:
        _dbg.warning("appinsights identity debug: token acquisition FAILED: %r", exc)
    # --- END TEMP DEBUG ---------------------------------------------------

    configure_azure_monitor(
        credential=_credential,
        enable_live_metrics=True,
    )
    # Azure Monitor adds its own LoggingHandler to the root logger; attach
    # the PII filter so its export path scrubs too.
    for _h in logging.getLogger().handlers:
        _h.addFilter(PII_FILTER)

# Surface the real traceback for OTel's swallowed 'Failed to detach context'
# errors so we can identify which instrumentation is mis-detaching.
logging.getLogger("opentelemetry.context").addFilter(OTEL_DETACH_FILTER)

logger = logging.getLogger(__name__)

# Load build metadata written by CI
_build_info_path = Path(__file__).parent / "build-info.json"
try:
    _build_info = json.loads(_build_info_path.read_text())
except Exception:
    _build_info = {"commit": "dev", "branch": "local", "build_time": "unknown", "build_id": "unknown"}


async def _telemetry_heartbeat() -> None:
    """Emit a tiny span+log every 2 min so the Azure Monitor exporter's HTTP
    connections don't sit idle. App Service outbound SNAT evicts idle TCP
    sockets after ~4 min; without traffic to ship during quiet periods the
    next batch hits a dead connection and the envelopes are dropped
    (ConnectionResetError -> ServiceResponseError, classified as non-
    retryable). Touching both the trace and log exporters resets the SNAT
    idle timer on both Sessions."""
    hb_logger = logging.getLogger("heartbeat")
    hb_tracer = get_tracer("heartbeat")
    while True:
        await asyncio.sleep(120)
        with hb_tracer.start_as_current_span("exporter_heartbeat"):
            hb_logger.info(
                "exporter heartbeat",
                extra={"microsoft.custom_event.name": "exporter_heartbeat"},
            )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task: asyncio.Task | None = None
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        task = asyncio.create_task(_telemetry_heartbeat())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(lifespan=_lifespan)
# app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS for frontend calls (chat history endpoints are hit directly from the browser)
cors_allow_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
allowed_origins = [origin.strip() for origin in cors_allow_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Emits one structured http_request event per response via the shared
    request_scope helper. Health probes log at DEBUG to keep dashboards
    readable; the `is_health_check` dimension lets queries filter them either
    way. WebSocket traffic bypasses HTTP middleware entirely — the WS handler
    enters its own request_scope blocks for connection and per-message events."""
    path = request.url.path
    is_health = path == "/healthz"
    async with request_scope(
        "http_request",
        inbound_request_id=request.headers.get("x-request-id"),
        base_extra={
            "method": request.method,
            "path": path,
            "is_health_check": is_health,
        },
        log_level=logging.DEBUG if is_health else logging.INFO,
    ) as scope:
        scope.extra["status_code"] = 500
        response = await call_next(request)
        scope.extra["status_code"] = response.status_code
        response.headers["X-Request-Id"] = scope.request_id
        return response


# Redis client
redis_client = Redis.from_url(REDIS_URL_CHAT_STORE)

# Chat history service
chat_history_service = ChatHistoryManager(redis_client)


@app.get("/healthz")
async def healthz():
    """Report local dependencies and cloud RAG availability."""
    try:
        await redis_client.ping()  # pyright: ignore[reportGeneralTypeIssues]
    except Exception:
        return {
            "status": "error",
            "mode": CAMPFIRE_RAG_MODE,
            "rag_available": CLOUD_RAG_ENABLED,
            "error": "Redis connection failed",
        }

    if not CLOUD_RAG_ENABLED:
        return {
            "status": "ok",
            "mode": CAMPFIRE_RAG_MODE,
            "rag_available": False,
            "redis": "ok",
            "version": _build_info,
        }

    try:
        assert book_search_client is not None
        assert letter_search_client is not None
        book_count = await book_search_client.print_document_count()
        letter_count = await letter_search_client.print_document_count()
    except Exception as e:
        return {
            "status": "error",
            "mode": CAMPFIRE_RAG_MODE,
            "rag_available": False,
            "error": str(e),
        }

    return {
        "status": "ok",
        "mode": CAMPFIRE_RAG_MODE,
        "rag_available": True,
        "redis": "ok",
        "book_documents": book_count,
        "letter_documents": letter_count,
        "version": _build_info,
    }


def _require_cloud_rag() -> None:
    if not CLOUD_RAG_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Campfire RAG is unavailable in degraded local mode. "
                "Configure cloud-backed mode to enable Azure OpenAI and Search."
            ),
        )


async def _resolve_mode(chat_id: str, user_id: str, requested: ChatMode | None) -> ChatMode:
    """Resolve the mode for a chat turn.

    - If metadata already exists for this chat, use the stored mode. A request that
      supplies a different mode for an existing chat is rejected (HTTP 400) — modes
      are set on chat creation and cannot change mid-session.
    - Otherwise, use the requested mode if provided, else DEFAULT_MODE.

    Redis lookup failures are treated as "no stored metadata" — losing the
    mode-conflict guardrail is acceptable; refusing to serve the chat is not.
    The caller persists via `set_chat_metadata` once the turn succeeds.
    """
    try:
        stored = await chat_history_service.get_chat_metadata(chat_id, user_id)
    except Exception:
        logger.exception("Failed to read chat metadata (non-fatal); falling back to requested mode")
        stored = None
    stored_mode: ChatMode | None = stored.get("mode") if stored else None
    if stored_mode and requested and stored_mode != requested:
        raise HTTPException(status_code=400, detail="Mode cannot change for existing chat")
    return stored_mode or requested or DEFAULT_MODE


@app.post("/api/chat")
async def chat_rest(
    request: ChatRequest,
    _: str = Depends(verify_api_key),
) -> ChatFinalResponse | ChatErrorResponse:
    """Handles chat via REST API (non-streaming)"""
    _require_cloud_rag()
    chat_id = request.chat_id or uuid4().hex
    user_id = request.user_id or uuid4().hex
    mode = await _resolve_mode(chat_id, user_id, request.mode)

    try:
        assert EndToEndAgent is not None
        engine = EndToEndAgent(chat_id=chat_id, user_id=user_id, chat_history_manager=chat_history_service, mode=mode)
        logger.info(
            f"Initialized chat engine: chat_id={chat_id}, user_id={user_id}",
            extra={
                "microsoft.custom_event.name": "chat_engine_initialized",
                "chat_id": chat_id,
                "user_id": user_id,
                "mode": mode,
            },
        )

        # Collect all chunks and return only the final response
        final_response = None
        async for chunk in engine.ask_stream(request.message):
            chunk["chat_id"] = chat_id
            chunk["user_id"] = user_id
            if chunk["type"] == "final":
                final_response = ChatFinalResponse.model_validate(chunk)
            elif chunk["type"] == "error":
                if is_safety_filter_error(chunk.get("error")):
                    return ChatFinalResponse(
                        chat_id=chat_id,
                        user_id=user_id,
                        type="final",
                        text=SAFE_REFUSAL_MESSAGE,
                        citations=[],
                    )
                error_response = ChatErrorResponse.model_validate(chunk)
                return error_response

        if final_response:
            # Persist chat-level metadata so the mode survives chat resumption.
            # Best-effort: a Redis hiccup must not fail the user response that
            # has already been built.
            try:
                await chat_history_service.set_chat_metadata(chat_id, user_id, mode=mode)
            except Exception:
                logger.exception("Failed to persist chat metadata (non-fatal)")
            return final_response
        else:
            return ChatErrorResponse(chat_id=chat_id, user_id=user_id, type="error", error="No final response received")

    except Exception as e:
        logger.exception("REST chat error: %s", e)
        if is_safety_filter_error(e):
            return ChatFinalResponse(
                chat_id=chat_id,
                user_id=user_id,
                type="final",
                text=SAFE_REFUSAL_MESSAGE,
                citations=[],
            )
        return ChatErrorResponse(chat_id=chat_id, user_id=user_id, type="error", error=str(e))


@app.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """Handles real-time chat via WebSocket (streaming). Wrapped in two
    request_scope levels so dashboards see WS traffic at parity with HTTP:
    one ws_connection event per socket lifetime, and one ws_message event
    per turn (with status, chunk_count, message_index, and connection_id
    for correlation). The connection's request_id honors a handshake
    X-Request-Id header when present, so an upstream proxy or client can
    trace a session end-to-end."""
    engine: EndToEndAgentType | None = None
    chat_id: str | None = None
    user_id: str | None = None
    mode: ChatMode | None = None
    message_index = 0

    async with request_scope(
        "ws_connection",
        inbound_request_id=websocket.headers.get("x-request-id"),
        base_extra={"path": websocket.url.path},
    ) as conn_scope:
        conn_scope.extra["status"] = "error"
        try:
            # Read bearer token from the Authorization header. URL query strings
            # are routinely captured by access logs (App Service, proxies); headers
            # are not, so this keeps the API key out of cleartext log streams.
            auth_header = websocket.headers.get("authorization") or ""
            token: str | None = None
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip() or None

            if token is None or not await verify_websocket_auth(token):
                conn_scope.extra["status"] = "auth_failed"
                await websocket.close(code=1008, reason="Authentication failed")
                return

            await websocket.accept()

            while True:
                raw_message = await websocket.receive_text()
                try:
                    user_message = ChatRequest.model_validate_json(raw_message)
                except Exception as parse_error:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id or "",
                            user_id=user_id or "",
                            type="error",
                            error=f"Invalid request: {parse_error}",
                        ).model_dump_json()
                    )
                    continue

                received_chat_id = user_message.chat_id or chat_id or uuid4().hex
                received_user_id = user_message.user_id or user_id or uuid4().hex

                if chat_id is None:
                    chat_id = received_chat_id
                elif chat_id != received_chat_id:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id,
                            user_id=user_id or "",
                            type="error",
                            error="chat_id cannot be changed during a session",
                        ).model_dump_json()
                    )
                    continue

                if user_id is None:
                    user_id = received_user_id
                elif user_id != received_user_id:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id,
                            user_id=user_id,
                            type="error",
                            error="user_id cannot be changed during a session",
                        ).model_dump_json()
                    )
                    continue

                if not CLOUD_RAG_ENABLED:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id,
                            user_id=user_id,
                            type="error",
                            error=(
                                "Campfire RAG is unavailable in degraded local mode. "
                                "Configure cloud-backed mode to enable Azure OpenAI and Search."
                            ),
                        ).model_dump_json()
                    )
                    await websocket.send_text("[END]")
                    continue

                # Resolve mode on the first message of the session. Subsequent
                # messages that supply a different mode are rejected — mode is
                # locked for the chat's lifetime, like chat_id and user_id.
                if mode is None:
                    try:
                        mode = await _resolve_mode(chat_id, user_id, user_message.mode)
                    except HTTPException as mode_error:
                        await websocket.send_text(
                            ChatErrorResponse(
                                chat_id=chat_id,
                                user_id=user_id,
                                type="error",
                                error=mode_error.detail,
                            ).model_dump_json()
                        )
                        continue
                elif user_message.mode is not None and user_message.mode != mode:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id,
                            user_id=user_id,
                            type="error",
                            error="mode cannot be changed during a session",
                        ).model_dump_json()
                    )
                    continue

                if engine is None:
                    assert EndToEndAgent is not None
                    engine = EndToEndAgent(
                        chat_id=chat_id,
                        user_id=user_id,
                        chat_history_manager=chat_history_service,
                        mode=mode,
                    )
                    logger.info(
                        f"Initialized chat engine: chat_id={chat_id}, user_id={user_id}",
                        extra={
                            "microsoft.custom_event.name": "chat_engine_initialized",
                            "chat_id": chat_id,
                            "user_id": user_id,
                            "mode": mode,
                        },
                    )

                message_index += 1
                # Per-message scope: fresh request_id for this turn, structured
                # ws_message event on exit. The connection's request_id is in
                # `connection_id` so log queries can group all messages from a
                # single socket. Transient stream failures don't tear down the
                # session — WebSocketDisconnect alone propagates.
                async with request_scope(
                    "ws_message",
                    base_extra={
                        "connection_id": conn_scope.request_id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "message_index": message_index,
                    },
                ) as msg_scope:
                    msg_scope.extra["status"] = "error"
                    chunk_count = 0
                    try:
                        async for chunk in engine.ask_stream(user_message.message):
                            chunk_count += 1
                            chunk["chat_id"] = chat_id
                            chunk["user_id"] = user_id
                            if chunk["type"] == "progress":
                                progress_response = ChatProgressResponse.model_validate(chunk)
                                await websocket.send_text(progress_response.model_dump_json())
                            elif chunk["type"] == "delta":
                                delta_response = ChatDeltaResponse.model_validate(chunk)
                                await websocket.send_text(delta_response.model_dump_json())
                            elif chunk["type"] == "final":
                                final_response = ChatFinalResponse.model_validate(chunk)
                                await websocket.send_text(final_response.model_dump_json())
                            elif chunk["type"] == "extra":
                                follow_up_response = ChatFollowUpResponse.model_validate(chunk)
                                await websocket.send_text(follow_up_response.model_dump_json())
                            elif chunk["type"] == "fact_check":
                                fact_check_response = ChatFactCheckResponse.model_validate(chunk)
                                await websocket.send_text(fact_check_response.model_dump_json())
                            elif chunk["type"] == "cost":
                                cost_response = ChatCostResponse.model_validate(chunk)
                                await websocket.send_text(cost_response.model_dump_json())
                            elif chunk["type"] == "error":
                                if is_safety_filter_error(chunk.get("error")):
                                    msg_scope.extra["status"] = "safety_refusal"
                                    final_response = ChatFinalResponse(
                                        chat_id=chat_id,
                                        user_id=user_id,
                                        type="final",
                                        text=SAFE_REFUSAL_MESSAGE,
                                        citations=[],
                                    )
                                    await websocket.send_text(final_response.model_dump_json())
                                else:
                                    error_response = ChatErrorResponse.model_validate(chunk)
                                    await websocket.send_text(error_response.model_dump_json())
                        if msg_scope.extra["status"] == "error":
                            msg_scope.extra["status"] = "ok"
                    except WebSocketDisconnect:
                        msg_scope.extra["status"] = "disconnect"
                        raise
                    except Exception as stream_error:
                        logger.exception("Per-message stream error: %s", stream_error)
                        if is_safety_filter_error(stream_error):
                            msg_scope.extra["status"] = "safety_refusal"
                            await websocket.send_text(
                                ChatFinalResponse(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    type="final",
                                    text=SAFE_REFUSAL_MESSAGE,
                                    citations=[],
                                ).model_dump_json()
                            )
                        else:
                            await websocket.send_text(
                                ChatErrorResponse(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    type="error",
                                    error=str(stream_error),
                                ).model_dump_json()
                            )
                    finally:
                        msg_scope.extra["chunk_count"] = chunk_count

                # Persist chat-level metadata so the mode survives chat resumption.
                # Best-effort: a Redis hiccup must not prevent the [END] sentinel.
                try:
                    await chat_history_service.set_chat_metadata(chat_id, user_id, mode=mode)
                except Exception:
                    logger.exception("Failed to persist chat metadata (non-fatal)")

                await websocket.send_text("[END]")

        except WebSocketDisconnect:
            conn_scope.extra["status"] = "client_disconnect"
            logger.info(f"Client disconnected (chat_id={chat_id})")
        except Exception as e:
            try:
                if is_safety_filter_error(e):
                    await websocket.send_text(
                        ChatFinalResponse(
                            chat_id=chat_id or "",
                            user_id=user_id or "",
                            type="final",
                            text=SAFE_REFUSAL_MESSAGE,
                            citations=[],
                        ).model_dump_json()
                    )
                else:
                    await websocket.send_text(
                        ChatErrorResponse(
                            chat_id=chat_id or "", user_id=user_id or "", type="error", error=str(e)
                        ).model_dump_json()
                    )
                await websocket.send_text("[END]")
            except Exception:
                pass
            logger.exception("WebSocket chat error: %s", e)
        finally:
            conn_scope.extra["message_count"] = message_index


# Chat History API Endpoints
@app.get("/api/chat-history/{user_id}")
async def get_user_chats(
    user_id: str,
    _: str = Depends(verify_api_key),
) -> list[ChatSummary]:
    """Get this user's chats (most-recent first) with the stored mode for each."""
    try:
        return await chat_history_service.get_user_chats(user_id)
    except Exception as e:
        logger.error(f"Failed to get chat history: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve chat history")


@app.get("/api/chat-history/{user_id}/{chat_id}/messages", response_model=list[ChatMessageItemPublic])
async def get_chat_messages(
    user_id: str,
    chat_id: str,
    _: str = Depends(verify_api_key),
) -> list[ChatMessageItem]:
    """Get messages for a specific chat.

    Returns the public projection — backend-only debug fields (search_queries,
    fact_check) on ChatMessageItem are stripped by the response model.
    """
    try:
        messages = await chat_history_service.get_chat_messages(chat_id=chat_id, user_id=user_id)
        return messages
    except Exception as e:
        logger.error(f"Failed to get chat messages: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve chat messages")


@app.delete("/api/chat-history/{user_id}/{chat_id}")
async def delete_chat(
    user_id: str,
    chat_id: str,
    _: str = Depends(verify_api_key),
) -> dict:
    """Delete a chat from history (messages and metadata)."""
    try:
        await chat_history_service.delete_chat(chat_id, user_id)
        return {"status": "success", "message": "Chat deleted"}
    except Exception as e:
        logger.error(f"Failed to delete chat: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete chat")


@app.post("/api/artifacts")
async def get_artifact(
    request: ArtifactRequest,
    _: str = Depends(verify_api_key),
) -> dict:
    """Fetch a single artifact from an Azure Search index by ID."""
    _require_cloud_rag()
    assert letter_search_client is not None
    assert book_search_client is not None
    client = letter_search_client if request.index == "letter" else book_search_client
    try:
        return await client.get_document(request.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")
    except Exception as e:
        logger.error(f"Failed to fetch artifact: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch artifact")
