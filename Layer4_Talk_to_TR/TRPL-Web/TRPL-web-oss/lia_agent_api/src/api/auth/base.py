# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Base auth types and dependency factories for lia-agent API."""


class AuthError(Exception):
    """Custom exception for authentication errors."""

    pass


def require_auth():
    from .multi import require_auth as multi_require_auth

    return multi_require_auth
