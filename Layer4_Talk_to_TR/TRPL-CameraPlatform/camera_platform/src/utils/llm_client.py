# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Build an OpenAI-compatible chat client from environment configuration.

Supports three endpoint styles, auto-detected from LLM_BASE_URL:
  1. Public OpenAI       — LLM_BASE_URL unset.
  2. OpenAI-compatible   — LLM_BASE_URL set to a /v1 base (vLLM, LiteLLM, etc.).
  3. Azure OpenAI        — LLM_BASE_URL is an *.azure.com URL of the form
       https://<res>.cognitiveservices.azure.com/openai/deployments/<dep>/chat/completions?api-version=<ver>
     Azure needs the AzureOpenAI client (api-key header + different URL layout)
     and the **deployment name** as the `model` argument — not the model family.

Returns (client, model). The client exposes .chat.completions.create(...).
Returns (None, default_model) when no API key is configured, so callers can
degrade gracefully exactly as before.
"""
import logging
import os
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


def _is_azure(url: str) -> bool:
    u = url.lower()
    return "azure.com" in u or "api-version=" in u or "/deployments/" in u


def _parse_azure(url: str) -> Tuple[str, str, Optional[str]]:
    """Return (azure_endpoint, api_version, deployment) from a full Azure URL."""
    p = urlparse(url)
    endpoint = f"{p.scheme}://{p.netloc}"
    api_version = parse_qs(p.query).get("api-version", ["2024-06-01"])[0]
    deployment = None
    parts = [s for s in p.path.split("/") if s]
    if "deployments" in parts:
        i = parts.index("deployments")
        if i + 1 < len(parts):
            deployment = parts[i + 1]
    return endpoint, api_version, deployment


def build_chat_client(default_model: str) -> Tuple[Optional[object], str]:
    """Construct the right client for the configured endpoint.

    Args:
        default_model: model/deployment to use when not otherwise determined.

    Returns:
        (client, model) — or (None, default_model) if LLM_API_KEY is unset.
    """
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, default_model

    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL") or default_model

    if base_url and _is_azure(base_url):
        endpoint, api_version, deployment = _parse_azure(base_url)
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=api_key, azure_endpoint=endpoint, api_version=api_version
        )
        # For Azure the deployment name in the URL is authoritative; LLM_MODEL
        # can still override if the operator names the deployment differently.
        model = os.getenv("LLM_MODEL") or deployment or default_model
        logger.info(
            f"LLM client: Azure endpoint={endpoint} "
            f"api_version={api_version} deployment={model}"
        )
        return client, model

    from openai import OpenAI
    if base_url:
        client = OpenAI(api_key=api_key, base_url=base_url)
        logger.info(f"LLM client: OpenAI-compatible endpoint={base_url} model={model}")
    else:
        client = OpenAI(api_key=api_key)
        logger.info(f"LLM client: OpenAI model={model}")
    return client, model
