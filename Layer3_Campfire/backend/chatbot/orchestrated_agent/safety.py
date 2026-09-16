# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Keep this aligned with the user-facing refusal copy used by scoped refusals.
SAFE_REFUSAL_MESSAGE = "I can't help with that request. Please ask something related to Theodore Roosevelt or his era."

def _load_safety_error_patterns() -> tuple[str, ...]:
    inline_value = os.getenv("CAMPFIRE_SAFETY_ERROR_PATTERNS")
    file_name = os.getenv("CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE")
    if inline_value is not None and file_name:
        raise RuntimeError(
            "Set only one of CAMPFIRE_SAFETY_ERROR_PATTERNS or "
            "CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE."
        )
    if file_name:
        try:
            raw_value = Path(file_name).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                "Unable to read CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE."
            ) from exc
    elif inline_value is not None:
        raw_value = inline_value
    else:
        raise RuntimeError(
            "Set CAMPFIRE_SAFETY_ERROR_PATTERNS or "
            "CAMPFIRE_SAFETY_ERROR_PATTERNS_FILE."
        )

    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Campfire safety patterns must be valid JSON.") from exc
    if not isinstance(values, list) or not values:
        raise RuntimeError("Campfire safety patterns must be a non-empty JSON list.")
    patterns = tuple(str(value).lower() for value in values if str(value).strip())
    if not patterns:
        raise RuntimeError("Campfire safety patterns must contain non-empty strings.")
    return patterns


_SAFETY_ERROR_PATTERNS = _load_safety_error_patterns()


def _contains_safety_pattern(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, dict):
        return any(_contains_safety_pattern(v) for v in value.values())

    if isinstance(value, (list, tuple, set)):
        return any(_contains_safety_pattern(v) for v in value)

    text = str(value).lower()
    return any(pattern in text for pattern in _SAFETY_ERROR_PATTERNS)


def is_safety_filter_error(error: Any) -> bool:
    """Best-effort classifier for safety/content-filter failures.

    Handles strings, exception objects, and nested SDK payload dictionaries.
    """
    if error is None:
        return False

    candidates = [
        error,
        getattr(error, "code", None),
        getattr(error, "message", None),
        getattr(error, "body", None),
        getattr(error, "response", None),
        getattr(getattr(error, "error", None), "code", None),
        getattr(getattr(error, "error", None), "message", None),
    ]
    return any(_contains_safety_pattern(candidate) for candidate in candidates if candidate is not None)
