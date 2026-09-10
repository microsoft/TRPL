# -*- coding: utf-8 -*-
"""
WelcomeNewNode — camera-aware welcome node.

Inherits WelcomeNode; the key differences are:
  - Does NOT block waiting for ParticipantJoinedInput when roster is empty
  - Passes visitor appearance from CameraState to the agent
  - Uses WelcomeNewAgent (welcome_new) instead of WelcomeAgent
  - Listens for CameraEventInput to keep camera_state up-to-date
  - Time-limited turns when multiple visitors are waiting
"""
import logging
import time
from datetime import datetime

from debate.graph import MiniGraph
from debate.models.constants import TR_SPEAKER
from debate.models.inputs import CameraEventInput, HandRaiseInput, SpokenTextInput
from debate.models.state import (
    CameraPersonEntry,
    CameraState,
    DebateState,
    HistoryEntry,
)
from debate.services.io import OutputStreamSession, TimeoutInput
from debate.scenarios import get_scenario_context
from debate.nodes.welcome.welcome_node import WelcomeNode

logger = logging.getLogger(f"lia.{__name__}")

# Per-person soft time limit when others are waiting (seconds)
_TURN_SOFT_LIMIT = 120   # 2 min — start wrapping up
_TURN_HARD_LIMIT = 180   # 3 min — invite next person


