# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CameraNode — processes camera platform events and generates LLM responses.

This node runs in a loop, waiting for CameraEventInput from the input queue.
When an event arrives, it generates a contextual LLM response via CameraAgent
and streams it to the output.
"""
import logging
from datetime import datetime

from api.config import config
from debate.graph import MiniGraph
from debate.models.constants import TR_SPEAKER
from debate.models.inputs import CameraEventInput, TimeoutInput
from debate.models.state import CameraPersonEntry, CameraState, DebateState, HistoryEntry
from debate.nodes.base import BaseNode
from debate.services.io import TimeoutInput as IOTimeoutInput

logger = logging.getLogger(f"lia.{__name__}")

# Events that always trigger an LLM response
_RESPONSE_EVENTS = {
    "BATCH_INVITE",
    "MIC_ZONE_ENGAGED",
    "HAND_RAISE_RESPONSE",
    "MIC_ZONE_LEFT",
}

# Events that are informational only (no LLM response needed)
_INFO_EVENTS = {
    "PERSON_ENTERED_ROOM",
    "ENGAGEMENT_SNAPSHOT",
}

# Events that generate a nudge ONLY when config.camera_urge_visitors is True.
# Keeps the default behavior (silent avatar) while letting deployments opt in
# to a more proactive "come on in" persona via env var CAMERA_URGE_VISITORS.
_URGE_EVENTS = {
    "VISITOR_HESITATING",
    "VISITOR_IDLE_IN_ROOM",
}

# How many recent exchanges to keep for short-term memory
_MAX_RECENT_HISTORY = 8


class CameraNode(BaseNode):
    """Processes camera events and generates AI responses."""

    async def run(self, state: DebateState) -> str:
        self.log_start(state)

        if not state.camera_state:
            state.camera_state = CameraState()

        # Wait for a camera event
        event = await self.io.input.wait_for_input(CameraEventInput, IOTimeoutInput)

        if isinstance(event, IOTimeoutInput):
            logger.debug("Camera node timeout, looping")
            return "CameraNode"

        if not isinstance(event, CameraEventInput):
            logger.debug(f"Unexpected input type: {type(event)}, looping")
            return "CameraNode"

        event_type = event.event_type
        payload = event.payload
        logger.info(f"[CameraNode] Received event: {event_type} payload_keys={list(payload.keys())}")

        # Update camera state from event
        self._update_camera_state(state.camera_state, event_type, payload)

        # Informational events — just update state, no response
        if event_type in _INFO_EVENTS:
            logger.info(f"[CameraNode] Info event {event_type}, no response needed")
            return "CameraNode"

        # MIC_ZONE_ENGAGED is a transition trigger, not a chat turn. We used
        # to speak a "welcome to the mic" line here, but the next phase
        # (welcome/storys) immediately opens with its own greeting, so the
        # visitor heard two welcomes back-to-back. Just update state and
        # hand off; the next phase does the talking.
        if event_type == "MIC_ZONE_ENGAGED":
            logger.info("[CameraNode] MIC_ZONE_ENGAGED — handing off to next phase")
            return MiniGraph.END

        # Build the active response set. Urge events only count when the
        # operator has opted in; this lets a quieter deployment leave
        # hesitating visitors alone while a livelier one nudges them.
        response_set = _RESPONSE_EVENTS
        if config.camera_urge_visitors:
            response_set = response_set | _URGE_EVENTS

        if event_type in response_set:
            await self._generate_and_send_response(state, event_type, payload)

        return "CameraNode"

    def _update_camera_state(
        self, camera_state: CameraState, event_type: str, payload: dict
    ) -> None:
        """Update the camera state based on the event."""
        if event_type == "PERSON_ENTERED_ROOM":
            pid = payload.get("person_id")
            if pid is not None and pid not in camera_state.persons:
                camera_state.persons[pid] = CameraPersonEntry(person_id=pid)

        elif event_type == "BATCH_INVITE":
            batch_id = payload.get("batch_id")
            camera_state.last_batch_id = batch_id
            person_ids = payload.get("person_ids", [])
            appearances = payload.get("appearances", {})
            for pid in person_ids:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.invited = True
                entry.invite_batch_id = batch_id
                # appearances may be keyed by int or str
                app = appearances.get(pid) or appearances.get(str(pid)) or {}
                if app:
                    entry.appearance = app
                    entry.appearance_ready = True
            camera_state.total_greeted += len(person_ids)

        elif event_type == "MIC_ZONE_ENGAGED":
            pid = payload.get("person_id")
            if pid is not None:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.in_mic_zone = True
                entry.mic_zone_visits = payload.get("mic_zone_visits", entry.mic_zone_visits + 1)
                app = payload.get("appearance")
                if app:
                    entry.appearance = app
                    entry.appearance_ready = payload.get("appearance_ready", True)
                camera_state.active_mic_person = pid

        elif event_type == "MIC_ZONE_LEFT":
            pid = payload.get("person_id")
            if pid is not None and pid in camera_state.persons:
                camera_state.persons[pid].in_mic_zone = False
            if camera_state.active_mic_person == pid:
                camera_state.active_mic_person = None

        elif event_type == "HAND_RAISE_RESPONSE":
            pid = payload.get("person_id")
            if pid is not None:
                if pid not in camera_state.persons:
                    camera_state.persons[pid] = CameraPersonEntry(person_id=pid)
                entry = camera_state.persons[pid]
                entry.hand_raise_count = payload.get("hand_raise_count", entry.hand_raise_count + 1)
                app = payload.get("appearance")
                if app:
                    entry.appearance = app
                    entry.appearance_ready = payload.get("appearance_ready", True)

    async def _generate_and_send_response(
        self, state: DebateState, event_type: str, payload: dict
    ) -> None:
        """Generate an LLM response for the camera event and send it."""
        camera_state = state.camera_state
        room_context = {
            "total_persons": len(camera_state.persons) if camera_state else 0,
            "greeted_count": camera_state.total_greeted if camera_state else 0,
            "active_mic_person": camera_state.active_mic_person if camera_state else None,
        }

        # Build the event payload that will also be recorded in short-term memory
        event_record = {
            "event_type": event_type,
            "payload": payload,
            "room_context": room_context,
        }

        agent = self.agent_registry.camera
        stream_session = await self.io.output.start_stream(
            speaker=TR_SPEAKER,
            phase="camera",
        )

        parsed = await agent.generate_camera_response(
            event_type=event_type,
            payload=payload,
            room_context=room_context,
            recent_history=camera_state.recent_exchanges,
            on_response_chunk=stream_session.write,
        )

        response = (parsed.get("response") or "").strip()
        if not response:
            response = stream_session.full_text.strip()

        if not response:
            logger.warning(f"[CameraNode] No response for event {event_type}")
            stream_session.cancel()
            return

        # Record in short-term memory (keep last N exchanges)
        camera_state.recent_exchanges.append({
            "event": event_record,
            "reply": parsed,
        })
        if len(camera_state.recent_exchanges) > _MAX_RECENT_HISTORY:
            camera_state.recent_exchanges = camera_state.recent_exchanges[-_MAX_RECENT_HISTORY:]

        # Record in history
        state.history.append(
            HistoryEntry(
                speaker=TR_SPEAKER,
                text=response,
                audience="all",
                phase="camera",
                timestamp=datetime.now(),
                target=None,
            )
        )

        await stream_session.finish(
            text=response,
            waiting_for_input=False,
        )

        logger.info(f"[CameraNode] Response sent for {event_type}: {response[:60]}...")
