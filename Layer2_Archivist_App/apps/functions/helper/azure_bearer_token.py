"""Acquire Entra bearer tokens for Azure resources via managed identity / az login."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential


def resource_token_scope(base_url: str) -> str:
    """OAuth scope for calling an Azure-hosted app (App Service, Functions, etc.)."""
    parsed = urlparse(base_url.strip())
    host = parsed.netloc or parsed.path.split('/')[0]
    scheme = parsed.scheme or 'https'
    return f'{scheme}://{host}/.default'


@lru_cache(maxsize=4)
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


def get_bearer_token(base_url: str) -> Optional[str]:
    """Return a bearer token for ``base_url``, or None if the URL is empty."""
    if not base_url or not base_url.strip():
        return None
    scope = resource_token_scope(base_url)
    return _credential().get_token(scope).token
