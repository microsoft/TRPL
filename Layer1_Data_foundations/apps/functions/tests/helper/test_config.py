# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for model-specific Azure OpenAI request parameters."""

from helper.config import AzureConfig, get_chat_completion_parameters


def _config() -> AzureConfig:
    return AzureConfig(
        endpoint="https://example.openai.azure.com",
        max_tokens=4000,
        temperature=0.2,
    )


def test_gpt5_uses_reasoning_model_parameters(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_MODEL_NAME", "gpt-5")

    assert get_chat_completion_parameters(
        _config(), "arbitrary-deployment-name"
    ) == {"max_completion_tokens": 4000}


def test_non_reasoning_model_preserves_existing_parameters(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_MODEL_NAME", "gpt-4o")

    assert get_chat_completion_parameters(_config(), "batch-deployment") == {
        "max_tokens": 4000,
        "temperature": 0.2,
    }
