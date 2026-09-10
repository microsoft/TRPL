"""Cosmos export_tracking flags for outbound retrieval filtering."""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def default_export_tracking() -> Dict[str, Any]:
    """Return a fresh export_tracking dict populated with default values."""
    return {
        "pipeline_processed": False,
        "pipeline_ocr_at": None,
        "pipeline_metadata_at": None,
        "ocr_modified_by_archivist": False,
        "metadata_modified_by_archivist": False,
        "archivist_ocr_modified_at": None,
        "archivist_metadata_modified_at": None,
        "pipeline_ocr_snapshot": None,
        "pipeline_metadata_snapshot": None,
        "last_exported_at": None,
    }


def ensure_export_tracking(record: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure export_tracking exists with defaults on a Cosmos record dict."""
    tracking = record.get("export_tracking")
    if not isinstance(tracking, dict):
        tracking = default_export_tracking()
    else:
        defaults = default_export_tracking()
        for key, value in defaults.items():
            tracking.setdefault(key, value)
    record["export_tracking"] = tracking
    return tracking


def ensure_snapshots(record: Dict[str, Any], *, domain: str) -> None:
    """
    Copy pipeline baseline into snapshots on first write for a domain.
    domain: 'ocr' | 'metadata'
    """
    tracking = ensure_export_tracking(record)
    if domain == "ocr" and tracking.get("pipeline_ocr_snapshot") is None:
        assets = record.get("asset_details") or []
        tracking["pipeline_ocr_snapshot"] = {
            "asset_details": copy.deepcopy(assets),
            "visual_description_possible": record.get("visual_description_possible"),
            "visual_summary_description_flexible": record.get(
                "visual_summary_description_flexible"
            ),
        }
    if domain == "metadata" and tracking.get("pipeline_metadata_snapshot") is None:
        metadata = record.get("metadata") or record.get("extracted_metadata") or {}
        tracking["pipeline_metadata_snapshot"] = copy.deepcopy(metadata)


def mark_pipeline_ocr(record: Dict[str, Any]) -> Dict[str, Any]:
    """Set pipeline OCR completion flags (call when ocr_processing_status becomes completed)."""
    tracking = ensure_export_tracking(record)
    now = _utc_now()
    ensure_snapshots(record, domain="ocr")
    if not tracking.get("pipeline_ocr_at"):
        tracking["pipeline_ocr_at"] = now
    tracking["pipeline_processed"] = True
    record_id = record.get("record_id") or record.get("id")
    logger.info("Marked export_tracking OCR complete for record %s", record_id)
    return tracking


def mark_pipeline_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    """Set pipeline metadata completion flags."""
    tracking = ensure_export_tracking(record)
    now = _utc_now()
    ensure_snapshots(record, domain="metadata")
    if not tracking.get("pipeline_metadata_at"):
        tracking["pipeline_metadata_at"] = now
    tracking["pipeline_processed"] = True
    record_id = record.get("record_id") or record.get("id")
    logger.info("Marked export_tracking metadata complete for record %s", record_id)
    return tracking
