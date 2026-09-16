# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import sys

from fastapi.testclient import TestClient


def test_degraded_mode_starts_without_azure_configuration(monkeypatch):
    for name in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_API_KEY",
        "AZURE_SEARCH_BOOK_INDEX",
        "AZURE_SEARCH_LETTER_INDEX",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CAMPFIRE_RAG_MODE", "degraded")
    monkeypatch.setenv("RAG_API_KEY", "local-test-key")
    monkeypatch.setenv("REDIS_SSL", "false")

    previous_modules = {
        name: sys.modules.pop(name)
        for name in ("main", "auth", "common_config")
        if name in sys.modules
    }
    try:
        local_main = importlib.import_module("main")
        with TestClient(local_main.app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "hello"},
                headers={"Authorization": "Bearer local-test-key"},
            )
        assert response.status_code == 503
        assert "unavailable in degraded local mode" in response.json()["detail"]
    finally:
        for name in ("main", "auth", "common_config"):
            sys.modules.pop(name, None)
        sys.modules.update(previous_modules)
