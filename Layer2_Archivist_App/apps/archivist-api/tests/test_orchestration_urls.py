# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for durable orchestration management URL normalization."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.routes import (
    _apply_orchestration_reason,
    _normalize_orchestration_management_url,
    _normalize_orchestration_urls_in_payload,
    _prepare_orchestration_action_url,
)


@pytest.fixture(autouse=True)
def _function_settings():
    from core.config import settings

    with (
        patch.object(
            settings,
            "azure_functions_base_url",
            "https://data-function.example.com",
        ),
        patch.object(settings, "azure_functions_code", "pipeline-key"),
        patch.object(settings, "azure_functions_task_hub", "BatchOrchestrationHub"),
        patch.object(
            settings,
            "data_ingest_function_url",
            "https://ingest-function.example.com",
        ),
        patch.object(settings, "data_ingest_function_code", "ingest-key"),
        patch.object(settings, "data_ingest_task_hub", "DurableIngestTaskHub"),
    ):
        yield


def test_rewrite_agw_host_to_function_app():
    agw_url = (
        "https://archivist.example.com/runtime/webhooks/durableTask/instances/abc/terminate"
        "?taskHub=BatchOrchestrationHub&connection=Storage&code=existing"
    )
    result = _normalize_orchestration_management_url(agw_url)
    assert result.startswith(
        "https://data-function.example.com/runtime/webhooks/durableTask/"
    )
    assert "instances/abc/terminate" in result
    assert "code=existing" in result


def test_appends_missing_function_key():
    url = (
        "https://wrong-host.example.com/runtime/webhooks/durableTask/instances/abc/terminate"
        "?taskHub=BatchOrchestrationHub"
    )
    result = _normalize_orchestration_management_url(url)
    assert "data-function.example.com" in result
    assert "code=pipeline-key" in result


def test_data_ingest_task_hub_uses_ingest_function_app():
    url = (
        "https://agw.example.com/runtime/webhooks/durableTask/instances/xyz/terminate"
        "?taskHub=DurableIngestTaskHub"
    )
    result = _normalize_orchestration_management_url(url)
    assert "ingest-function.example.com" in result
    assert "code=ingest-key" in result


def test_normalize_payload_fields():
    payload = {
        "terminate_url": (
            "https://agw.example.com/runtime/webhooks/durableTask/instances/a/terminate"
            "?taskHub=BatchOrchestrationHub"
        ),
        "status_url": (
            "https://agw.example.com/runtime/webhooks/durableTask/instances/a"
            "?taskHub=BatchOrchestrationHub"
        ),
    }
    out = _normalize_orchestration_urls_in_payload(payload)
    assert "data-function.example.com" in out["terminate_url"]
    assert "data-function.example.com" in out["status_url"]


def test_apply_orchestration_reason_replaces_text_placeholder():
    url = (
        "https://func.example.net/runtime/webhooks/durabletask/instances/abc/suspend"
        "?reason={text}&taskHub=BatchOrchestrationHub&code=key"
    )
    result = _apply_orchestration_reason(url, "Suspended by user")
    assert "{text}" not in result
    assert "reason=Suspended" in result or "reason=Suspended%20by%20user" in result
    assert "taskHub=BatchOrchestrationHub" in result


def test_apply_orchestration_reason_empty_when_no_reason():
    url = ".../terminate?reason={text}&code=k"
    result = _apply_orchestration_reason(url, None)
    assert "{text}" not in result
    assert "reason=" in result


def test_prepare_orchestration_action_url_rewrites_host_and_reason():
    url = (
        "https://agw.example.com/runtime/webhooks/durableTask/instances/job-1/suspend"
        "?reason={text}&taskHub=BatchOrchestrationHub"
    )
    result = _prepare_orchestration_action_url(url, "Paused")
    assert "data-function.example.com" in result
    assert "/instances/job-1/suspend" in result
    assert "{text}" not in result
