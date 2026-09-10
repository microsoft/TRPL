from unittest.mock import MagicMock, patch

import pytest

from helper.config import CosmosDBConfig
from helper.cosmos_client import _resolve_cosmos_credential


def _config(key=None):
    return CosmosDBConfig(
        endpoint="https://example.documents.azure.com",
        database_name="contentdb",
        container_name="recordsmetadata",
        key=key,
    )


def test_local_environment_uses_emulator_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")

    assert _resolve_cosmos_credential(_config(" emulator-key ")) == "emulator-key"


def test_non_local_environment_rejects_cosmos_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(ValueError, match="allowed only"):
        _resolve_cosmos_credential(_config("emulator-key"))


def test_cloud_environment_uses_managed_identity(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    credential = MagicMock()

    with patch(
        "helper.cosmos_client.DefaultAzureCredential",
        return_value=credential,
    ) as credential_class:
        result = _resolve_cosmos_credential(_config())

    assert result is credential
    credential_class.assert_called_once_with()
