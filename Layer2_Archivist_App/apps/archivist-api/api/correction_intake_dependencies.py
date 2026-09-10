"""HTTP guards for correction request intake (POST only)."""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from core.config import settings


def assert_correction_intake_http(request: Request) -> None:
    """Require application/json and enforce a maximum Content-Length before body read."""
    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ct != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "message": "Content-Type must be application/json",
                "code": "unsupported_media_type",
            },
        )
    cl = request.headers.get("content-length")
    if cl:
        try:
            n = int(cl)
        except ValueError:
            return
        if n > settings.correction_intake_max_body_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "message": "Request body too large",
                    "code": "payload_too_large",
                },
            )


async def enforce_correction_intake_http(request: Request) -> None:
    assert_correction_intake_http(request)
