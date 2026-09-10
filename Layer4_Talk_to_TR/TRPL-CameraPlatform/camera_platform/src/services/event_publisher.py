"""
Event Publisher — unified outbound channel for camera service → agent server.

All events (both low-level person events and high-level scene events) are
wrapped in a standardized envelope and POST'd to the agent server with:

  - 3-retry exponential backoff (1s / 2s / 4s) for transient failures
  - Rolling JSONL append to `event_log/<YYYY-MM-DD>.jsonl` for audit and
    post-crash recovery (one file per UTC day, unbounded in count but
    each day is a separate file for easy truncation)
  - Per-category counters surfaced via published_count / failed_count

The publish path stays non-blocking (submitted to a thread pool).
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests

from ..utils.config import AGENT_SERVER_CONFIG

logger = logging.getLogger(__name__)

_MAX_PUBLISHER_WORKERS = 4
_DEFAULT_LOG_DIR = Path(__file__).parent.parent.parent / "event_log"


def _redact_large(obj, maxlen: int = 200):
    """Recursively replace long strings (e.g. base64 crops/thumbnails) with a
    short placeholder, so the /monitor tap stays small and readable. The real
    WS stream still carries the full data — this only affects the monitor view."""
    if isinstance(obj, dict):
        return {k: _redact_large(v, maxlen) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_large(v, maxlen) for v in obj]
    if isinstance(obj, str) and len(obj) > maxlen:
        return f"<{len(obj)} chars omitted>"
    return obj


class EventPublisher:
    """
    Two entry points:
      publish(event_type, payload) — scene events from orchestrator
      forward(event_dict)          — low-level EventManager events
    Both are non-blocking.

    Transport (config AGENT_SERVER_CONFIG["transport"] / env EVENT_TRANSPORT):
      - "ws" (default): camera HOSTS a WebSocket server. Every event is pushed
        onto a bounded consume-once FIFO stack; a downstream client connects in
        and drains it. Nothing is "sent" until a client reads — events buffer
        in the stack meanwhile.
      - "http": legacy. Each event is POSTed to AGENT_SERVER_URL with 3× retry.

    Reliability (both transports):
      - Every envelope is appended to a rolling daily JSONL file BEFORE delivery,
        so crashes / downstream downtime never lose events (durable backstop).
    """

    _MAX_RETRIES = 3
    _RETRY_BACKOFFS = (1.0, 2.0, 4.0)  # seconds between attempts

    def __init__(self):
        cfg = AGENT_SERVER_CONFIG
        self._url = cfg["url"].rstrip("/")
        self._api_key = cfg.get("api_key", "")
        self._timeout = cfg.get("timeout", 5.0)
        self._enabled = cfg.get("enabled", True)
        self._transport = cfg.get("transport", "ws").lower()
        # Event types we never output downstream (e.g. room-leave signals — we
        # only emit MIC_ZONE_LEFT, not person_left).
        self._suppressed = set(cfg.get("suppress_event_types", []))
        self._executor = ThreadPoolExecutor(
            max_workers=_MAX_PUBLISHER_WORKERS,
            thread_name_prefix="event-pub",
        )

        # JSONL audit log
        self._log_dir = Path(os.environ.get("EVENT_LOG_DIR", str(_DEFAULT_LOG_DIR)))
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"EventPublisher: cannot create log dir {self._log_dir}: {e}")
        self._log_lock = threading.Lock()

        # Counters (lock-free; single writer per worker but reads are eventual)
        self._published_count: int = 0
        self._failed_count: int = 0
        self._retried_count: int = 0

        # --- WebSocket transport state (consume-once FIFO event stack) ---
        # Downstream connects in and drains this stack; events buffer here while
        # no client is reading (bounded — oldest drop past stack_maxlen, but the
        # JSONL audit log keeps a full record regardless).
        self._stack: deque = deque(maxlen=cfg.get("stack_maxlen", 1000))
        self._stack_lock = threading.Lock()
        self._ws_host = cfg.get("ws_host", "0.0.0.0")
        self._ws_port = int(cfg.get("ws_port", 8765))
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_client_count: int = 0

        # Live monitor tap: a bounded ring of recently-emitted envelopes that the
        # /monitor page polls. Independent of the consume-once stack, so viewing
        # it never steals events from the real downstream consumer.
        self._recent: deque = deque(maxlen=500)
        self._recent_seq: int = 0
        self._recent_lock = threading.Lock()

        # Kiosk session attachment only applies to the legacy HTTP transport,
        # where every POST must carry the session_id so lia routes it to the
        # kiosk session. In WS mode the downstream connects to us and reads the
        # whole stack, so no session lookup is needed.
        self._session_id: str | None = (
            self._fetch_kiosk_session_id() if self._transport == "http" else None
        )

        if self._enabled and self._transport == "ws":
            self._start_ws_server()

    def publish(self, event_type: str, group_id: str, payload: Dict[str, Any]) -> None:
        """Publish a semantic scene event (called by SceneOrchestrator)."""
        if not self._enabled:
            return
        envelope = {
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "camera_service",
            "event_type": event_type,
            "payload": payload,
            "session_id": self._session_id,
        }
        self._emit(envelope)

    def forward(self, event_dict: Dict[str, Any]) -> None:
        """Forward a raw EventManager event dict to the agent server."""
        if not self._enabled:
            return
        envelope = {
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "camera_service",
            "event_type": event_dict.get("event_type", "unknown"),
            "payload": event_dict,
            "session_id": self._session_id,
        }
        self._emit(envelope)

    def _emit(self, envelope: dict) -> None:
        """Record the envelope to the JSONL audit log, then deliver it via the
        configured transport (WS stack push, or legacy HTTP POST)."""
        # Suppressed event types are not output at all (no audit, no monitor,
        # no delivery) — they're handled internally upstream for state only.
        if envelope.get("event_type") in self._suppressed:
            return
        self._append_jsonl(envelope)
        with self._recent_lock:
            self._recent_seq += 1
            self._recent.append((self._recent_seq, envelope))
        if self._transport == "ws":
            # Consume-once FIFO stack — a connected downstream client drains it.
            with self._stack_lock:
                self._stack.append(envelope)
        else:
            self._executor.submit(self._post_with_retry, envelope)

    def shutdown(self, wait: bool = True, timeout: float = 5.0) -> None:
        """Drain pending events and shut down the thread pool / WS server."""
        self._executor.shutdown(wait=wait)
        loop = self._ws_loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        logger.info("EventPublisher shut down")

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def published_count(self) -> int:
        return self._published_count

    def failed_count(self) -> int:
        return self._failed_count

    def retried_count(self) -> int:
        return self._retried_count

    def pending_count(self) -> int:
        """Events not yet delivered.

        WS mode: events sitting in the consume-once stack (no client draining,
        or arriving faster than the client reads). HTTP mode: queued POSTs.
        """
        if self._transport == "ws":
            with self._stack_lock:
                return len(self._stack)
        q = getattr(self._executor, "_work_queue", None)
        return q.qsize() if q is not None else 0

    def ws_client_count(self) -> int:
        """Number of downstream WebSocket clients currently connected."""
        return self._ws_client_count

    def recent_events(self, after_seq: int = 0, limit: int = 300) -> Dict[str, Any]:
        """Read-only tap of recently-emitted envelopes for the /monitor page.

        Returns events with seq > after_seq (poll cursor), the latest seq, and
        live delivery stats. Reads the in-memory ring only — never the
        consume-once stack — so monitoring does not divert events from the real
        downstream consumer.
        """
        with self._recent_lock:
            items = [(s, e) for (s, e) in self._recent if s > after_seq]
            latest = self._recent_seq
        if len(items) > limit:
            items = items[-limit:]
        return {
            "events": [dict(_redact_large(e), _seq=s) for s, e in items],
            "latest_seq": latest,
            "stats": {
                "published": self._published_count,
                "buffered": self.pending_count(),
                "ws_clients": self._ws_client_count,
                "transport": self._transport,
            },
        }

    # ------------------------------------------------------------------
    # WebSocket server (transport="ws")
    # ------------------------------------------------------------------

    def _start_ws_server(self) -> None:
        """Run an asyncio WebSocket server in a daemon thread. Downstream
        clients connect and drain the consume-once event stack."""
        try:
            import websockets  # noqa: F401  (import early to fail fast / clearly)
        except ImportError:
            logger.error(
                "EventPublisher: transport='ws' but the 'websockets' package is "
                "not installed — no events will be delivered. "
                "pip install websockets, or set EVENT_TRANSPORT=http."
            )
            return

        def _run() -> None:
            loop = asyncio.new_event_loop()
            self._ws_loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._serve_ws())
                loop.run_forever()
            except Exception as e:
                logger.error(f"EventPublisher: WS server stopped: {e}")
            finally:
                loop.close()

        threading.Thread(target=_run, daemon=True, name="ws-event-server").start()
        logger.info(
            f"EventPublisher: WebSocket server starting on "
            f"ws://{self._ws_host}:{self._ws_port} (consume-once stack)"
        )

    async def _serve_ws(self) -> None:
        import websockets
        # websockets >= 11: serve() handler takes a single (websocket) arg.
        await websockets.serve(self._ws_handler, self._ws_host, self._ws_port)

    async def _ws_handler(self, websocket) -> None:
        """Stream events to one connected downstream client, consume-once.

        Peek-then-pop-on-success: an event is only removed from the stack after
        it has been sent, so a disconnect mid-send leaves it for the next
        (re)connecting client rather than dropping it.
        """
        self._ws_client_count += 1
        peer = getattr(websocket, "remote_address", "?")
        logger.info(f"EventPublisher: downstream WS client connected ({peer})")
        try:
            while True:
                with self._stack_lock:
                    envelope = self._stack[0] if self._stack else None
                if envelope is None:
                    await asyncio.sleep(0.05)
                    continue
                await websocket.send(json.dumps(envelope, ensure_ascii=False, default=str))
                with self._stack_lock:
                    if self._stack and self._stack[0] is envelope:
                        self._stack.popleft()
                self._published_count += 1
        except Exception as e:
            logger.info(f"EventPublisher: WS client {peer} disconnected: {type(e).__name__}")
        finally:
            self._ws_client_count = max(0, self._ws_client_count - 1)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_jsonl(self, envelope: dict) -> None:
        """Append envelope to today's JSONL audit log.

        Writes happen on the caller thread (before executor submit) so even
        if the process crashes immediately after the submit, the event is on
        disk. Performance cost is a single flush per event — fine at event
        rates well under ~1000/sec.
        """
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = self._log_dir / f"{today}.jsonl"
            line = json.dumps(envelope, ensure_ascii=False, default=str)
            with self._log_lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            logger.warning(f"EventPublisher: JSONL append failed: {e}")

    def _fetch_kiosk_session_id(self) -> str | None:
        """One-shot lookup of the current kiosk session_id from the agent
        server's admin endpoint. Three short attempts; failure is silent
        (logged at WARNING) and returns None so the publisher keeps working.
        """
        if not self._enabled:
            return None
        url = f"{self._url}/api/admin/kiosk-session"
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=self._timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    sid = data.get("session_id")
                    if sid:
                        logger.info(f"EventPublisher: kiosk session_id={sid}")
                        return sid
                    logger.warning(
                        "EventPublisher: kiosk-session endpoint returned no "
                        "session_id; falling back to server's heuristic routing"
                    )
                    return None
                logger.warning(
                    f"EventPublisher: kiosk-session lookup HTTP "
                    f"{resp.status_code}: {resp.text[:120]}"
                )
            except Exception as e:
                logger.warning(
                    f"EventPublisher: kiosk-session lookup failed "
                    f"(attempt {attempt + 1}/3): {type(e).__name__}: {e}"
                )
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
        return None

    def _post_with_retry(self, envelope: dict) -> None:
        """POST with exponential backoff. Runs inside the worker thread pool.

        On HTTP 404 we treat the session_id as stale (likely livekit_worker_kiosk
        restarted and minted a fresh session) and re-fetch once before retrying.
        """
        last_err = None
        refreshed = False
        for attempt in range(self._MAX_RETRIES):
            try:
                resp = requests.post(
                    f"{self._url}/api/camera/events",
                    json=envelope,
                    headers=(
                        {"X-API-Key": self._api_key}
                        if self._api_key
                        else {}
                    ),
                    timeout=self._timeout,
                )
                if resp.status_code in (200, 201, 202):
                    self._published_count += 1
                    if attempt > 0:
                        self._retried_count += 1
                    return
                if resp.status_code == 404 and envelope.get("session_id") and not refreshed:
                    # Stale session_id: refresh once and retry on the next loop.
                    new_sid = self._fetch_kiosk_session_id()
                    if new_sid and new_sid != envelope.get("session_id"):
                        logger.info(
                            f"EventPublisher: refreshed session_id "
                            f"{envelope['session_id']} -> {new_sid}"
                        )
                        self._session_id = new_sid
                        envelope["session_id"] = new_sid
                    refreshed = True
                last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except requests.exceptions.ConnectionError as e:
                last_err = f"connection refused: {e}"
            except requests.exceptions.Timeout as e:
                last_err = f"timeout: {e}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

            # Don't sleep after the last attempt
            if attempt < self._MAX_RETRIES - 1:
                time.sleep(self._RETRY_BACKOFFS[attempt])

        self._failed_count += 1
        logger.warning(
            f"EventPublisher: dropped event {envelope.get('event_type')} "
            f"after {self._MAX_RETRIES} attempts — {last_err}"
        )
