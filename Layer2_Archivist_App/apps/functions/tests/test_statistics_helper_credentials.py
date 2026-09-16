# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from helper.statistics_helper import _resolve_cosmos_credential


def test_local_statistics_use_emulator_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("COSMOS_DB_KEY", " emulator-key ")

    assert _resolve_cosmos_credential() == "emulator-key"


def test_non_local_statistics_reject_cosmos_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("COSMOS_DB_KEY", "emulator-key")

    with pytest.raises(ValueError, match="allowed only"):
        _resolve_cosmos_credential()


def test_cloud_statistics_use_managed_identity(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("COSMOS_DB_KEY", raising=False)
    credential = MagicMock()

    with patch(
        "helper.statistics_helper.DefaultAzureCredential",
        return_value=credential,
    ) as credential_class:
        result = _resolve_cosmos_credential()

    assert result is credential
    credential_class.assert_called_once_with()
