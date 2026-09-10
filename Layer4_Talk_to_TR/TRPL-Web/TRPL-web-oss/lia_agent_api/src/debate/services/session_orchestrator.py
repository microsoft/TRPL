# -*- coding: utf-8 -*-
import logging
import asyncio
from datetime import datetime
from pathlib import Path
import json
from debate.services.agent_registry import AgentRegistry
from debate.services.session_store import DebateSession, session_store
from debate.services.tts_streamer import TTSOutputBridge as AzureTTSOutputBridge
from debate.services.tts_streamer_elevenlabs import (
    TTSOutputBridge as ElevenLabsTTSOutputBridge,
)
from debate.services.notes_updater import NotesUpdater
from debate.services.io import AsyncInputHandler, IOService, OutputManager
from debate.services.safety import safety_from_env
from debate.phases.welcome_engine import WelcomeEngine
from debate.models.constants import Phase
from api.config import config, VoicePreset

logger = logging.getLogger(f"lia.{__name__}")


class SessionOrchestrator:
    """Orchestrates session lifecycle and phase transitions"""

    def __init__(
        self, session: DebateSession, agent_registry: AgentRegistry, phases: list[Phase]
    ):
        self.session = session
        self.agent_registry = agent_registry
        self.phases = phases
        # Store reference to orchestrator in session for external access
        # TODO: refactor to avoid circular reference
        session.orchestrator = self
        self._current_engine_task: asyncio.Task | None = None
        self._manual_advance_phases = {"intro"}
        self._came_from_camera: bool = "camera" in self.phases
        self._manual_advance_event = asyncio.Event()
        self._forced_next_phase: str | None = None

    def _get_next_phase(self, current_phase: str) -> str | None:
        """Determine the next phase based on current phase"""
        phase_transitions = {
            "camera": "welcome",
            "welcome": "intro",
            "intro": "storys",
            "storys": "scenario",
            # VIP is a standalone honored-guest visit entered via explicit jump
            # (see /vip trigger). When it ends, return to camera standby if
            # configured; otherwise the while-loop below falls through to None
            # and the session finalizes.
            "vip": "camera",
            "scenario": "scenario_vote",
            "scenario_vote": "brainstorm_vote",
            "brainstorm_vote": "outro",
            "outro": None, # End of session
        }
        next_phase = phase_transitions.get(current_phase)
        while next_phase is not None and next_phase not in self.phases:
            next_phase = phase_transitions.get(next_phase)
        return next_phase

    def _create_engine_for_phase(self, phase: str):
        """Create the appropriate engine for a given phase"""
        if phase == "camera":
            from debate.phases.camera_engine import CameraEngine
            return CameraEngine(self.session, self.agent_registry)
        elif phase == "welcome":
            if self._came_from_camera:
                from debate.phases.welcome_new_engine import WelcomeNewEngine
                return WelcomeNewEngine(self.session, self.agent_registry)
            return WelcomeEngine(self.session, self.agent_registry)
        elif phase == "intro":
            from debate.phases.intro_engine import IntroEngine
            return IntroEngine(self.session, self.agent_registry)
        elif phase == "scenario":
            from debate.phases.scenario_engine import ScenarioEngine
            return ScenarioEngine(self.session, self.agent_registry)
        elif phase == "storys":
            from debate.phases.storys_engine import StorysEngine
            return StorysEngine(self.session, self.agent_registry)
        elif phase == "vip":
            from debate.phases.vip_engine import VipEngine
            return VipEngine(self.session, self.agent_registry)
        elif phase == "scenario_vote":
            from debate.phases.vote_engine import VoteEngine
            return VoteEngine(self.session, self.agent_registry, vote_type="scenario")
        elif phase == "brainstorm_vote":
            from debate.phases.vote_engine import VoteEngine
            return VoteEngine(self.session, self.agent_registry, vote_type="brainstorm")
        elif phase == "outro":
            from debate.phases.outro_engine import OutroEngine
            return OutroEngine(self.session, self.agent_registry)
        else:
            raise ValueError(f"Unknown phase: {phase}")

    def _get_graph_name_for_phase(self, phase: str) -> str:
        """Get the graph name for a given phase"""
        graph_names = {
            "camera": "camera",
            "welcome": "welcome",
            "intro": "intro",
            "brainstorm_vote": "vote",
            "scenario": "scenario",
            "storys": "storys",
            "vip": "vip",
            "scenario_vote": "vote",
            "outro": "outro",
        }
        return graph_names.get(phase, phase)

    async def _transition_to_phase(self, next_phase: str):
        """Transition session to the next phase"""
        logger.info(f"Transitioning from {self.session.state.phase} to {next_phase}")

        # Update session state
        self.session.state.phase = next_phase
        self.session.current_graph = self._get_graph_name_for_phase(next_phase)

        # Send debug message
        await self.session.output_queue.put(
            {
                "type": "debug_message",
                "timestamp": datetime.now().isoformat(),
                "debug": True,
                "agent": "System",
                "content": f"Transitioning to {next_phase} phase",
                "phase": next_phase,
            }
        )

    async def end_current_phase(self):
        """End the current phase - cancels the current engine task.
        The run_session loop will handle the cancellation and transition to the next phase."""
        if self._current_engine_task and not self._current_engine_task.done():
            logger.info(f"Ending current phase: {self.session.state.phase}")
            self._current_engine_task.cancel()
            # Don't await here - let run_session() handle the cancellation

    def request_manual_advance(self, phase: str) -> None:
        if phase in self._manual_advance_phases:
            logger.info("Manual advance requested for phase: %s", phase)
            self._manual_advance_event.set()

    def request_jump_to_phase(self, target_phase: str) -> None:
        if target_phase not in self.phases:
            logger.warning(
                "Jump requested to %s but phase is not configured: %s",
                target_phase,
                self.phases,
            )
            return
        logger.info("Jump requested to phase: %s", target_phase)
        self._forced_next_phase = target_phase
        # Unblock manual-advance phases if needed.
        self._manual_advance_event.set()

    async def _wait_for_manual_advance(self, phase: str) -> None:
        if phase not in self._manual_advance_phases:
            return
        logger.info("Waiting for manual advance from phase: %s", phase)
        await self._manual_advance_event.wait()
        logger.info("Manual advance received for phase: %s", phase)

    async def run_session(self):
        """Run the entire session, managing phase transitions"""
        voice_preset: VoicePreset = config.get_voice_preset(self.session.tts_voice)
        if voice_preset.provider == "elevenlabs":
            tts_bridge_class = ElevenLabsTTSOutputBridge
        else:
            tts_bridge_class = AzureTTSOutputBridge
        tts_bridge = tts_bridge_class(
            self.session.output_queue,
            voice_preset=voice_preset,
            audio_format=self.session.audio_format,
            audio_chunk_size_bytes=self.session.audio_chunk_size_bytes,
        )
        tts_task = asyncio.create_task(tts_bridge.run())
        self._tts_bridge = tts_bridge
        notes_updater = NotesUpdater(
            state=self.session.state,
            output_queue=self.session.output_queue,
            agent_registry=self.agent_registry,
        )
        notes_updater.start()
        self.session.notes_updater = notes_updater
        if self.session.input_handler is None:
            self.session.input_handler = AsyncInputHandler(
                self.session.input_queue,
                self.session.output_queue,
                session=self.session,
                state=self.session.state,
            )
        await self.session.input_handler.start()
        # SafetyService: per-session content moderation. Gated by
        # SAFETY_ENABLED env var (default off). When disabled, every
        # method on it is a cheap no-op.
        if self.session.safety is None:
            self.session.safety = safety_from_env(self.session.output_queue)
            await self.session.safety.start()
        self.session.io_service = IOService(
            input=self.session.input_handler,
            output=OutputManager(
                self.session.output_queue,
                state=self.session.state,
                session=self.session,
                safety=self.session.safety,
            ),
            notes_updater=self.session.notes_updater,
        )
        try:
            # Start with the current phase from state
            current_phase = self.phases[0]
            logger.info(f"Starting session orchestrator with phase: {current_phase}")

            # Run phases until there are no more
            while current_phase is not None:
                logger.info(f"Starting phase: {current_phase}")

                # Create engine for current phase
                engine = self._create_engine_for_phase(current_phase)
                self.session.current_graph = self._get_graph_name_for_phase(current_phase)

                if current_phase in self._manual_advance_phases:
                    self._manual_advance_event.clear()

                # Create and store the engine task
                self._current_engine_task = asyncio.create_task(engine.run())
                self.session.graph_engine_task = self._current_engine_task

                # Run the engine
                try:
                    await self._current_engine_task
                    logger.info(f"Phase {current_phase} completed successfully")
                except asyncio.CancelledError:
                    # Distinguish phase-engine cancellation from orchestrator-task cancellation.
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        logger.info(
                            "Session orchestrator cancellation requested during phase %s",
                            current_phase,
                        )
                        raise

                    # Phase was cancelled (e.g., by end_current_phase)
                    logger.info(f"Phase {current_phase} was cancelled")
                    # Continue to next phase
                except Exception as e:
                    logger.error(
                        f"Error in phase {current_phase}: {e}",
                        exc_info=True,
                    )
                    await self.session.output_queue.put(
                        {
                            "type": "debug_message",
                            "timestamp": datetime.now().isoformat(),
                            "debug": True,
                            "agent": "System",
                            "content": f"Phase {current_phase} failed: {str(e)}",
                            "phase": "error",
                        }
                    )
                    await session_store.update_session_status(
                        self.session.session_id,
                        "error",
                        error_message=f"Phase {current_phase} failed: {str(e)}",
                    )
                    break
                finally:
                    # Clear task references
                    self._current_engine_task = None
                    self.session.graph_engine_task = None

                await self._drain_audio_after_phase(current_phase)

                if current_phase in self._manual_advance_phases:
                    await self._wait_for_manual_advance(current_phase)

                # Determine next phase
                if self._forced_next_phase is not None:
                    next_phase = self._forced_next_phase
                    self._forced_next_phase = None
                elif (
                    current_phase in ("welcome", "storys")
                    and self._came_from_camera
                    and self.session.state.camera_state
                    and self.session.state.camera_state.return_to_camera
                ):
                    # Person left mic zone → return to camera standby
                    self.session.state.camera_state.return_to_camera = False
                    next_phase = "camera"
                    logger.info("%s phase ended with mic zone empty, returning to camera", current_phase)
                else:
                    next_phase = self._get_next_phase(current_phase)

                if next_phase is not None:
                    # Transition to next phase
                    await self._transition_to_phase(next_phase)
                    current_phase = next_phase
                else:
                    # No more phases - session complete
                    await self._finalize_session()
                    logger.info("All phases completed, session finished")
                    current_phase = None

        except Exception as e:
            logger.error(
                f"Error in session orchestrator: {e}",
                exc_info=True,
            )
            await self.session.output_queue.put(
                {
                    "type": "debug_message",
                    "timestamp": datetime.now().isoformat(),
                    "debug": True,
                    "agent": "System",
                    "content": f"Session orchestrator error: {str(e)}",
                    "phase": "error",
                }
            )
            await session_store.update_session_status(
                self.session.session_id,
                "error",
                error_message=f"Session orchestrator error: {str(e)}",
            )
        finally:
            if self.session.notes_updater is not None:
                await self.session.notes_updater.stop()
                self.session.notes_updater = None
            if self.session.input_handler is not None:
                await self.session.input_handler.stop()
                self.session.input_handler = None
            if self.session.safety is not None:
                await self.session.safety.close()
                self.session.safety = None
            self.session.io_service = None
            tts_task.cancel()
            try:
                await tts_task
            except asyncio.CancelledError:
                pass
            self._tts_bridge = None
            # Clear orchestrator references on normal shutdown path.
            if self.session.orchestrator is self:
                self.session.orchestrator = None
            self.session.orchestrator_task = None

    async def _drain_audio_after_phase(self, phase: str) -> None:
        # Do not block between phases. We only require a full audio flush
        # before session completion in _finalize_session().
        return

    async def _finalize_session(self) -> None:
        state = self.session.state
        if config.tts_enabled and getattr(self.session.output_queue, "any_audio_subscribers", False):
            if self._tts_bridge is not None:
                # Ensure all TTS chunks have been emitted before session_complete.
                await self._tts_bridge.flush()
            if config.tts_audio_flush_grace_seconds > 0:
                await asyncio.sleep(config.tts_audio_flush_grace_seconds)

        await self.session.output_queue.put(
            {
                "type": "session_complete",
                "timestamp": datetime.now().isoformat(),
                "history_id": self.session.session_id,
            }
        )
        await session_store.update_session_status(
            self.session.session_id,
            "completed",
        )

        try:
            history_dir = Path.home() / "debate_histories"
            history_dir.mkdir(exist_ok=True)

            history_file = history_dir / f"{self.session.session_id}.json"

            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(
                    [entry.model_dump(mode="json") for entry in state.history],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            logger.info(f"History saved to {history_file}")

            await self.session.output_queue.put(
                {
                    "type": "debug_message",
                    "timestamp": datetime.now().isoformat(),
                    "debug": True,
                    "agent": "System",
                    "content": f"History saved to debate_histories/{self.session.session_id}.json",
                    "phase": "session_complete",
                }
            )

        except Exception as e:
            logger.error(f"Failed to save history: {e}")
