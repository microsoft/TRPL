# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from debate.models.sockets import (
    AudioControl,
    AudioIdle,
    InboundSocketMessage,
    Ping,
    inbound_socket_message_adapter,
)

logger = logging.getLogger(f"lia.{__name__}")

SocketRole = Literal["controller", "observer"]

ROLE_ALLOWED_MESSAGE_TYPES: dict[SocketRole, set[str]] = {
    "controller": {
        "participant_input",
        "participant_reaction",
        "participant_info",
        "participant_joined",
        "start_debate",
        "start_intro",
        "ping",
        "audio_idle",
        "audio_control",
        "user_interrupt",
    },
    "observer": {
        "ping",
        "audio_idle",
        "audio_control",
    },
}


class IngressResult(BaseModel):
    role: SocketRole
    parsed: InboundSocketMessage | None = None
    enqueue_message: InboundSocketMessage | None = None
    pong: bool = False
    audio_enabled: bool | None = None
    audio_idle: bool = False
    error: dict[str, Any] | None = None


def process_inbound_message(
    raw_message: Any,
    *,
    role: SocketRole,
    session_id: str,
) -> IngressResult:
    result = IngressResult(role=role)

    if not isinstance(raw_message, dict):
        result.error = {"type": "error", "error": "invalid_message_format"}
        return result

    try:
        parsed = inbound_socket_message_adapter.validate_python(raw_message)
    except ValidationError as exc:
        logger.warning(
            "Invalid websocket payload for role '%s' in session %s: %s",
            role,
            session_id,
            exc.errors(),
        )
        result.error = {
            "type": "error",
            "error": "invalid_message_payload",
            "details": exc.errors(),
        }
        return result

    msg_type = parsed.type
    allowed_types = ROLE_ALLOWED_MESSAGE_TYPES[role]
    if msg_type not in allowed_types:
        logger.warning(
            "Rejected websocket message type '%s' for role '%s' in session %s",
            msg_type,
            role,
            session_id,
        )
        result.error = {
            "type": "error",
            "error": "forbidden_message_type",
            "message_type": msg_type,
            "role": role,
        }
        return result

    result.parsed = parsed

    if isinstance(parsed, Ping):
        result.pong = True
        return result

    if isinstance(parsed, AudioControl):
        result.audio_enabled = parsed.enabled
        return result

    if isinstance(parsed, AudioIdle):
        result.audio_idle = True
        return result

    # Forward validated/typed message to session-level input processor.
    result.enqueue_message = parsed
    return result
