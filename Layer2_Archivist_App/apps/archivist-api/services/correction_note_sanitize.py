# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Sanitize correction request note text for intake.

Policy (security checklist):
- Max length from settings (env CORRECTION_NOTE_MAX_LENGTH, clamped 2000–5000).
- Strip NUL; strip non-printable control characters (allow tab/newline/CR only).
- Store plain text as-is (no HTML encoding at persistence; render-time safety in UI).
"""
from __future__ import annotations

import unicodedata
from typing import Optional, Tuple

from core.config import settings


def _strip_disallowed_controls(s: str) -> str:
    """Remove C0/C1 control chars except tab, LF, CR. Also drops NUL."""
    out: list[str] = []
    for ch in s:
        if ch == "\x00":
            continue
        if ch in "\t\n\r":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C"):
            continue
        out.append(ch)
    return "".join(out)


def validate_and_sanitize_note(raw: str) -> Tuple[str, Optional[str]]:
    """
    Returns (sanitized_note, error_code).
    error_code is one of: note_required, note_empty_after_sanitize, note_too_long, note_invalid.
    """
    if raw is None:
        return "", "note_required"
    if not isinstance(raw, str):
        return "", "note_invalid"
    max_len = settings.correction_note_max_length
    if len(raw) > max_len:
        return "", "note_too_long"
    cleaned = _strip_disallowed_controls(raw)
    if not cleaned.strip():
        return "", "note_empty_after_sanitize"
    if len(cleaned) > max_len:
        return "", "note_too_long"
    return cleaned, None


def sanitize_optional_source_label(raw: Optional[str]) -> Tuple[str, Optional[str]]:
    """Short optional label from client; strip controls and cap length."""
    if raw is None or raw == "":
        return "", None
    if not isinstance(raw, str):
        return "", "source_invalid"
    cleaned = _strip_disallowed_controls(raw).strip()
    if not cleaned:
        return "", None
    max_len = 120
    if len(cleaned) > max_len:
        return "", "source_too_long"
    return cleaned, None
