# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pre-greeting selection for StorysNode round 1."""
import logging

from debate.models.state import DebateState
from debate.nodes.storys import person_memory

logger = logging.getLogger(f"lia.{__name__}")


def build_returning_greeting(prev: dict) -> str:
    """Build a personalized greeting for a returning visitor."""
    name = prev.get("name")
    last_topic = prev.get("last_topic")
    if name and last_topic:
        return f"{name}! You're back! I was just thinking about our talk on {last_topic}."
    elif name:
        return f"{name}! Good to see you again. I was hoping you'd come back."
    else:
        return "There you are again! I remember you. Come on up."


def select_pre_greeting(
    state: DebateState,
    get_memory_fn,
    current_mode_fn,
) -> tuple[str, bool]:
    """Select the pre-greeting text for the current visitor.

    Returns (text, is_returning). If is_returning, the caller should
    also call person_memory.pre_seed_memory().
    """
    from debate.scenarios.pre_greeting import get_pre_greeting
    from debate.nodes.camera_utils import get_active_visitor_appearance

    prev = person_memory.lookup(state)
    if prev and prev.get("visits", 0) > 0:
        text = build_returning_greeting(prev)
        person_memory.pre_seed_memory(state, prev)
        logger.info(
            "[Storys] Returning visitor (pid=%s, name=%s, visit #%d): %s",
            person_memory.get_active_person_id(state),
            prev.get("name"), prev.get("visits", 0) + 1, text,
        )
        return text, True

    # New visitor — normal pre-greeting
    visitor_appearance = get_active_visitor_appearance(state)
    avoid_topics: set[str] = set()
    memory = get_memory_fn(state)
    for title in memory.get("stories_told", []) or []:
        avoid_topics.update(w for w in title.lower().split() if len(w) > 4)

    used_opener_indices: set[int] = set()
    if state.phase_memory:
        for entry in state.phase_memory.get("visitor_stack", []) or []:
            for topic in entry.get("topics", []) or []:
                avoid_topics.update(w for w in topic.lower().split() if len(w) > 4)
        used_opener_indices = set(
            state.phase_memory.get("pre_greet_used_openers", []) or []
        )

    mode = current_mode_fn(state)
    greeting = get_pre_greeting(
        visitor_appearance=visitor_appearance,
        avoid_topics=avoid_topics,
        used_opener_indices=used_opener_indices,
        mode=mode,
    )
    text = greeting["text"]
    logger.info(
        "[Storys] Pre-greeting (%s, avoided=%d topics, used=%d openers): %s",
        greeting["source"], len(avoid_topics), len(used_opener_indices), text,
    )

    # Track used opener index
    if greeting.get("source") in ("generic", "generic_child"):
        opener_idx = greeting.get("opener_index")
        if opener_idx is not None:
            if state.phase_memory is None:
                state.phase_memory = {}
            used = list(state.phase_memory.get("pre_greet_used_openers", []) or [])
            if opener_idx not in used:
                used.append(opener_idx)
            state.phase_memory["pre_greet_used_openers"] = used

    return text, False
