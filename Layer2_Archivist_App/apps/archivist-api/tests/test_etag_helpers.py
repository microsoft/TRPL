"""Tests for Cosmos etag comparison helpers."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.cosmos_service import (
    DocumentVersionConflictError,
    _assert_client_etag_matches,
    _normalize_etag_for_compare,
)


def test_normalize_etag_strips_outer_quotes():
    assert _normalize_etag_for_compare('"abc123"') == "abc123"
    assert _normalize_etag_for_compare("abc123") == "abc123"


def test_assert_client_etag_matches_accepts_quote_drift():
    _assert_client_etag_matches('"abc123"', "abc123")


def test_assert_client_etag_matches_rejects_stale_client():
    with pytest.raises(DocumentVersionConflictError):
        _assert_client_etag_matches("old", "new")


def test_assert_client_etag_matches_skips_when_client_missing():
    _assert_client_etag_matches(None, '"live"')
