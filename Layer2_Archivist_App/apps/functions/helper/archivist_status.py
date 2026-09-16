# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical archivist_status values: trimmed lowercase in Cosmos (aligned with Data Foundations ingest)."""

from typing import Any


def normalize_archivist_status(value: Any) -> str:
    """Lowercase string for archivist_status; missing or blank becomes 'pending'."""
    if value is None:
        return "pending"
    if isinstance(value, str):
        s = value.strip().lower()
        return s if s else "pending"
    s = str(value).strip().lower()
    return s if s else "pending"
