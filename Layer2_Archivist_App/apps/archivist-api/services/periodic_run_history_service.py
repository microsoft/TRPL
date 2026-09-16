# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Periodic sync run history (Cosmos ``periodic_run_history``, partition key ``/id``).

Written when the API starts the Azure Function ``content-source-periodic-sync`` (see ``record_periodic_sync_started``).
**All** triggered runs are persisted (including adhoc calendar windows) for the Batch / History UI.

Schedule **definitions** share this container with ``entity_type == "schedule"`` (see ``periodic_schedule_service``).
Run rows use ``entity_type == "run"`` (or omit for legacy documents).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos import exceptions

from core.config import settings
from services.cosmos_service import get_cosmos_client

logger = logging.getLogger(__name__)


def get_periodic_history_container():
    name = settings.cosmos_db_periodic_run_history_container_name
    if not name or not settings.cosmos_db_database_name:
        raise ValueError("periodic_run_history container is not configured")
    db = get_cosmos_client().get_database_client(settings.cosmos_db_database_name)
    return db.get_container_client(name)


def _is_manual_calendar_adhoc(payload: Dict[str, Any]) -> bool:
    """
    Calendar **From** and **To** both set, not a chain, not a scheduled automation trigger.
    Explicit ``periodic_run_origin=adhoc`` counts; ``scheduled`` does not.
    """
    fd = (str(payload.get("from_date") or "").strip())
    td = (str(payload.get("to_date") or "").strip())
    if not (bool(fd) and bool(td)):
        return False
    if str(payload.get("previous_run_id") or "").strip():
        return False
    origin = str(payload.get("periodic_run_origin") or payload.get("run_origin") or "").strip().lower()
    if origin == "scheduled":
        return False
    if origin and origin != "adhoc":
        return False
    return True


def _derive_run_workflow(payload: Dict[str, Any]) -> str:
    """High-level workflow for UI / batch history (``run_workflow`` on the Cosmos row)."""
    if str(payload.get("schedule_id") or "").strip():
        return "scheduled"
    origin = str(payload.get("periodic_run_origin") or payload.get("run_origin") or "").strip().lower()
    if origin == "scheduled":
        return "scheduled"
    if origin == "adhoc":
        return "adhoc"
    if _is_manual_calendar_adhoc(payload):
        return "adhoc"
    if str(payload.get("previous_run_id") or "").strip():
        return "continue"
    return "lookback"


def _derive_run_kind(payload: Dict[str, Any]) -> str:
    """Cosmos ``run_kind`` (legacy coarse enum)."""
    origin = str(payload.get("periodic_run_origin") or payload.get("run_origin") or "").strip().lower()
    if origin == "scheduled":
        return "scheduled"
    if origin == "adhoc" or _is_manual_calendar_adhoc(payload):
        return "adhoc"
    if str(payload.get("previous_run_id") or "").strip():
        return "continue"
    return "lookback"


def _resolved_history_dates(
    request_payload: Dict[str, Any],
    started_at: datetime,
) -> tuple[Optional[str], Optional[str], bool]:
    fd_raw = request_payload.get("from_date")
    td_raw = request_payload.get("to_date")
    fd = str(fd_raw).strip() if fd_raw is not None and str(fd_raw).strip() != "" else None
    td = str(td_raw).strip() if td_raw is not None and str(td_raw).strip() != "" else None

    if fd is not None and td is not None:
        return fd, td, False

    try:
        lb = int(request_payload.get("lookback_hours") or 0)
    except (TypeError, ValueError):
        lb = 0

    if fd is None and td is None and lb > 0:
        to_dt = started_at.astimezone(timezone.utc)
        from_dt = to_dt - timedelta(hours=lb)
        return from_dt.date().isoformat(), to_dt.date().isoformat(), True

    if fd is not None and td is None and lb > 0:
        to_d = started_at.astimezone(timezone.utc).date().isoformat()
        return fd, to_d, True

    return fd, td, False


def record_periodic_sync_started(
    instance_id: str,
    request_payload: Dict[str, Any],
    trigger_result: Dict[str, Any],
    started_by: str,
) -> None:
    """
    Upsert one periodic sync **run** row after ``content-source-periodic-sync`` returns an orchestration instance id.
    """
    oid = (instance_id or "").strip()
    if not oid:
        return
    try:
        started_at = datetime.now(timezone.utc)
        from_date, to_date, lookback_derived = _resolved_history_dates(request_payload, started_at)
        collection_ids = request_payload.get("collection_ids") or []
        if not isinstance(collection_ids, list):
            collection_ids = []
        run_kind = _derive_run_kind(request_payload)
        run_workflow = _derive_run_workflow(request_payload)
        prev_raw = str(request_payload.get("previous_run_id") or "").strip()
        origin_raw = str(request_payload.get("periodic_run_origin") or request_payload.get("run_origin") or "").strip()
        collection_names = request_payload.get("collection_names") or []
        if not isinstance(collection_names, list):
            collection_names = []
        repository_names = [str(x).strip() for x in collection_ids if x is not None and str(x).strip() != ""]
        body: Dict[str, Any] = {
            "id": oid,
            "entity_type": "run",
            "orchestration_instance_id": oid,
            "started_at": started_at.isoformat(),
            "started_by": started_by or "",
            "from_date": from_date,
            "to_date": to_date,
            "lookback_derived_window": lookback_derived,
            "chain_continuation": bool(prev_raw),
            "lookback_hours": int(request_payload.get("lookback_hours") or 168),
            "run_all_stages": bool(request_payload.get("run_all_stages", True)),
            "collection_ids": repository_names,
            "repository_names": repository_names,
            "collection_names": [str(x).strip() for x in collection_names if x is not None and str(x).strip() != ""],
            "batch_size": int(request_payload.get("batch_size") or 100),
            "status_url": trigger_result.get("status_url"),
            "terminate_url": trigger_result.get("terminate_url"),
            "previous_run_id": (prev_raw or None),
            "run_kind": run_kind,
            "run_workflow": run_workflow,
            "completed_at": None,
            "records_ingested": None,
        }
        if origin_raw:
            body["periodic_run_origin"] = origin_raw
        sid = str(request_payload.get("schedule_id") or "").strip()
        if sid:
            body["schedule_id"] = sid
        if request_payload.get("parallel_batches") is not None:
            try:
                body["parallel_batches"] = int(request_payload["parallel_batches"])
            except (TypeError, ValueError):
                pass
        get_periodic_history_container().upsert_item(body)
        logger.info("Recorded periodic sync run history %s (%s / %s)", oid, run_kind, run_workflow)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to record periodic sync run %s: %s", oid, exc)


