# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Helpers for resolving App Service Key Vault reference strings."""

import logging
import re
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

_KV_REF_PATTERN = re.compile(r"^@Microsoft\.KeyVault\(SecretUri=(?P<uri>https://[^)]+)\)$", re.IGNORECASE)


def is_keyvault_reference(value: Optional[str]) -> bool:
    """Return True when value uses App Service Key Vault reference syntax."""
    return bool(value and value.startswith("@Microsoft.KeyVault("))


@lru_cache(maxsize=256)
def resolve_keyvault_reference(value: str) -> str:
    """Resolve a Key Vault reference string to a plain secret value via managed identity."""
    match = _KV_REF_PATTERN.match(value.strip())
    if not match:
        return value

    secret_uri = match.group("uri")
    parsed = urlparse(secret_uri)
    segments = [segment for segment in parsed.path.split("/") if segment]

    # Expected: /secrets/{secret-name}/{optional-version}
    if len(segments) < 2 or segments[0].lower() != "secrets":
        raise ValueError(f"Invalid Key Vault SecretUri format: {secret_uri}")

    secret_name = segments[1]
    secret_version = segments[2] if len(segments) >= 3 else None
    vault_url = f"{parsed.scheme}://{parsed.netloc}"

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    client = SecretClient(vault_url=vault_url, credential=credential)
    secret = client.get_secret(secret_name, secret_version)
    if not secret.value:
        raise ValueError(f"Key Vault secret '{secret_name}' resolved but has no value")

    logger.info("Resolved Key Vault reference for secret '%s'", secret_name)
    return secret.value
