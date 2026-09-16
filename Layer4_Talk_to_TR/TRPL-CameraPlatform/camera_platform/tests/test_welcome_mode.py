#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the welcome-greeting mode toggle (welcome_use_llm / WELCOME_USE_LLM).

  - welcome_use_llm True  -> appearance-aware LLM/VLM greeting (legacy).
  - welcome_use_llm False -> no LLM: a random WELCOME_FALLBACKS line, and the
    BATCH_INVITE flush does NOT wait for VLM appearances.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import config
from src.utils.config import SCENE_CONFIG
from src.core.person_cache import PersonCache
from src.core.zone_detector import ZoneDetector
from src.core import scene_orchestrator as so
from src.core.scene_orchestrator import (
    SceneOrchestrator, _build_welcome, WELCOME_FALLBACKS,
)


class TestEnvBool(unittest.TestCase):
    VAR = "TEST_WELCOME_ENV_BOOL_XYZ"

    def tearDown(self):
        os.environ.pop(self.VAR, None)

    def test_unset_uses_default(self):
        os.environ.pop(self.VAR, None)
        self.assertTrue(config._env_bool(self.VAR, True))
        self.assertFalse(config._env_bool(self.VAR, False))

    def test_truthy_and_falsy(self):
        for v in ("1", "true", "TRUE", "Yes", "on"):
            os.environ[self.VAR] = v
            self.assertTrue(config._env_bool(self.VAR, False), v)
        for v in ("0", "false", "No", "off"):
            os.environ[self.VAR] = v
            self.assertFalse(config._env_bool(self.VAR, True), v)

    def test_unrecognized_uses_default(self):
        os.environ[self.VAR] = "maybe"
        self.assertTrue(config._env_bool(self.VAR, True))
        self.assertFalse(config._env_bool(self.VAR, False))


class TestBuildWelcome(unittest.TestCase):
    def test_no_llm_returns_a_fallback(self):
        cache = PersonCache()
        e = cache.create_entry(1)
        e.descriptor = "the person in a red jacket"  # present but must be ignored
        # Sample many times: every result must come from WELCOME_FALLBACKS and
        # must never leak the descriptor.
        seen = set()
        for _ in range(50):
            g = _build_welcome([e], use_llm=False)
            self.assertIn(g, WELCOME_FALLBACKS)
            self.assertNotIn("red jacket", g)
            seen.add(g)
        # Randomness actually varies the line (not always the same one).
        self.assertGreater(len(seen), 1)

    def test_llm_uses_appearance_descriptor(self):
        cache = PersonCache()
        e = cache.create_entry(1)
        e.descriptor = "the person in a red jacket"
        g = _build_welcome([e], use_llm=True)
        self.assertIn("red jacket", g)

    def test_llm_default_param_is_true(self):
        cache = PersonCache()
        e = cache.create_entry(1)
        e.descriptor = "the person in a blue hat"
        self.assertIn("blue hat", _build_welcome([e]))  # use_llm defaults True


class TestFlushRespectsMode(unittest.TestCase):
    def _orch(self):
        published = []
        orch = SceneOrchestrator(
            publish_cb=lambda et, p: published.append((et, p)),
            person_cache=PersonCache(),
            zone_detector=ZoneDetector(),
        )
        return orch, published

    def _arm_pending(self, orch, pid, now):
        """Put `pid` in the pending-invite batch with the debounce already
        expired, but WITHOUT any VLM appearance set (appearance_ready=False)."""
        orch._cache.create_entry(pid)
        orch._pending_invite_ids = [pid]
        orch._invite_timer_started = True
        orch._last_entry_time = now - orch._debounce_base - 0.1

    def test_no_llm_flushes_without_waiting(self):
        orch, published = self._orch()
        orch._welcome_use_llm = False
        now = time.time()
        self._arm_pending(orch, 1, now)

        orch._check_invite_flush(now)

        invites = [p for et, p in published if et == "BATCH_INVITE"]
        self.assertEqual(len(invites), 1, "no-LLM welcome should flush immediately")
        self.assertIn(invites[0]["greeting"], WELCOME_FALLBACKS)
        self.assertFalse(orch._waiting_for_vlm)

    def test_llm_waits_for_appearance(self):
        orch, published = self._orch()
        orch._welcome_use_llm = True
        now = time.time()
        self._arm_pending(orch, 1, now)

        orch._check_invite_flush(now)

        invites = [p for et, p in published if et == "BATCH_INVITE"]
        self.assertEqual(invites, [], "LLM welcome must wait for VLM appearance")
        self.assertTrue(orch._waiting_for_vlm)

    def test_config_default_is_llm_on(self):
        self.assertTrue(SCENE_CONFIG.get("welcome_use_llm"))


if __name__ == "__main__":
    unittest.main()
