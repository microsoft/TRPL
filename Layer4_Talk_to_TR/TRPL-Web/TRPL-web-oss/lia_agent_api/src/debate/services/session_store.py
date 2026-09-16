# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from typing import TYPE_CHECKING
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from contextlib import suppress
import hashlib
import secrets
from debate.models.state import DebateState
from debate.models.sockets import InboundSocketMessage
from debate.utils import BroadcastQueue
from api.config import config

if TYPE_CHECKING:
    from debate.services.session_orchestrator import SessionOrchestrator
    from debate.services.notes_updater import NotesUpdater
    from debate.services.io import AsyncInputHandler
    from debate.services.io import IOService
    from debate.services.safety import SafetyService
    from debate.debug_manager import DebugAgentManager


logger = logging.getLogger(f"lia.{__name__}")


@dataclass
class DebateSession:
    """Represents an active debate session"""

    session_id: str
    state: DebateState
    input_queue: asyncio.Queue[InboundSocketMessage]
    output_queue: BroadcastQueue
    status: str = "running"  # running, completed, error
    created_at: datetime = field(default_factory=datetime.now)
    error_message: str | None = None
    websocket_connected: bool = False  # Controller connection state
    connection_event: asyncio.Event = field(default_factory=asyncio.Event)
    disconnected_at: datetime | None = None
    _connection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_connection_count: int = 0
    controller_connection_id: str | None = None
    utterance_counter: int = -1
    participant_utterance_counter: int = -1
    default_audio_enabled: bool = False
    websocket_streaming: bool = False
    audio_format: str = "Raw24Khz16BitMonoPcm"
    audio_chunk_size_bytes: int | None = None
    tts_voice: str | None = None
    # Graph switching support
    current_graph: str | None = None  # "welcome" or "debate"
    graph_engine_task: asyncio.Task | None = None
    orchestrator_task: asyncio.Task | None = None
    orchestrator: "SessionOrchestrator | None" = None  # Reference to orchestrator managing this session
    notes_updater: "NotesUpdater | None" = None
    input_handler: "AsyncInputHandler | None" = None
    io_service: "IOService | None" = None
    safety: "SafetyService | None" = None
    debug_manager: "DebugAgentManager | None" = None
    controller_token_hash: str | None = None
    controller_token_expires_at: datetime | None = None
    controller_token_used: bool = False
    reconnect_token_hash: str | None = None
    reconnect_token_expires_at: datetime | None = None
    observer_join_code_hash: str | None = None
    observer_join_code_expires_at: datetime | None = None
    observer_token_hashes: dict[str, datetime] = field(default_factory=dict)


