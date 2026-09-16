# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Short canned "they left mid-conversation" lines.

Used when a visitor walks out of the mic zone without saying goodbye, not
hitting the hard time limit, and not tripping the threat guard. Instead of
exiting silently we have TR murmur a quick one-liner so the transition
feels intentional rather than broken.

Kept as plain strings (no LLM call) — we want zero latency here so the
line plays in the ~1 second gap before the avatar goes back to idle.
"""
import random

# Keep each line short (<= ~15 words) and self-directed, as if TR is
# reflecting out loud rather than speaking to someone. The visitor is
# already walking away so the tone is "oh, they're gone", not a farewell.
LEFT_ACK_LINES: list[str] = [
    "Oh — they're off. Well, a story half-told keeps for another day.",
    "Hm. Wandered off. I hope some of it stuck.",
    "They've gone on their way. Good — don't let me hold you up.",
    "Off to the next thing. Bully — onward, then.",
    "There they go. I'll hold the rest of the tale for the next soul.",
    "Slipped away mid-thought. Funny how a story can outlast its listener.",
    "And just like that, alone in the room again. Fine — I've got plenty to think on.",
]


def pick_left_ack() -> str:
    return random.choice(LEFT_ACK_LINES)
