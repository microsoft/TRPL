"""Periodic ingestion schedules stored in the same Cosmos container as run history.

Documents use ``entity_type == "schedule"``. Run rows omit ``entity_type`` or use ``"run"``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from azure.cosmos import exceptions

from services.periodic_run_history_service import get_periodic_history_container

logger = logging.getLogger(__name__)

VALID_RECURRENCE = frozenset({"none", "daily", "weekly", "monthly", "yearly"})


def repeat_stride_calendar_days(recurrence: str) -> Optional[int]:
    """
    UTC calendar days per repeat for ``daily`` / ``weekly`` / ``monthly`` / ``yearly``.

    Same stride drives: (1) repeat-based ingestion period tiling from ``schedule_start_date``,
    (2) ``ingestion_days_from_recurrence`` when ``ingestion_window_days`` is absent, and
    (3) ``next_run_at`` advancement via :func:`_advance_next_run`. ``none`` → ``None``.
    """
    r = str(recurrence or "none").strip().lower()
    if r == "none":
        return None
    if r == "daily":
        return 1
    if r == "weekly":
        return 7
    if r == "monthly":
        return 30
    if r == "yearly":
        return 365
    return None


def ingestion_days_from_recurrence(recurrence: str) -> int:
    """Calendar days per run when ``ingestion_window_days`` is absent (same as repeat stride, else 1)."""
    d = repeat_stride_calendar_days(recurrence)
    return d if d is not None else 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _is_repeat_based_rolling_schedule(doc: Dict[str, Any]) -> bool:
    wf = str(doc.get("window_from") or "").strip()
    wt = str(doc.get("window_to") or "").strip()
    if wf and wt:
        return False
    return bool(str(doc.get("schedule_start_date") or "").strip())


def _schedule_anchor_date_from_doc(doc: Dict[str, Any]) -> date:
    """UTC calendar day when the schedule was first saved (or migrated from ``created_at``)."""
    raw = str(doc.get("ingestion_period_anchor_date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    ca = _parse_iso_dt(doc.get("created_at"))
    if ca is not None:
        return ca.astimezone(timezone.utc).date()
    ssd = str(doc.get("schedule_start_date") or "").strip()
    if ssd:
        try:
            return date.fromisoformat(ssd)
        except ValueError:
            pass
    return _utcnow().date()


def _period_bounds_for_occurrence(occurrence_day: date, d0: date, n: int) -> Tuple[date, date]:
    """
    Inclusive UTC calendar window [start, end] for the repeat block containing ``occurrence_day``.

    For recurrence-derived windows, ``n`` must equal :func:`repeat_stride_calendar_days` for that
    recurrence so period boundaries stay aligned with ``next_run_at`` (daily 1, weekly 7, monthly 30, yearly 365).
    """
    n = max(1, min(int(n), 366))
    delta = (occurrence_day - d0).days
    if delta < 0:
        idx = 0
    else:
        idx = delta // n
    start = d0 + timedelta(days=idx * n)
    end = start + timedelta(days=n - 1)
    return start, end


def resolve_schedule_ingestion_calendar_window(
    doc: Dict[str, Any],
    *,
    run_at: datetime,
    occurrence_at: Optional[datetime] = None,
) -> Tuple[str, str]:
    """
    ``(from_date, to_date)`` as ``YYYY-MM-DD`` for ``content-source-periodic-sync``.

    1. Legacy: fixed ``window_from`` / ``window_to`` when both are set.
    2. Repeat-based (``schedule_start_date`` without legacy windows): calendar periods of length ``n``
       from ``schedule_start_date`` (``n`` from ``ingestion_window_days`` or the same stride as recurrence:
       daily 1, weekly 7, monthly 30, yearly 365). The slot being
       executed is ``occurrence_at`` (defaults to ``run_at``); ``to_date`` is capped at the run day so
       early **Run now** does not ingest future dates. ``from_date`` uses the period start, or the
       schedule anchor (save date) when it falls before the period start.
    3. Else optional explicit ``ingestion_window_days`` on older documents (1–366).
    4. Else rolling window from ``recurrence`` ending on ``run_at``'s UTC date (legacy).
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

    run_at_utc = run_at if run_at.tzinfo else run_at.replace(tzinfo=timezone.utc)
    run_day = run_at_utc.astimezone(timezone.utc).date()

    if _is_repeat_based_rolling_schedule(doc):
        occ = occurrence_at or run_at_utc
        occ_utc = occ if occ.tzinfo else occ.replace(tzinfo=timezone.utc)
        occ_day = occ_utc.astimezone(timezone.utc).date()
        ssd = str(doc.get("schedule_start_date") or "").strip()
        try:
            d0 = date.fromisoformat(ssd)
        except ValueError:
            pass
        else:
            ps, pe = _period_bounds_for_occurrence(occ_day, d0, n)
            anchor = _schedule_anchor_date_from_doc(doc)
            has_prior_run = bool(str(doc.get("last_run_at") or "").strip())
            if anchor >= ps:
                eff_from = ps
            elif not has_prior_run and (ps - anchor).days <= n:
                # First run only: include days from save through period start (e.g. saved 27th, first slot 28th).
                eff_from = anchor
            else:
                eff_from = ps
            eff_to = min(pe, run_day)
            eff_from = min(eff_from, eff_to)
            return eff_from.isoformat(), eff_to.isoformat()

    to_day = run_day
    from_day = to_day - timedelta(days=n - 1)
    return from_day.isoformat(), to_day.isoformat()


