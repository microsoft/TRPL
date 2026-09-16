# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Cursor paging and orchestration-state regression tests."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from helper import content_source_client
from helper.content_source import (
    ContentAsset,
    ContentMetadata,
    ContentRecord,
    ContentSourceError,
    ContentSourceErrorCode,
    OpaquePage,
    RightsMetadata,
    advance_sync_orchestration,
    cursor_fingerprint,
    normalize_sync_query_state,
    retry_sync_failure_chain,
    sync_query_state_from_failure,
)


RIGHTS = RightsMetadata(
    status="public-domain-dedication",
    statement="Generated fictional test content.",
    license_url="https://example.org/rights/test",
    credit_line="Synthetic test.",
)


def run(coro):
    return asyncio.run(coro)


def _record(index: int) -> ContentRecord:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ContentRecord(
        id=f"synthetic-page-record-{index:04d}",
        source_identifier=f"SAMPLE-{index:04d}",
        title=f"Record {index}",
        record_type="document",
        summary="Generated pagination fixture.",
        collection_id="synthetic-pagination",
        record_url=f"https://example.org/synthetic-records/page-record-{index:04d}",
        published=True,
        created_at=timestamp,
        updated_at=timestamp,
        metadata=ContentMetadata(),
        rights=RIGHTS,
    )


class CursorRecordAdapter:
    def __init__(self, count: int, *, total=None):
        self.records = tuple(_record(index) for index in range(count))
        self.total = total
        self.calls = []

    async def query_records(self, query):
        self.calls.append(query.cursor)
        offset = 0 if query.cursor is None else int(query.cursor.removeprefix("cursor-"))
        items = self.records[offset : offset + query.page_size]
        next_offset = offset + len(items)
        next_cursor = (
            f"cursor-{next_offset}" if next_offset < len(self.records) else None
        )
        return OpaquePage(items=items, next_cursor=next_cursor, total=self.total)


class CursorAssetAdapter:
    def __init__(self, count: int, *, fail_at=None, repeat_at=None):
        self.assets = tuple(
            ContentAsset(
                id=f"synthetic-page-asset-{index:04d}",
                record_id="synthetic-page-record",
                name=f"synthetic-page-asset-{index:04d}.png",
                media_type="image/png",
                sequence=index + 1,
                metadata=ContentMetadata(),
                rights=RIGHTS,
            )
            for index in range(count)
        )
        self.fail_at = fail_at
        self.repeat_at = repeat_at
        self.calls = []

    async def query_assets(self, record_id, *, page_size=100, cursor=None):
        self.calls.append(cursor)
        offset = 0 if cursor is None else int(cursor.removeprefix("asset-cursor-"))
        if self.fail_at == offset:
            raise ContentSourceError(
                ContentSourceErrorCode.SOURCE_FAILURE,
                "Synthetic asset source failure",
                retryable=True,
            )
        items = self.assets[offset : offset + page_size]
        next_offset = offset + len(items)
        next_cursor = (
            f"asset-cursor-{next_offset}"
            if next_offset < len(self.assets)
            else None
        )
        if self.repeat_at == offset:
            next_cursor = cursor
        return OpaquePage(items=items, next_cursor=next_cursor, total=None)


