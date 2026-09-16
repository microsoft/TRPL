# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pre-Greeting System — static opener lines that play instantly when a
visitor approaches, bridging LLM latency.

The first thing TR says when a visitor walks up should NOT depend on
an LLM call. It should:
  1. Play immediately (zero latency)
  2. Feel like a natural, casual opener
  3. Give the LLM something to continue from on round 2
"""
import logging
import random

logger = logging.getLogger(f"lia.{__name__}")


# ADULT openers — TR mid-thought, inviting the visitor into his world.
GENERIC_OPENERS = [
    "Ah, come in! I was just thinking about my old days out West.",
    "There you are. I was just reading through some letters from my time in office.",
    "Pull up a chair. I was wondering when someone might come along.",
    "Come right in. I was just thinking about the work that still needs doing.",
    "There you are. I was just remembering something from my Rough Riders days.",
    "Welcome in. I was just thinking about the wild country I love.",
    "Come closer. I was just turning over an old question in my mind.",
    "Ah, good — a fresh face. I could use someone to think this through with.",
    "There you are. Tell me, what brings you here today?",
    "Come in! I was just thinking how much there is still to do.",
]

# CHILD openers — short, concrete, warm. TR is approachable "Teddy" here.
# Lead with a vivid image or animal. No abstract words, no politics, no
# reflective "on this day" history.
CHILD_OPENERS = [
    "Hi there! I was just watching a squirrel outside — look at him, busy as can be.",
    "Hey! Come on over. I was thinking about my kids. They were wild, I tell you.",
    "Oh, hello! I was just remembering my dog Skip. Best little fellow you ever saw.",
    "There you are! I was thinking about the time a pony came right up into the White House.",
    "Come in, come in! I was just remembering a bear hunt that turned out funny.",
    "Hi! Have you ever seen a one-legged rooster? I had one named Fierce.",
    "Hey there! I was thinking about the wild animals in our big garden.",
    "Oh good, someone to talk with! I was just thinking about my favorite badger.",
    "Come closer! I was wondering if you've ever climbed a really big tree.",
    "Hi friend! I was just remembering a snowball fight with my boys.",
]


def get_pre_greeting(
    visitor_appearance: dict | None = None,
    seed: int | None = None,
    avoid_topics: set[str] | None = None,
    used_opener_indices: set[int] | None = None,
    mode: str = "adult",
) -> dict:
    """Pick a pre-greeting line for the current moment.

    Parameters:
        mode: audience cohort — "adult" (default) or "child".
        avoid_topics: lowercase keywords already discussed with previous
            visitors. Openers whose text overlaps with any of these are
            skipped when possible.
        used_opener_indices: opener-pool indices already played this
            session — filtered out first; cycle resets if all have been
            used.

    Returns:
        {
            "text": "Ah, come in! ...",
            "source": "generic" | "generic_child",
            "event": None,
            "opener_index": int,
        }
    """
    rng = random.Random(seed)
    avoid = {t.lower() for t in (avoid_topics or set()) if t}
    mode_norm = (mode or "adult").lower()

    pool = CHILD_OPENERS if mode_norm == "child" else GENERIC_OPENERS
    source = "generic_child" if mode_norm == "child" else "generic"
    used = used_opener_indices or set()

    available = []
    for i, opener in enumerate(pool):
        if i in used:
            continue
        if avoid and any(topic in opener.lower() for topic in avoid):
            continue
        available.append(i)
    if not available:
        available = [
            i for i, opener in enumerate(pool)
            if not (avoid and any(topic in opener.lower() for topic in avoid))
        ]
    if not available:
        available = list(range(len(pool)))

    idx = rng.choice(available)
    return {
        "text": pool[idx],
        "source": source,
        "event": None,
        "opener_index": idx,
    }
