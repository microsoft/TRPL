# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Threat detection for visitor input."""
import logging

from debate.services.private_config import load_private_json

logger = logging.getLogger(f"lia.{__name__}")

_THREAT_PATTERNS = frozenset(
    str(pattern).strip().lower()
    for pattern in load_private_json(
        "LIA_THREAT_PATTERNS",
        "LIA_THREAT_PATTERNS_FILE",
        list,
    )
    if str(pattern).strip()
)
if not _THREAT_PATTERNS:
    raise ValueError("At least one private threat pattern is required.")


def is_threat(text: str) -> bool:
    """Check if visitor input contains threatening language."""
    lower = text.lower()
    return any(pattern in lower for pattern in _THREAT_PATTERNS)


async def emit_threat_alert(io, text: str) -> None:
    """Push an alert to the staff dashboard. Conversation continues."""
    await io.output.send_debug(
        agent="THREAT_ALERT",
        content=f"Threatening language detected from visitor: {text[:200]}",
        phase="storys",
    )
    logger.warning("[Storys] THREAT DETECTED — alert sent, conversation continues")
