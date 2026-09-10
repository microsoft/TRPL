# -*- coding: utf-8 -*-
"""
Story RAG — retrieval-augmented story system.

Architecture:
  1. COMPACT INDEX (always in system prompt):
     - 85 stories, each = title + one-liner (~3K chars total)
     - Agent knows what's available but not the details

  2. RETRIEVAL (per-turn, by StoryCurator):
     - Curator reads conversation + index → picks 1 story title
     - This module loads that story's full narrative from disk

  3. INJECTION (into LLM payload):
     - Full narrative injected as "active_story" field
     - Agent uses it for the current response only
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(f"lia.{__name__}")

_STORYS_DIR = Path(__file__).resolve().parents[4] / "storys" / "quiz_stories"


# ─── Index ───────────────────────────────────────────────────────────

class StoryIndex:
    """Compact in-memory index of all stories."""

    def __init__(self):
        self.entries: list[dict] = []  # [{file, title, quiz, one_liner}, ...]
        self._by_title: dict[str, dict] = {}
        self._load()

    def _load(self):
        path = _STORYS_DIR / "_one_liners.json"
        if not path.exists():
            logger.warning("One-liners not found at %s", path)
            return
        with open(path, encoding="utf-8") as f:
            self.entries = json.load(f)
        for e in self.entries:
            self._by_title[e["title"].lower().strip()] = e

    def find_by_title(self, title: str) -> dict | None:
        """Fuzzy-ish title lookup."""
        key = title.lower().strip()
        # Exact match
        if key in self._by_title:
            return self._by_title[key]
        # Substring match
        for k, v in self._by_title.items():
            if key in k or k in key:
                return v
        return None

    def build_prompt_index(self) -> str:
        """Build the compact story catalog for the system prompt.

        Format:
          ## Adversity
          - "I lost my mother and wife on the same day..." — The Day the Sun Went Out
          - "Standing before thousands in Paris..." — Dust, Sweat, and Triumph
        """
        categories: dict[str, list[dict]] = {}
        for e in self.entries:
            cat = e.get("quiz", "unknown").replace("_quiz", "").replace("_", " ").title()
            categories.setdefault(cat, []).append(e)

        lines = ["You know the following stories. Each has a one-line hook and a title.", ""]
        for cat, entries in sorted(categories.items()):
            lines.append(f"## {cat}")
            for e in entries:
                lines.append(f'  - "{e["one_liner"]}" — {e["title"]}')
            lines.append("")
        return "\n".join(lines)


# ─── Retrieval ───────────────────────────────────────────────────────

def load_full_story(title: str, index: StoryIndex | None = None) -> str | None:
    """Load the full narrative for a story by title."""
    if index is None:
        index = StoryIndex()

    entry = index.find_by_title(title)
    if not entry:
        logger.warning("Story not found in index: %s", title)
        return None

    filepath = _STORYS_DIR / entry["file"]
    if not filepath.exists():
        logger.warning("Story file not found: %s", filepath)
        return None

    text = filepath.read_text(encoding="utf-8")

    # Strip evidence/audit section
    marker = "=" * 40
    parts = text.split(marker)
    narrative_parts = []
    for part in parts:
        if part.strip().upper().startswith("EVIDENCE SOURCES"):
            break
        narrative_parts.append(part)

    return marker.join(narrative_parts).strip()


# ─── Singleton ───────────────────────────────────────────────────────

_index: StoryIndex | None = None

def get_index() -> StoryIndex:
    global _index
    if _index is None:
        _index = StoryIndex()
    return _index
