"""
Boot self-test — fail loud on startup if critical dependencies are broken.

Philosophy: in production "nobody watches" mode, silent runtime degradation
is the enemy. Every dependency we rely on for the happy path is probed here
before the main loop starts. Any failure raises SelfTestError, caller is
expected to log + exit(1). The watchdog will either retry (transient issue)
or trip its circuit breaker (hard failure — a human will finally notice
because the avatar isn't starting).

Each check is idempotent, short (< 5 sec), and tolerant of the fact that
some dependencies may be disabled by config. Disabled ≠ broken.
"""
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Callable, List, Tuple

import requests

logger = logging.getLogger(__name__)


class SelfTestError(Exception):
    """Raised when a boot-time self-test check fails fatally."""


# A check is (name, callable). Callable raises on failure, returns None on OK.
CheckResult = Tuple[str, bool, str]  # (name, passed, detail)


def _check_yolo_model() -> None:
    """Confirm the configured YOLO model file loads. This also primes the
    runtime so the first real inference is not a 3-5s stall."""
    from ..utils.config import YOLO_CONFIG
    model_path = YOLO_CONFIG["model_path"]
    if not os.path.exists(model_path):
        # ultralytics auto-downloads if path is a bare name like "yolov8n-pose.pt"
        # — that's OK, we just warn. Real failure is when neither path nor
        # registry download works.
        logger.info(f"YOLO model path not on disk (will try registry): {model_path}")
    try:
        from ultralytics import YOLO
        _ = YOLO(model_path)
    except Exception as e:
        raise SelfTestError(f"YOLO model load failed: {e}") from e


def _check_agent_server_reachable() -> None:
    """POST a synthetic event to the agent server. 2xx = pass.

    This catches the full class of URL-mismatch / service-down / schema-drift
    failures. In a "nobody watches" world this is the single most important
    check — if events can't land, the whole downstream avatar pipeline is
    silently dead.
    """
    from ..utils.config import AGENT_SERVER_CONFIG
    if not AGENT_SERVER_CONFIG.get("enabled", True):
        logger.info("Agent server forwarding disabled — skipping reachability check")
        return

    # In WebSocket mode the camera HOSTS the event server and downstream
    # connects in — there is no upstream HTTP endpoint to POST to, so the HTTP
    # reachability probe doesn't apply. The WS server's own bind is validated at
    # startup (EventPublisher._start_ws_server).
    transport = AGENT_SERVER_CONFIG.get("transport", "ws").lower()
    if transport == "ws":
        logger.info(
            "Event transport is WebSocket (camera hosts ws server) — "
            "skipping HTTP reachability check"
        )
        return

    url = AGENT_SERVER_CONFIG["url"].rstrip("/") + "/api/camera/events"
    envelope = {
        "event_id": "boot_selftest",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "camera_service",
        "event_type": "BOOT_SELFTEST",
        "payload": {"probe": True},
    }
    try:
        headers = {}
        api_key = AGENT_SERVER_CONFIG.get("api_key", "")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = requests.post(url, json=envelope, headers=headers, timeout=3.0)
    except requests.exceptions.ConnectionError as e:
        raise SelfTestError(f"agent_server unreachable at {url}: {e}") from e
    except requests.exceptions.Timeout as e:
        raise SelfTestError(f"agent_server timeout at {url}: {e}") from e

    if resp.status_code not in (200, 201, 202):
        raise SelfTestError(
            f"agent_server at {url} returned HTTP {resp.status_code}: {resp.text[:120]}"
        )


def _check_vlm_api() -> None:
    """Minimal VLM probe: 1×1 JPEG, trivial prompt, expect a response within 10s.

    Uses OpenAI chat.completions with vision content to validate both the API
    key and the endpoint. If VLM is disabled by config, skip the check — we
    degrade gracefully without VLM, it's not a hard blocker.
    """
    from ..utils.config import VLM_CONFIG
    if not VLM_CONFIG.get("enabled", False):
        logger.info("VLM disabled by config — skipping probe")
        return

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("VLM enabled but no API key set — skipping probe (will degrade at runtime)")
        return

    try:
        from ..utils.llm_client import build_chat_client
        client, probe_model = build_chat_client(VLM_CONFIG.get("model", "gpt-4o-mini"))
    except Exception as e:
        raise SelfTestError(f"VLM client init failed: {e}") from e
    if client is None:
        logger.warning("VLM enabled but no API key set — skipping probe (will degrade at runtime)")
        return

    # Small valid PNG accepted by Azure's vision parser.
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    try:
        resp = client.chat.completions.create(
            model=probe_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "reply with just the word ok"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{tiny_png_b64}",
                                   "detail": "low"}},
                ],
            }],
            max_tokens=5,
            timeout=10.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise SelfTestError("VLM probe returned empty content")
    except SelfTestError:
        raise
    except Exception as e:
        raise SelfTestError(f"VLM probe call failed: {e}") from e


