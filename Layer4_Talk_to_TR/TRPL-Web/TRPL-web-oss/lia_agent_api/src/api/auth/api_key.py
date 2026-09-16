# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API key authentication helpers."""

import secrets

from api.config import config
from .base import AuthError


def authenticate_api_key(x_api_key: str | None) -> dict:
    """
    Authenticate using API key from X-Api-Key header.

    Args:
        x_api_key: API key from X-Api-Key header

    Returns:
        dict: User claims

    Raises:
        AuthError: If API key is missing or invalid
    """
    if not config.client_api_keys:
        raise AuthError("API key authentication not configured")

    if not x_api_key:
        raise AuthError("API key required")

    # Constant-time comparison against each configured key to avoid leaking
    # key material through response-timing differences.
    if not any(secrets.compare_digest(x_api_key, key) for key in config.client_api_keys):
        raise AuthError("Invalid API key")

    return {"auth_mode": "api_key"}