class SessionStore:
    """In-memory session storage with thread-safe access"""

    def __init__(self):
        self.sessions: dict[str, DebateSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    @staticmethod
    def _hash_secret(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _new_secret(prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(32)}"

    @staticmethod
    def _is_expired(expires_at: datetime | None) -> bool:
        if expires_at is None:
            return True
        return datetime.now() >= expires_at

    @staticmethod
    def _expiry_after(seconds: int) -> datetime:
        return datetime.fromtimestamp(datetime.now().timestamp() + max(1, seconds))

    @staticmethod
    def _prune_expired_observer_tokens(session: DebateSession) -> None:
        now = datetime.now()
        session.observer_token_hashes = {
            token_hash: expires_at
            for token_hash, expires_at in session.observer_token_hashes.items()
            if expires_at > now
        }

    async def create_session(
        self,
        state: DebateState,
        *,
        audio_enabled: bool = False,
        websocket_streaming: bool = False,
        audio_format: str = "Raw24Khz16BitMonoPcm",
        audio_chunk_size_bytes: int | None = None,
        tts_voice: str | None = None,
    ) -> str:
        """Create a new session and return its ID"""
        async with self._lock:
            session_id = str(uuid.uuid4())
            session = DebateSession(
                session_id=session_id,
                state=state,
                input_queue=asyncio.Queue[InboundSocketMessage](),
                output_queue=BroadcastQueue(),
                default_audio_enabled=audio_enabled,
                websocket_streaming=websocket_streaming,
                audio_format=audio_format,
                audio_chunk_size_bytes=audio_chunk_size_bytes,
                tts_voice=tts_voice,
            )
            self.sessions[session_id] = session
            return session_id

    async def get_session(self, session_id: str) -> DebateSession | None:
        """Retrieve a session by ID"""
        async with self._lock:
            return self.sessions.get(session_id)

    def active_sessions(self) -> list[str]:
        """Return session IDs of all running sessions."""
        return [
            sid for sid, s in self.sessions.items()
            if s.status == "running"
        ]

    async def update_session_status(
        self, session_id: str, status: str, error_message: str | None = None
    ):
        """Update session status"""
        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].status = status
                if error_message:
                    self.sessions[session_id].error_message = error_message

    async def remove_session(self, session_id: str):
        """Backward-compatible alias for cleanup_session."""
        await self.cleanup_session(session_id)

    async def _cancel_task(self, task: asyncio.Task | None, name: str, session_id: str) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.info("Cancelled %s for session %s", name, session_id)

    async def cleanup_session(self, session_id: str) -> None:
        """Stop background resources for a session and remove it from the store."""
        async with self._lock:
            session = self.sessions.pop(session_id, None)

        if session is None:
            return

        # Stop orchestrator/run-loop tasks first to prevent new work from being scheduled.
        await self._cancel_task(session.graph_engine_task, "graph_engine_task", session_id)
        await self._cancel_task(session.orchestrator_task, "orchestrator_task", session_id)
        session.graph_engine_task = None
        session.orchestrator_task = None

        # Stop helper services if they are still running.
        if session.notes_updater is not None:
            try:
                await session.notes_updater.stop()
            except Exception as e:
                logger.warning("Error stopping notes_updater for %s: %s", session_id, e, exc_info=True)
            session.notes_updater = None

        if session.input_handler is not None:
            try:
                await session.input_handler.stop()
            except Exception as e:
                logger.warning("Error stopping input_handler for %s: %s", session_id, e, exc_info=True)
            session.input_handler = None

        if session.safety is not None:
            try:
                await session.safety.close()
            except Exception as e:
                logger.warning("Error stopping safety for %s: %s", session_id, e, exc_info=True)
            session.safety = None

        if session.debug_manager is not None:
            try:
                await session.debug_manager.stop()
            except Exception as e:
                logger.warning("Error stopping debug_manager for %s: %s", session_id, e, exc_info=True)
            session.debug_manager = None

        session.io_service = None
        session.orchestrator = None
        session.connection_event.clear()
        session.websocket_connected = False
        session.controller_connection_id = None
        session.active_connection_count = 0

        # Clean up per-session KB async caches/tasks.
        try:
            from debate.services.shared import get_kb_search_manager

            kb_manager = await get_kb_search_manager()
            await kb_manager.cleanup_session(session_id)
        except Exception as e:
            logger.warning("Error cleaning KB async state for %s: %s", session_id, e, exc_info=True)

        logger.info("Session %s cleaned up and removed", session_id)

    async def list_sessions(self) -> dict[str, dict]:
        """List all sessions (for debugging/admin)"""
        async with self._lock:
            return {
                sid: {
                    "status": session.status,
                    "created_at": session.created_at.isoformat(),
                    "current_round": session.state.current_round,
                    "max_rounds": session.state.max_rounds,
                }
                for sid, session in self.sessions.items()
            }

    async def initialize_ws_auth(self, session_id: str) -> dict[str, str]:
        """Create fresh controller/reconnect/join credentials for the session."""
        async with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                raise ValueError("Session not found")

            controller_token = self._new_secret("ctrl")
            reconnect_token = self._new_secret("recon")
            observer_join_code = self._new_secret("join")

            session.controller_token_hash = self._hash_secret(controller_token)
            session.controller_token_expires_at = self._expiry_after(
                config.ws_controller_token_ttl_seconds
            )
            session.controller_token_used = False

            session.reconnect_token_hash = self._hash_secret(reconnect_token)
            session.reconnect_token_expires_at = self._expiry_after(
                config.ws_reconnect_token_ttl_seconds
            )

            session.observer_join_code_hash = self._hash_secret(observer_join_code)
            session.observer_join_code_expires_at = self._expiry_after(
                config.ws_join_code_ttl_seconds
            )
            session.observer_token_hashes.clear()

            return {
                "controller_token": controller_token,
                "reconnect_token": reconnect_token,
                "observer_join_code": observer_join_code,
            }

    async def exchange_join_code_for_observer_token(
        self, session_id: str, join_code: str
    ) -> str | None:
        """Validate join code and mint a single-use observer websocket token."""
        async with self._lock:
            session = self.sessions.get(session_id)
            if session is None or not join_code:
                return None
            if self._is_expired(session.observer_join_code_expires_at):
                return None
            expected_hash = session.observer_join_code_hash
            if expected_hash is None:
                return None
            if not secrets.compare_digest(self._hash_secret(join_code), expected_hash):
                return None

            observer_token = self._new_secret("obs")
            observer_hash = self._hash_secret(observer_token)
            session.observer_token_hashes[observer_hash] = self._expiry_after(
                config.ws_observer_token_ttl_seconds
            )
            self._prune_expired_observer_tokens(session)
            return observer_token

    async def rotate_controller_tokens(
        self, session_id: str, reconnect_token: str
    ) -> dict[str, str] | None:
        """Use reconnect token to issue fresh controller/reconnect tokens."""
        async with self._lock:
            session = self.sessions.get(session_id)
            if session is None or not reconnect_token:
                return None
            if self._is_expired(session.reconnect_token_expires_at):
                return None
            expected_hash = session.reconnect_token_hash
            if expected_hash is None:
                return None
            if not secrets.compare_digest(
                self._hash_secret(reconnect_token), expected_hash
            ):
                return None

            controller_token = self._new_secret("ctrl")
            next_reconnect_token = self._new_secret("recon")

            session.controller_token_hash = self._hash_secret(controller_token)
            session.controller_token_expires_at = self._expiry_after(
                config.ws_controller_token_ttl_seconds
            )
            session.controller_token_used = False

            session.reconnect_token_hash = self._hash_secret(next_reconnect_token)
            session.reconnect_token_expires_at = self._expiry_after(
                config.ws_reconnect_token_ttl_seconds
            )

            return {
                "controller_token": controller_token,
                "reconnect_token": next_reconnect_token,
            }

    async def consume_websocket_token(self, session_id: str, token: str) -> str | None:
        """Consume a single-use websocket token and return role."""
        async with self._lock:
            session = self.sessions.get(session_id)
            if session is None or not token:
                return None
            token_hash = self._hash_secret(token)

            self._prune_expired_observer_tokens(session)

            if (
                session.controller_token_hash
                and not session.controller_token_used
                and not self._is_expired(session.controller_token_expires_at)
                and secrets.compare_digest(token_hash, session.controller_token_hash)
            ):
                session.controller_token_used = True
                return "controller"

            observer_expiry = session.observer_token_hashes.get(token_hash)
            if observer_expiry and observer_expiry > datetime.now():
                session.observer_token_hashes.pop(token_hash, None)
                return "observer"

            return None

    async def _cleanup_stale_sessions(self):
        """Background task to clean up stale sessions"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes

                now = datetime.now()
                sessions_to_remove = []

                async with self._lock:
                    for session_id, session in list(self.sessions.items()):
                        # Remove sessions that are disconnected for > 1 hour
                        if session.disconnected_at:
                            time_disconnected = (
                                now - session.disconnected_at
                            ).total_seconds()
                            if time_disconnected > 3600:  # 1 hour
                                sessions_to_remove.append(session_id)
                                logger.info(
                                    f"Removing stale session {session_id} "
                                    f"(disconnected for {time_disconnected/3600:.1f} hours, status={session.status})"
                                )

                # Remove sessions outside the lock
                for session_id in sessions_to_remove:
                    await self.cleanup_session(session_id)

            except asyncio.CancelledError:
                logger.info("Session cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in session cleanup task: {e}", exc_info=True)

    def start_cleanup_task(self):
        """Start the background cleanup task"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_stale_sessions())
            logger.info("Started session cleanup task")

    async def stop_cleanup_task(self):
        """Stop the background cleanup task"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped session cleanup task")
        self._cleanup_task = None

    async def cleanup_all_sessions(self) -> None:
        """Cleanup and remove all active sessions."""
        async with self._lock:
            session_ids = list(self.sessions.keys())

        if not session_ids:
            return

        logger.info("Cleaning up %d active session(s)", len(session_ids))
        for session_id in session_ids:
            try:
                await self.cleanup_session(session_id)
            except Exception as e:
                logger.warning(
                    "Failed to cleanup session %s during shutdown: %s",
                    session_id,
                    e,
                    exc_info=True,
                )


# Global singleton instance
session_store = SessionStore()