def validate_and_normalize_schedule_start_time_utc(raw: Optional[str]) -> str:
    """``HH:MM`` 24-hour UTC. Empty / missing → ``00:00``."""
    if not raw or not str(raw).strip():
        return "00:00"
    s = str(raw).strip()
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError("schedule_start_time_utc must be HH:MM (24-hour UTC).")
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError as e:
        raise ValueError("schedule_start_time_utc must use numeric hours and minutes.") from e
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("schedule_start_time_utc: hours must be 0–23, minutes 0–59.")
    return f"{h:02d}:{m:02d}"


def next_run_at_from_schedule_start(
    schedule_start_date: str,
    *,
    schedule_start_time_utc: Optional[str] = None,
    now: Optional[datetime] = None,
) -> datetime:
    """First ``next_run_at`` on ``schedule_start_date`` at ``schedule_start_time_utc`` (default midnight UTC), or ``now`` if past."""
    now = now or _utcnow()
    d0 = date.fromisoformat(schedule_start_date.strip())
    tnorm = validate_and_normalize_schedule_start_time_utc(schedule_start_time_utc)
    h = int(tnorm[:2])
    m = int(tnorm[3:5])
    start = datetime(d0.year, d0.month, d0.day, h, m, 0, tzinfo=timezone.utc)
    return start if start >= now else now


def _attach_next_ingestion_calendar_fields(doc: Dict[str, Any]) -> None:
    """
    Denormalize the calendar window for the **next** scheduled trigger (``next_run_at``) for Cosmos clarity.

    Sets ``next_ingestion_from_date`` / ``next_ingestion_to_date`` as ``YYYY-MM-DD`` (same semantics as
    ``resolve_schedule_ingestion_calendar_window``). Removes them when ``next_run_at`` is missing.
    """
    nd = _parse_iso_dt(doc.get("next_run_at"))
    if nd is None:
        doc.pop("next_ingestion_from_date", None)
        doc.pop("next_ingestion_to_date", None)
        return
    wf, wt = resolve_schedule_ingestion_calendar_window(doc, run_at=nd, occurrence_at=nd)
    doc["next_ingestion_from_date"] = wf
    doc["next_ingestion_to_date"] = wt


def _advance_next_run(*, recurrence: str, from_dt: datetime) -> Optional[datetime]:
    days = repeat_stride_calendar_days(recurrence)
    if days is None:
        return None
    return from_dt + timedelta(days=days)


