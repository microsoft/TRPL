# -*- coding: utf-8 -*-
"""
Token + static server. Generates LiveKit JWTs and serves the SPA.

The LiveKit Cloud worker (livekit_agent.py) is auto-dispatched when the
browser joins a room, so this server does NOT need to talk to the agent.
"""
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

import dotenv
import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

dotenv.load_dotenv()

app = FastAPI(title="TR Avatar Frontend (lia_agent_api bridge)")

RUNTIME_MODE = os.getenv("TALK_TO_TR_RUNTIME_MODE", "cloud").strip().lower()
if RUNTIME_MODE not in {"cloud", "deterministic"}:
    raise RuntimeError(
        "TALK_TO_TR_RUNTIME_MODE must be 'cloud' or 'deterministic'"
    )

_avatar_enabled = os.getenv("AVATAR_ENABLED", "true").strip().lower()
if _avatar_enabled not in {"true", "false"}:
    raise RuntimeError("AVATAR_ENABLED must be 'true' or 'false'")
AVATAR_ENABLED = _avatar_enabled == "true"

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://localhost:7880")
LIVEKIT_INTERNAL_URL = os.getenv("LIVEKIT_INTERNAL_URL", LIVEKIT_URL)
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
LIVEKIT_CONFIGURED = bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET)
LIVEKIT_AVAILABLE = RUNTIME_MODE == "cloud" and LIVEKIT_CONFIGURED
if RUNTIME_MODE == "cloud" and not LIVEKIT_CONFIGURED:
    raise RuntimeError(
        "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required in cloud mode"
    )

# Admin proxy → brain /api/prompts. All three env vars must be set in
# Azure App Settings before /admin is usable.
BRAIN_URL = os.getenv("BRAIN_URL", "").rstrip("/")          # e.g. http://BRAIN_VM_IP:8010
BRAIN_API_KEY = os.getenv("BRAIN_API_KEY", "")              # brain's X-Api-Key
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")                  # shared secret for /admin access
# Read-only access code for the public jailbreak gallery (/jailbreak-cases).
# Falls back to ADMIN_TOKEN when unset so the gate works without extra config.
JAILBREAK_VIEW_CODE = os.getenv("JAILBREAK_VIEW_CODE", "") or ADMIN_TOKEN


# ── Per-IP rate limiting (dependency-free, in-process) ──────────────────
# This is the public edge: /api/token mints joinable LiveKit tokens (each can
# spin up an LLM+TTS+avatar pipeline) and the jailbreak proxy writes to disk.
# Cap both per client IP to blunt cost-drain / spam abuse. Limits are per
# process; front with a real limiter if you scale horizontally.
class _SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_token_limiter = _SlidingWindowLimiter(
    max_requests=int(os.getenv("TOKEN_RATE_MAX", "30")),
    window_seconds=float(os.getenv("TOKEN_RATE_WINDOW", "60")),
)
_report_limiter = _SlidingWindowLimiter(
    max_requests=int(os.getenv("REPORT_RATE_MAX", "10")),
    window_seconds=float(os.getenv("REPORT_RATE_WINDOW", "60")),
)


def _rate_limit(limiter: _SlidingWindowLimiter, request: Request) -> None:
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests — slow down.")


