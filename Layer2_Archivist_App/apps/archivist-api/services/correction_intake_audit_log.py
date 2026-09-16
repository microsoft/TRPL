# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Structured audit logs for POST /correction-requests (Log Analytics / KQL friendly).

One JSON object per line on logger correction.intake.audit (stdout only for that logger).
Fields: timestamp, event, caller_app_id, record_guid, outcome, reason, http_status, request_id.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

_logger = logging.getLogger("correction.intake.audit")

Outcome = Literal["accepted", "rejected"]


def emit_correction_intake_audit(
    *,
    caller_app_id: Optional[str],
    record_guid: Optional[str],
    outcome: Outcome,
    reason: str,
    http_status: int,
) -> None:
    """Emit a single JSON log line; internal detail belongs in reason / server logs elsewhere."""
    try:
        from core.request_context import get_request_id

        rid = get_request_id() or None
    except Exception:
        rid = None

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "correction_intake",
        "caller_app_id": caller_app_id,
        "record_guid": (record_guid or "").strip() or None,
        "outcome": outcome,
        "reason": reason,
        "http_status": http_status,
        "request_id": rid,
    }
    _logger.info("%s", json.dumps(payload, ensure_ascii=False, default=str))
