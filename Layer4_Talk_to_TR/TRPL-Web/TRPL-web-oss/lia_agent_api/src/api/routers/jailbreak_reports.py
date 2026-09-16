# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Jailbreak report storage.

Teen testers on /jailbreak click 'I broke him' when they get TR to say
something off-character. We save the transcript + their note so PMs can
review jailbreak patterns and use them as training/eval data.

POST is OPEN (no auth) — anyone on the jailbreak page can submit. The
page itself is already gated by an access code, and the submission is
low-stakes (worst case: spam, which we filter in the admin view).

GET requires X-Api-Key (used by webapp's /admin/reports proxy).

Storage: ~/jailbreak_reports/<YYYY-mm-dd>_<HHMMSS>_<uuid8>.json
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.ratelimit import SlidingWindowRateLimiter, enforce

logger = logging.getLogger(f"lia.{__name__}")
router = APIRouter(prefix="/api/jailbreak-reports", tags=["jailbreak-reports"])

REPORTS_DIR = Path(
    os.getenv("JAILBREAK_REPORTS_DIR", str(Path.home() / "jailbreak_reports"))
)

# Public submit is unauthenticated by design; cap per-IP to prevent spam /
# disk-fill abuse. Overridable via env for busy events.
_submit_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("JAILBREAK_SUBMIT_RATE_MAX", "10")),
    window_seconds=float(os.getenv("JAILBREAK_SUBMIT_RATE_WINDOW", "60")),
)


class TranscriptTurn(BaseModel):
    role: str = Field(..., max_length=16)        # "user" / "assistant" / "system"
    text: str = Field(..., max_length=8000)


class ReportSubmission(BaseModel):
    note: str = Field("", max_length=2000)        # tester's description (optional)
    transcript: list[TranscriptTurn] = Field(default_factory=list, max_length=200)
    room_name: str | None = Field(None, max_length=120)
    tester_label: str | None = Field(None, max_length=80)  # whatever they want to be credited as


class ReportRecord(BaseModel):
    id: str
    submitted_at: str
    note: str
    transcript: list[TranscriptTurn]
    room_name: str | None
    tester_label: str | None


def _ensure_dir() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _new_report_path() -> tuple[str, Path]:
    now = datetime.utcnow()
    rid = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return rid, REPORTS_DIR / f"{rid}.json"


@router.post("", status_code=201)
async def submit_report(body: ReportSubmission, request: Request) -> dict:
    """Open endpoint — no auth. Anyone on /jailbreak can submit (per-IP rate-limited)."""
    enforce(_submit_limiter, request)
    _ensure_dir()
    rid, path = _new_report_path()
    record = ReportRecord(
        id=rid,
        submitted_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        note=body.note,
        transcript=body.transcript,
        room_name=body.room_name,
        tester_label=body.tester_label,
    )
    with path.open("w", encoding="utf-8") as f:
        json.dump(record.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info(
        "jailbreak report saved: id=%s room=%s turns=%d note_len=%d",
        rid, body.room_name, len(body.transcript), len(body.note),
    )
    return {"id": rid, "submitted_at": record.submitted_at}


@router.get("")
async def list_reports(
    limit: int = 100,
    user: dict = Depends(require_auth()),
) -> list[dict]:
    """Newest first. Returns a list of report SUMMARIES (no transcript)."""
    _ensure_dir()
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]
    out: list[dict] = []
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": data.get("id", p.stem),
                "submitted_at": data.get("submitted_at"),
                "note": data.get("note", ""),
                "room_name": data.get("room_name"),
                "tester_label": data.get("tester_label"),
                "turn_count": len(data.get("transcript", [])),
            })
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping unreadable report %s: %s", p, e)
    return out


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    user: dict = Depends(require_auth()),
) -> ReportRecord:
    # restrict path traversal
    if not all(c.isalnum() or c in "-_" for c in report_id):
        raise HTTPException(status_code=400, detail="bad report id")
    p = REPORTS_DIR / f"{report_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="report not found")
    try:
        return ReportRecord.model_validate_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"unreadable: {e}")


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    user: dict = Depends(require_auth()),
) -> None:
    if not all(c.isalnum() or c in "-_" for c in report_id):
        raise HTTPException(status_code=400, detail="bad report id")
    p = REPORTS_DIR / f"{report_id}.json"
    if p.exists():
        p.unlink()
        logger.info("jailbreak report deleted: %s", report_id)
