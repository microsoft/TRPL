# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
VipEngine — storytelling engine for recognized, honored guests.

Identical to StorysEngine (same RAG + visitor-history wiring, same cleanup)
except it builds a VipNode instead of a StorysNode and runs under the "vip"
phase label. The node swap is driven by the inherited ``_node_class`` hook,
so build_graph / after_run / cleanup are all reused unchanged.
"""
import asyncio
import logging

from debate.nodes.storys.vip_node import VipNode
from debate.phases.storys_engine import StorysEngine
from debate.services import vip_config

logger = logging.getLogger(f"lia.{__name__}")


class VipEngine(StorysEngine):
    """Storys engine for an honored guest — builds VipNode under the 'vip' phase."""

    _node_class = VipNode

    @property
    def phase_label(self) -> str:
        return "vip"

    async def run(self) -> None:
        """Run VIP, with a camera-driven idle-watchdog as the time-based exit.

        VIP is sticky — VipNode ignores camera-driven transitions — so in a
        KIOSK deployment a guest who simply walked off would park forever; the
        watchdog ends VIP once the mic zone is empty. But the watchdog's
        "mic empty" signal is `camera_state.active_mic_person`, which only a
        camera ever sets. In the WEB /vip flow there is NO camera (phases are
        just ["vip"]), so that field is permanently None and the watchdog would
        wrongly end an ACTIVE conversation at the first interval. Web VIP ends
        naturally on browser disconnect instead — so only arm the watchdog when
        this session is actually camera-driven.
        When VIP ends, the orchestrator routes 'vip' onward via _get_next_phase
        (→ camera standby if configured, else the session finalizes).
        """
        orch = self.session.orchestrator
        camera_driven = orch is not None and "camera" in getattr(orch, "phases", ())
        watchdog = (
            asyncio.create_task(self._idle_watchdog(), name="vip_idle_watchdog")
            if camera_driven else None
        )
        if watchdog is None:
            logger.info("[VIP] no camera in this session — idle-watchdog disabled "
                        "(ends on disconnect / agent done)")
        try:
            await super().run()
        finally:
            if watchdog is not None:
                watchdog.cancel()

    async def _idle_watchdog(self) -> None:
        try:
            while True:
                interval = vip_config.idle_timeout_sec()
                await asyncio.sleep(interval)
                cs = self.session.state.camera_state
                if cs and cs.active_mic_person is not None:
                    logger.info("[VIP] idle-watchdog: mic still occupied — re-arming (%ss)", interval)
                    continue
                logger.info("[VIP] idle-watchdog: mic empty after %ss — ending VIP", interval)
                orch = self.session.orchestrator
                if orch is not None:
                    await orch.end_current_phase()
                if cs:
                    cs.active_mic_person = None
                if self.session.state.phase_memory:
                    self.session.state.phase_memory.pop("visit", None)
                return
        except asyncio.CancelledError:
            pass
