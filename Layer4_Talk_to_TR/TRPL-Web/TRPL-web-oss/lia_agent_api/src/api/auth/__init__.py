# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Authentication helpers for lia-agent API."""

from .base import AuthError, require_auth

__all__ = [
    "AuthError",
    "require_auth",
]
