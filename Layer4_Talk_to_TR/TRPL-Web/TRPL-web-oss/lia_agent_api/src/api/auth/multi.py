"""
Multi-method authentication module for lia-agent API.
Supports Basic Auth and API Key authentication.
"""

import logging
from fastapi import HTTPException, Header, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from api.config import config
from .base import AuthError
from .api_key import authenticate_api_key
from .basic import authenticate_basic

logger = logging.getLogger(f"lia.{__name__}")

# FastAPI security schemes
security_basic = HTTPBasic(auto_error=False)


def require_auth(
    x_api_key: str | None = Header(None, alias="x-api-key"),
    basic_credentials: HTTPBasicCredentials | None = Security(security_basic),
) -> dict:
    """
    Authenticate using any of the enabled authentication methods.
    Tries each enabled method in order until one succeeds.

    Args:
        x_api_key: API key from X-Api-Key header
        basic_credentials: Basic auth credentials

    Returns:
        dict: User claims from first successful authentication method

    Raises:
        HTTPException: If all enabled authentication methods fail
    """
    if not config.auth_modes:
        raise HTTPException(
            status_code=401, detail="No authentication methods are properly configured"
        )

    errors = []

    for auth_mode in config.auth_modes:
        try:
            logger.debug(f"Attempting {auth_mode} authentication")
            if auth_mode == "basic":
                result = authenticate_basic(basic_credentials)
                logger.info(f"Basic auth successful")
                return result
            elif auth_mode == "api_key":
                result = authenticate_api_key(x_api_key)
                logger.info(f"API key auth successful")
                return result
        except AuthError as e:
            errors.append(e)
            logger.debug(f"Authentication method '{auth_mode}' failed: {e}")

    # Build WWW-Authenticate header
    if "basic" in config.auth_modes:
        modes = "Basic"
    else:
        modes = "ApiKey"

    if errors:
        message = "\n".join(str(error) for error in errors)
    else:
        message = "Unauthenticated"

    raise HTTPException(
        status_code=401,
        detail=message,
        headers={"WWW-Authenticate": modes},
    )
