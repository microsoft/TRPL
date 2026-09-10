"""Process-wide credential singletons for Azure data-plane SDKs.

All helpers should authenticate to Azure via :func:`get_credential` rather than
account keys, connection strings, or SAS tokens. Sharing one
``DefaultAzureCredential`` across the process ensures the underlying token
cache is reused, which avoids extra IMDS round-trips on every call.

Resolution order (per ``DefaultAzureCredential`` defaults, minus the
interactive browser flow which would block a Functions worker):

* ``EnvironmentCredential`` — service principal via ``AZURE_*`` env vars
* ``ManagedIdentityCredential`` — Function App system-assigned MI in Azure
* ``AzureCliCredential`` — developer's ``az login`` session locally
* ``AzureDeveloperCliCredential`` — ``azd auth login`` session locally
"""

from functools import lru_cache

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
