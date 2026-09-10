"""
Periodic schedule + run history helpers (Cosmos ``periodic_run_history``).

This module is intentionally aligned with Archivist's schedule/history contract:
- Schedule documents: ``entity_type == "schedule"``
- Run rows: ``entity_type == "run"`` (or missing for legacy)

Data Foundations uses this for the timer-based schedule poller that starts
``content-source-periodic-sync`` without requiring Archivist to call "process-due".
"""

from __future__ import annotations

from calendar import monthrange
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from azure.cosmos import exceptions

from helper.cosmos_client import get_container

logger = logging.getLogger(__name__)


def _parse_iso_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def ingestion_days_from_recurrence(recurrence: str) -> int:
    """Calendar days ingested per run (UTC), aligned with repeat cadence."""
    r = str(recurrence or "none").strip().lower()
    if r == "daily":
        return 1
    if r == "weekly":
        return 7
    if r == "monthly":
        return 30
    if r == "yearly":
        return 365
    return 1


def _database_name() -> Optional[str]:
    # Prefer the Functions convention; allow Archivist-style fallback if present.
    db = (os.getenv("COSMOS_DATABASE_NAME") or os.getenv("COSMOS_DB_DATABASE_NAME") or "").strip()
    return db or None


def _container_name() -> Optional[str]:
    name = (
        os.getenv("COSMOS_DB_PERIODIC_RUN_HISTORY_CONTAINER_NAME")
        or os.getenv("COSMOS_PERIODIC_RUN_HISTORY_CONTAINER_NAME")
        or "periodic_run_history"
    )
    s = (name or "").strip()
    return s or None


def periodic_run_history_container():
    """Return Cosmos container client or None if not configured."""
    dbn = _database_name()
    cn = _container_name()
    if not dbn or not cn:
        logger.warning(
            "periodic_run_history not configured "
            "(need COSMOS_DATABASE_NAME and periodic container env)"
        )
        return None
    try:
        return get_container(cn, database_name=dbn)
    except (ValueError, exceptions.CosmosHttpResponseError) as exc:
        logger.warning("Could not open periodic_run_history container: %s", exc)
        return None


def resolve_schedule_ingestion_calendar_window(
    doc: Dict[str, Any],
    *,
    run_at: datetime,
) -> Tuple[str, str]:
    """
    ``(from_date, to_date)`` as ``YYYY-MM-DD`` for ``content-source-periodic-sync``.

    1) Legacy: fixed ``window_from`` / ``window_to`` when both are set.
    2) Else optional explicit ``ingestion_window_days`` on older documents (1–366).
    3) Else rolling window from ``recurrence`` (daily=1, weekly=7, etc).
    """
    wf = str(doc.get("window_from") or "").strip()
    wt = str(doc.get("window_to") or "").strip()
    if wf and wt:
        return wf, wt

    raw = doc.get("ingestion_window_days")
    n = 0
    if raw is not None and str(raw).strip() != "":
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 0
    if not 1 <= n <= 366:
        n = ingestion_days_from_recurrence(str(doc.get("recurrence") or "none"))

    at = run_at if run_at.tzinfo else run_at.replace(tzinfo=timezone.utc)
    to_day = at.astimezone(timezone.utc).date()
    from_day = to_day - timedelta(days=n - 1)
    return from_day.isoformat(), to_day.isoformat()


