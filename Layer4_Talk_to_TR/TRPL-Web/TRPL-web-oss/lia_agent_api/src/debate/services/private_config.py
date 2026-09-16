# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Load deployment-only configuration from environment variables or files."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class PrivateConfigError(RuntimeError):
    """Raised when required deployment-only configuration is unavailable."""


def _load_value(env_name: str, file_env_name: str) -> str:
    inline_value = os.getenv(env_name)
    file_name = os.getenv(file_env_name)

    if inline_value is not None and file_name:
        raise PrivateConfigError(
            f"Set only one of {env_name} or {file_env_name}, not both."
        )
    if inline_value is not None:
        if not inline_value.strip():
            raise PrivateConfigError(f"{env_name} must not be empty.")
        return inline_value
    if file_name:
        path = Path(file_name).expanduser()
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PrivateConfigError(
                f"Unable to read private configuration from {path}."
            ) from exc
        if not value.strip():
            raise PrivateConfigError(f"Private configuration file {path} is empty.")
        return value
    raise PrivateConfigError(
        f"Set {env_name} or {file_env_name} to deployment-only configuration."
    )


def load_private_text(env_name: str, file_env_name: str) -> str:
    """Load required private text without providing a public fallback."""
    return _load_value(env_name, file_env_name)


def load_private_json(
    env_name: str,
    file_env_name: str,
    expected_type: type,
) -> Any:
    """Load and type-check required private JSON configuration."""
    raw_value = _load_value(env_name, file_env_name)
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise PrivateConfigError(
            f"{env_name}/{file_env_name} must contain valid JSON."
        ) from exc
    if not isinstance(value, expected_type):
        raise PrivateConfigError(
            f"{env_name}/{file_env_name} must contain a JSON "
            f"{expected_type.__name__}."
        )
    return value
