# -*- coding: utf-8 -*-
"""
VIP config — single JSON file controlling the honored-guest (VIP) mode.

One file, hot-reloaded: edit it and the next match picks up the change with NO
lia restart (matching only runs on an explicit VIP entry, never on a hot path,
so we just re-read the file when its mtime changes).

File location (first match wins):
  1. $VIP_CONFIG_PATH  (absolute or relative to CWD)
  2. <lia_agent_api root>/vip_config.json   (default — alongside .env)

Schema (all keys optional; missing file == defaults == VIP effectively off):
  {
    "enabled": true,              // master switch
    "names": ["Jane Doe"],        // matched case-insensitively on "first last"
                                  //   and on last-name alone as a fallback
    "visitor_ids": ["12345"],     // optional stable badge IDs
    "min_dwell_sec": 8,           // ignore a re-tap this soon after entry (bounce)
    "idle_timeout_sec": 1200      // VipEngine idle-watchdog interval
  }
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(f"lia.{__name__}")

# lia_agent_api root = 3 parents up from this file
#   .../lia_agent_api/src/debate/services/vip_config.py
#   [0]services [1]debate [2]src [3]lia_agent_api
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "vip_config.json"

_DEFAULTS = {
    "enabled": True,
    "names": [],
    "visitor_ids": [],
    "min_dwell_sec": 8.0,
    "idle_timeout_sec": 1200.0,
}

# mtime-keyed cache so we re-read only when the file actually changes.
_cache: dict = {"mtime": None, "data": dict(_DEFAULTS)}


def _path() -> Path:
    override = os.getenv("VIP_CONFIG_PATH")
    return Path(override) if override else _DEFAULT_PATH


def _load() -> dict:
    """Return the current config dict, re-reading the file iff it changed."""
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # No file → defaults (VIP off: empty names match nobody).
        if _cache["mtime"] is not None:
            logger.info("vip_config: %s gone — reverting to defaults", path)
            _cache["mtime"], _cache["data"] = None, dict(_DEFAULTS)
        return _cache["data"]

    if mtime == _cache["mtime"]:
        return _cache["data"]

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("vip_config.json must be a JSON object")
        data = {**_DEFAULTS, **raw}
        _cache["mtime"], _cache["data"] = mtime, data
        logger.info(
            "vip_config loaded from %s: enabled=%s names=%d visitor_ids=%d dwell=%ss idle=%ss",
            path, data["enabled"], len(data["names"]),
            len(data["visitor_ids"]), data["min_dwell_sec"], data["idle_timeout_sec"],
        )
    except Exception as e:  # noqa: BLE001 — keep last good config on bad edits
        logger.error("vip_config: failed to read %s (%s) — keeping previous config", path, e)
    return _cache["data"]


def enabled() -> bool:
    return bool(_load().get("enabled", True))


def min_dwell_sec() -> float:
    try:
        return float(_load().get("min_dwell_sec", _DEFAULTS["min_dwell_sec"]))
    except (TypeError, ValueError):
        return _DEFAULTS["min_dwell_sec"]


def idle_timeout_sec() -> float:
    try:
        return float(_load().get("idle_timeout_sec", _DEFAULTS["idle_timeout_sec"]))
    except (TypeError, ValueError):
        return _DEFAULTS["idle_timeout_sec"]


def matches(rfid: dict | None) -> bool:
    """True if this visit's RFID badge is on the VIP allow-list.

    Matches a stable visitor_id (optional), else the full "first last" name,
    else the last name alone. Disabled / empty list / non-dict rfid → never VIP.

    NOTE: this branch currently has no RFID/badge ingest, so VIP is entered
    explicitly (see the /vip trigger endpoint). This helper is kept intact so a
    future badge feed can light up name-based auto-routing with no changes here.
    """
    cfg = _load()
    if not cfg.get("enabled", True):
        return False
    if not isinstance(rfid, dict):
        return False

    ids = {str(i).strip() for i in (cfg.get("visitor_ids") or []) if str(i).strip()}
    vid = str(rfid.get("visitor_id") or "").strip()
    if vid and vid in ids:
        return True

    names = {n.strip().lower() for n in (cfg.get("names") or []) if n and n.strip()}
    if not names:
        return False
    first = (rfid.get("first_name") or "").strip().lower()
    last = (rfid.get("last_name") or "").strip().lower()
    if not first and not last:
        return False
    full = (first + " " + last).strip()
    if full and full in names:
        return True
    # Last-name-only fallback (badges sometimes carry only a surname)
    return bool(last) and last in names