def list_due_schedules(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Return enabled schedule docs whose ``next_run_at`` is due (UTC)."""
    c = periodic_run_history_container()
    if c is None:
        return []
    now = datetime.now(timezone.utc)
    q = (
        "SELECT * FROM c WHERE c.entity_type = 'schedule' AND c.enabled = true "
        "AND IS_DEFINED(c.next_run_at)"
    )
    try:
        items = list(c.query_items(query=q, enable_cross_partition_query=True))
    except exceptions.CosmosHttpResponseError as exc:
        logger.exception("list_due_schedules query failed: %s", exc)
        return []
    due: List[Dict[str, Any]] = []
    for d in items:
        nd = _parse_iso_dt(d.get("next_run_at"))
        if nd is not None and nd <= now:
            due.append(d)
    due.sort(key=lambda d: (_parse_iso_dt(d.get("next_run_at")) or now))
    return due[: max(1, min(limit, 50))]


def get_schedule(schedule_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one schedule document by id (returns None if missing / wrong entity_type)."""
    sid = (schedule_id or "").strip()
    if not sid:
        return None
    c = periodic_run_history_container()
    if c is None:
        return None
    try:
        doc = dict(c.read_item(item=sid, partition_key=sid))
    except exceptions.CosmosResourceNotFoundError:
        return None
    if doc.get("entity_type") != "schedule":
        return None
    return doc


def _advance_next_run(*, recurrence: str, from_dt: datetime) -> Optional[datetime]:
    r = str(recurrence or "none").strip().lower()
    if r == "none":
        return None
    if r == "daily":
        return from_dt + timedelta(days=1)
    if r == "weekly":
        return from_dt + timedelta(weeks=1)
    if r == "monthly":
        month_index = from_dt.month - 1 + 1
        year = from_dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(from_dt.day, monthrange(year, month)[1])
        return from_dt.replace(year=year, month=month, day=day)
    if r == "yearly":
        month_index = from_dt.month - 1 + 12
        year = from_dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(from_dt.day, monthrange(year, month)[1])
        return from_dt.replace(year=year, month=month, day=day)
    return None


def mark_schedule_after_trigger(
    schedule_id: str, *, last_run_at: Optional[datetime] = None
) -> None:
    """After a successful orchestration start, advance or disable the schedule."""
    doc = get_schedule(schedule_id)
    if not doc:
        return
    c = periodic_run_history_container()
    if c is None:
        return
    last = last_run_at or datetime.now(timezone.utc)
    recurrence = str(doc.get("recurrence") or "none").lower()
    doc["last_run_at"] = last.isoformat()
    doc["recurring"] = recurrence != "none"
    if recurrence != "none":
        next_dt = _advance_next_run(recurrence=recurrence, from_dt=last)
        doc["next_run_at"] = next_dt.isoformat() if next_dt else None
        if not doc.get("next_run_at"):
            doc["enabled"] = False
    else:
        doc["next_run_at"] = None
        doc["enabled"] = False
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        c.upsert_item(doc)
        logger.info(
            "Updated schedule %s after trigger (next_run_at=%s)",
            schedule_id,
            doc.get("next_run_at"),
        )
    except exceptions.CosmosHttpResponseError as exc:
        logger.exception("mark_schedule_after_trigger failed for %s: %s", schedule_id, exc)


def _is_run_history_row(doc: Dict[str, Any]) -> bool:
    et = doc.get("entity_type")
    if et is None or et == "":
        return True
    return str(et) == "run"


def _completed_at_iso_from_durable(last_updated_time: Optional[str]) -> str:
    if last_updated_time and str(last_updated_time).strip():
        s = str(last_updated_time).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _records_ingested_from_periodic_output(output: Any) -> Optional[int]:
    if not isinstance(output, dict):
        return None

    sr = output.get("stage_results")
    if not isinstance(sr, dict):
        return None

    content_source = sr.get("ContentSourceSync")
    if not isinstance(content_source, dict):
        return None

    tp = content_source.get("total_processed_so_far")
    if isinstance(tp, bool):
        return None

    rec: Optional[int] = None
    if isinstance(tp, int):
        rec = tp
    elif isinstance(tp, float):
        rec = int(tp)
    elif isinstance(tp, str) and tp.strip():
        try:
            rec = int(float(tp.strip()))
        except ValueError:
            rec = None

    return max(0, rec) if rec is not None else None


_TERMINAL = frozenset({"Completed", "Failed", "Terminated", "Canceled"})


def record_periodic_sync_started_from_timer(
    instance_id: str,
    request_payload: Dict[str, Any],
    trigger_result: Dict[str, Any],
    started_by: str,
) -> None:
    """
    Upsert one periodic sync **run** row (entity_type=run) for a scheduled start.
    """
    oid = (instance_id or "").strip()
    if not oid:
        return
    c = periodic_run_history_container()
    if c is None:
        logger.warning("record_periodic_sync_started_from_timer: no periodic_run_history container")
        return
    try:
        started_at = datetime.now(timezone.utc)
        fd = str(request_payload.get("from_date") or "").strip()
        td = str(request_payload.get("to_date") or "").strip()
        collection_ids = request_payload.get("collection_ids") or []
        if not isinstance(collection_ids, list):
            collection_ids = []
        collection_names = request_payload.get("collection_names") or []
        if not isinstance(collection_names, list):
            collection_names = []
        repository_names = [
            str(x).strip() for x in collection_ids if x is not None and str(x).strip() != ""
        ]
        body: Dict[str, Any] = {
            "id": oid,
            "entity_type": "run",
            "orchestration_instance_id": oid,
            "started_at": started_at.isoformat(),
            "started_by": started_by or "",
            "from_date": fd or None,
            "to_date": td or None,
            "lookback_derived_window": False,
            "chain_continuation": False,
            "lookback_hours": int(request_payload.get("lookback_hours") or 168),
            "run_all_stages": bool(request_payload.get("run_all_stages", True)),
            "collection_ids": repository_names,
            "repository_names": repository_names,
            "collection_names": [
                str(x).strip()
                for x in collection_names
                if x is not None and str(x).strip() != ""
            ],
            "batch_size": int(request_payload.get("batch_size") or 100),
            "status_url": trigger_result.get("status_url"),
            "terminate_url": trigger_result.get("terminate_url"),
            "previous_run_id": None,
            "run_kind": "scheduled",
            "run_workflow": "scheduled",
            "completed_at": None,
            "records_ingested": None,
            "periodic_run_origin": "scheduled",
        }
        sid = str(request_payload.get("schedule_id") or "").strip()
        if sid:
            body["schedule_id"] = sid
        if request_payload.get("parallel_batches") is not None:
            try:
                body["parallel_batches"] = int(request_payload["parallel_batches"])
            except (TypeError, ValueError):
                pass
        c.upsert_item(body)
        logger.info("Recorded periodic run history for timer start %s (scheduled)", oid)
    except (TypeError, ValueError, KeyError, exceptions.CosmosHttpResponseError) as exc:
        logger.warning("record_periodic_sync_started_from_timer failed for %s: %s", oid, exc)


def merge_periodic_run_history_completion(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch a periodic run row when ``content-source-periodic-sync`` orchestration finishes.
    """
    oid = str(payload.get("instance_id") or "").strip()
    rs = str(payload.get("runtime_status") or "").strip()
    output = payload.get("output")
    last_updated = payload.get("last_updated_time")
    if not oid or rs not in _TERMINAL:
        return {"success": False, "skipped": True}
    c = periodic_run_history_container()
    if c is None:
        return {"success": False, "error": "container_unavailable"}
    try:
        doc = dict(c.read_item(item=oid, partition_key=oid))
    except exceptions.CosmosResourceNotFoundError:
        logger.debug("merge periodic run: no document %s", oid)
        return {"success": False, "skipped": True}
    if not _is_run_history_row(doc):
        return {"success": False, "skipped": True}
    doc["completed_at"] = _completed_at_iso_from_durable(
        str(last_updated) if last_updated is not None else None
    )
    rec = _records_ingested_from_periodic_output(output)
    if rec is not None:
        doc["records_ingested"] = rec
    try:
        c.upsert_item(doc)
        logger.info(
            "merge periodic run history %s completed_at=%s records=%s",
            oid,
            doc.get("completed_at"),
            rec,
        )
        return {"success": True}
    except (TypeError, ValueError, KeyError, exceptions.CosmosHttpResponseError) as exc:
        logger.exception("merge_periodic_run_history_completion failed: %s", exc)
        return {"success": False, "error": str(exc)}
