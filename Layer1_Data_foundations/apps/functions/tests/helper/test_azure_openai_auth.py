# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Azure OpenAI authentication selection."""

import pytest

from helper import credential


def test_local_api_key_file_is_used(monkeypatch, tmp_path):
    key_file = tmp_path / "azure-openai-key"
    key_file.write_text("local-key\n")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    assert credential.get_azure_openai_auth_kwargs() == {
        "api_key": "local-key"
    }


def test_api_key_is_rejected_outside_local(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "cloud-key")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY_FILE", raising=False)

    with pytest.raises(ValueError, match="ENVIRONMENT=local"):
        credential.get_azure_openai_auth_kwargs()


def test_missing_key_file_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv(
        "AZURE_OPENAI_API_KEY_FILE", str(tmp_path / "missing")
    )
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="Unable to read"):
        credential.get_azure_openai_auth_kwargs()


def test_entra_auth_is_default(monkeypatch):
    token_provider = object()
    monkeypatch.delenv("AZURE_OPENAI_API_KEY_FILE", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        credential,
        "get_cognitive_services_token_provider",
        lambda: token_provider,
    )

    assert credential.get_azure_openai_auth_kwargs() == {
        "azure_ad_token_provider": token_provider
    }
