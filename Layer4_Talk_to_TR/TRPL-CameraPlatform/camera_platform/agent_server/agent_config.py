# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Agent server configuration
──────────────────────────
Tune these values to match your physical room setup.
"""

# ── Microphone position ───────────────────────────────────────────────────────
# Normalized (x, y) in the camera frame  (0,0 = top-left, 1,1 = bottom-right)
# Default: front-centre of room, slightly below mid-frame
MIC_POSITION: dict[str, tuple[float, float]] = {
    "default": (0.5, 0.75),   # works for most frontal cameras
    # Add per-camera overrides:
    # "camera_0": (0.45, 0.80),
    # "camera_1": (0.55, 0.70),
}

# Normalised Euclidean distance: person is "far" when dist > this threshold
# 0.30 ≈ 30% of the frame diagonal
FAR_THRESHOLD: float = 0.30

# ── Group detection ───────────────────────────────────────────────────────────
# How long (seconds) to wait after the first PERSON_ENTERED before deciding
# "this is a group" vs "single person".  Multiple entries within this window
# from the same camera are treated as one group entering together.
GROUP_WINDOW_SEC: float = 2.5

# Max person crops sent to VLM in a group description (keep API payload small)
MAX_GROUP_CROPS: int = 4