def _is_stored_manual_calendar_adhoc(doc: Dict[str, Any]) -> bool:
    """Calendar-only adhoc rows (explicit From/To, not lookback-derived or scheduled)."""
    wf = str(doc.get("run_workflow") or "").strip().lower()
    if wf == "adhoc":
        return True
    if str(doc.get("run_kind") or "").strip().lower() == "adhoc":
        return True
    if doc.get("lookback_derived_window") is True:
        return False
    if doc.get("chain_continuation") is True:
        return False
    if str(doc.get("run_kind") or "").strip().lower() == "scheduled":
        return False
    if str(doc.get("periodic_run_origin") or doc.get("run_origin") or "").strip():
        return False
    fd = str(doc.get("from_date") or "").strip()
    td = str(doc.get("to_date") or "").strip()
    return bool(fd) and bool(td)


def _is_run_row(doc: Dict[str, Any]) -> bool:
    et = doc.get("entity_type")
    if et is None or et == "":
        return True
    return str(et) == "run"


def list_periodic_sync_runs(
    limit: int = 50,
    *,
    exclude_adhoc: bool = False,
    run_workflow: Optional[str] = None,
) -> List[Dict[str, Any]]:
    q = "SELECT * FROM c WHERE (NOT IS_DEFINED(c.entity_type) OR c.entity_type = 'run')"
    items = list(
        get_periodic_history_container().query_items(
            query=q,
            enable_cross_partition_query=True,
        )
    )
    if run_workflow:
        rw = str(run_workflow).strip().lower()
        items = [d for d in items if str(d.get("run_workflow") or "").strip().lower() == rw]
    if exclude_adhoc:
        items = [d for d in items if not _is_stored_manual_calendar_adhoc(d)]
    items.sort(key=lambda d: d.get("started_at") or "", reverse=True)
    return items[: max(1, min(limit, 200))]


def _completed_at_iso(last_updated_time: Optional[str]) -> str:
    """Prefer Durable ``lastUpdatedTime`` when parseable; else UTC now."""
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
    """
    Content Source rows touched in the sync stage (``ContentSourceSync`` ``total_processed_so_far``).

    Do not sum across later stages (same logical records).
    """
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
    if isinstance(tp, int):
        return max(0, tp)
    if isinstance(tp, float):
        return max(0, int(tp))
    if isinstance(tp, str) and tp.strip():
        try:
            return max(0, int(float(tp.strip())))
        except ValueError:
            return None
    return None


_TERMINAL_ORCH_STATUSES = frozenset({"Completed", "Failed", "Terminated", "Canceled"})


def record_periodic_sync_finished(
    instance_id: str,
    *,
    runtime_status: str,
    output: Any = None,
    last_updated_time: Optional[str] = None,
) -> None:
    """
    Patch a stored periodic sync **run** row when the durable orchestration reaches a terminal status.

    Called from the pipeline job PATCH handler (browser polling) and may run again idempotently.
    """
    oid = (instance_id or "").strip()
    if not oid:
        return
    rs = str(runtime_status or "").strip()
    if rs not in _TERMINAL_ORCH_STATUSES:
        return
    try:
        doc = get_periodic_sync_run(oid)
        if not doc:
            logger.debug("periodic run completion: no history row for instance %s", oid)
            return
        completed = _completed_at_iso(last_updated_time)
        rec = _records_ingested_from_periodic_output(output)
        doc["completed_at"] = completed
        if rec is not None:
            doc["records_ingested"] = rec
        get_periodic_history_container().upsert_item(doc)
        logger.info(
            "Updated periodic run history %s: completed_at set, records_ingested=%s",
            oid,
            rec,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to update periodic run completion %s: %s", oid, exc)


def get_periodic_sync_run(run_id: str) -> Optional[Dict[str, Any]]:
    rid = (run_id or "").strip()
    if not rid:
        return None
    try:
        doc = dict(get_periodic_history_container().read_item(item=rid, partition_key=rid))
    except exceptions.CosmosResourceNotFoundError:
        return None
    if not _is_run_row(doc):
        return None
    return doc