class WelcomeNewNode(WelcomeNode):
    """Camera-aware welcome node — no tablet, pure mic + camera interaction."""

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        if not state.welcome_state:
            from api.config import config
            from debate.models.state import WelcomeState
            state.welcome_state = WelcomeState(
                max_rounds_per_visitor=config.welcome_max_rounds_per_visitor
            )

        # Ensure camera_state exists
        if not state.camera_state:
            state.camera_state = CameraState()

        has_roster = bool(state.roster)

        # Status 1 with roster: check for hand raise like original
        if has_roster:
            next_node = await self._maybe_handle_raised_hand(state)
            if next_node:
                return next_node

        # ── Time management ──
        turn_start = self._get_turn_start(state)
        people_waiting = self._count_people_waiting(state)
        time_pressure = self._calc_time_pressure(turn_start, people_waiting)

        welcome_round = self._count_welcome_rounds(state) + 1

        # Hard limit exceeded → wrap up and invite next
        if time_pressure == "hard_limit":
            return await self._hand_off_to_next(state)

        # Use the camera-aware generation path
        (
            response,
            target,
            stream_session,
            memory_update,
            relations,
        ) = await self._generate_and_stream_prompt_new(
            state,
            welcome_round=welcome_round,
            final=False,
            wrapping_up=(time_pressure == "soft_limit"),
            people_waiting=people_waiting,
        )

        if not response:
            logger.warning("No response generated, ending welcome_new")
            stream_session.cancel()
            return MiniGraph.END

        self._record_tr_response(state, response, target)
        if has_roster:
            self._apply_memory_updates(state, memory_update)
            self._record_relations(state, relations)

        # No tablet → no speaker_prompt or eligible_speakers
        await stream_session.finish(
            text=response,
            waiting_for_input=True,
            speaker_prompt=None,
            eligible_speakers=None,
        )

        # ── Wait for input (also process camera events) ──
        event = await self._wait_for_input_with_camera(state)

        if isinstance(event, CameraEventInput) and event.event_type == "MIC_ZONE_LEFT":
            logger.info("[WelcomeNew] Person left mic zone, returning to camera phase")
            state.camera_state.return_to_camera = True
            return MiniGraph.END
        elif isinstance(event, SpokenTextInput):
            return await self._handle_participant_input_new(state, event)
        elif isinstance(event, HandRaiseInput):
            logger.info("Hand raise received during welcome_new, continuing")
            return "WelcomeNewNode"
        else:
            logger.info("Timeout in welcome_new, sending nudge")
            return "TimeoutNudgeNode"

    # ------------------------------------------------------------------
    # Input handling with camera event passthrough
    # ------------------------------------------------------------------

    async def _wait_for_input_with_camera(self, state: DebateState):
        """Wait for spoken text / hand raise / timeout, while also
        consuming CameraEventInput to keep camera_state fresh."""
        while True:
            event = await self.io.input.wait_for_input(
                SpokenTextInput, HandRaiseInput, CameraEventInput, TimeoutInput
            )

            if isinstance(event, CameraEventInput):
                # Silently update camera state, don't generate LLM response
                try:
                    self._apply_camera_event(state.camera_state, event)
                except Exception as e:
                    logger.warning(
                        f"[WelcomeNew] Failed to apply camera event "
                        f"{event.event_type}: {e}"
                    )
                logger.info(
                    f"[WelcomeNew] Camera event during welcome: "
                    f"{event.event_type} (people_waiting={self._count_people_waiting(state)})"
                )

                # Person left mic zone and no one else at mic → return to camera
                if (
                    event.event_type == "MIC_ZONE_LEFT"
                    and state.camera_state
                    and state.camera_state.active_mic_person is None
                ):
                    logger.info("[WelcomeNew] Mic zone empty after MIC_ZONE_LEFT, returning to camera")
                    return event

                continue  # keep waiting for actual input

            return event

    # ------------------------------------------------------------------
    # Camera state update (shared logic with CameraNode)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_camera_event(camera_state: CameraState, event: CameraEventInput) -> None:
        """Update camera_state from a CameraEventInput (same logic as CameraNode)."""
        et = event.event_type
        p = event.payload

        if et == "PERSON_ENTERED_ROOM":
            pid = p.get("person_id")
            if pid is not None and pid not in camera_state.persons:
                camera_state.persons[pid] = CameraPersonEntry(person_id=pid)

        elif et == "BATCH_INVITE":
            batch_id = p.get("batch_id")
            camera_state.last_batch_id = batch_id
            person_ids = p.get("person_ids", [])
            appearances = p.get("appearances", {})
            for pid in person_ids:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.invited = True
                entry.invite_batch_id = batch_id
                app = appearances.get(pid) or appearances.get(str(pid)) or {}
                if app:
                    entry.appearance = app
                    entry.appearance_ready = True
            camera_state.total_greeted += len(person_ids)

        elif et == "MIC_ZONE_ENGAGED":
            pid = p.get("person_id")
            if pid is not None:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.in_mic_zone = True
                entry.mic_zone_visits = p.get("mic_zone_visits", entry.mic_zone_visits + 1)
                app = p.get("appearance")
                if app:
                    entry.appearance = app
                    entry.appearance_ready = p.get("appearance_ready", True)
                camera_state.active_mic_person = pid

        elif et == "MIC_ZONE_LEFT":
            pid = p.get("person_id")
            if pid is not None and pid in camera_state.persons:
                camera_state.persons[pid].in_mic_zone = False
            if camera_state.active_mic_person == pid:
                camera_state.active_mic_person = None

        elif et == "HAND_RAISE_RESPONSE":
            pid = p.get("person_id")
            if pid is not None:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.hand_raise_count = p.get(
                    "hand_raise_count", entry.hand_raise_count + 1
                )
                app = p.get("appearance")
                if app:
                    entry.appearance = app
                    entry.appearance_ready = p.get("appearance_ready", True)

        elif et == "ENGAGEMENT_SNAPSHOT":
            # VLM periodic observation of visitor behavior
            # payload: {"engagement": "attentive", "posture": "...", "gaze": "...", ...}
            camera_state.latest_engagement = p

    # ------------------------------------------------------------------
    # Time management
    # ------------------------------------------------------------------

    def _get_turn_start(self, state: DebateState) -> float:
        """Get the timestamp when the current mic person started their turn."""
        ws = state.welcome_state
        if ws and hasattr(ws, "current_turn_start") and ws.current_turn_start:
            return ws.current_turn_start

        # First round — record now
        now = time.time()
        if ws:
            ws.current_turn_start = now
        return now

    def _count_people_waiting(self, state: DebateState) -> int:
        """How many people are in the room but NOT at the mic."""
        cs = state.camera_state
        if not cs:
            return 0
        active = cs.active_mic_person
        return sum(
            1 for pid, entry in cs.persons.items()
            if pid != active
        )

    @staticmethod
    def _calc_time_pressure(turn_start: float, people_waiting: int) -> str:
        """Determine time pressure level for current speaker."""
        if people_waiting == 0:
            return "none"  # unlimited chat

        elapsed = time.time() - turn_start
        if elapsed >= _TURN_HARD_LIMIT:
            return "hard_limit"
        elif elapsed >= _TURN_SOFT_LIMIT:
            return "soft_limit"
        return "none"

    async def _hand_off_to_next(self, state: DebateState) -> str:
        """Generate a wrap-up + invite-next message and reset turn state."""
        cs = state.camera_state
        next_person = self._pick_next_visitor(state)
        next_desc = ""
        if next_person:
            app = next_person.appearance or {}
            parts = [v for k, v in app.items() if v and k in ("top", "bottom", "notable")]
            next_desc = f" — the friend in the {parts[0]}" if parts else ""

        wrap_text = (
            f"It's been wonderful chatting with you! "
            f"Now, I see another visitor waiting{next_desc}. Please, come on up!"
        )

        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=wrap_text,
                audience="all",
                phase="welcome",
                timestamp=datetime.now(),
                target=None,
            )
        )

        await self.io.output.send_output(
            text=wrap_text,
            waiting_for_input=True,
            phase="welcome",
        )

        # Reset turn timer for next visitor
        if state.welcome_state:
            state.welcome_state.current_turn_start = time.time()

        # Update active mic person if we know the next one
        if next_person and cs:
            cs.active_mic_person = next_person.person_id

        logger.info(f"[WelcomeNew] Handed off to next visitor: {next_person}")
        return "WelcomeNewNode"

    def _pick_next_visitor(self, state: DebateState) -> CameraPersonEntry | None:
        """Pick the next person to invite (first non-active person with appearance)."""
        cs = state.camera_state
        if not cs:
            return None
        active = cs.active_mic_person
        for entry in cs.persons.values():
            if entry.person_id != active and entry.appearance:
                return entry
        # Fallback: anyone not active
        for entry in cs.persons.values():
            if entry.person_id != active:
                return entry
        return None

    # ------------------------------------------------------------------
    # Participant input override
    # ------------------------------------------------------------------

    async def _handle_participant_input_new(
        self, state: DebateState, result: SpokenTextInput
    ) -> str:
        """Same as parent but returns to WelcomeNewNode."""
        participant_text = result.text.strip()
        participant_name = result.participant_id

        state.history.append(
            HistoryEntry(
                speaker=participant_name,
                text=participant_text,
                audience="all",
                phase="welcome",
                timestamp=datetime.now(),
                target=participant_name,
            )
        )

        logger.info(f"Received from {participant_name}: {participant_text[:50]}...")
        await self._schedule_kb_search_if_needed(state, participant_text)
        return "WelcomeNewNode"

    # ------------------------------------------------------------------
    # LLM generation override
    # ------------------------------------------------------------------

    async def _generate_and_stream_prompt_new(
        self,
        state: DebateState,
        *,
        welcome_round: int,
        final: bool,
        wrapping_up: bool = False,
        people_waiting: int = 0,
    ) -> tuple[
        str,
        str | None,
        OutputStreamSession,
        dict | None,
        list | None,
    ]:
        """Like parent's _generate_and_stream_prompt but adds camera context."""
        history = self._build_welcome_history(state)
        last_speaker = state.last_speaker
        participants = self._build_participants(state)
        kb_context = await self._get_kb_context(state)

        has_roster = bool(state.roster)
        status = 1 if has_roster else 2
        visitor_appearance = self._get_active_visitor_appearance(state)

        # Camera phase conversation as context (avoids repeating greetings)
        camera_context = self._build_camera_context(state)
        # Latest VLM engagement observation
        engagement = (
            state.camera_state.latest_engagement
            if state.camera_state
            else None
        )

        agent = self.agent_registry.welcome_new
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase="welcome",
        )

        scenario_context = get_scenario_context(state.scenario_id)
        parsed = await agent.generate_response_streaming(
            round=welcome_round,
            status=status,
            history=history,
            last_speaker=last_speaker,
            participants=participants,
            visitor_appearance=visitor_appearance,
            camera_context=camera_context,
            engagement=engagement,
            knowledge_base_context=kb_context,
            upcoming_scenario=scenario_context,
            final=final,
            wrapping_up=wrapping_up,
            people_waiting=people_waiting,
            on_response_chunk=stream_session.write,
        )

        response = (parsed.get("response") or "").strip()
        if not response:
            response = stream_session.full_text.strip()

        target = parsed.get("target")
        if not isinstance(target, str) or target not in state.roster:
            target = None

        memory_update = parsed.get("memory_update")
        relations = parsed.get("relations")

        return response, target, stream_session, memory_update, relations

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_camera_context(state: DebateState) -> list[dict]:
        """Build a summary of camera-phase exchanges for welcome agent context.

        Returns a compact list like:
          [{"event": "BATCH_INVITE", "response": "Welcome, friend in the blue jacket!"},
           {"event": "MIC_ZONE_ENGAGED", "response": "Come right up to the mic!"}]
        """
        cs = state.camera_state
        if not cs or not cs.recent_exchanges:
            return []

        context = []
        for ex in cs.recent_exchanges:
            evt = ex.get("event", {})
            reply = ex.get("reply", {})
            context.append({
                "event": evt.get("event_type", ""),
                "response": reply.get("response", ""),
            })
        return context

    def _get_active_visitor_appearance(self, state: DebateState) -> dict | None:
        """Get the appearance of the person currently at the mic zone."""
        camera_state = state.camera_state
        if not camera_state:
            return None

        # Try active mic person first
        active_pid = camera_state.active_mic_person
        if active_pid is not None and active_pid in camera_state.persons:
            entry = camera_state.persons[active_pid]
            return {
                "person_id": entry.person_id,
                **(entry.appearance if isinstance(entry.appearance, dict) else {}),
            }

        # Fallback: last person with appearance
        for entry in reversed(list(camera_state.persons.values())):
            if entry.appearance:
                return {
                    "person_id": entry.person_id,
                    **(entry.appearance if isinstance(entry.appearance, dict) else {}),
                }

        return None
