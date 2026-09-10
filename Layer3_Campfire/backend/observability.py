"""PII-scrubbed JSON logging, request-id context, OpenTelemetry tracer access.

PII detection and redaction are delegated to Microsoft Presidio. We call each
`PatternRecognizer` directly with `nlp_artifacts=None`, which keeps detection
on Presidio's built-in regex/checksum patterns and skips the spaCy NLP engine
— recognizers that rely on context-word boosting (e.g. US SSN) will
under-detect; that's the tradeoff for avoiding a spaCy model download.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    PhoneRecognizer,
    UsSsnRecognizer,
)
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Set per HTTP request (middleware) or per WS message; read by the JSON
# formatter so logs in the request scope are correlatable.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Snapshot the attribute names a freshly-constructed LogRecord carries — these
# are stdlib internals (pathname, funcName, levelno, etc.). Caller-supplied
# `extra=` keys land on the same `__dict__`, so we filter against this set to
# keep stdlib noise out of the JSON output. Built dynamically so we don't have
# to maintain a list in sync with the stdlib across Python versions.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())

_RECOGNIZERS = (
    EmailRecognizer(),
    CreditCardRecognizer(),
    IbanRecognizer(),
    PhoneRecognizer(),
    UsSsnRecognizer(),
)
_ANONYMIZER = AnonymizerEngine()
_OPERATORS = {"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})}


def scrub(text: str) -> str:
    """Pass a string through Presidio; return the scrubbed version."""
    if not text:
        return text
    results: list = []
    for r in _RECOGNIZERS:
        results.extend(r.analyze(text, entities=r.supported_entities, nlp_artifacts=None))  # pyright: ignore[reportArgumentType]
    if not results:
        return text
    return _ANONYMIZER.anonymize(text=text, analyzer_results=results, operators=_OPERATORS).text


class PiiFilter(logging.Filter):
    """Scrub PII from every string attribute on the record. Renders `msg % args`
    once up front so PII in positional args isn't missed when the loop hits the
    format string. Stdlib attrs (pathname, funcName, etc.) get scrubbed too —
    Presidio's fast path returns them unchanged when no PII is detected."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = None
        for k, v in list(record.__dict__.items()):
            if isinstance(v, str):
                record.__dict__[k] = scrub(v)
        return True


# Shared instance — adding twice to the same handler is a no-op (filter
# membership uses identity).
PII_FILTER = PiiFilter()


class OtelDetachContextFilter(logging.Filter):
    """OTel's `context.detach()` catches the underlying exception and logs a
    bare 'Failed to detach context' error with no `exc_info`, making the
    resulting telemetry impossible to root-cause (open-telemetry/opentelemetry-
    python#2606, open since 2022). The filter runs synchronously inside the
    `except` block, so `sys.exc_info()` still holds the swallowed exception —
    we reattach it to the record so the traceback surfaces locally and in App
    Insights."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is None and record.getMessage() == "Failed to detach context":
            exc_info = sys.exc_info()
            if exc_info[0] is not None:
                record.exc_info = exc_info
        return True


OTEL_DETACH_FILTER = OtelDetachContextFilter()


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Adds request_id from the contextvar and
    OTel trace/span ids when a span is active."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        tid, sid = _current_trace_ids()
        if tid:
            payload["trace_id"] = tid
            payload["span_id"] = sid
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_") and k not in payload:
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


def _current_trace_ids() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx.is_valid:
            return None, None
        return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
    except Exception:
        return None, None


def get_tracer(name: str):
    from opentelemetry import trace

    return trace.get_tracer(name)


_scope_logger = logging.getLogger(__name__)


@dataclass
class RequestScope:
    """Mutable handle yielded by `request_scope`. Mutate `extra` to add
    per-call dimensions (status, chunk_count, etc.) before the block exits;
    they're merged into the single log event emitted on close."""

    request_id: str
    extra: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def request_scope(
    event_name: str,
    *,
    inbound_request_id: str | None = None,
    base_extra: dict[str, Any] | None = None,
    log_level: int = logging.INFO,
) -> AsyncIterator[RequestScope]:
    """Shared instrumentation for HTTP requests, WS connections, and WS
    messages. Resolves a request_id (honoring `inbound_request_id` when
    supplied), binds it to the current OTel span and the `request_id_var`
    contextvar, times the block, and emits exactly one `event_name` log
    record on exit — including when the block raises. Nest freely:
    contextvar tokens restore the outer scope's id when an inner scope
    exits, so a per-message scope inside a per-connection scope correctly
    reverts the contextvar to the connection's id between messages.
    """
    request_id = inbound_request_id or uuid4().hex
    token = request_id_var.set(request_id)

    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("request_id", request_id)
            if inbound_request_id:
                span.set_attribute("request_id.inbound", True)
    except Exception:
        pass

    start = time.perf_counter()
    scope = RequestScope(request_id=request_id, extra=dict(base_extra or {}))
    try:
        yield scope
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        extra = {
            "microsoft.custom_event.name": event_name,
            "duration_ms": round(duration_ms, 2),
            **scope.extra,
        }
        _scope_logger.log(log_level, event_name, extra=extra)
        request_id_var.reset(token)