def _check_disk_space() -> None:
    """Require at least 1 GB free on the filesystem hosting this project.

    VLM debug images + JSONL event log + room_monitor.log all grow over time.
    Below 1 GB we are in danger of the disk filling mid-run.
    """
    project_root = Path(__file__).resolve().parents[2]
    try:
        free = shutil.disk_usage(project_root).free
    except Exception as e:
        raise SelfTestError(f"cannot stat disk usage at {project_root}: {e}") from e
    min_free = int(os.environ.get("MIN_FREE_BYTES", str(1 * 1024 * 1024 * 1024)))
    if free < min_free:
        raise SelfTestError(
            f"only {free // (1024*1024)} MB free at {project_root} "
            f"(minimum {min_free // (1024*1024)} MB)"
        )


def _check_zones_configured() -> None:
    """mic_zone and entry_zone must have ≥ 3 vertices or the whole scene
    orchestrator is dead on arrival."""
    from ..utils.config import ZONE_CONFIG
    for required in ("entry_zone", "mic_zone"):
        poly = ZONE_CONFIG.get(required)
        if not poly or len(poly) < 3:
            raise SelfTestError(
                f"ZONE_CONFIG[{required!r}] has < 3 vertices — orchestrator cannot function"
            )


def _check_cameras_configured() -> None:
    """At least one camera must be registered."""
    from ..utils.config import CAMERAS
    if not CAMERAS:
        raise SelfTestError("CAMERAS dict is empty — nothing to monitor")


def _check_realsense_device() -> None:
    """If any camera is configured with 'realsense://', confirm SDK + hardware.

    Skipped entirely when no RealSense sources are configured — we don't want
    to force the pyrealsense2 dependency on RGB-only deployments.
    """
    from ..utils.config import CAMERAS
    rs_cameras = [
        (cid, c) for cid, c in CAMERAS.items()
        if isinstance(c.get("source"), str) and c["source"].startswith("realsense://")
    ]
    if not rs_cameras:
        logger.info("No RealSense cameras configured — skipping probe")
        return

    try:
        import pyrealsense2 as rs
    except ImportError as e:
        raise SelfTestError(
            "pyrealsense2 not installed but a 'realsense://' camera is configured. "
            "Install with: pip install pyrealsense2"
        ) from e

    try:
        ctx = rs.context()
        devices = list(ctx.query_devices())
    except Exception as e:
        raise SelfTestError(f"RealSense context query failed: {e}") from e

    if len(devices) == 0:
        raise SelfTestError(
            f"No RealSense devices found on USB (configured: {[c[0] for c in rs_cameras]}). "
            "Check USB 3.0 cable + power."
        )

    # If a specific serial was requested, verify it's present.
    present_serials = set()
    for d in devices:
        try:
            present_serials.add(d.get_info(rs.camera_info.serial_number))
        except Exception:
            pass
    for cid, cam_cfg in rs_cameras:
        suffix = cam_cfg["source"][len("realsense://"):].strip()
        if suffix and suffix not in present_serials:
            raise SelfTestError(
                f"Camera {cid} requests RealSense serial {suffix!r} but only "
                f"{sorted(present_serials)} are connected"
            )
    logger.info(
        f"RealSense probe: {len(devices)} device(s) detected "
        f"(serials={sorted(present_serials)})"
    )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

_CHECKS: List[Tuple[str, Callable[[], None]]] = [
    ("cameras_configured", _check_cameras_configured),
    ("realsense_device", _check_realsense_device),
    ("zones_configured", _check_zones_configured),
    ("disk_space", _check_disk_space),
    ("yolo_model", _check_yolo_model),
    ("agent_server_reachable", _check_agent_server_reachable),
    ("vlm_api", _check_vlm_api),
]


def run_boot_selftest(strict: bool = True) -> List[CheckResult]:
    """Run all boot checks in order.

    Args:
        strict: If True, raise SelfTestError on the first hard failure.
                If False, collect all results and return. Caller decides.

    Returns:
        List of (name, passed, detail) tuples — useful for logging.
    """
    results: List[CheckResult] = []
    first_failure: SelfTestError = None

    for name, check in _CHECKS:
        t0 = time.time()
        try:
            check()
            elapsed = time.time() - t0
            logger.info(f"SELFTEST OK  [{name}] ({elapsed:.2f}s)")
            results.append((name, True, f"ok in {elapsed:.2f}s"))
        except SelfTestError as e:
            elapsed = time.time() - t0
            logger.critical(f"SELFTEST FAIL [{name}] ({elapsed:.2f}s): {e}")
            results.append((name, False, str(e)))
            if first_failure is None:
                first_failure = e
            if strict:
                break
        except Exception as e:
            elapsed = time.time() - t0
            logger.critical(f"SELFTEST ERROR [{name}] ({elapsed:.2f}s): {e}")
            results.append((name, False, f"unexpected: {e}"))
            if first_failure is None:
                first_failure = SelfTestError(f"{name}: unexpected {type(e).__name__}: {e}")
            if strict:
                break

    if strict and first_failure is not None:
        raise first_failure

    return results