def upsert_schedule(body: Dict[str, Any], user_display: str) -> Dict[str, Any]:
    c = get_periodic_history_container()
    sid = str(body.get("id") or "").strip() or str(uuid.uuid4())
    ex = get_schedule(sid) or {}
    now = _utcnow().isoformat()
    recurrence = str(body.get("recurrence", ex.get("recurrence") or "none")).strip().lower()
    if recurrence not in VALID_RECURRENCE:
        recurrence = "none"
    collection_ids = body.get("collection_ids", ex.get("collection_ids") or [])
    if not isinstance(collection_ids, list):
        collection_ids = []
    collection_names = body.get("collection_names", ex.get("collection_names") or [])
    if not isinstance(collection_names, list):
        collection_names = []

    wf = str(body.get("window_from", ex.get("window_from") or "")).strip()
    wt = str(body.get("window_to", ex.get("window_to") or "")).strip()
    legacy = bool(wf and wt)

    ssd = str(body.get("schedule_start_date") or ex.get("schedule_start_date") or "").strip()
    recurring = recurrence != "none"
    doc_start_time_utc = "00:00"

    if legacy:
        window_from = wf
        window_to = wt
        ingestion_window_days = body.get("ingestion_window_days", ex.get("ingestion_window_days"))
        schedule_start_date = str(body.get("schedule_start_date") or ex.get("schedule_start_date") or "").strip()
        doc_start_time_utc = validate_and_normalize_schedule_start_time_utc(
            str(body.get("schedule_start_time_utc") or ex.get("schedule_start_time_utc") or "").strip() or None
        )
        first_run_at = _parse_iso_dt(body.get("first_run_at")) or _parse_iso_dt(ex.get("first_run_at")) or _utcnow()
        next_dt = _parse_iso_dt(body.get("next_run_at")) or _parse_iso_dt(ex.get("next_run_at")) or first_run_at
        first_run_iso = first_run_at.isoformat()
        next_iso = next_dt.isoformat()
    elif ssd:
        window_from = ""
        window_to = ""
        ingestion_window_days = None
        schedule_start_date = ssd
        st_raw = str(body.get("schedule_start_time_utc") or ex.get("schedule_start_time_utc") or "").strip()
        schedule_start_time_utc = validate_and_normalize_schedule_start_time_utc(st_raw or None)
        doc_start_time_utc = schedule_start_time_utc
        d0 = date.fromisoformat(ssd)
        h = int(schedule_start_time_utc[:2])
        m = int(schedule_start_time_utc[3:5])
        first_run_at = datetime(d0.year, d0.month, d0.day, h, m, 0, tzinfo=timezone.utc)
        next_override = _parse_iso_dt(body.get("next_run_at"))
        next_dt = next_override or next_run_at_from_schedule_start(
            ssd,
            schedule_start_time_utc=schedule_start_time_utc,
        )
        first_run_iso = first_run_at.isoformat()
        next_iso = next_dt.isoformat()
    else:
        raise ValueError(
            "Schedule requires either schedule_start_date (repeat-based rolling window) "
            "or legacy window_from and window_to."
        )

    doc: Dict[str, Any] = {
        "id": sid,
        "entity_type": "schedule",
        "title": str(body.get("title", ex.get("title") or "")).strip() or "Scheduled run",
        "enabled": bool(body.get("enabled", ex.get("enabled", True))),
        "run_all_stages": bool(body.get("run_all_stages", ex.get("run_all_stages", True))),
        "collection_ids": collection_ids,
        "collection_names": [str(x).strip() for x in collection_names if str(x).strip()],
        "batch_size": int(body.get("batch_size", ex.get("batch_size") or 100)),
        "parallel_batches": int(body.get("parallel_batches", ex.get("parallel_batches") or 20)),
        "lookback_hours": int(body.get("lookback_hours", ex.get("lookback_hours") or 168)),
        "schedule_start_date": schedule_start_date,
        "schedule_start_time_utc": doc_start_time_utc,
        "recurrence": recurrence,
        "recurring": recurring,
        "first_run_at": first_run_iso,
        "next_run_at": next_iso,
        "last_run_at": body.get("last_run_at", ex.get("last_run_at")),
        "created_by": str(ex.get("created_by") or user_display),
        "updated_by": user_display,
        "updated_at": now,
        "created_at": str(ex.get("created_at") or now),
    }
    iap_existing = str(ex.get("ingestion_period_anchor_date") or "").strip()
    iap_body = str(body.get("ingestion_period_anchor_date") or "").strip()
    if iap_body:
        doc["ingestion_period_anchor_date"] = iap_body
    elif iap_existing:
        doc["ingestion_period_anchor_date"] = iap_existing
    elif not ex:
        doc["ingestion_period_anchor_date"] = str(_utcnow().date())
    else:
        ca = _parse_iso_dt(ex.get("created_at"))
        doc["ingestion_period_anchor_date"] = (
            ca.astimezone(timezone.utc).date().isoformat() if ca else str(_utcnow().date())
        )
    # Rolling / repeat-based schedules do not use fixed windows; omit keys so Cosmos docs stay lean.
    if legacy:
        doc["window_from"] = window_from
        doc["window_to"] = window_to
        if ingestion_window_days is not None:
            doc["ingestion_window_days"] = ingestion_window_days
    _attach_next_ingestion_calendar_fields(doc)
    c.upsert_item(doc)
    return doc