def _require_admin(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured on server")
    # Constant-time compare — this gate protects live prompt edits.
    if not secrets.compare_digest(x_admin_token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Bad admin token")


def _require_view_code(x_access_code: Optional[str]) -> None:
    if not JAILBREAK_VIEW_CODE:
        raise HTTPException(status_code=503, detail="Gallery access code not configured on server")
    if not secrets.compare_digest(x_access_code or "", JAILBREAK_VIEW_CODE):
        raise HTTPException(status_code=401, detail="Bad access code")


async def _proxy_brain(method: str, path: str, json_body: Optional[dict] = None) -> Response:
    if RUNTIME_MODE == "deterministic":
        raise HTTPException(
            status_code=503,
            detail="Provider-backed brain routes are unavailable in deterministic mode",
        )
    if not BRAIN_URL or not BRAIN_API_KEY:
        raise HTTPException(status_code=503, detail="BRAIN_URL / BRAIN_API_KEY not configured")
    url = f"{BRAIN_URL}{path}"
    headers = {"X-Api-Key": BRAIN_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.request(method, url, headers=headers, json=json_body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Brain unreachable at {BRAIN_URL}: {e}")
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@app.get("/api/token")
async def get_token(request: Request, identity: str = "visitor", mode: str | None = None):
    _rate_limit(_token_limiter, request)
    if not LIVEKIT_AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={
                "error": "LiveKit is unavailable in deterministic mode",
                "runtime_mode": RUNTIME_MODE,
            },
        )

    from livekit.api import AccessToken, VideoGrants

    # The room-name prefix is the signal the LiveKit worker uses to swap in
    # an alternate behaviour. See livekit_agent.entrypoint.
    #   mode=kiosk     → "kiosk-..."     → camera→welcome→storys phase chain
    #   mode=jailbreak → "jailbreak-..." → same phase as web flow, but
    #                                       LemonSlice avatar skipped (no
    #                                       Interactive Call minutes spent)
    if mode == "kiosk":
        room_name = f"kiosk-{uuid.uuid4().hex[:8]}"
    elif mode == "jailbreak":
        room_name = f"jailbreak-{uuid.uuid4().hex[:8]}"
    elif mode == "vip":
        # "vip-…" → worker boots the honored-guest storymode (VipEngine) and,
        # like jailbreak, skips the LemonSlice avatar (audio only).
        room_name = f"vip-{uuid.uuid4().hex[:8]}"
    else:
        room_name = f"tr-avatar-{uuid.uuid4().hex[:8]}"
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_grants(VideoGrants(room_join=True, room=room_name))
    )
    return {
        "token": token.to_jwt(),
        "url": LIVEKIT_URL,
        "room": room_name,
        "avatar_enabled": AVATAR_ENABLED and mode not in {"jailbreak", "vip"},
    }


# ─────────────────────────────────────────────────────────────────────
# Live "people online now" count = number of active LiveKit rooms with
# at least one participant (each visitor session gets its own room).
# Cached briefly so many polling clients don't hammer the LiveKit API.
# ─────────────────────────────────────────────────────────────────────
_online_cache = {"count": 0, "at": 0.0}
_ONLINE_TTL_SEC = 4.0


@app.get("/api/online")
async def online_count():
    now = time.monotonic()
    if now - _online_cache["at"] < _ONLINE_TTL_SEC:
        return {"online": _online_cache["count"]}

    count = _online_cache["count"]  # fall back to last good value on error
    if LIVEKIT_AVAILABLE:
        from livekit import api as lk_api

        http_url = (
            LIVEKIT_INTERNAL_URL.replace("wss://", "https://").replace("ws://", "http://")
        )
        lk = lk_api.LiveKitAPI(
            url=http_url,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )
        try:
            rooms = (await lk.room.list_rooms(lk_api.ListRoomsRequest())).rooms
            count = sum(1 for r in rooms if r.num_participants > 0)
        except Exception:
            pass  # keep last known count on transient LiveKit errors
        finally:
            await lk.aclose()

    _online_cache["count"] = count
    _online_cache["at"] = now
    return {"online": count}


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "runtime_mode": RUNTIME_MODE,
        "providers": {"livekit": LIVEKIT_AVAILABLE},
    }


@app.get("/")
async def serve_index():
    return FileResponse("index.html")


@app.get("/camera-avatar")
async def serve_camera_avatar():
    """Debug page for the local-kiosk camera_platform integration. Joins a
    kiosk-prefixed LiveKit room so the worker switches to camera→welcome→…
    phases. Does NOT replace index.html; existing web flow is untouched.
    """
    return FileResponse("camera_avatar.html")


@app.get("/jailbreak")
async def serve_jailbreak():
    """Safety / jailbreak test page. Joins a jailbreak-prefixed LiveKit
    room so the worker skips LemonSlice but keeps STT/TTS/LLM intact —
    testers get the full conversational pipeline without burning avatar
    Interactive Call minutes.
    """
    return FileResponse("jailbreak.html")


@app.get("/vip")
async def serve_vip():
    """Honored-guest (VIP) page. Joins a vip-prefixed LiveKit room so the
    worker boots the VIP storymode (VipEngine + the warmer storys.vip agent)
    and skips LemonSlice — audio only, no avatar, no Interactive Call minutes.
    """
    return FileResponse("vip.html")


# ─────────────────────────────────────────────────────────────────────
# Prompts admin (PM-editable). UI at /admin, proxied API at /admin/api/*.
# Reaches brain via BRAIN_URL; requires X-Admin-Token header on every call.
# ─────────────────────────────────────────────────────────────────────

@app.get("/admin")
async def admin_page():
    return FileResponse("admin.html")


@app.get("/admin/api/prompts")
async def admin_list_prompts(x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("GET", "/api/prompts")


@app.get("/admin/api/prompts/{key}")
async def admin_get_prompt(key: str, x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("GET", f"/api/prompts/{key}")


@app.put("/admin/api/prompts/{key}")
async def admin_put_prompt(
    key: str,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin(x_admin_token)
    body = await request.json()
    return await _proxy_brain("PUT", f"/api/prompts/{key}", json_body=body)


@app.delete("/admin/api/prompts/{key}")
async def admin_delete_prompt(key: str, x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("DELETE", f"/api/prompts/{key}")


# ─────────────────────────────────────────────────────────────────────
# Jailbreak reports — teens on /jailbreak click "I got him" → POST here.
# Public POST (no auth, page itself is gated by access code).
# Admin GET/DELETE for review at /admin/reports.
# ─────────────────────────────────────────────────────────────────────

@app.post("/api/jailbreak-reports", status_code=201)
async def public_submit_report(request: Request):
    """Open submission endpoint. Anyone on /jailbreak can post here (rate-limited)."""
    _rate_limit(_report_limiter, request)
    body = await request.json()
    return await _proxy_brain("POST", "/api/jailbreak-reports", json_body=body)


@app.get("/admin/reports")
async def admin_reports_page():
    return FileResponse("admin_reports.html")


@app.get("/admin/api/jailbreak-reports")
async def admin_list_reports(x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("GET", "/api/jailbreak-reports")


@app.get("/admin/api/jailbreak-reports/{report_id}")
async def admin_get_report(report_id: str, x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("GET", f"/api/jailbreak-reports/{report_id}")


@app.delete("/admin/api/jailbreak-reports/{report_id}")
async def admin_delete_report(report_id: str, x_admin_token: Optional[str] = Header(None)):
    _require_admin(x_admin_token)
    return await _proxy_brain("DELETE", f"/api/jailbreak-reports/{report_id}")


# ─────────────────────────────────────────────────────────────────────
# Jailbreak gallery — read-only, access-code-gated card view of the
# successful jailbreak cases. Page is public; the data endpoints below
# require the X-Access-Code header (checked against JAILBREAK_VIEW_CODE).
# ─────────────────────────────────────────────────────────────────────

@app.get("/jailbreak-cases")
async def serve_jailbreak_gallery():
    return FileResponse("jailbreak_gallery.html")


@app.get("/api/jailbreak-cases")
async def list_jailbreak_cases(x_access_code: Optional[str] = Header(None)):
    _require_view_code(x_access_code)
    return await _proxy_brain("GET", "/api/jailbreak-reports")


@app.get("/api/jailbreak-cases/{case_id}")
async def get_jailbreak_case(case_id: str, x_access_code: Optional[str] = Header(None)):
    _require_view_code(x_access_code)
    return await _proxy_brain("GET", f"/api/jailbreak-reports/{case_id}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("TOKEN_SERVER_PORT", "8001"))
    print(f"🎟  Token server on http://localhost:{port}")
    print(f"🔗  LiveKit URL: {LIVEKIT_URL}")
    uvicorn.run(app, host="0.0.0.0", port=port)
