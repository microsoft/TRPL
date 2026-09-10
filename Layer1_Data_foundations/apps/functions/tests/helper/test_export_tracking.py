"""Unit tests for the helper.export_tracking module."""
from helper.export_tracking import (
    default_export_tracking,
    ensure_export_tracking,
    mark_pipeline_metadata,
    mark_pipeline_ocr,
)


def test_default_export_tracking():
    """default_export_tracking returns flags initialized to their defaults."""
    tracking = default_export_tracking()
    assert tracking["pipeline_processed"] is False
    assert tracking["ocr_modified_by_archivist"] is False


def test_mark_pipeline_ocr_sets_flags():
    """mark_pipeline_ocr sets the OCR completion flags and timestamp."""
    record = {"record_id": "r1", "asset_details": []}
    mark_pipeline_ocr(record)
    tracking = record["export_tracking"]
    assert tracking["pipeline_processed"] is True
    assert tracking["pipeline_ocr_at"]


def test_mark_pipeline_metadata_sets_flags():
    """mark_pipeline_metadata sets metadata flags and snapshots the metadata."""
    record = {"record_id": "r1", "metadata": {"Title": "A"}}
    mark_pipeline_metadata(record)
    tracking = record["export_tracking"]
    assert tracking["pipeline_processed"] is True
    assert tracking["pipeline_metadata_at"]
    assert record["export_tracking"]["pipeline_metadata_snapshot"] == {"Title": "A"}


def test_ensure_export_tracking_on_prepare():
    """ensure_export_tracking adds an export_tracking entry to a bare record."""
    record = {}
    ensure_export_tracking(record)
    assert "export_tracking" in record
