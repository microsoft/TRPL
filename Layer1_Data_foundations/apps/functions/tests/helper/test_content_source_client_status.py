"""Unit tests for content-source persistence and export tracking integration."""
import asyncio
from unittest.mock import AsyncMock, patch

from helper.content_source_client import (
    save_records_batch,
    update_record_status,
)


@patch("helper.content_source_client.upsert_with_retry")
@patch("helper.content_source_client.container")
def test_update_record_status_marks_export_tracking_on_ocr_complete(mock_container, mock_upsert):
    """Completing ocr_processing_status via update_record_status sets export_tracking flags."""
    mock_container.read_item.return_value = {
        "id": "rec-1",
        "record_id": "rec-1",
        "ocr_processing_status": "pending",
    }
    mock_upsert.return_value = {"success": True, "error": None}

    assert update_record_status("rec-1", "ocr_processing_status", "completed", "inst-1") is True

    saved = mock_upsert.call_args.kwargs["item"]
    assert saved["ocr_processing_status"] == "completed"
    assert saved["export_tracking"]["pipeline_processed"] is True
    assert saved["export_tracking"]["pipeline_ocr_at"]


@patch("helper.content_source_client.upsert_with_retry")
@patch("helper.content_source_client.container")
def test_update_record_status_marks_export_tracking_on_metadata_complete(mock_container, mock_upsert):
    """Completing metadata_extraction_status via update_record_status sets export_tracking flags."""
    mock_container.read_item.return_value = {
        "id": "rec-2",
        "record_id": "rec-2",
        "metadata_extraction_status": "pending",
        "metadata": {"Title": "Sample"},
    }
    mock_upsert.return_value = {"success": True, "error": None}

    assert update_record_status("rec-2", "metadata_extraction_status", "completed") is True

    saved = mock_upsert.call_args.kwargs["item"]
    assert saved["metadata_extraction_status"] == "completed"
    assert saved["export_tracking"]["pipeline_processed"] is True
    assert saved["export_tracking"]["pipeline_metadata_at"]
    assert saved["export_tracking"]["pipeline_metadata_snapshot"] == {"Title": "Sample"}


@patch(
    "helper.content_source_client.async_upsert_with_retry",
    new_callable=AsyncMock,
)
@patch("helper.content_source_client.query_records_by_ids")
def test_save_records_batch_preserves_workflow_state_on_reingestion(
    mock_query_records,
    mock_upsert,
):
    existing = {
        "id": "rec-1",
        "record_id": "rec-1",
        "title": "Old source title",
        "validated_by": "Archivist",
        "published_by": "Publisher",
        "archivist_status": "published",
        "related_assets_status": "completed",
        "asset_details_status": "completed",
        "original_file_status": "completed",
        "ocr_processing_status": "completed",
        "metadata_extraction_status": "completed",
        "related_assets": ["asset-1"],
        "asset_details": [
            {
                "asset_id": "asset-1",
                "sequence": 1,
                "blob_url": "https://example.test/asset-1.jpg",
            }
        ],
        "export_tracking": {"pipeline_processed": True},
    }
    mock_query_records.return_value = ({"rec-1": existing}, [])
    mock_upsert.return_value = {
        "record_id": "rec-1",
        "success": True,
        "error": None,
    }

    result = asyncio.run(
        save_records_batch(
            [
                {
                    "record_id": "rec-1",
                    "record_data": {
                        "id": "rec-1",
                        "record_id": "rec-1",
                        "source_record_id": "SRC-1",
                        "title": "Updated source title",
                        "metadata": {"Title": "Updated source title"},
                        "metadata_key": {"Title": "Title"},
                        "related_assets": [],
                        "asset_details": [],
                    },
                }
            ]
        )
    )

    assert result["successful"] == 1
    saved = mock_upsert.call_args.kwargs["item"]
    assert saved["title"] == "Updated source title"
    assert saved["source_record_id"] == "SRC-1"
    assert saved["validated_by"] == "Archivist"
    assert saved["published_by"] == "Publisher"
    assert saved["archivist_status"] == "published"
    assert saved["ocr_processing_status"] == "completed"
    assert saved["metadata_extraction_status"] == "completed"
    assert saved["related_assets"] == ["asset-1"]
    assert saved["asset_details"] == existing["asset_details"]
    assert saved["export_tracking"]["pipeline_processed"] is True