def test_total_is_advisory_and_large_cursor_flow_is_linear():
    adapter = CursorRecordAdapter(1201, total=None)
    cursor = None
    seen_ids = []

    with patch.object(content_source_client, "_adapter", return_value=adapter):
        while True:
            page = run(
                content_source_client.query_content_records(
                    limit=100,
                    cursor=cursor,
                )
            )
            assert page["total"] is None
            seen_ids.extend(record["id"] for record in page["data"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert len(seen_ids) == 1201
    assert len(set(seen_ids)) == 1201
    assert len(adapter.calls) == 13
    assert adapter.calls[:3] == [None, "cursor-100", "cursor-200"]


def test_record_adapter_failure_is_not_coerced_to_an_empty_page():
    class FailingAdapter:
        async def query_records(self, query):
            raise ContentSourceError(
                ContentSourceErrorCode.SOURCE_FAILURE,
                "Synthetic record source failure",
                retryable=True,
            )

    with patch.object(content_source_client, "_adapter", return_value=FailingAdapter()):
        with pytest.raises(ContentSourceError) as failure:
            run(content_source_client.query_content_records())
    assert failure.value.as_dict() == {
        "code": "source_failure",
        "message": "Synthetic record source failure",
        "retryable": True,
        "details": {},
    }


def test_orchestration_state_follows_each_cursor_until_none():
    state = {"batch_size": 100}
    first = advance_sync_orchestration(
        state,
        {
            "success": True,
            "successful": 100,
            "failed": 0,
            "reported_total": None,
            "next_cursor": "opaque-a",
        },
    )
    assert first["status"] == "continue"
    assert first["advisory_total"] is None

    second = advance_sync_orchestration(
        first["next_state"],
        {
            "success": True,
            "successful": 100,
            "failed": 0,
            "reported_total": 250,
            "next_cursor": "opaque-b",
        },
    )
    final = advance_sync_orchestration(
        second["next_state"],
        {
            "success": True,
            "successful": 50,
            "failed": 0,
            "reported_total": None,
            "next_cursor": None,
        },
    )
    assert final["status"] == "completed"
    assert final["total_processed_so_far"] == 250
    assert final["pages_processed_so_far"] == 3
    assert final["advisory_total"] == 250


def test_orchestration_rejects_cursor_cycles_and_propagates_failures():
    first = advance_sync_orchestration(
        {},
        {
            "success": True,
            "successful": 1,
            "failed": 0,
            "next_cursor": "opaque-a",
        },
    )
    second = advance_sync_orchestration(
        first["next_state"],
        {
            "success": True,
            "successful": 1,
            "failed": 0,
            "next_cursor": "opaque-b",
        },
    )
    repeated = advance_sync_orchestration(
        second["next_state"],
        {
            "success": True,
            "successful": 1,
            "failed": 0,
            "next_cursor": "opaque-a",
        },
    )
    assert repeated["status"] == "error"
    assert repeated["error"]["code"] == "invalid_data"
    assert repeated["error"]["details"]["cursor_repeated"] is True

    source_error = {
        "code": "source_failure",
        "message": "Adapter unavailable",
        "retryable": True,
        "details": {"status": 503},
    }
    failed = advance_sync_orchestration(
        second["next_state"],
        {"success": False, "error": source_error},
    )
    assert failed["status"] == "error"
    assert failed["error"] == source_error


def _sync_failure(cursor="cursor-middle"):
    return {
        "id": "failure-middle",
        "query_state": normalize_sync_query_state(
            {
                "version": 1,
                "cursor": cursor,
                "page_size": 2,
                "collection_ids": ["collection-a"],
                "date_from": "2026-01-05T03:00:00-03:00",
                "date_to": "2026-01-06T02:59:59-03:00",
                "seen_cursor_fingerprints": [cursor_fingerprint("cursor-start")],
            },
            require_complete=True,
        ),
    }


def test_retry_resumes_middle_page_drains_later_pages_and_deduplicates_writes():
    failure = _sync_failure()
    calls = []
    checkpoints = []
    completions = []
    stored = {
        "record-1": {"id": "record-1"},
        # A partial first attempt may already have upserted this page record.
        "record-2": {"id": "record-2"},
    }
    pages = {
        "cursor-middle": (
            [{"id": "record-2"}, {"id": "record-3"}],
            "cursor-last",
        ),
        "cursor-last": ([{"id": "record-4"}], None),
    }

    async def fetch_page(query_state):
        calls.append(dict(query_state))
        records, next_cursor = pages[query_state["cursor"]]
        for record in records:
            stored[record["id"]] = record
        return {
            "success": True,
            "successful": len(records),
            "failed": 0,
            "next_cursor": next_cursor,
        }

    def checkpoint(query_state, error):
        checkpoints.append((dict(query_state), error))
        return True

    def complete(query_state):
        completions.append(dict(query_state))
        return True

    result = run(
        retry_sync_failure_chain(
            failure,
            fetch_page,
            checkpoint,
            complete,
        )
    )

    assert result["success"] is True
    assert [call["cursor"] for call in calls] == ["cursor-middle", "cursor-last"]
    assert all(call["collection_ids"] == ["collection-a"] for call in calls)
    assert all(call["date_from"] == "2026-01-05T06:00:00Z" for call in calls)
    assert all(call["date_to"] == "2026-01-06T05:59:59Z" for call in calls)
    assert [state["cursor"] for state, error in checkpoints if error is None] == [
        "cursor-last"
    ]
    assert completions[0]["cursor"] is None
    assert sorted(stored) == ["record-1", "record-2", "record-3", "record-4"]


def test_retry_rejects_repeated_cursor_without_completing_failure():
    failure = _sync_failure()
    checkpoints = []
    completions = []

    async def fetch_page(query_state):
        return {
            "success": True,
            "successful": 1,
            "failed": 0,
            "next_cursor": query_state["cursor"],
        }

    result = run(
        retry_sync_failure_chain(
            failure,
            fetch_page,
            lambda state, error: checkpoints.append((state, error)) or True,
            lambda state: completions.append(state) or True,
        )
    )

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_data"
    assert result["error"]["details"]["cursor_repeated"] is True
    assert checkpoints[0][1]["details"]["cursor_repeated"] is True
    assert completions == []


def test_retry_checkpoints_next_cursor_and_propagates_second_failure():
    failure = _sync_failure()
    calls = []
    checkpoints = []
    completions = []
    source_error = {
        "code": "source_failure",
        "message": "Second retry page failed",
        "retryable": True,
        "details": {"status": 503},
    }

    async def fetch_page(query_state):
        calls.append(query_state["cursor"])
        if query_state["cursor"] == "cursor-middle":
            return {
                "success": True,
                "successful": 2,
                "failed": 0,
                "next_cursor": "cursor-last",
            }
        return {"success": False, "successful": 0, "failed": 0, "error": source_error}

    result = run(
        retry_sync_failure_chain(
            failure,
            fetch_page,
            lambda state, error: checkpoints.append((dict(state), error)) or True,
            lambda state: completions.append(state) or True,
        )
    )

    assert result["success"] is False
    assert result["error"] == source_error
    assert calls == ["cursor-middle", "cursor-last"]
    assert checkpoints[-1][0]["cursor"] == "cursor-last"
    assert checkpoints[-1][1] == source_error
    assert completions == []


def test_failed_page_persists_complete_utc_query_state_and_legacy_state_is_rejected():
    with patch.object(
        content_source_client.missing_original_container,
        "upsert_item",
    ) as upsert:
        failure_id = content_source_client.log_failed_sync_page(
            cursor="cursor-middle",
            limit=25,
            collection_ids=["collection-a"],
            date_from=datetime(2026, 1, 5, 3, tzinfo=timezone.utc),
            date_to=datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
            seen_cursor_fingerprints=[cursor_fingerprint("cursor-start")],
            error="transient",
            instance_id="sync-instance",
        )

    assert failure_id
    saved = upsert.call_args.args[0]
    assert saved["query_state"] == {
        "version": 1,
        "cursor": "cursor-middle",
        "page_size": 25,
        "collection_ids": ["collection-a"],
        "date_from": "2026-01-05T03:00:00Z",
        "date_to": "2026-01-06T03:00:00Z",
        "seen_cursor_fingerprints": sorted(
            {
                cursor_fingerprint("cursor-start"),
                cursor_fingerprint("cursor-middle"),
            }
        ),
    }

    with pytest.raises(ContentSourceError) as missing:
        sync_query_state_from_failure(
            {
                "cursor": "cursor-middle",
                "limit": 25,
                "collection_ids": ["collection-a"],
            }
        )
    assert missing.value.code is ContentSourceErrorCode.INVALID_QUERY
    assert missing.value.details["missing_query_state"] is True


def test_related_assets_drains_more_than_one_hundred_exactly_once():
    adapter = CursorAssetAdapter(205)
    with patch.object(content_source_client, "_adapter", return_value=adapter):
        result = run(
            content_source_client.fetch_related_assets(
                "synthetic-page-record",
                limit=100,
            )
        )
    ids = [item["id"] for item in result["data"]]
    assert len(ids) == 205
    assert len(set(ids)) == 205
    assert adapter.calls == [None, "asset-cursor-100", "asset-cursor-200"]
    assert result["next_cursor"] is None

    record = {
        "id": "synthetic-page-record",
        "record_id": "synthetic-page-record",
    }
    with patch.object(content_source_client.container, "upsert_item") as upsert:
        assert content_source_client.save_record_with_related_assets(record, ids)
    upsert.assert_called_once()
    stored = upsert.call_args.args[0]
    assert stored["related_assets"] == ids


def test_related_assets_propagates_failure_and_rejects_repeated_cursor():
    failing = CursorAssetAdapter(205, fail_at=100)
    with patch.object(content_source_client, "_adapter", return_value=failing):
        with pytest.raises(ContentSourceError) as failure:
            run(content_source_client.fetch_related_assets("synthetic-page-record"))
    assert failure.value.code is ContentSourceErrorCode.SOURCE_FAILURE
    assert failing.calls == [None, "asset-cursor-100"]

    repeated = CursorAssetAdapter(205, repeat_at=100)
    with patch.object(content_source_client, "_adapter", return_value=repeated):
        with pytest.raises(ContentSourceError) as cycle:
            run(content_source_client.fetch_related_assets("synthetic-page-record"))
    assert cycle.value.code is ContentSourceErrorCode.INVALID_DATA
    assert cycle.value.details["cursor_repeated"] is True


def test_related_assets_rejects_duplicate_ids_across_pages():
    asset = CursorAssetAdapter(1).assets[0]

    class DuplicateAdapter:
        async def query_assets(self, record_id, *, page_size=100, cursor=None):
            return OpaquePage(
                items=(asset,),
                next_cursor="second-page" if cursor is None else None,
                total=2,
            )

    with patch.object(
        content_source_client,
        "_adapter",
        return_value=DuplicateAdapter(),
    ):
        with pytest.raises(ContentSourceError) as duplicate:
            run(content_source_client.fetch_related_assets("synthetic-page-record"))
    assert duplicate.value.code is ContentSourceErrorCode.INVALID_DATA
    assert duplicate.value.details["asset_id"] == asset.id
