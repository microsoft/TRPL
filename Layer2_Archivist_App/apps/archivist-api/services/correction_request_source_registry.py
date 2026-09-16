# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Map Entra application (client) ids to allowlisted source_app_id + display_name."""
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.config import settings


@dataclass(frozen=True)
class SourceRegistration:
    """Allowlisted downstream source identity for correction intake."""

    source_app_id: str
    display_name: str


def resolve_source_for_client_app(entra_client_app_id: str) -> Optional[SourceRegistration]:
    """
    Look up registration for the caller's Entra application id (azp / appid claim).

    Keys in CORRECTION_INTAKE_SOURCE_MAP are matched case-insensitively.
    """
    if not entra_client_app_id:
        return None
    key = entra_client_app_id.strip().lower()
    raw: Dict[str, Any] = settings.correction_intake_source_map or {}
    entry = raw.get(key)
    if not entry or not isinstance(entry, dict):
        return None
    sid = entry.get("source_app_id")
    name = entry.get("display_name")
    if not sid or not name:
        return None
    return SourceRegistration(source_app_id=str(sid), display_name=str(name))
