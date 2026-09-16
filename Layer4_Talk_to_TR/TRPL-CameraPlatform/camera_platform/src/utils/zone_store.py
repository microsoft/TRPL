# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Zone persistence — read/write labeled zones to config/zones.json.

The web zone labeler (POST /api/zones) writes here, and config.py loads this
file at import time to override the ZONE_CONFIG defaults. This lets zones
labeled in the browser survive restarts without editing config.py by hand.

Polygons are stored in normalized coordinates (0.0–1.0) as lists of [x, y].
This module deliberately does NOT import config.py (config imports it), so it
stays a leaf with no circular dependency.
"""
import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Keys that are single polygons (vs. bench_zones which is a list of polygons).
_SINGLE_ZONE_KEYS = ("entry_zone", "mic_zone")


def zones_json_path() -> str:
    """Absolute path to config/zones.json (project_root/config/zones.json)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "config", "zones.json")


def _clean_polygon(poly) -> Optional[List[List[float]]]:
    """Validate a polygon: >=3 vertices, each a numeric (x, y) in [0, 1]."""
    if not poly or len(poly) < 3:
        return None
    out = []
    for pt in poly:
        if len(pt) < 2:
            return None
        x, y = float(pt[0]), float(pt[1])
        # Clamp to the valid normalized range rather than reject — a click a
        # pixel off the edge shouldn't lose the whole polygon.
        x = min(1.0, max(0.0, x))
        y = min(1.0, max(0.0, y))
        out.append([x, y])
    return out


def _clean_point(pt) -> Optional[List[float]]:
    """Validate a single (x, y) point in [0, 1] (clamped)."""
    if not pt or len(pt) < 2:
        return None
    try:
        x, y = float(pt[0]), float(pt[1])
    except (TypeError, ValueError):
        return None
    x = min(1.0, max(0.0, x))
    y = min(1.0, max(0.0, y))
    return [x, y]


def _clean_direction(direction) -> Optional[Dict[str, List[float]]]:
    """Validate an entry-direction arrow: {"from": [x,y], "to": [x,y]}.

    The vector from->to encodes the "into the room" direction used by the
    SceneOrchestrator to tell an arrival (inbound crossing) from a departure
    (outbound crossing) at the entry zone. Returns None if either endpoint is
    malformed or the two points coincide (zero-length arrow → no direction).
    """
    if not isinstance(direction, dict):
        return None
    p_from = _clean_point(direction.get("from"))
    p_to = _clean_point(direction.get("to"))
    if p_from is None or p_to is None:
        return None
    # Reject a degenerate (zero-length) arrow — it carries no direction.
    if abs(p_to[0] - p_from[0]) < 1e-6 and abs(p_to[1] - p_from[1]) < 1e-6:
        return None
    return {"from": p_from, "to": p_to}


def normalize_zones(data: Dict) -> Dict[str, object]:
    """Coerce an incoming zones dict into the canonical persisted shape.

    Drops any zone that fails validation. Returns a dict containing only the
    zones (and the optional entry_direction arrow) that are well-formed.
    """
    out: Dict[str, object] = {}
    for key in _SINGLE_ZONE_KEYS:
        cleaned = _clean_polygon(data.get(key))
        if cleaned:
            out[key] = cleaned
    bench = data.get("bench_zones")
    if bench:
        cleaned_bench = [c for c in (_clean_polygon(p) for p in bench) if c]
        if cleaned_bench:
            out["bench_zones"] = cleaned_bench
    direction = _clean_direction(data.get("entry_direction"))
    if direction:
        out["entry_direction"] = direction
    return out


def load_persisted_zones() -> Optional[Dict[str, List]]:
    """Load + validate zones.json, or None if absent/unreadable/empty."""
    path = zones_json_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read persisted zones at {path}: {e}")
        return None
    cleaned = normalize_zones(data)
    return cleaned or None


def save_persisted_zones(data: Dict) -> str:
    """Validate and atomically write zones to config/zones.json.

    Returns the path written. Raises ValueError if no valid zone is present.
    """
    cleaned = normalize_zones(data)
    if not cleaned:
        raise ValueError("No valid zone provided (each needs >= 3 vertices in 0..1)")
    path = zones_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)
    os.replace(tmp, path)
    logger.info(f"Persisted zones written to {path}: { {k: len(v) for k, v in cleaned.items()} }")
    return path
