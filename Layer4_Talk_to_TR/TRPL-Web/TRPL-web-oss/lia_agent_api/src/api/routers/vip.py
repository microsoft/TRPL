# -*- coding: utf-8 -*-
"""
VIP trigger router — enter / leave the honored-guest (VIP) storymode.

This branch has no RFID/badge feed, so VIP is entered explicitly: a small page
(similar to /jailbreak-cases) POSTs here to jump the active session into the
"vip" phase (VipEngine + VipNode + the warmer "storys.vip" agent). VIP is sticky
once entered — it ignores camera-driven exits — and ends only via /vip/leave,
the VipEngine idle-watchdog (mic empty for idle_timeout_sec), or the guest's
agent marking the visit done.

Endpoints (all additive; nothing else routes through here):
  POST /vip/enter   — jump the target session to the VIP phase.
  POST /vip/leave   — end VIP early (returns to camera standby if configured).
  GET  /vip/status  — current phase of the target session + VIP availability.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_auth
from debate.models.constants import Phase
from debate.services import vip_config
from debate.services.session_store import session_store

logger = logging.getLogger(f"lia.{__name__}")

router = APIRouter(prefix="/vip", tags=["vip"])


class VipRequest(BaseModel):
    # Optional: target a specific session. Omit to use the single active /
    # WS-connected session (the one on screen), matching the camera router's
    # heuristic for a single-kiosk deployment.
    session_id: str | None = None


class VipResponse(BaseModel):
    ok: bool
    session_id: str
    phase: str
    detail: str | None = None


async def _resolve_session(session_id: str | None):
    """Return the target session. Explicit id wins; else prefer a WS-connected
    session, else any active session. Raises 404 when none is found."""
    if session_id:
        session = await session_store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session_id {session_id} not found")
        return session

    candidates = []
    for sid in session_store.active_sessions():
        session = await session_store.get_session(sid)
        if session and session.state:
            candidates.append(session)
    for s in candidates:
        if bool(getattr(s, "websocket_connected", False)):
            return s
    if candidates:
        return candidates[0]
    raise HTTPException(status_code=404, detail="no active session to drive")


def _ensure_vip_phase(orchestrator) -> None:
    """Make sure 'vip' is a jump target for this orchestrator. Appending it is
    safe: nothing transitions INTO vip except an explicit jump, so the normal
    linear flow is unchanged."""
    phases = getattr(orchestrator, "phases", None)
    if phases is None:
        return
    if Phase.vip not in phases and "vip" not in phases:
        phases.append(Phase.vip)
        logger.info("[VIP] 'vip' phase injected into session phase list for jump")


@router.post("/enter", response_model=VipResponse)
async def enter_vip(
    body: VipRequest | None = None,
    user: dict = Depends(require_auth()),
):
    """Jump the target session into the VIP storymode. Auth-gated."""
    body = body or VipRequest()
    if not vip_config.enabled():
        raise HTTPException(status_code=403, detail="VIP mode is disabled (vip_config.json)")

    session = await _resolve_session(body.session_id)
    orch = session.orchestrator
    if orch is None:
        raise HTTPException(status_code=409, detail="session has no running orchestrator")

    if session.state and session.state.phase == "vip":
        return VipResponse(ok=True, session_id=session.session_id, phase="vip",
                           detail="already in VIP")

    _ensure_vip_phase(orch)
    logger.info("[VIP] enter requested for session %s (from phase %s)",
                session.session_id, session.state.phase if session.state else "?")
    orch.request_jump_to_phase("vip")
    await orch.end_current_phase()
    return VipResponse(ok=True, session_id=session.session_id, phase="vip",
                       detail="jumping to VIP")


@router.post("/leave", response_model=VipResponse)
async def leave_vip(
    body: VipRequest | None = None,
    user: dict = Depends(require_auth()),
):
    """End VIP early. Returns to camera standby if configured, else the session
    finalizes (mirrors the orchestrator's vip→camera transition). Auth-gated."""
    body = body or VipRequest()
    session = await _resolve_session(body.session_id)
    orch = session.orchestrator
    if orch is None:
        raise HTTPException(status_code=409, detail="session has no running orchestrator")

    if not session.state or session.state.phase != "vip":
        return VipResponse(ok=True, session_id=session.session_id,
                           phase=session.state.phase if session.state else "?",
                           detail="not in VIP — nothing to leave")

    logger.info("[VIP] leave requested for session %s", session.session_id)
    await orch.end_current_phase()
    if session.state.camera_state:
        session.state.camera_state.active_mic_person = None
    if session.state.phase_memory:
        session.state.phase_memory.pop("visit", None)
    return VipResponse(ok=True, session_id=session.session_id, phase="vip",
                       detail="ending VIP")


@router.get("/status", response_model=VipResponse)
async def vip_status(
    session_id: str | None = None,
    user: dict = Depends(require_auth()),
):
    """Report the target session's current phase and whether VIP is enabled.
    Auth-gated (leaks the live session_id otherwise)."""
    session = await _resolve_session(session_id)
    phase = session.state.phase if session.state else "?"
    return VipResponse(ok=True, session_id=session.session_id, phase=phase,
                       detail=f"vip_enabled={vip_config.enabled()}")
