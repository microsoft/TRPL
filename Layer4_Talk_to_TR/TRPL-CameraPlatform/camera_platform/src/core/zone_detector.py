# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Zone Detector — polygon-based hit-test for named zones.

Zones are defined in normalized coordinates (0.0–1.0) as convex or concave polygons.
The detector converts pixel positions to normalized coords and tests point-in-polygon.

Features:
  - Per-person hysteresis: once inside a zone, the point must move beyond a
    configurable margin before being considered "outside". This prevents
    rapid enter/leave flicker when a person stands near a zone boundary.
"""
import logging
from typing import Dict, List, Optional, Set, Tuple

from ..utils.config import ZONE_CONFIG

logger = logging.getLogger(__name__)

# Normalized hysteresis margin: person must move this far outside the zone
# boundary (as fraction of frame) before being considered "left".
_DEFAULT_HYSTERESIS_MARGIN = 0.02  # ~2% of frame dimension


def _point_in_polygon(px: float, py: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _expand_polygon(polygon: List[Tuple[float, float]],
                    margin: float) -> List[Tuple[float, float]]:
    """
    Expand a polygon outward by `margin` (normalized coords).

    Uses a simple centroid-based expansion: each vertex is pushed
    away from the centroid by `margin`. This is an approximation that
    works well for convex and near-convex polygons.
    """
    if len(polygon) < 3 or margin <= 0:
        return polygon
    cx = sum(x for x, y in polygon) / len(polygon)
    cy = sum(y for x, y in polygon) / len(polygon)
    expanded = []
    for x, y in polygon:
        dx, dy = x - cx, y - cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 1e-8:
            expanded.append((x, y))
        else:
            scale = (dist + margin) / dist
            expanded.append((cx + dx * scale, cy + dy * scale))
    return expanded


class ZoneDetector:
    """
    Tests whether pixel-space person positions fall inside named zones.

    Zones are stored as normalized polygons (0–1). Frame dimensions are needed
    to convert pixel coords to normalized coords at query time.

    Hysteresis: maintains per-person-per-zone "inside" state. Once a person is
    inside a zone, they must cross an expanded boundary to be considered outside.
    """

    def __init__(self, hysteresis_margin: float = _DEFAULT_HYSTERESIS_MARGIN,
                 zone_margins: Optional[Dict[str, float]] = None):
        self._zones: Dict[str, List[Tuple[float, float]]] = {}
        self._zones_expanded: Dict[str, List[Tuple[float, float]]] = {}
        self._hysteresis_margin = hysteresis_margin
        # Per-zone exit-hysteresis overrides (normalized margin). A larger margin
        # makes a zone "stickier" — once inside, the test point must move that
        # much further past the boundary before counting as left. The mic zone
        # uses a bigger margin so a person standing at the mic doesn't flicker
        # out (and fire a false MIC_ZONE_LEFT) when the body-center test point
        # jitters near the edge. Zones not listed fall back to hysteresis_margin.
        self._zone_margins: Dict[str, float] = (
            zone_margins if zone_margins is not None
            else (ZONE_CONFIG.get("zone_hysteresis_margins") or {})
        )

        # Per-zone set of person IDs currently considered "inside"
        self._inside_state: Dict[str, Set[int]] = {}

        # Dynamic bench zone names ("bench_0", "bench_1", ...) derived from
        # ZONE_CONFIG["bench_zones"]. Tracked separately so callers can iterate.
        self._bench_zone_names: List[str] = []

        # Load named singletons
        for name in ("entry_zone", "mic_zone"):
            poly = ZONE_CONFIG.get(name)
            self._register_zone(name, poly)

        # Load bench zones — list of polygons, registered as "bench_0", "bench_1"...
        bench_list = ZONE_CONFIG.get("bench_zones") or []
        for idx, poly in enumerate(bench_list):
            name = f"bench_{idx}"
            if self._register_zone(name, poly):
                self._bench_zone_names.append(name)
        if not self._bench_zone_names:
            logger.info("ZoneDetector: no bench_zones configured (will skip bench events)")

        # Entry-direction arrow: an optional {"from": [x,y], "to": [x,y]} that
        # encodes the "into the room" direction (normalized coords). The
        # SceneOrchestrator dots a person's motion against this to tell an
        # arrival (inbound) from a departure (outbound) at the entry zone.
        self._entry_direction: Optional[Dict[str, List[float]]] = None
        self._entry_inward_unit: Optional[Tuple[float, float]] = None
        self._set_entry_direction(ZONE_CONFIG.get("entry_direction"))

    def _register_zone(self, name: str, poly) -> bool:
        if poly and len(poly) >= 3:
            margin = self._zone_margins.get(name, self._hysteresis_margin)
            self._zones[name] = [(float(x), float(y)) for x, y in poly]
            self._zones_expanded[name] = _expand_polygon(self._zones[name], margin)
            self._inside_state[name] = set()
            logger.info(
                f"Zone '{name}' loaded with {len(poly)} vertices "
                f"(hysteresis={margin})"
            )
            return True
        logger.warning(f"Zone '{name}' not configured or has < 3 vertices")
        return False

    def reload_zones(self, zones: Dict[str, list]) -> Dict[str, int]:
        """Replace entry_zone / mic_zone / bench_zones at runtime and recompute
        all derived state (expanded polygons, centroids, hysteresis sets).

        `zones` maps name -> list of (x, y) normalized vertices; "bench_zones"
        maps to a list of polygons. The SceneOrchestrator holds a reference to
        this detector and calls its methods live, so the new zones take effect
        on the next tick — no restart needed.

        Returns {zone_name: vertex_count} for the zones that took effect.
        """
        self._zones.clear()
        self._zones_expanded.clear()
        self._inside_state.clear()
        self._bench_zone_names = []

        applied: Dict[str, int] = {}
        for name in ("entry_zone", "mic_zone"):
            poly = zones.get(name)
            if self._register_zone(name, poly):
                applied[name] = len(poly)

        bench_list = zones.get("bench_zones") or []
        for idx, poly in enumerate(bench_list):
            name = f"bench_{idx}"
            if self._register_zone(name, poly):
                self._bench_zone_names.append(name)
                applied[name] = len(poly)

        # Entry-direction arrow: re-apply the annotated direction (recomputing
        # the inward unit) on every reload. When the payload omits it, fall back
        # to the entry_zone→mic_zone centroid so live re-labeling of either zone
        # still yields a sensible inward direction without a restart.
        self._set_entry_direction(zones.get("entry_direction"))
        if self._entry_direction is not None:
            applied["entry_direction"] = 2

        logger.info(f"ZoneDetector reloaded at runtime: {applied}")
        return applied

    # ------------------------------------------------------------------
    # Entry-direction (inbound vs outbound) helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _centroid(poly: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        if not poly:
            return None
        n = len(poly)
        return (sum(x for x, _ in poly) / n, sum(y for _, y in poly) / n)

    @staticmethod
    def _unit(dx: float, dy: float) -> Optional[Tuple[float, float]]:
        mag = (dx * dx + dy * dy) ** 0.5
        if mag < 1e-9:
            return None
        return (dx / mag, dy / mag)

    def _set_entry_direction(self, direction) -> None:
        """Store the annotated entry-direction arrow and (re)compute the inward
        unit vector. `direction` is {"from": [x,y], "to": [x,y]} or None."""
        self._entry_direction = None
        if isinstance(direction, dict):
            p_from = direction.get("from")
            p_to = direction.get("to")
            if (p_from and p_to and len(p_from) >= 2 and len(p_to) >= 2):
                self._entry_direction = {
                    "from": [float(p_from[0]), float(p_from[1])],
                    "to": [float(p_to[0]), float(p_to[1])],
                }
        self._entry_inward_unit = self._compute_inward_unit()
        if self._entry_inward_unit is not None:
            src = "annotated arrow" if self._entry_direction else "entry→mic centroid"
            logger.info(
                f"ZoneDetector: entry inward direction = "
                f"({self._entry_inward_unit[0]:.3f}, {self._entry_inward_unit[1]:.3f}) "
                f"[{src}]"
            )
        else:
            logger.info(
                "ZoneDetector: no entry inward direction (no arrow and "
                "entry_zone/mic_zone unavailable) — direction gate will fall back"
            )

    def _compute_inward_unit(self) -> Optional[Tuple[float, float]]:
        """Resolve the 'into the room' unit vector (normalized coords).

        Priority:
          1. annotated arrow (from → to),
          2. entry_zone centroid → mic_zone centroid,
          3. None (caller falls back to legacy position-only behavior).
        """
        if self._entry_direction is not None:
            p_from = self._entry_direction["from"]
            p_to = self._entry_direction["to"]
            unit = self._unit(p_to[0] - p_from[0], p_to[1] - p_from[1])
            if unit is not None:
                return unit
        entry_c = self._centroid(self._zones.get("entry_zone"))
        mic_c = self._centroid(self._zones.get("mic_zone"))
        if entry_c is not None and mic_c is not None:
            return self._unit(mic_c[0] - entry_c[0], mic_c[1] - entry_c[1])
        return None

    def get_entry_inward_unit(self) -> Optional[Tuple[float, float]]:
        """Return the 'into the room' unit vector (normalized), or None if the
        direction gate has nothing to work with (caller should fall back)."""
        return self._entry_inward_unit

    def get_entry_direction(self) -> Optional[Dict[str, List[float]]]:
        """Return the annotated entry-direction arrow {"from","to"} or None.

        Used by GET /api/zones so the web labeler can preload an existing arrow.
        """
        return self._entry_direction

    def get_all_zones_norm(self) -> Dict[str, List[List[float]]]:
        """Return all registered zones as {name: [[x, y], ...]} normalized.

        Used by the web zone labeler to render the currently-active zones.
        """
        return {name: [[x, y] for x, y in poly] for name, poly in self._zones.items()}

    def is_in_zone(self, zone_name: str, px: int, py: int,
                   frame_w: int, frame_h: int,
                   person_id: Optional[int] = None) -> bool:
        """
        Test if pixel position (px, py) is inside the named zone.

        When person_id is provided, hysteresis is applied:
        - Enter: point must be inside the original polygon
        - Exit: point must be outside the expanded polygon

        Args:
            zone_name: "entry_zone" or "mic_zone"
            px, py: Pixel coordinates of person center
            frame_w, frame_h: Frame dimensions for normalization
            person_id: Optional person ID for hysteresis tracking
        """
        poly = self._zones.get(zone_name)
        if poly is None:
            return False
        if frame_w <= 0 or frame_h <= 0:
            return False
        nx = px / frame_w
        ny = py / frame_h

        if person_id is None:
            return _point_in_polygon(nx, ny, poly)

        inside_set = self._inside_state.get(zone_name)
        if inside_set is None:
            return _point_in_polygon(nx, ny, poly)

        was_inside = person_id in inside_set

        if was_inside:
            # Use expanded polygon for exit check — harder to leave
            expanded = self._zones_expanded.get(zone_name, poly)
            still_inside = _point_in_polygon(nx, ny, expanded)
            if not still_inside:
                inside_set.discard(person_id)
            return still_inside
        else:
            # Use original polygon for entry check
            now_inside = _point_in_polygon(nx, ny, poly)
            if now_inside:
                inside_set.add(person_id)
            return now_inside

    def is_bbox_in_zone(self, zone_name: str, bbox, frame_w: int, frame_h: int,
                        person_id: Optional[int] = None) -> bool:
        """Like is_in_zone, but tests SEVERAL points down the body's vertical
        centre line (torso → feet) and counts the person as inside if ANY of
        them is in the zone.

        Why: a single mid-torso point sits high in the image, so a zone painted
        at floor level is missed even when the person stands on it. Sampling
        toward the feet makes "stand on the zone" reliably register, while still
        catching torso-height zones and tolerating feet that are occluded /
        out of frame (those samples simply fall outside and are ignored).

        bbox = [x1, y1, x2, y2] in pixels. Hysteresis is applied once on the
        aggregate (enter on the original polygon, exit on the expanded one).
        """
        poly = self._zones.get(zone_name)
        if poly is None or frame_w <= 0 or frame_h <= 0 or not bbox:
            return False
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        # Sample mid-torso through to near the feet.
        sample_pts = [
            (cx, y1 + (y2 - y1) * f) for f in (0.5, 0.65, 0.8, 0.95)
        ]

        def _any_in(polygon) -> bool:
            for px, py in sample_pts:
                if _point_in_polygon(px / frame_w, py / frame_h, polygon):
                    return True
            return False

        if person_id is None:
            return _any_in(poly)
        inside_set = self._inside_state.get(zone_name)
        if inside_set is None:
            return _any_in(poly)

        if person_id in inside_set:
            expanded = self._zones_expanded.get(zone_name, poly)
            still_inside = _any_in(expanded)
            if not still_inside:
                inside_set.discard(person_id)
            return still_inside
        now_inside = _any_in(poly)
        if now_inside:
            inside_set.add(person_id)
        return now_inside

    def clear_person(self, person_id: int) -> None:
        """Remove a person from all zone hysteresis state."""
        for inside_set in self._inside_state.values():
            inside_set.discard(person_id)

    def get_person_zones(self, px: int, py: int,
                         frame_w: int, frame_h: int) -> List[str]:
        """Return list of all zone names the point is inside."""
        result = []
        for name in self._zones:
            if self.is_in_zone(name, px, py, frame_w, frame_h):
                result.append(name)
        return result

    def get_zone_polygon_pixels(self, zone_name: str,
                                frame_w: int, frame_h: int) -> Optional[List[Tuple[int, int]]]:
        """Return zone polygon in pixel coords (for visualization)."""
        poly = self._zones.get(zone_name)
        if poly is None:
            return None
        return [(int(x * frame_w), int(y * frame_h)) for x, y in poly]

    def get_bench_zone_names(self) -> List[str]:
        """Return registered bench zone names (empty if none configured)."""
        return list(self._bench_zone_names)

    def which_bench_zone(self, px: int, py: int,
                         frame_w: int, frame_h: int,
                         person_id: Optional[int] = None) -> Optional[str]:
        """Return the name of the first bench zone containing (px,py), or None.

        Hysteresis is applied per-zone when person_id is provided.
        """
        for name in self._bench_zone_names:
            if self.is_in_zone(name, px, py, frame_w, frame_h, person_id=person_id):
                return name
        return None

