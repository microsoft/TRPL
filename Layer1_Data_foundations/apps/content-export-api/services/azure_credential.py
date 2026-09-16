# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared Azure credential for Cosmos (local dev: TRPL tenant via ENTRA_TENANT_ID)."""
from __future__ import annotations

import os

from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

from core.config import settings

_credential: TokenCredential | None = None


def get_azure_credential() -> TokenCredential:
    global _credential
    if _credential is not None:
        return _credential

    tenant_id = (settings.entra_tenant_id or "").strip()
    if tenant_id:
        # DefaultAzureCredential does not accept tenant_id; sub-credentials read AZURE_TENANT_ID.
        os.environ.setdefault("AZURE_TENANT_ID", tenant_id)

    dac_kwargs: dict = {"exclude_interactive_browser_credential": False}

    if tenant_id and settings.environment == "local":
        # Prefer az login token from the TRPL tenant (multi-tenant CLI accounts).
        _credential = ChainedTokenCredential(
            AzureCliCredential(tenant_id=tenant_id),
            DefaultAzureCredential(**dac_kwargs, exclude_cli_credential=True),
        )
    else:
        _credential = DefaultAzureCredential(**dac_kwargs)

    return _credential
