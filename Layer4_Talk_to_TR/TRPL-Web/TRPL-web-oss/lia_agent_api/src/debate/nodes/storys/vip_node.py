# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
VipNode — storytelling node for recognized, honored guests.

Same conversation loop as StorysNode, but for an honored guest. It differs ONLY
in the few behaviors the host asked for, each implemented as an override of a
StorysNode hook:

  - _select_agent ............. uses the VIP scenario agent (prompt key
                                "storys.vip" — warmer/looser/more candid).
  - _wait_for_input_with_camera  immune to camera events: a camera-detected
                                crowd or MIC_ZONE_LEFT never ends or redirects
                                the VIP conversation. The visit ends only via
                                the VipEngine idle-watchdog (mic empty) or an
                                explicit phase jump.
  - _count_people_waiting ..... always 0 (no crowd urgency in the prompt).
  - _time_pressure ............ always "none" (never time-limited / handed off).
  - _apply_input_guardrail .... observe-only: still logs/reports via the async
                                L2 check, but never sanitizes input or arms a
                                safety directive. A trusted guest isn't fenced.

Everything else (RAG, history, greeting, memory, streaming) is inherited
unchanged from StorysNode.
"""
import asyncio
import logging

from api.config import config
from debate.models.inputs import CameraEventInput, SpokenTextInput
from debate.models.state import DebateState
from debate.nodes.camera_utils import apply_camera_event
from debate.nodes.storys.node import StorysNode
from debate.services import prompt_injection
from debate.services.io import TimeoutInput

logger = logging.getLogger(f"lia.{__name__}")


class VipNode(StorysNode):
    """Storys node for an honored, recognized guest. See module docstring."""

    def _select_agent(self, mode: str):
        # Prefer the dedicated VIP agent; fall back to the normal storys
        # agent if (mis)configured without a VIP prompt so VIP never breaks.
        vip = getattr(self.agent_registry, "storys_vip", None)
        if vip is not None:
            return vip
        logger.warning("[VIP] storys_vip agent missing — falling back to storys_for_mode")
        return self.agent_registry.storys_for_mode(mode)

    async def _wait_for_input_with_camera(self, state: DebateState):
        # VIP is immune to camera-driven transitions. We still apply camera
        # events to camera_state (keeps background context coherent) but NEVER
        # return one, so the run loop's MIC_ZONE_LEFT / return-to-camera paths
        # stay dead for VIP. Only spoken text / timeout flow through.
        while True:
            event = await self.io.input.wait_for_input(
                SpokenTextInput, CameraEventInput, TimeoutInput
            )
            if isinstance(event, CameraEventInput):
                try:
                    apply_camera_event(state.camera_state, event)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[VIP] Camera event error %s: %s",
                                   event.event_type, e)
                continue
            return event

    def _count_people_waiting(self, state: DebateState) -> int:
        return 0

    def _time_pressure(self, state: DebateState, people_waiting: int) -> str:
        return "none"

    def _apply_input_guardrail(
        self,
        state: DebateState,
        participant_text: str,
        participant_name: str,
        result,
    ) -> str:
        # Honored guest: observe-only. Keep the audit trail (async L2 check
        # logs + auto-reports) but never sanitize the input or arm a safety
        # directive — no enforcement fencing for a trusted visitor.
        if config.prompt_injection_enabled:
            asyncio.create_task(
                prompt_injection.check_input(participant_text),
                name="prompt_injection_check_vip",
            )
        return participant_text
