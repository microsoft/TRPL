"""HTTP Basic authentication helpers."""

import logging
import secrets
from fastapi.security import HTTPBasicCredentials
from api.config import config
from .base import AuthError

logger = logging.getLogger(f"lia.{__name__}")

def authenticate_basic(credentials: HTTPBasicCredentials | None) -> dict:
    """Authenticate using basic auth and return user info."""
    # Use secrets.compare_digest for timing attack protection
    if not credentials:
        raise AuthError("No credentials provided")
    if not (
        secrets.compare_digest(credentials.username, config.basic_auth_username)
        and secrets.compare_digest(credentials.password, config.basic_auth_password)
    ):
        raise AuthError("Invalid credentials")

    logger.info(f"Basic auth successful for user: {credentials.username}")
    return {"username": credentials.username, "auth_mode": "basic"}
