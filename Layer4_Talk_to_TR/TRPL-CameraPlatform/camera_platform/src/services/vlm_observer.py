"""
VLM Observer Worker — async person appearance description via vision LLM.

Accepts base64-encoded person crops and calls a vision-capable LLM to produce
structured appearance descriptions (top/bottom clothing, notable features).

Design:
  - Non-blocking: queue-based, processed in daemon threads
  - Semaphore controls max concurrent requests
  - Graceful fallback: disabled if LLM_API_KEY not set
"""
import json
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from ..utils.config import VLM_CONFIG
from ..utils.image import encode_frame, stitch_and_encode, stitch_crops_horizontal, save_vlm_debug_image

logger = logging.getLogger(__name__)

# (person_id: int, result: dict) → None
AppearanceCallback = Callable[[int, dict], None]


class VLMObserverWorker:
    """
    Background worker that processes VLM appearance requests.

    Usage:
        worker = VLMObserverWorker(appearance_cb=on_appearance)
        worker.start()
        worker.request_person_appearance_b64(person_id, crop_b64)
        worker.stop()
    """

    # Bounded queue: under load we drop oldest pending requests instead of
    # growing unbounded memory. 50 in-flight appearance requests is plenty for
    # a 20-person room; anything past that is runaway.
    _QUEUE_MAXSIZE = 50

    # Circuit breaker: after N consecutive failed VLM calls we stop calling
    # the API entirely for HALF_OPEN_PROBE_SEC, then allow ONE trial request.
    # When closed: normal operation. When open: calls return a synthetic
    # "unknown" appearance immediately so the orchestrator's VLM-wait path
    # collapses and batches flush on time instead of stalling 5 seconds each.
    _BREAKER_OPEN_THRESHOLD = 5
    _BREAKER_HALF_OPEN_PROBE_SEC = 60.0

    def __init__(self, appearance_cb: Optional[AppearanceCallback] = None,
                 result_cb=None):
        """
        Args:
            appearance_cb: Called with (person_id, result_dict) when VLM returns.
            result_cb: Legacy group-level callback (unused in v2, accepted for compat).
        """
        self._appearance_cb = appearance_cb
        self._queue: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Observability counters
        self._dropped_count: int = 0
        self._call_count: int = 0
        self._error_count: int = 0

        # Circuit breaker state
        self._breaker_consecutive_failures: int = 0
        self._breaker_open: bool = False
        self._breaker_opened_at: float = 0.0
        self._breaker_half_open_in_flight: bool = False
        self._breaker_state_lock = threading.Lock()

        cfg = VLM_CONFIG
        self.enabled = cfg.get("enabled", False)
        self.model = cfg.get("model", "gpt-4o-mini")
        self.timeout = cfg.get("timeout_sec", 15.0)
        self.appearance_prompt = cfg.get("appearance_prompt", "")
        self.max_concurrent = cfg.get("max_concurrent", 2)
        self.appearance_detail = cfg.get("appearance_detail", "low")

        self._semaphore = threading.Semaphore(self.max_concurrent)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrent,
            thread_name_prefix="vlm-worker",
        )
        self._client = None
        if self.enabled:
            self._init_client()

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="VLMObserver")
        self._thread.start()
        logger.info("VLMObserverWorker started")

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        self._executor.shutdown(wait=False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("VLMObserverWorker stopped")

    def _enqueue(self, item: dict) -> None:
        """Non-blocking enqueue with drop-oldest fallback.

        If the queue is full, drop the OLDEST pending request and enqueue the
        new one. Under sustained overload this prevents unbounded growth while
        keeping the freshest requests — appearance VLM results go stale quickly
        since they describe a person's current frame.
        """
        try:
            self._queue.put_nowait(item)
            return
        except queue.Full:
            pass
        # Drop one oldest then retry
        try:
            dropped = self._queue.get_nowait()
            self._dropped_count += 1
            logger.warning(
                f"VLM queue full (size={self._queue.qsize()}), dropped oldest "
                f"(total dropped: {self._dropped_count})"
            )
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._dropped_count += 1
            logger.error("VLM queue still full after drop-oldest — dropping new request")

    def request_person_appearance_b64(self, person_id: int, crop_b64: str) -> None:
        """Queue appearance request from a single base64-encoded JPEG crop."""
        if not self.enabled or not self._client or not self._appearance_cb:
            return
        self._enqueue({"person_id": person_id, "crop_b64": crop_b64})

    def request_queue_check(self, frame_b64: str,
                            queue_cb: Callable[[bool], None]) -> None:
        """Queue a full-frame check for whether someone is waiting behind the
        mic person. Result (a single bool) is delivered via queue_cb.

        The callback is ALWAYS invoked (with False on any failure / breaker-open)
        so the caller never wedges waiting on a lost response.
        """
        if not self.enabled or not self._client:
            queue_cb(False)
            return
        self._enqueue({
            "frame_b64": frame_b64,
            "_task": "queue_check",
            "_queue_cb": queue_cb,
        })

    def request_scene_observation(self, frame_b64: str,
                                  obs_cb: Callable[[dict], None]) -> None:
        """Queue a full-frame environment observation. Result dict is delivered
        via obs_cb: {description, queue_behind}. The callback is ALWAYS invoked
        (empty dict on failure / breaker-open) so the caller never wedges."""
        if not self.enabled or not self._client:
            obs_cb({})
            return
        self._enqueue({
            "frame_b64": frame_b64,
            "_task": "scene_observe",
            "_obs_cb": obs_cb,
        })

    def request_person_appearance_multi(self, person_id: int, crop_b64_list: list[str]) -> None:
        """Queue appearance request from multiple base64-encoded JPEG crops.

        The crops are stitched side-by-side into one image before sending to VLM.
        The stitched image is also saved to vlm_debug/ for inspection.
        """
        if not self.enabled or not self._client or not self._appearance_cb:
            return
        if not crop_b64_list:
            return
        if len(crop_b64_list) == 1:
            self._enqueue({"person_id": person_id, "crop_b64": crop_b64_list[0]})
            return
        self._enqueue({"person_id": person_id, "crop_b64_list": crop_b64_list})

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def queue_depth(self) -> int:
        """Current in-flight request count (approximate)."""
        return self._queue.qsize()

    def dropped_count(self) -> int:
        """Cumulative count of requests dropped due to queue overflow."""
        return self._dropped_count

    def call_count(self) -> int:
        """Cumulative count of VLM API calls issued."""
        return self._call_count

    def error_count(self) -> int:
        """Cumulative count of VLM API calls that raised or returned garbage."""
        return self._error_count

    def breaker_open(self) -> bool:
        """True when the circuit breaker is suppressing VLM calls."""
        return self._breaker_open

    # ------------------------------------------------------------------
    # Circuit breaker helpers (internal)
    # ------------------------------------------------------------------

    def _breaker_should_skip_call(self) -> bool:
        """Check whether to skip the real VLM call.

        Returns True if the breaker is open AND we are not in a half-open
        probe window. Returns False if the call should proceed.
        """
        with self._breaker_state_lock:
            if not self._breaker_open:
                return False
            # Half-open: after HALF_OPEN_PROBE_SEC since opening, let ONE call
            # through. If it succeeds, the breaker will close in _record_success.
            elapsed = time.time() - self._breaker_opened_at
            if elapsed >= self._BREAKER_HALF_OPEN_PROBE_SEC and not self._breaker_half_open_in_flight:
                self._breaker_half_open_in_flight = True
                logger.info("VLM circuit breaker: half-open probe allowed")
                return False
            return True

    def _record_success(self) -> None:
        """Call this when a VLM call returned a valid response."""
        with self._breaker_state_lock:
            if self._breaker_open:
                logger.info("VLM circuit breaker: CLOSED (probe succeeded)")
            self._breaker_consecutive_failures = 0
            self._breaker_open = False
            self._breaker_half_open_in_flight = False

    def _record_failure(self) -> None:
        """Call this when a VLM call failed or returned garbage."""
        with self._breaker_state_lock:
            self._breaker_consecutive_failures += 1
            if self._breaker_half_open_in_flight:
                # Probe failed — re-arm the open timer.
                self._breaker_half_open_in_flight = False
                self._breaker_opened_at = time.time()
                logger.warning(
                    "VLM circuit breaker: half-open probe failed, remaining OPEN"
                )
                return
            if (not self._breaker_open
                and self._breaker_consecutive_failures >= self._BREAKER_OPEN_THRESHOLD):
                self._breaker_open = True
                self._breaker_opened_at = time.time()
                logger.error(
                    f"VLM circuit breaker: OPEN "
                    f"(after {self._breaker_consecutive_failures} consecutive failures) "
                    f"— degrading to synthetic 'unknown' appearances for "
                    f"~{self._BREAKER_HALF_OPEN_PROBE_SEC:.0f}s"
                )

    @staticmethod
    def _synthetic_unknown_appearance() -> dict:
        """Fallback appearance delivered when the breaker is open.

        Importantly: appearance_ready will be set to True on the cache entry,
        so the orchestrator's batch flush path does not wait for VLM anymore.
        The age_group is 'unknown' with 0.0 confidence, which downstream can
        treat as "no demographic info available".
        """
        return {
            "top": "",
            "bottom": "",
            "notable": "none",
            "age_group": "unknown",
            "age_confidence": 0.0,
            "_degraded": True,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_client(self) -> None:
        try:
            from ..utils.llm_client import build_chat_client
            client, model = build_chat_client(self.model)
        except Exception as e:
            logger.error(f"VLM client init failed; VLM disabled: {e}")
            self.enabled = False
            return
        if client is None:
            logger.warning("LLM_API_KEY / OPENAI_API_KEY not set; VLM observer disabled")
            self.enabled = False
            return
        self._client = client
        self.model = model  # for Azure this becomes the deployment name
        logger.info(f"VLMObserverWorker ready (model/deployment={self.model})")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            self._executor.submit(self._process, item)

    def _process(self, item: dict) -> None:
        # Queue-check branch (full frame, no person_id). Always calls back.
        if item.get("_task") == "queue_check":
            frame_b64 = item.get("frame_b64")
            queue_cb = item.get("_queue_cb")
            if not queue_cb:
                return
            confirmed = False
            if frame_b64:
                with self._semaphore:
                    confirmed = bool(self._call_queue_check(frame_b64))
            try:
                queue_cb(confirmed)
            except Exception as e:
                logger.error(f"VLM queue_cb error: {e}")
            return

        # Scene-observation branch (full frame, no person_id). Always calls back.
        if item.get("_task") == "scene_observe":
            frame_b64 = item.get("frame_b64")
            obs_cb = item.get("_obs_cb")
            if not obs_cb:
                return
            result: dict = {}
            if frame_b64:
                with self._semaphore:
                    result = self._call_scene_observation(frame_b64) or {}
            try:
                obs_cb(result)
            except Exception as e:
                logger.error(f"VLM obs_cb error: {e}")
            return

        person_id: int = item["person_id"]

        crop_b64_list: Optional[List[str]] = item.get("crop_b64_list")
        single_crop: Optional[str] = item.get("crop_b64")

        with self._semaphore:
            if crop_b64_list and len(crop_b64_list) > 1:
                # Multi-crop: stitch, save debug image, then send to VLM
                stitched_b64 = stitch_and_encode(crop_b64_list)
                if stitched_b64 is None:
                    logger.warning(f"VLM: failed to stitch {len(crop_b64_list)} crops for P{person_id}")
                    return

                # Save debug image
                stitched_cv2 = stitch_crops_horizontal(crop_b64_list)
                if stitched_cv2 is not None:
                    save_vlm_debug_image(stitched_cv2, person_id, label=f"{len(crop_b64_list)}frames")

                result = self._call_appearance(stitched_b64)
            else:
                crop_b64 = single_crop or (crop_b64_list[0] if crop_b64_list else None)
                if not crop_b64:
                    return

                # Save single crop debug image too
                from ..utils.image import decode_b64_to_cv2
                img = decode_b64_to_cv2(crop_b64)
                if img is not None:
                    save_vlm_debug_image(img, person_id, label="1frame")

                result = self._call_appearance(crop_b64)

        # Degraded-mode fallback: if the breaker is open (or a real call
        # failed), deliver a synthetic "unknown" appearance so the orchestrator
        # batch-flush path doesn't stall forever waiting for appearance_ready.
        if result is None and self._breaker_open:
            result = self._synthetic_unknown_appearance()
            logger.debug(f"VLM delivering synthetic fallback for P{person_id}")

        if result and self._appearance_cb:
            try:
                self._appearance_cb(person_id, result)
            except Exception as e:
                logger.error(f"VLM appearance_cb error for P{person_id}: {e}")

    _QUEUE_CHECK_PROMPT = (
        "This is a view of a room with a microphone/podium area. Is there at "
        "least one person waiting or lining up behind (or near) the person "
        "currently at the microphone, as if waiting for their turn? "
        'Answer with ONLY a JSON object: {"waiting": true} or {"waiting": false}.'
    )

    def _call_queue_check(self, frame_b64: str) -> bool:
        """Call VLM on a full frame to confirm someone is waiting behind the mic.

        Returns True only on a confident positive. Circuit-breaker aware; any
        failure / open breaker returns False (best-effort, no synthetic fallback).
        """
        if not self._client:
            return False
        if self._breaker_should_skip_call():
            return False

        self._call_count += 1
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": self._QUEUE_CHECK_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}", "detail": "low"}},
            ],
        }]
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=30,
                timeout=self.timeout,
            )
            raw = resp.choices[0].message.content or ""
            logger.info(f"VLM queue-check raw: {raw[:120]}")
            parsed = self._parse_response(raw)
            if parsed is None:
                self._error_count += 1
                self._record_failure()
                return False
            self._record_success()
            return bool(parsed.get("waiting", False))
        except Exception as e:
            self._error_count += 1
            self._record_failure()
            logger.warning(f"VLM queue-check call failed: {e}")
            return False

    _SCENE_OBSERVE_PROMPT = (
        "This is a view of a room with a microphone/podium area. Describe the "
        "current scene briefly and report whether anyone is waiting/queuing "
        "behind the person at the microphone. Return ONLY a JSON object: "
        '{"description": "<one short sentence>", "queue_behind": true|false}.'
    )

    def _call_scene_observation(self, frame_b64: str) -> Optional[dict]:
        """Call VLM on a full frame for a coarse environment read.

        Returns {description, queue_behind} or None on failure (circuit-breaker
        aware; open breaker returns None → caller falls back to geometry).
        """
        if not self._client:
            return None
        if self._breaker_should_skip_call():
            return None

        self._call_count += 1
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": self._SCENE_OBSERVE_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}", "detail": "low"}},
            ],
        }]
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=80,
                timeout=self.timeout,
            )
            raw = resp.choices[0].message.content or ""
            logger.info(f"VLM scene-observe raw: {raw[:160]}")
            parsed = self._parse_response(raw)
            if parsed is None:
                self._error_count += 1
                self._record_failure()
                return None
            self._record_success()
            return parsed
        except Exception as e:
            self._error_count += 1
            self._record_failure()
            logger.warning(f"VLM scene-observe call failed: {e}")
            return None

    def _call_appearance(self, crop_b64: str) -> Optional[dict]:
        """Call VLM with a person crop to get structured appearance description.

        Circuit-breaker aware: when open, returns None (caller delivers the
        synthetic-unknown fallback so batch flushes don't stall).
        """
        if not self._client:
            return None
        if self._breaker_should_skip_call():
            return None

        self._call_count += 1
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": self.appearance_prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{crop_b64}", "detail": self.appearance_detail}},
            ],
        }]
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=120,  # slightly higher now that age_group is in the JSON
                timeout=self.timeout,
            )
            raw = resp.choices[0].message.content or ""
            logger.info(f"VLM appearance raw: {raw[:200]}")
            parsed = self._parse_response(raw)
            if parsed is None:
                self._error_count += 1
                self._record_failure()
            else:
                self._record_success()
            return parsed
        except Exception as e:
            self._error_count += 1
            self._record_failure()
            logger.warning(f"VLM appearance call failed: {e}")
            return None

    @staticmethod
    def _parse_response(raw: str) -> Optional[dict]:
        """Extract JSON from the model response."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"VLM response not valid JSON: {raw[:200]}")
            return None
