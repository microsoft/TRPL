"""
Authentication middleware for the Reading Room API
"""

import hmac
import logging
from typing import Optional

from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends

from common_config import RAG_API_KEY

logger = logging.getLogger(__name__)

# Security scheme for Bearer token authentication
security = HTTPBearer(auto_error=False)


async def verify_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    """
    Verify the API key from Authorization header.

    Expects: Authorization: Bearer <RAG_API_KEY>

    Returns:
        str: The verified API key

    Raises:
        HTTPException: If authentication fails
    """
    if not credentials:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.scheme.lower() != "bearer":
        logger.warning(f"Invalid auth scheme: {credentials.scheme}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Expected Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(credentials.credentials, RAG_API_KEY):
        logger.warning("Invalid API key provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("API key authentication successful")
    return credentials.credentials


async def verify_websocket_auth(token: str) -> bool:
    """
    Verify API key for WebSocket connections.

    Args:
        token: The API key token

    Returns:
        bool: True if authentication is successful
    """
    if not token:
        logger.warning("WebSocket: Missing token")
        return False

    if not hmac.compare_digest(token, RAG_API_KEY):
        logger.warning("WebSocket: Invalid API key")
        return False

    logger.info("WebSocket: API key authentication successful")
    return True
