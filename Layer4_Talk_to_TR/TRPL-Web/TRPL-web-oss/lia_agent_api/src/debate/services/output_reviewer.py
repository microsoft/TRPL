# -*- coding: utf-8 -*-
"""L3 Output Reviewer — parallel safety audit of TR's response stream.

Runs in parallel with the main agent's streaming output. Accumulates
text deltas keyed by utterance_id and, when a flush threshold or
stream-end is hit, fires an async LLM call using deployment-only review
criteria.

If the reviewer flags something in ENFORCE mode, it calls
SafetyService.kill_utterance(uid). The existing kill machinery raises
StreamCancelled on the next write, halting the OpenAI stream mid-
sentence and emitting a safety_interrupt event to the frontend.

All behavior is env-driven (see api/config.py output_reviewer_* fields).
Default is OFF — flip OUTPUT_REVIEWER_ENABLED=true to opt in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from api.config import config
from debate.services.private_config import PrivateConfigError, load_private_json
from debate.services.shared import get_openai_client

logger = logging.getLogger(f"lia.{__name__}")

# Module-level semaphore lazy-initialized on first use (needs an event
# loop to attach to). Caps concurrent in-flight LLM reviews.
_concurrency_sem: Optional[asyncio.Semaphore] = None


def _get_sem() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(config.output_reviewer_max_concurrent)
    return _concurrency_sem


if config.output_reviewer_enabled:
    _REVIEWER_CONFIG = load_private_json(
        "LIA_OUTPUT_REVIEWER_CONFIG",
        "LIA_OUTPUT_REVIEWER_CONFIG_FILE",
        dict,
    )
    _REVIEWER_SYSTEM = _REVIEWER_CONFIG.get("system_prompt")
    _category_values = _REVIEWER_CONFIG.get("categories")
    _clean_category_value = _REVIEWER_CONFIG.get("clean_category")
    if (
        not isinstance(_REVIEWER_SYSTEM, str)
        or not _REVIEWER_SYSTEM.strip()
        or not isinstance(_category_values, list)
        or not _category_values
        or not isinstance(_clean_category_value, str)
    ):
        raise PrivateConfigError(
            "Output reviewer private configuration is incomplete."
        )
    _VALID_CATEGORIES = frozenset(
        str(category).lower() for category in _category_values
    )
    _CLEAN_CATEGORY = _clean_category_value.lower()
    try:
        _DEFAULT_CONFIDENCE = float(_REVIEWER_CONFIG["default_confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PrivateConfigError(
            "Output reviewer default_confidence must be numeric."
        ) from exc
    if (
        _CLEAN_CATEGORY not in _VALID_CATEGORIES
        or not 0.0 <= _DEFAULT_CONFIDENCE <= 1.0
    ):
        raise PrivateConfigError(
            "Output reviewer private configuration is invalid."
        )
else:
    _REVIEWER_SYSTEM = ""
    _VALID_CATEGORIES = frozenset({"clean"})
    _CLEAN_CATEGORY = "clean"
    _DEFAULT_CONFIDENCE = 0.0


class ReviewResult:
    __slots__ = ("category", "confidence", "reason")

    def __init__(self, category: str, confidence: float, reason: str):
        self.category = (
            category if category in _VALID_CATEGORIES else _CLEAN_CATEGORY
        )
        self.confidence = confidence
        self.reason = reason

    def is_problem(self) -> bool:
        return self.category != _CLEAN_CATEGORY

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "reason": self.reason,
        }


# ─── Reviewer service ───────────────────────────────────────────────────

class OutputReviewer:
    """Module-level reviewer. Per-utterance state keyed by uid."""

    def __init__(self) -> None:
        self._buffers: dict[str, str] = {}
        self._safety_refs: dict[str, object] = {}
        self._in_flight: set[str] = set()  # uids currently being LLM-checked
        self._already_killed: set[str] = set()
        self._first_seen: dict[str, float] = {}  # uid -> monotonic ts for TTL gc

    # ─── public hooks (called from io.py streaming path) ──────────────

    def feed_chunk(self, utterance_id: str, text: str, safety) -> None:
        """Accumulate streamed text. May fire an async LLM check.
        Non-blocking — the streaming path never waits on the reviewer."""
        if not config.output_reviewer_enabled or not text:
            return
        if utterance_id in self._already_killed:
            return
        buf = self._buffers.get(utterance_id, "") + text
        self._buffers[utterance_id] = buf
        self._safety_refs[utterance_id] = safety
        self._first_seen.setdefault(utterance_id, time.monotonic())
        # Opportunistic GC of stale buffers from abnormal session ends.
        self._gc_stale()
        # Mid-stream check is only useful in ENFORCE mode (early kill).
        # In observe mode it's just redundant cost — wait for flush.
        if config.output_reviewer_mode != "enforce":
            return
        if (
            len(buf) >= config.output_reviewer_flush_chars
            and utterance_id not in self._in_flight
        ):
            self._in_flight.add(utterance_id)
            asyncio.create_task(
                self._check_and_maybe_kill(utterance_id, final=False),
                name=f"output_review_{utterance_id}",
            )

    def flush(self, utterance_id: str) -> None:
        """Stream ended — final check on whatever's left in the buffer."""
        if not config.output_reviewer_enabled:
            return
        buf = self._buffers.get(utterance_id, "")
        if utterance_id in self._already_killed:
            self._cleanup(utterance_id)
            return
        if not buf.strip():
            self._cleanup(utterance_id)
            return
        # Skip ultra-short replies (canned TR deflections like "I'm
        # afraid I don't know what you mean"). Not worth the LLM cost,
        # and they're already covered by the input-side detector.
        if len(buf) < config.output_reviewer_min_chars:
            self._cleanup(utterance_id)
            return
        asyncio.create_task(
            self._check_and_maybe_kill(utterance_id, final=True),
            name=f"output_review_final_{utterance_id}",
        )

    def reset(self, utterance_id: str) -> None:
        """Caller is recycling this uid — clear state."""
        self._cleanup(utterance_id)
        self._already_killed.discard(utterance_id)

    # ─── internals ────────────────────────────────────────────────────

    def _cleanup(self, utterance_id: str) -> None:
        self._buffers.pop(utterance_id, None)
        self._safety_refs.pop(utterance_id, None)
        self._first_seen.pop(utterance_id, None)
        self._in_flight.discard(utterance_id)

    def _gc_stale(self) -> None:
        """Drop buffers older than the configured TTL. Handles abnormal
        session ends where flush() never fired (e.g. websocket dropped)."""
        ttl = config.output_reviewer_buffer_ttl_seconds
        if ttl <= 0:
            return
        now = time.monotonic()
        stale = [
            uid for uid, ts in self._first_seen.items()
            if now - ts > ttl
        ]
        for uid in stale:
            logger.warning("[L3] GC stale buffer uid=%s age=%.0fs", uid, now - self._first_seen[uid])
            self._cleanup(uid)

    async def _check_and_maybe_kill(self, utterance_id: str, *, final: bool) -> None:
        try:
            text = self._buffers.get(utterance_id, "")
            if not text or not text.strip():
                return
            try:
                result = await asyncio.wait_for(
                    self._call_llm(text),
                    timeout=config.output_reviewer_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("[L3] reviewer timeout on uid=%s", utterance_id)
                return
            except Exception as e:  # noqa: BLE001
                logger.error("[L3] reviewer crashed: %s", e, exc_info=True)
                return
            if not result or not result.is_problem():
                return

            logger.warning(
                "[L3] caught %s (conf=%.2f) on uid=%s — reason: %s",
                result.category, result.confidence, utterance_id, result.reason,
            )

            # Auto-report regardless of mode — observe just logs;
            # enforce additionally kills.
            if config.output_reviewer_auto_report:
                try:
                    _auto_report(text, result, utterance_id)
                except Exception as e:  # noqa: BLE001
                    logger.error("[L3] auto-report failed: %s", e)

            if config.output_reviewer_mode == "enforce":
                safety = self._safety_refs.get(utterance_id)
                if safety is not None and not self._already_killed.intersection({utterance_id}):
                    logger.warning("[L3/enforce] killing uid=%s", utterance_id)
                    self._already_killed.add(utterance_id)
                    try:
                        await safety.kill_utterance(
                            utterance_id,
                            event_type="safety_interrupt",
                            role="assistant",
                            reason=f"L3 reviewer: {result.category}",
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.error("[L3] kill_utterance failed: %s", e)
        finally:
            self._in_flight.discard(utterance_id)
            if final:
                self._cleanup(utterance_id)

    async def _call_llm(self, text: str) -> Optional[ReviewResult]:
        # Cap concurrent LLM calls so a high-traffic moment doesn't
        # starve the main agent or burn the Foundry quota.
        async with _get_sem():
            client = get_openai_client()
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=config.output_reviewer_model,
                messages=[
                    {"role": "system", "content": _REVIEWER_SYSTEM},
                    {"role": "user", "content": text},
                ],
                max_tokens=160,
                response_format={"type": "json_object"},
            )
        content = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[L3] JSON parse error: %s on %r", e, content[:120])
            return None
        category = str(data.get("category", _CLEAN_CATEGORY)).lower()
        if category == _CLEAN_CATEGORY:
            return None
        return ReviewResult(
            category=category,
            confidence=float(data.get("confidence", _DEFAULT_CONFIDENCE)),
            reason=str(data.get("reason", "")),
        )


# Module-level singleton (parallel to how SafetyService is exposed via
# the session). The reviewer is stateless across sessions — per-utterance
# state is keyed by the unique utterance_id from io.py.
_reviewer = OutputReviewer()


def feed_chunk(utterance_id: str, text: str, safety) -> None:
    _reviewer.feed_chunk(utterance_id, text, safety)


def flush(utterance_id: str) -> None:
    _reviewer.flush(utterance_id)


def reset(utterance_id: str) -> None:
    _reviewer.reset(utterance_id)


# ─── Auto-report (same dir /admin/reports reads from) ───────────────────

_REPORTS_DIR = Path(
    os.getenv("JAILBREAK_REPORTS_DIR", str(Path.home() / "jailbreak_reports"))
)


def _auto_report(text: str, result: ReviewResult, utterance_id: str) -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow()
    rid = f"{now.strftime('%Y-%m-%d_%H%M%S')}_L3_{uuid.uuid4().hex[:6]}"
    record = {
        "id": rid,
        "submitted_at": now.isoformat(timespec="seconds") + "Z",
        "note": f"[L3/{result.category} conf={result.confidence:.2f}] {result.reason}",
        "transcript": [{"role": "assistant", "text": text}],
        "room_name": utterance_id,
        "tester_label": "[L3-output-review]",
    }
    p = _REPORTS_DIR / f"{rid}.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info("[L3] auto-report saved: %s", rid)
