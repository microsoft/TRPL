# -*- coding: utf-8 -*-
"""LemonSlice pose-trigger control. Lightweight async helper used by the
bridge to fire `<pose:NAME/>` markers emitted inline by the LLM stream.

Sibling CLI version lives at vm-backend/pose_tragger/pose_trigger.py — that
one is for ad-hoc poking from the shell and scrapes the worker log for a
session_id. Here the session_id is passed in directly by the worker, so
there is no log scraping or argparse.
"""
from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger("pose_control")

LEMONSLICE_BASE = "https://lemonslice.com/api/liveai/sessions"


async def trigger_pose(session_id: str, pose_name: str) -> bool:
    api_key = os.getenv("LEMONSLICE_API_KEY")
    if not api_key:
        logger.error("LEMONSLICE_API_KEY is not set; pose %r dropped", pose_name)
        return False
    pose_name = pose_name.strip()
    if not pose_name:
        return False

    url = f"{LEMONSLICE_BASE}/{session_id}/control"
    payload = {"event": "pose-trigger", "pose_trigger": {"name": pose_name}, "model": "pro"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                url,
                headers={"Content-Type": "application/json", "X-API-Key": api_key},
                content=body,
            )
    except httpx.HTTPError as e:
        logger.error("pose-trigger network error pose=%r: %s", pose_name, e)
        return False

    if r.is_success:
        logger.info(
            "pose-trigger ok pose=%r session=%s… status=%s body=%s",
            pose_name, session_id[:16], r.status_code, (r.text or "")[:300],
        )
        return True

    logger.error(
        "pose-trigger failed: %s %s (pose=%r)",
        r.status_code, (r.text or "")[:300], pose_name,
    )
    return False
