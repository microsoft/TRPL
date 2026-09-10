from unittest.mock import MagicMock, patch

import pytest
from azure.core.credentials import AzureKeyCredential

from helper import credential


def test_local_openai_key_file_is_used(monkeypatch, tmp_path):
    key_file = tmp_path / "openai-key"
    key_file.write_text("local-openai-key\n", encoding="utf-8")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    assert credential.get_azure_openai_auth_kwargs() == {
        "api_key": "local-openai-key"
    }


def test_local_search_key_file_is_used(monkeypatch, tmp_path):
    key_file = tmp_path / "search-key"
    key_file.write_text("local-search-key\n", encoding="utf-8")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("AZURE_SEARCH_API_KEY", raising=False)

    result = credential.get_search_credential()

    assert isinstance(result, AzureKeyCredential)
    assert result.key == "local-search-key"


def test_data_plane_keys_are_rejected_outside_local(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "cloud-key")

    with pytest.raises(ValueError, match="ENVIRONMENT=local"):
        credential.get_search_credential()


def test_search_uses_shared_managed_identity_by_default(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_SEARCH_API_KEY_FILE", raising=False)
    monkeypatch.delenv("AZURE_SEARCH_API_KEY", raising=False)
    shared = MagicMock()

    with patch("helper.credential.get_credential", return_value=shared):
        result = credential.get_search_credential()

    assert result is shared
