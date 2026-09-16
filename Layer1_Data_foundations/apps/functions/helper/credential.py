# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Process-wide authentication helpers for Azure data-plane SDKs.

Cloud services authenticate through :func:`get_credential`. Local Azure OpenAI
testing may instead read an API key from a runtime secret. Sharing one
``DefaultAzureCredential`` across the process ensures the underlying token cache
is reused, which avoids extra IMDS round-trips on every call.

Resolution order (per ``DefaultAzureCredential`` defaults, minus the
interactive browser flow which would block a Functions worker):

* ``EnvironmentCredential`` — service principal via ``AZURE_*`` env vars
* ``ManagedIdentityCredential`` — Function App system-assigned MI in Azure
* ``AzureCliCredential`` — developer's ``az login`` session locally
* ``AzureDeveloperCliCredential`` — ``azd auth login`` session locally
"""

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider


COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def get_credential() -> DefaultAzureCredential:
    """Return the shared :class:`DefaultAzureCredential` for the process."""
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache(maxsize=1)
def get_cognitive_services_token_provider():
    """Return a bearer-token provider scoped to Azure Cognitive Services.

    Used to wire ``azure_ad_token_provider`` into the ``openai.AzureOpenAI``
    client so that calls to Azure OpenAI / AI Foundry deployments use Entra ID
    tokens instead of API keys.
    """
    return get_bearer_token_provider(get_credential(), COGNITIVE_SERVICES_SCOPE)


def get_azure_openai_auth_kwargs() -> dict[str, Any]:
    """Select secure Azure OpenAI authentication for the current environment."""
    api_key_file = os.getenv("AZURE_OPENAI_API_KEY_FILE")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    environment = os.getenv("ENVIRONMENT", "").strip().lower()

    if (api_key_file or api_key) and environment != "local":
        raise ValueError(
            "Azure OpenAI API-key authentication is allowed only when "
            "ENVIRONMENT=local"
        )

    if api_key_file:
        key_path = Path(api_key_file)
        try:
            api_key = key_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"Unable to read AZURE_OPENAI_API_KEY_FILE: {key_path}"
            ) from exc

        if not api_key:
            raise ValueError(
                f"AZURE_OPENAI_API_KEY_FILE is empty: {key_path}"
            )

    if api_key:
        return {"api_key": api_key}

    return {
        "azure_ad_token_provider": get_cognitive_services_token_provider()
    }
