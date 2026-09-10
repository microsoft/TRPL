"""Per-request context (correlation / request id) for logging without leaking internals to clients."""
from __future__ import annotations

from contextvars import ContextVar

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    _request_id_ctx.set(request_id or "")


def get_request_id() -> str:
    return _request_id_ctx.get() or ""
