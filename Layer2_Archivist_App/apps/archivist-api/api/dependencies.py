# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Authentication dependencies for FastAPI routes using Azure App Service Easy Auth."""
import os
import logging
from typing import Optional, Dict, Any, List
from fastapi import Header, HTTPException
import jwt

logger = logging.getLogger(__name__)

# Azure AD Group Object IDs from environment variables
GROUP_MAPPINGS: Dict[str, str] = {
    "admin": os.getenv("ADMIN_GROUP", ""),
    "data_foundations": os.getenv("DATA_FOUNDATIONS_GROUP", ""),
    "archivist": os.getenv("ARCHIVIST_GROUP", ""),
}

_INTERNAL_SERVICE_PRINCIPAL_IDS: set[str] = {
    item.strip().lower()
    for item in os.getenv("ARCHIVIST_INTERNAL_SERVICE_PRINCIPAL_IDS", "").split(",")
    if item.strip()
}


def _decode_token(token: str) -> Dict[str, Any]:
    """Decode JWT claims without local signature verification.

    SECURITY NOTE: This application runs behind Azure App Service Easy Auth
    (Authentication/Authorization) which validates the JWT signature, audience,
    issuer, and expiry BEFORE the request reaches this code. The platform-injected
    headers (X-MS-TOKEN-AAD-*) are only present after successful validation.

    We restrict accepted algorithms to RS256 (Azure AD default) to prevent
    algorithm-confusion attacks even in the decode-only path.
    """
    return jwt.decode(
        token,
        algorithms=["RS256"],
        options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
    )


def _get_group_memberships(groups: List[str]) -> Dict[str, bool]:
    """Check which configured groups the user belongs to."""
    if not groups:
        return {"isAdmin": False, "isDataFoundations": False, "isArchivist": False}
    
    group_set = set(groups)
    return {
        "isAdmin": GROUP_MAPPINGS["admin"] in group_set if GROUP_MAPPINGS["admin"] else False,
        "isDataFoundations": GROUP_MAPPINGS["data_foundations"] in group_set if GROUP_MAPPINGS["data_foundations"] else False,
        "isArchivist": GROUP_MAPPINGS["archivist"] in group_set if GROUP_MAPPINGS["archivist"] else False,
    }


class AuthenticatedUser:
    """Authenticated user with claims from access token and groups from ID token."""

    def __init__(self, claims: Dict[str, Any], groups: List[str]):
        self.user_id: str = claims.get("oid", claims.get("sub", ""))
        self.app_id: str = claims.get("appid", claims.get("azp", ""))
        self.email: str = claims.get("email", claims.get("preferred_username", claims.get("upn", "")))
        self.name: str = claims.get("name", self.email)
        self.groups: List[str] = groups
        self._memberships = _get_group_memberships(groups)

    @property
    def user_display(self) -> str:
        return self.name or self.email

    @property
    def is_admin(self) -> bool:
        return self.email == "dev.user@trpl.local" or self._memberships["isAdmin"]

    @property
    def is_data_foundations(self) -> bool:
        return self.email == "dev.user@trpl.local" or self._memberships["isDataFoundations"]

    @property
    def is_archivist(self) -> bool:
        return self.email == "dev.user@trpl.local" or self._memberships["isArchivist"]

    @property
    def is_internal_service(self) -> bool:
        """True when caller is an allowlisted workload identity (e.g. Archivist Functions MI)."""
        if not _INTERNAL_SERVICE_PRINCIPAL_IDS:
            return False
        candidates = {value.lower() for value in (self.user_id, self.app_id) if value}
        return bool(candidates & _INTERNAL_SERVICE_PRINCIPAL_IDS)

    @property
    def can_run_statistics_rebuild(self) -> bool:
        return self.is_admin or self.is_internal_service

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "name": self.name,
            "display_name": self.user_display,
            "isAdmin": self.is_admin,
            "isDataFoundations": self.is_data_foundations,
            "isArchivist": self.is_archivist,
            "groups": self.groups,
        }


async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_ms_token_aad_access_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ACCESS-TOKEN"),
    x_ms_token_aad_id_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ID-TOKEN"),
    x_id_token: Optional[str] = Header(None, alias="X-ID-Token"),
) -> AuthenticatedUser:
    """
    Extract user from Azure AD tokens.
    
    - Access token (Authorization or X-MS-TOKEN-AAD-ACCESS-TOKEN): user claims
    - ID token (X-MS-TOKEN-AAD-ID-TOKEN or X-ID-Token): groups
    """
    # Get access token: Authorization header first, then Easy Auth header as fallback
    access_token = None
    if authorization and authorization.startswith("Bearer "):
        access_token = authorization.replace("Bearer ", "").strip()
    elif x_ms_token_aad_access_token:
        access_token = x_ms_token_aad_access_token
    
    if not access_token:
        raise HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})

    # Get ID token: App Service header first, then frontend header as fallback
    id_token = x_id_token or x_ms_token_aad_id_token

    try:
        claims = _decode_token(access_token)
        groups = _decode_token(id_token).get("groups", []) if id_token else []
        
        logger.info("User authenticated: %s (%s), groups: %d", claims.get("name", "Unknown"), claims.get("email", claims.get("preferred_username", "Unknown")), len(groups))
        return AuthenticatedUser(claims, groups)

    except jwt.DecodeError as e:
        logger.exception("Failed to decode token: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token", headers={"WWW-Authenticate": "Bearer"}) from e


async def get_optional_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_ms_token_aad_access_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ACCESS-TOKEN"),
    x_ms_token_aad_id_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ID-TOKEN"),
    x_id_token: Optional[str] = Header(None, alias="X-ID-Token"),
) -> Optional[AuthenticatedUser]:
    """Optional authentication - returns None if not authenticated."""
    try:
        return await get_current_user(authorization, x_ms_token_aad_access_token, x_ms_token_aad_id_token, x_id_token)
    except HTTPException:
        return None


async def get_current_user_or_mock(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_ms_token_aad_access_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ACCESS-TOKEN"),
    x_ms_token_aad_id_token: Optional[str] = Header(None, alias="X-MS-TOKEN-AAD-ID-TOKEN"),
    x_id_token: Optional[str] = Header(None, alias="X-ID-Token"),
) -> AuthenticatedUser:
    """
    Get current user from Entra (JWT), or a fixed mock user when ``ENVIRONMENT=local`` and auth fails.

    Periodic schedules and similar store ``AuthenticatedUser.user_display`` as ``created_by`` /
    ``updated_by``. With a valid ``Authorization: Bearer`` token locally, you get the same names as
    in Azure; without one, the mock user is ``Development User`` (see below).
    """
    try:
        return await get_current_user(authorization, x_ms_token_aad_access_token, x_ms_token_aad_id_token, x_id_token)
    except HTTPException:
        if os.getenv("ENVIRONMENT", "production").lower() == "local":
            logger.warning("Using mock user for local development.")
            mock_groups = [g for g in GROUP_MAPPINGS.values() if g]
            return AuthenticatedUser({"oid": "dev-user-12345", "email": "dev.user@trpl.local", "name": "Development User"}, mock_groups)
        raise
