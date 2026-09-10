"""Workload (Entra) JWT validation for correction-request intake."""
import logging
import os
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from core.config import settings
from services.correction_intake_audit_log import emit_correction_intake_audit
from services.correction_request_source_registry import (
    SourceRegistration,
    resolve_source_for_client_app,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntakeCaller:
    """Resolved downstream caller after auth + allowlist."""

    entra_client_app_id: str
    source: SourceRegistration


def _environment_local() -> bool:
    return os.getenv("ENVIRONMENT", "production").lower() == "local"


def _try_local_intake_bypass(
    x_local_correction_intake_key: Optional[str],
) -> Optional[IntakeCaller]:
    if not _environment_local():
        return None
    if not settings.correction_intake_local_enabled:
        return None
    if not settings.correction_intake_local_key:
        return None
    if not x_local_correction_intake_key:
        return None
    if x_local_correction_intake_key != settings.correction_intake_local_key:
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="invalid_local_intake_key",
            http_status=401,
        )
        raise HTTPException(status_code=401, detail="Invalid local intake key")
    src = SourceRegistration(
        source_app_id=settings.correction_intake_local_source_app_id,
        display_name=settings.correction_intake_local_source_display_name,
    )
    return IntakeCaller(entra_client_app_id="local-bypass", source=src)


def _decode_entra_workload_token(token: str) -> dict:
    tenant_id = settings.correction_intake_tenant_id
    audience = settings.correction_intake_audience
    if not tenant_id or not audience:
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="intake_not_configured",
            http_status=503,
        )
        raise HTTPException(
            status_code=503,
            detail="Correction intake is not configured (tenant/audience).",
        )
    issuer = settings.correction_intake_issuer
    if not issuer:
        issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    try:
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp"]},
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="token_expired",
            http_status=401,
        )
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid workload token: %s", exc)
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="invalid_token",
            http_status=401,
        )
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _caller_app_id_from_claims(claims: dict) -> Optional[str]:
    return claims.get("azp") or claims.get("appid")


async def get_correction_intake_caller(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_local_correction_intake_key: Optional[str] = Header(
        None, alias="X-Local-Correction-Intake-Key"
    ),
) -> IntakeCaller:
    """
    Authenticate downstream correction intake.

    - Local: optional header X-Local-Correction-Intake-Key when ENVIRONMENT=local
      and CORRECTION_INTAKE_LOCAL_ENABLED + CORRECTION_INTAKE_LOCAL_KEY are set.
    - Otherwise: Bearer JWT validated against Entra JWKS + allowlist map.
    """
    bypass = _try_local_intake_bypass(x_local_correction_intake_key)
    if bypass:
        logger.info("Correction intake authenticated via local bypass")
        return bypass

    if not authorization or not authorization.startswith("Bearer "):
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="authentication_required",
            http_status=401,
        )
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Bearer token or local intake key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.replace("Bearer ", "", 1).strip()
    claims = _decode_entra_workload_token(token)
    app_id = _caller_app_id_from_claims(claims)
    if not app_id:
        emit_correction_intake_audit(
            caller_app_id=None,
            record_guid=None,
            outcome="rejected",
            reason="missing_application_identity",
            http_status=403,
        )
        raise HTTPException(
            status_code=403,
            detail="Token missing application identity (azp/appid).",
        )
    reg = resolve_source_for_client_app(app_id)
    if not reg:
        emit_correction_intake_audit(
            caller_app_id=app_id,
            record_guid=None,
            outcome="rejected",
            reason="caller_not_registered",
            http_status=403,
        )
        raise HTTPException(
            status_code=403,
            detail="Caller application is not registered for correction intake.",
        )
    return IntakeCaller(entra_client_app_id=app_id, source=reg)
