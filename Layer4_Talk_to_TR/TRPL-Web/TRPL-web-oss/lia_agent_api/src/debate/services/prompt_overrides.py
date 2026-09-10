"""JSON-backed prompt override store for PM-editable prompts.

Overrides are keyed as "<scenario>.<slot>" (e.g. "storys.adult").
When an override exists, agent_registry uses it instead of the compiled-in
default. Overrides persist to a JSON file so they survive server restarts,
but never get baked into Python source.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(f"lia.{__name__}")

_DEFAULT_PATH = Path(
    os.getenv("PROMPT_OVERRIDES_PATH", str(Path.home() / ".lia_prompt_overrides.json"))
)
_lock = threading.Lock()

# Registry of known slots → factory for the default text.
# Slots must be explicitly registered so the UI can enumerate them and
# so we never accept overrides for unknown agents.
_DEFAULT_LOADERS: dict[str, Callable[[], str]] = {}


def register_slot(key: str, default_loader: Callable[[], str]) -> None:
    _DEFAULT_LOADERS[key] = default_loader


def known_slots() -> list[str]:
    return sorted(_DEFAULT_LOADERS.keys())


def get_default(key: str) -> str:
    loader = _DEFAULT_LOADERS.get(key)
    if loader is None:
        raise KeyError(f"Unknown prompt slot: {key}")
    return loader()


def _load_all() -> dict[str, str]:
    if not _DEFAULT_PATH.exists():
        return {}
    try:
        with _DEFAULT_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Prompt overrides file is not a dict; ignoring: %s", _DEFAULT_PATH)
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read prompt overrides (%s): %s", _DEFAULT_PATH, e)
        return {}


def _save_all(data: dict[str, str]) -> None:
    _DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DEFAULT_PATH.with_suffix(_DEFAULT_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DEFAULT_PATH)


def get_override(key: str) -> str | None:
    with _lock:
        return _load_all().get(key)


def get_effective(key: str) -> tuple[str, bool]:
    """Return (text, is_override)."""
    override = get_override(key)
    if override is not None:
        return override, True
    return get_default(key), False


def set_override(key: str, text: str) -> None:
    if key not in _DEFAULT_LOADERS:
        raise KeyError(f"Unknown prompt slot: {key}")
    with _lock:
        data = _load_all()
        data[key] = text
        _save_all(data)
    logger.info("Prompt override saved: %s (%d chars)", key, len(text))


def delete_override(key: str) -> bool:
    with _lock:
        data = _load_all()
        if key not in data:
            return False
        del data[key]
        _save_all(data)
    logger.info("Prompt override deleted: %s", key)
    return True


def list_all() -> list[dict]:
    """Return one entry per registered slot with current/default text."""
    with _lock:
        overrides = _load_all()
    out = []
    for key in known_slots():
        override_text = overrides.get(key)
        default_text = get_default(key)
        out.append({
            "key": key,
            "is_override": override_text is not None,
            "text": override_text if override_text is not None else default_text,
            "default": default_text,
        })
    return out


def register_storys_slots() -> None:
    """Register the storys scenario slots. Called once at import time."""
    from debate.scenarios.storys import get_storys_prompt
    from debate.agents.storys.rag_agent import get_picker_default_prompt

    register_slot("storys.adult", lambda: get_storys_prompt("adult"))
    register_slot("storys.child", lambda: get_storys_prompt("child"))
    register_slot("storys.vip", lambda: get_storys_prompt("vip"))
    register_slot("storys.picker", get_picker_default_prompt)


register_storys_slots()
