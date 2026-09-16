#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pose-trigger CLI for the currently running LemonSlice avatar session.

Reuses the livekit_worker venv (httpx, python-dotenv already installed there)
and its .env (for LEMONSLICE_API_KEY).

Flow:
    1. Find the most recent "LemonSlice avatar session_id=..." entry in
       livekit_worker/logs/livekit_worker.log.
    2. POST {"event": "pose-trigger", "pose_trigger": {"name": <pose>}}
       to https://lemonslice.com/api/liveai/sessions/{session_id}/control
       with X-API-Key from livekit_worker/.env.

If that session has already ended, LemonSlice returns an error — pass
--session-id to override.

Usage:
    ../livekit_worker/.venv/bin/python pose_trigger.py <pose_name>
    ./pose_trigger.py <pose_name>                        # via shebang
    ./pose_trigger.py <pose_name> --session-id <id>      # skip log lookup
    ./pose_trigger.py --list-poses                       # show known poses
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import dotenv
import httpx

logger = logging.getLogger("pose_trigger")

REPO_ROOT = Path(__file__).resolve().parent.parent  # vm-backend/
WORKER_DIR = REPO_ROOT / "livekit_worker"
WORKER_ENV = WORKER_DIR / ".env"
WORKER_LOG = WORKER_DIR / "logs" / "livekit_worker.log"

LEMONSLICE_BASE = "https://lemonslice.com/api/liveai/sessions"

KNOWN_POSES: tuple[str, ...] = (
    "ted_wave",
    "ted_point_v2",
    "ted_open_hands",
    "ted_welcome3",
    "ted_clap",
    "ted_chin",
    "ted-head-tilt-1x-listen",
    "ted-sway-small",
    "ted-sway-medium",
)


def lemonslice_session_control_url(session_id: str) -> str:
    return f"{LEMONSLICE_BASE}/{session_id}/control"


_SESSION_RE = re.compile(r"LemonSlice avatar session_id=([0-9a-fA-F-]{36})")


def find_latest_session_id(log_path: Path = WORKER_LOG) -> str:
    """Return the most recent LemonSlice session_id logged by livekit_worker.

    Scans the log backwards and returns the first session_id it sees. The
    caller is responsible for knowing whether that session is still alive
    (LemonSlice will return an error on the POST if it isn't).
    """
    if not log_path.exists():
        raise RuntimeError(f"livekit_worker log not found: {log_path}")

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in reversed(f.readlines()):
            m = _SESSION_RE.search(line)
            if m:
                return m.group(1)

    raise RuntimeError("no LemonSlice session_id found in worker log")


async def lemonslice_control_pose_trigger(session_id: str, pose_name: str) -> bool:
    """Same control endpoint and auth as ``lemonslice_control_update_image``;
    event ``pose-trigger``."""
    api_key = os.getenv("LEMONSLICE_API_KEY")
    if not api_key:
        logger.error("LEMONSLICE_API_KEY is not set")
        return False
    pose_name = pose_name.strip()
    if not pose_name:
        logger.error("LemonSlice pose-trigger needs a non-empty pose name")
        return False

    url = lemonslice_session_control_url(session_id)
    payload = {"event": "pose-trigger", "pose_trigger": {"name": pose_name}, "model": "pro"}
    post_json = json.dumps(payload, ensure_ascii=False)
    logger.info("LemonSlice pose-trigger POST JSON: %s", post_json)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
            content=post_json.encode("utf-8"),
            timeout=30.0,
        )

    if response.is_success:
        logger.info(
            "LemonSlice control pose-trigger ok: pose_name=%r session_id=%s… status=%s body=%s",
            pose_name,
            session_id[:16],
            response.status_code,
            (response.text or "")[:500],
        )
        return True

    logger.error(
        "LemonSlice control pose-trigger failed: %s %s (pose_name=%r)",
        response.status_code,
        (response.text or "")[:500],
        pose_name,
    )
    return False


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Trigger a pose on the running LemonSlice avatar.")
    p.add_argument("pose_name", nargs="?", help="Pose name to trigger.")
    p.add_argument(
        "--session-id",
        help="Override session_id. Default: scan worker log for newest live session.",
    )
    p.add_argument("--log-path", default=str(WORKER_LOG), help="Path to livekit_worker.log.")
    p.add_argument("--list-poses", action="store_true", help="List known pose names and exit.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list_poses:
        if not KNOWN_POSES:
            print("(no poses registered yet — fill KNOWN_POSES in pose_trigger.py)")
        else:
            for p in KNOWN_POSES:
                print(p)
        return 0

    if not args.pose_name:
        logger.error("pose_name is required (or pass --list-poses)")
        return 2

    if WORKER_ENV.exists():
        dotenv.load_dotenv(WORKER_ENV)
    else:
        logger.warning("worker .env not found at %s; relying on process env", WORKER_ENV)

    if args.session_id:
        session_id = args.session_id
    else:
        try:
            session_id = find_latest_session_id(Path(args.log_path))
        except RuntimeError as e:
            logger.error("could not resolve session_id: %s", e)
            return 1
        logger.info("using newest session_id from worker log: %s", session_id)

    ok = asyncio.run(lemonslice_control_pose_trigger(session_id, args.pose_name))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
