"""Tests for the local Azure AI acceptance verifier."""

import pytest

from scripts import verify_local_ai_flow


def _configuration(**overrides):
    configuration = {
        "environment": "local",
        "adapter": "synthetic",
        "cosmos_endpoint": "http://cosmos:8081/",
        "endpoint_configured": True,
        "deployment": "batch-model",
        "model": "gpt-5",
        "api_version": "2025-03-01-preview",
        "secret_ready": True,
        "disabled_pollers": [],
    }
    configuration.update(overrides)
    return configuration


def test_validate_runtime_accepts_local_ai_configuration():
    verify_local_ai_flow._validate_runtime(_configuration())


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"environment": "production"}, "ENVIRONMENT=local"),
        ({"adapter": "remote"}, "synthetic adapter"),
        ({"cosmos_endpoint": "https://cosmos.example.test/"}, "local Cosmos"),
        ({"endpoint_configured": False}, "AZURE_OPENAI_ENDPOINT"),
        ({"secret_ready": False}, "API key"),
        ({"disabled_pollers": ["OcrBatchStatusPoller"]}, "pollers are disabled"),
    ],
)
def test_validate_runtime_rejects_unsafe_or_incomplete_configuration(
    override, message
):
    with pytest.raises(RuntimeError, match=message):
        verify_local_ai_flow._validate_runtime(_configuration(**override))


def test_wait_for_durable_requires_completed_state(monkeypatch):
    responses = iter(
        [
            {"runtimeStatus": "Running"},
            {"runtimeStatus": "Failed", "output": {"message": "bad request"}},
        ]
    )
    monkeypatch.setattr(
        verify_local_ai_flow, "_get_json", lambda *_args, **_kwargs: next(responses)
    )
    monkeypatch.setattr(verify_local_ai_flow.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="ended in Failed"):
        verify_local_ai_flow._wait_for_durable(
            "https://status.example.test",
            "test",
            timeout=5,
            poll_interval=0.01,
        )


def test_validate_stage_scope_accepts_only_synthetic_records(monkeypatch, tmp_path):
    monkeypatch.setattr(
        verify_local_ai_flow,
        "_functions_json",
        lambda *_args, **_kwargs: list(
            verify_local_ai_flow.SYNTHETIC_RECORD_IDS
        ),
    )

    verify_local_ai_flow._validate_stage_scope(
        tmp_path / ".env.local", verify_local_ai_flow.STAGES[0]
    )


def test_validate_stage_scope_rejects_other_pending_records(monkeypatch, tmp_path):
    monkeypatch.setattr(
        verify_local_ai_flow,
        "_functions_json",
        lambda *_args, **_kwargs: [
            *verify_local_ai_flow.SYNTHETIC_RECORD_IDS,
            "other-record",
        ],
    )

    with pytest.raises(RuntimeError, match="unexpected=.*other-record"):
        verify_local_ai_flow._validate_stage_scope(
            tmp_path / ".env.local", verify_local_ai_flow.STAGES[0]
        )


def test_wait_for_stage_requires_new_completed_batch(monkeypatch, tmp_path):
    stage = verify_local_ai_flow.STAGES[0]
    rows = [
        {
            "id": record_id,
            "batch": "completed",
            "processing": "completed",
            "output_ready": True,
            "error": None,
        }
        for record_id in verify_local_ai_flow.SYNTHETIC_RECORD_IDS
    ]
    monkeypatch.setattr(
        verify_local_ai_flow, "_stage_records", lambda *_args: rows
    )
    monkeypatch.setattr(
        verify_local_ai_flow,
        "_batches",
        lambda *_args: [
            {
                "batch_id": "new-batch",
                "status": "completed",
                "record_ids": list(verify_local_ai_flow.SYNTHETIC_RECORD_IDS),
            }
        ],
    )

    assert (
        verify_local_ai_flow._wait_for_stage(
            tmp_path / ".env.local",
            stage,
            previous_batch_ids={"old-batch"},
            timeout=5,
            poll_interval=0.01,
        )
        == "new-batch"
    )


def test_wait_for_stage_rejects_unexpected_batch_records(monkeypatch, tmp_path):
    stage = verify_local_ai_flow.STAGES[0]
    rows = [
        {
            "id": record_id,
            "batch": "completed",
            "processing": "completed",
            "output_ready": True,
            "error": None,
        }
        for record_id in verify_local_ai_flow.SYNTHETIC_RECORD_IDS
    ]
    monkeypatch.setattr(
        verify_local_ai_flow, "_stage_records", lambda *_args: rows
    )
    monkeypatch.setattr(
        verify_local_ai_flow,
        "_batches",
        lambda *_args: [
            {
                "batch_id": "new-batch",
                "status": "completed",
                "record_ids": ["unexpected-record"],
            }
        ],
    )

    with pytest.raises(RuntimeError, match="unexpected record IDs"):
        verify_local_ai_flow._wait_for_stage(
            tmp_path / ".env.local",
            stage,
            previous_batch_ids={"old-batch"},
            timeout=5,
            poll_interval=0.01,
        )
