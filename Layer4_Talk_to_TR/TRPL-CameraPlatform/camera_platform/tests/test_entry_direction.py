#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the entry-direction gate — telling an arrival (inbound crossing of
the entry zone) from a departure (outbound crossing) so that leaving the room
no longer re-triggers a welcome.

Covers three layers:
  1. zone_store: validation/persistence of the entry_direction arrow.
  2. ZoneDetector: resolving the inward unit vector (annotated arrow vs the
     entry_zone -> mic_zone centroid fallback).
  3. SceneOrchestrator._is_inbound_crossing + the wired-in entry check.
"""
import os
import sys
import time
import unittest
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import zone_store
from src.core.zone_detector import ZoneDetector
from src.core.person_cache import PersonCache
from src.core.scene_orchestrator import SceneOrchestrator


class TestEntryDirectionStore(unittest.TestCase):
    """zone_store validates and carries the entry_direction arrow."""

    def test_valid_arrow(self):
        d = zone_store._clean_direction({"from": [0.72, 0.50], "to": [0.55, 0.82]})
        self.assertEqual(d, {"from": [0.72, 0.50], "to": [0.55, 0.82]})

    def test_zero_length_rejected(self):
        # A zero-length arrow carries no direction.
        self.assertIsNone(zone_store._clean_direction({"from": [0.5, 0.5], "to": [0.5, 0.5]}))

    def test_malformed_rejected(self):
        self.assertIsNone(zone_store._clean_direction({"from": [0.5], "to": [0.5, 0.5]}))
        self.assertIsNone(zone_store._clean_direction({"to": [0.5, 0.5]}))
        self.assertIsNone(zone_store._clean_direction([1, 2]))
        self.assertIsNone(zone_store._clean_direction(None))

    def test_out_of_range_clamped(self):
        d = zone_store._clean_direction({"from": [1.5, -0.2], "to": [0.3, 0.4]})
        self.assertEqual(d, {"from": [1.0, 0.0], "to": [0.3, 0.4]})

    def test_normalize_zones_carries_direction(self):
        out = zone_store.normalize_zones({
            "entry_zone": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]],
            "entry_direction": {"from": [0.7, 0.5], "to": [0.55, 0.82]},
        })
        self.assertIn("entry_direction", out)
        self.assertIn("entry_zone", out)

    def test_normalize_zones_drops_bad_direction(self):
        out = zone_store.normalize_zones({
            "entry_zone": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]],
            "entry_direction": {"from": [0.5, 0.5], "to": [0.5, 0.5]},  # degenerate
        })
        self.assertNotIn("entry_direction", out)


class TestEntryInwardVector(unittest.TestCase):
    """ZoneDetector resolves the inward unit vector with the right priority."""

    def test_centroid_fallback(self):
        # Defaults have entry_zone + mic_zone but no arrow -> entry->mic centroid.
        zd = ZoneDetector()
        u = zd.get_entry_inward_unit()
        self.assertIsNotNone(u)
        # entry zone is on the right, mic zone is lower-left: inward points
        # left (x<0) and down (y>0).
        self.assertLess(u[0], 0.0)
        self.assertGreater(u[1], 0.0)
        # Unit length.
        self.assertAlmostEqual((u[0] ** 2 + u[1] ** 2) ** 0.5, 1.0, places=5)

    def test_annotated_arrow_overrides_centroid(self):
        zd = ZoneDetector()
        centroid_u = zd.get_entry_inward_unit()
        applied = zd.reload_zones({
            "entry_zone": [[0.67, 0.52], [0.65, 0.85], [0.72, 0.91],
                           [0.77, 0.51], [0.69, 0.44], [0.69, 0.45]],
            "mic_zone": [[0.40, 0.80], [0.40, 1.0], [0.56, 1.0], [0.56, 0.80]],
            "entry_direction": {"from": [0.72, 0.50], "to": [0.55, 0.82]},
        })
        self.assertEqual(applied.get("entry_direction"), 2)
        arrow_u = zd.get_entry_inward_unit()
        self.assertIsNotNone(arrow_u)
        self.assertNotAlmostEqual(arrow_u[0], centroid_u[0], places=3)
        # The arrow (0.72,0.50)->(0.55,0.82) also points left+down.
        self.assertLess(arrow_u[0], 0.0)
        self.assertGreater(arrow_u[1], 0.0)
        self.assertEqual(zd.get_entry_direction(),
                         {"from": [0.72, 0.50], "to": [0.55, 0.82]})

    def test_none_when_no_zones(self):
        zd = ZoneDetector()
        # Reload with neither a usable mic zone nor an arrow -> no direction.
        zd.reload_zones({"entry_zone": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]})
        self.assertIsNone(zd.get_entry_inward_unit())


class TestInboundCrossing(unittest.TestCase):
    """SceneOrchestrator._is_inbound_crossing: the direction decision itself."""

    def _make_orch(self):
        published = []
        orch = SceneOrchestrator(
            publish_cb=lambda et, p: published.append((et, p)),
            person_cache=PersonCache(),
            zone_detector=ZoneDetector(),  # centroid inward ~ (-0.605, 0.796)
        )
        return orch, published

    def _seed(self, orch, pid, samples, now):
        orch._recent_pos[pid] = deque(samples, maxlen=40)

    def test_inbound_true(self):
        orch, _ = self._make_orch()
        now = time.time()
        # Move left + down = into the room.
        self._seed(orch, 1, [(now - 0.3, (0.71, 0.56)), (now, (0.69, 0.72))], now)
        self.assertTrue(orch._is_inbound_crossing(1, now))

    def test_outbound_false(self):
        orch, _ = self._make_orch()
        now = time.time()
        # Move right + up = toward the door (leaving).
        self._seed(orch, 1, [(now - 0.3, (0.69, 0.72)), (now, (0.71, 0.56))], now)
        self.assertFalse(orch._is_inbound_crossing(1, now))

    def test_ambiguous_uses_fallback(self):
        orch, _ = self._make_orch()
        now = time.time()
        # Tiny displacement (< min_disp) -> ambiguous.
        self._seed(orch, 1, [(now - 0.3, (0.70, 0.62)), (now, (0.703, 0.624))], now)
        orch._dir_fallback_welcome = True
        self.assertTrue(orch._is_inbound_crossing(1, now))
        orch._dir_fallback_welcome = False
        self.assertFalse(orch._is_inbound_crossing(1, now))

    def test_insufficient_buffer_uses_fallback(self):
        orch, _ = self._make_orch()
        now = time.time()
        self._seed(orch, 1, [(now, (0.70, 0.62))], now)  # only one sample
        orch._dir_fallback_welcome = True
        self.assertTrue(orch._is_inbound_crossing(1, now))
        orch._dir_fallback_welcome = False
        self.assertFalse(orch._is_inbound_crossing(1, now))

    def test_gate_disabled_always_inbound(self):
        orch, _ = self._make_orch()
        now = time.time()
        # Clearly outbound, but the gate is off -> treated as inbound (legacy).
        self._seed(orch, 1, [(now - 0.3, (0.69, 0.72)), (now, (0.71, 0.56))], now)
        orch._dir_gate_enabled = False
        self.assertTrue(orch._is_inbound_crossing(1, now))

    def test_no_inward_direction_is_legacy(self):
        orch, _ = self._make_orch()
        now = time.time()
        # No direction available -> behave like the old position-only logic.
        orch._zones.reload_zones({"entry_zone": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]})
        self.assertIsNone(orch._zones.get_entry_inward_unit())
        self._seed(orch, 1, [(now - 0.3, (0.69, 0.72)), (now, (0.71, 0.56))], now)
        self.assertTrue(orch._is_inbound_crossing(1, now))


class TestEntryGateWiring(unittest.TestCase):
    """End-to-end through tick(): an arrival is welcomed, a departure is not."""

    FW = FH = 1000

    def _run_trajectory(self, norm_path, dwell=0.12, dt=0.06):
        """Feed a person along norm_path (list of (nx,ny)) and return the list
        of (event_type, payload) the orchestrator published."""
        published = []
        orch = SceneOrchestrator(
            publish_cb=lambda et, p: published.append((et, p)),
            person_cache=PersonCache(),
            zone_detector=ZoneDetector(),
        )
        orch._entry_dwell_sec = dwell
        pid = 1
        for nx, ny in norm_path:
            cx, cy = int(nx * self.FW), int(ny * self.FH)
            orch.tick({pid: {
                "center": (cx, cy),
                "frame_w": self.FW, "frame_h": self.FH,
                "frame_count": 5,
            }})
            time.sleep(dt)
        return published

    def test_inbound_arrival_is_welcomed(self):
        # Approach from the door (right), walk left+down into the room.
        path = [(0.76, 0.50), (0.74, 0.54), (0.72, 0.58),
                (0.705, 0.63), (0.69, 0.70), (0.68, 0.74)]
        events = self._run_trajectory(path)
        entered = [p for et, p in events
                   if et == "PERSON_ENTERED_ROOM" and p.get("via_entry_zone")]
        self.assertTrue(entered, "inbound crossing should register an arrival")

    def test_outbound_departure_is_suppressed(self):
        # Come from the interior (lower-left), walk right+up toward the door.
        path = [(0.66, 0.78), (0.68, 0.73), (0.70, 0.66),
                (0.715, 0.60), (0.73, 0.55), (0.745, 0.51)]
        events = self._run_trajectory(path)
        entered = [p for et, p in events
                   if et == "PERSON_ENTERED_ROOM" and p.get("via_entry_zone")]
        self.assertEqual(entered, [],
                         "outbound crossing must NOT register an arrival/welcome")


if __name__ == "__main__":
    unittest.main()
