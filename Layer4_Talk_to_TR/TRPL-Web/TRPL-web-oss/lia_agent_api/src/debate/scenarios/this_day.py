# -*- coding: utf-8 -*-
"""
This Day in TR History — calendar lookup for what happened on today's date
during Roosevelt's life (1858-1919).

Used to make TR's "thinking" feel grounded in the current moment:
  "I was just thinking about how I became president on this very day..."
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(f"lia.{__name__}")

_DATA_PATH = Path(__file__).resolve().parents[4] / "storys" / "this_day_in_history.json"


class ThisDayInHistory:
    """Calendar lookup for TR life events."""

    def __init__(self):
        self.data: dict[str, list[dict]] = {}
        self._load()

    def _load(self):
        if not _DATA_PATH.exists():
            logger.warning("this_day_in_history.json not found at %s", _DATA_PATH)
            return
        with open(_DATA_PATH, encoding="utf-8") as f:
            self.data = json.load(f)
        logger.info("Loaded this-day-in-history: %d dates", len(self.data))

    def for_date(self, dt: datetime | None = None) -> list[dict]:
        """Get events for the given date (default: today)."""
        if dt is None:
            dt = datetime.now()
        key = f"{dt.month:02d}-{dt.day:02d}"
        return self.data.get(key, [])

    def for_today(self) -> list[dict]:
        return self.for_date()

    def pick_one_for_today(self) -> dict | None:
        """Pick a single event for today (the most notable, or first)."""
        events = self.for_today()
        if not events:
            return None
        # Prefer presidency events, then military, then personal
        priority = {"presidency": 0, "military": 1, "adventure": 2,
                    "personal": 3, "early_life": 4}
        events_sorted = sorted(events, key=lambda e: priority.get(e.get("category", ""), 99))
        return events_sorted[0]


# Singleton
_instance: ThisDayInHistory | None = None


def get_this_day() -> ThisDayInHistory:
    global _instance
    if _instance is None:
        _instance = ThisDayInHistory()
    return _instance