def list_schedules(*, limit: int = 100) -> List[Dict[str, Any]]:
    c = get_periodic_history_container()
    q = "SELECT * FROM c WHERE c.entity_type = 'schedule'"
    items = list(c.query_items(query=q, enable_cross_partition_query=True))
    items.sort(key=lambda d: d.get("next_run_at") or "", reverse=False)
    return items[: max(1, min(limit, 200))]


def get_schedule(schedule_id: str) -> Optional[Dict[str, Any]]:
    sid = (schedule_id or "").strip()
    if not sid:
        return None
    try:
        doc = dict(get_periodic_history_container().read_item(item=sid, partition_key=sid))
    except exceptions.CosmosResourceNotFoundError:
        return None
    if doc.get("entity_type") != "schedule":
        return None
    return doc


def delete_schedule(schedule_id: str) -> bool:
    sid = (schedule_id or "").strip()
    if not sid:
        return False
    try:
        get_periodic_history_container().delete_item(item=sid, partition_key=sid)
        return True
    except exceptions.CosmosResourceNotFoundError:
        return False


def mark_schedule_after_trigger(schedule_id: str, *, last_run_at: datetime) -> None:
    """
    Record that a run started and advance ``next_run_at`` by one recurrence step.

    ``next_run_at`` advances from the **planned slot** stored on the document (the schedule clock),
    not from ``last_run_at``. That way **Run now** a few minutes early still moves the next run to
    the following calendar slot (e.g. daily 28th 03:00 → 29th 03:00), not ``now`` + 1 day.
    """
    max_retries = 3
    for attempt in range(max_retries):
        doc = get_schedule(schedule_id)
        if not doc:
            return
        etag = doc.get("_etag")
        recurrence = str(doc.get("recurrence") or "none").lower()
        planned = _parse_iso_dt(doc.get("next_run_at"))
        anchor = planned if planned is not None else last_run_at
        doc["last_run_at"] = last_run_at.isoformat()
        doc["recurring"] = recurrence != "none"
        if recurrence != "none":
            next_dt = _advance_next_run(recurrence=recurrence, from_dt=anchor)
            doc["next_run_at"] = next_dt.isoformat() if next_dt else None
            if not doc.get("next_run_at"):
                doc["enabled"] = False
        else:
            doc["next_run_at"] = None
            doc["enabled"] = False
        _attach_next_ingestion_calendar_fields(doc)
        doc["updated_at"] = _utcnow().isoformat()
        options: Dict[str, Any] = {}
        if etag:
            options["if_match"] = etag
        try:
            get_periodic_history_container().replace_item(item=schedule_id, body=doc, **options)
            return
        except exceptions.CosmosAccessConditionFailedError:
            if attempt < max_retries - 1:
                logger.warning(
                    "ETag conflict in mark_schedule_after_trigger for %s (attempt %d/%d)",
                    schedule_id, attempt + 1, max_retries,
                )
            else:
                logger.error("Failed to update schedule %s after %d retries", schedule_id, max_retries)


def list_due_schedules(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Schedules whose ``next_run_at`` is in the past or now (parsed ISO; avoids string-compare pitfalls)."""
    c = get_periodic_history_container()
    now = _utcnow()
    q = (
        "SELECT * FROM c WHERE c.entity_type = 'schedule' AND c.enabled = true "
        "AND IS_DEFINED(c.next_run_at)"
    )
    items = list(
        c.query_items(
            query=q,
            enable_cross_partition_query=True,
        )
    )
    due: List[Dict[str, Any]] = []
    for d in items:
        nd = _parse_iso_dt(d.get("next_run_at"))
        if nd is not None and nd <= now:
            due.append(d)
    due.sort(key=lambda d: (_parse_iso_dt(d.get("next_run_at")) or now))
    return due[: max(1, min(limit, 50))]
