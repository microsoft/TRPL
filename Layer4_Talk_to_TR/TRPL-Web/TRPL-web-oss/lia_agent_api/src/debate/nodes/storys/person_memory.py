# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Person memory — maps camera person_id to conversation data across visits."""
import logging

from debate.models.constants import TR_SPEAKER
from debate.models.state import DebateState

logger = logging.getLogger(f"lia.{__name__}")


def get_store(state: DebateState) -> dict:
    """Get or create the person_memory dict in phase_memory."""
    if state.phase_memory is None:
        state.phase_memory = {}
    return state.phase_memory.setdefault("person_memory", {})


def get_active_person_id(state: DebateState) -> str | None:
    """Get the person_id of whoever is at the mic right now."""
    cs = state.camera_state
    if not cs or cs.active_mic_person is None:
        return None
    return str(cs.active_mic_person)


def lookup(state: DebateState) -> dict | None:
    """Look up the current mic person in person_memory.
    Returns their saved data if they've been here before, None if new."""
    pid = get_active_person_id(state)
    if pid is None:
        return None
    return get_store(state).get(pid)


def save(state: DebateState, memory: dict, round_count: int) -> None:
    """Save the current visitor's conversation data to person_memory."""
    pid = get_active_person_id(state)
    if pid is None:
        return
    store = get_store(state)
    visits = store.get(pid, {}).get("visits", 0)
    store[pid] = {
        "name": memory.get("visitor_name"),
        "stories_told": memory.get("stories_told", []),
        "categories_covered": memory.get("categories_covered", []),
        "hooks_found": memory.get("hooks_found", []),
        "last_topic": memory.get("last_topic"),
        "visits": visits + 1,
        "total_rounds": round_count,
    }
    logger.info("[Storys] Saved person_memory for pid=%s (visit #%d)", pid, visits + 1)


def pre_seed_memory(state: DebateState, prev: dict) -> None:
    """Pre-seed conversation memory from a returning visitor's data."""
    if state.phase_memory is None:
        state.phase_memory = {}
    mem = state.phase_memory.setdefault("storys", {})
    name = prev.get("name")
    if name:
        mem["visitor_name"] = name
    mem["hooks_found"] = prev.get("hooks_found", [])
    mem["stories_told"] = prev.get("stories_told", [])
    mem["categories_covered"] = prev.get("categories_covered", [])
