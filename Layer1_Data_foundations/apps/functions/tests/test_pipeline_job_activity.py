# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Durable pipeline status tracking."""

from unittest.mock import patch

import pytest

from helper import pipeline_job_helper


def test_update_pipeline_job_status_persists_transition():
    payload = {
        "trigger_endpoint": "fetch-related-assets",
        "instance_id": "instance-123",
        "name": "Fetch Related Assets",
        "runtime_status": "Completed",
        "output_data": {"processed": 3},
    }

    with patch.object(
        pipeline_job_helper, "save_pipeline_job", return_value=True
    ) as save:
        result = pipeline_job_helper.persist_pipeline_job_status(payload)

    assert result == {"success": True}
    save.assert_called_once_with(
        trigger_endpoint="fetch-related-assets",
        instance_id="instance-123",
        name="Fetch Related Assets",
        runtime_status="Completed",
        custom_status=None,
        input_data=None,
        output_data={"processed": 3},
        error=None,
    )


def test_update_pipeline_job_status_rejects_missing_identity():
    with pytest.raises(ValueError, match="instance_id, name"):
        pipeline_job_helper.persist_pipeline_job_status(
            {"trigger_endpoint": "fetch-related-assets"}
        )


def test_update_pipeline_job_status_surfaces_persistence_failure():
    payload = {
        "trigger_endpoint": "fetch-related-assets",
        "instance_id": "instance-123",
        "name": "Fetch Related Assets",
    }

    with patch.object(pipeline_job_helper, "save_pipeline_job", return_value=False):
        with pytest.raises(RuntimeError, match="fetch-related-assets"):
            pipeline_job_helper.persist_pipeline_job_status(payload)
