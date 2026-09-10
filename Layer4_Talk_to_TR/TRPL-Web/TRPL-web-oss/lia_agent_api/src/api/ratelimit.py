# -*- coding: utf-8 -*-
"""Minimal, dependency-free per-client sliding-window rate limiter.

Kept intentionally simple so the project runs out-of-the-box without an extra
dependency (e.g. slowapi/redis). State is in-process, so limits are per-worker;
for multi-worker deployments treat these as a coarse abuse backstop and put a
real limiter at the ingress if you need global guarantees.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` per ``window_seconds`` per key."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


def client_ip(request: Request) -> str:
    """Best-effort client IP, honoring a single proxy hop via X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(limiter: SlidingWindowRateLimiter, request: Request) -> None:
    """Raise HTTP 429 if ``request``'s client has exceeded ``limiter``."""
    if not limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests — slow down.")
