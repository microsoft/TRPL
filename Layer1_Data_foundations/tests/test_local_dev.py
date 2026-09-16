# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for local environment configuration and Azure AI discovery."""

import pytest

from scripts import local_dev


ACCOUNT = {"name": "test-ai", "kind": "AIServices"}
ACCOUNT_DETAILS = {
    "properties": {
        "endpoints": {
            "OpenAI Language Model Instance API": "https://example.test/openai",
            "AI Foundry API": "https://example.test/foundry",
        }
    }
}


def _deployment(name: str) -> dict:
    return {
        "name": name,
        "sku": {"name": "GlobalBatch"},
        "properties": {
            "model": {"format": "OpenAI", "name": "gpt-5"},
            "provisioningState": "Succeeded",
        },
    }


def _mock_az(monkeypatch, deployments):
    responses = iter([{}, [ACCOUNT], deployments, ACCOUNT_DETAILS])
    monkeypatch.setattr(local_dev, "_run_az", lambda _: next(responses))


def test_ai_discovery_reports_missing_batch_deployment(monkeypatch):
    _mock_az(monkeypatch, [])

    with pytest.raises(RuntimeError, match="No compatible"):
        local_dev._discover_ai("subscription", "resource-group", None, None)


def test_ai_discovery_returns_single_batch_deployment(monkeypatch):
    _mock_az(monkeypatch, [_deployment("batch-model")])

    settings = local_dev._discover_ai(
        "subscription", "resource-group", None, None
    )

    assert settings == {
        "AZURE_OPENAI_ENDPOINT": "https://example.test/openai",
        "AZURE_OPENAI_API_VERSION": "2025-03-01-preview",
        "AZURE_OPENAI_BATCH_DEPLOYMENT_NAME": "batch-model",
        "AZURE_OPENAI_MODEL_NAME": "gpt-5",
        "AZURE_AI_FOUNDRY_MODEL_NAME": "batch-model",
        "AZURE_AI_FOUNDRY_ENDPOINT": "https://example.test/foundry",
    }


def test_ai_discovery_requires_selection_when_multiple_deployments(monkeypatch):
    _mock_az(monkeypatch, [_deployment("batch-a"), _deployment("batch-b")])

    with pytest.raises(RuntimeError, match="Multiple batch deployments"):
        local_dev._discover_ai("subscription", "resource-group", None, None)


def test_pip_index_uses_global_configuration(monkeypatch):
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    responses = iter(
        [
            local_dev.subprocess.CompletedProcess([], 1, "", ""),
            local_dev.subprocess.CompletedProcess([], 1, "", ""),
            local_dev.subprocess.CompletedProcess(
                [], 0, "https://packages.example.test/simple/\n", ""
            ),
        ]
    )
    monkeypatch.setattr(local_dev.subprocess, "run", lambda *_, **__: next(responses))

    assert local_dev._pip_index_url() == "https://packages.example.test/simple/"


def test_ai_pollers_are_enabled_explicitly():
    settings = local_dev._disabled_timer_settings(enable_ai_pollers=True)

    for poller in local_dev.AI_STATUS_POLLERS:
        assert f"AzureWebJobs.{poller}.Disabled" not in settings
    assert settings["AzureWebJobs.DigitalItemsOcrPoller.Disabled"] == "true"


def test_ai_pollers_are_disabled_by_default():
    settings = local_dev._disabled_timer_settings(enable_ai_pollers=False)

    for poller in local_dev.AI_STATUS_POLLERS:
        assert settings[f"AzureWebJobs.{poller}.Disabled"] == "true"


def test_local_service_bus_defaults_to_layer2_ingestion_queue():
    settings = local_dev._function_values()

    assert settings["SERVICEBUS_QUEUE_NAME"] == "data-ingestion-queue"


def test_write_secret_file_uses_owner_only_permissions(tmp_path):
    secret_path = tmp_path / ".env.secret"

    local_dev._write_secret_file(secret_path, "secret-value")

    assert secret_path.read_text() == "secret-value\n"
    assert secret_path.stat().st_mode & 0o777 == 0o600
