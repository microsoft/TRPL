# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Authentication helpers for Layer 2 Azure data-plane clients."""

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def get_credential() -> DefaultAzureCredential:
    """Return the shared managed-identity/developer credential."""
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def _read_local_secret(file_variable: str, value_variable: str) -> str:
    secret_file = os.getenv(file_variable, "").strip()
    secret = os.getenv(value_variable, "").strip()
    if (secret_file or secret) and os.getenv(
        "ENVIRONMENT", "production"
    ).strip().lower() != "local":
        raise ValueError(
            f"{value_variable} authentication is allowed only when ENVIRONMENT=local"
        )
    if secret_file:
        path = Path(secret_file)
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Unable to read {file_variable}: {path}") from exc
        if not secret:
            raise ValueError(f"{file_variable} is empty: {path}")
    return secret


def get_azure_openai_auth_kwargs() -> dict[str, Any]:
    """Use a local runtime secret or managed identity for Azure OpenAI."""
    api_key = _read_local_secret(
        "AZURE_OPENAI_API_KEY_FILE",
        "AZURE_OPENAI_API_KEY",
    )
    if api_key:
        return {"api_key": api_key}
    return {
        "azure_ad_token_provider": get_bearer_token_provider(
            get_credential(),
            COGNITIVE_SERVICES_SCOPE,
        )
    }


def get_search_credential():
    """Use a local runtime secret or managed identity for Azure AI Search."""
    api_key = _read_local_secret(
        "AZURE_SEARCH_API_KEY_FILE",
        "AZURE_SEARCH_API_KEY",
    )
    if api_key:
        return AzureKeyCredential(api_key)
    return get_credential()
