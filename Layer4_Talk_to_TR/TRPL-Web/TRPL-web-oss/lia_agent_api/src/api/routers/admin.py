# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Admin Router — internal management endpoints.

Currently exposes the kiosk session registry: livekit_worker_kiosk publishes its
long-lived session_id here on startup, and camera_platform reads it back so its
event POSTs can carry the right session_id.

These endpoints are auth-gated and not part of the public client surface.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_auth
from debate.services.session_store import session_store

logger = logging.getLogger(f"lia.{__name__}")

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Single-process FastAPI; one slot is enough for one kiosk per VM. If we ever
# host multiple kiosks on the same lia_agent_api, replace with a dict keyed by
# a kiosk identifier.
_kiosk_session_id: str | None = None


class RegisterKioskSessionRequest(BaseModel):
    session_id: str


class KioskSessionResponse(BaseModel):
    session_id: str | None
    registered: bool


@router.post("/register-kiosk-session", response_model=KioskSessionResponse)
async def register_kiosk_session(
    body: RegisterKioskSessionRequest,
    user: dict = Depends(require_auth()),
):
    """Register a session as the active kiosk session.

    Called by livekit_worker_kiosk after POST /api/debate/start succeeds.
    Validates that the session exists in session_store before accepting.
    """
    global _kiosk_session_id

    session = await session_store.get_session(body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"session_id {body.session_id} not found in session_store",
        )

    _kiosk_session_id = body.session_id
    logger.info(f"[admin] Kiosk session registered: {body.session_id}")
    return KioskSessionResponse(session_id=body.session_id, registered=True)


@router.get("/kiosk-session", response_model=KioskSessionResponse)
async def get_kiosk_session(user: dict = Depends(require_auth())):
    """Return the currently registered kiosk session_id (None if unregistered)."""
    sid = _kiosk_session_id
    return KioskSessionResponse(session_id=sid, registered=sid is not None)


@router.delete("/kiosk-session", response_model=KioskSessionResponse)
async def unregister_kiosk_session(user: dict = Depends(require_auth())):
    """Clear the registered kiosk session_id (called on worker shutdown)."""
    global _kiosk_session_id
    prev = _kiosk_session_id
    _kiosk_session_id = None
    if prev:
        logger.info(f"[admin] Kiosk session unregistered: {prev}")
    return KioskSessionResponse(session_id=None, registered=False)
