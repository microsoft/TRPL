#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Replay events from event_log JSONL files to an agent server.

Use this to reproduce a past failure offline or to re-drive downstream after
a crash. The rolling JSONL written by EventPublisher is the source of truth.

Usage:
    python scripts/replay_events.py                      # replay today's log
    python scripts/replay_events.py --file 2026-04-12.jsonl
    python scripts/replay_events.py --rate 10            # cap at 10 events/sec
    python scripts/replay_events.py --since 1712883600   # skip events before ts
    python scripts/replay_events.py --types BATCH_INVITE HAND_RAISE_RESPONSE
    python scripts/replay_events.py --url http://otherhost:8000  # override target
    python scripts/replay_events.py --dry-run            # just print, don't POST
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

import requests

DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "event_log"


def _iter_envelopes(path: Path) -> Iterator[dict]:
    """Yield valid envelopes from a JSONL file, skipping garbage lines."""
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"skip {path.name}:{lineno} — bad JSON: {e}", file=sys.stderr)


def _parse_ts(ts_str: str) -> Optional[float]:
    """Convert ISO timestamp to unix epoch."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def _should_skip(env: dict, since: Optional[float],
                 until: Optional[float],
                 types: Optional[List[str]]) -> bool:
    if types and env.get("event_type") not in types:
        return True
    if since is not None or until is not None:
        ts = _parse_ts(env.get("timestamp", ""))
        if ts is None:
            return True
        if since is not None and ts < since:
            return True
        if until is not None and ts > until:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=None,
                    help="JSONL file name inside event_log/ (default: today)")
    ap.add_argument("--dir", default=str(DEFAULT_LOG_DIR),
                    help=f"event log directory (default: {DEFAULT_LOG_DIR})")
    ap.add_argument("--url", default="http://localhost:8000",
                    help="agent server base URL (default: http://localhost:8000)")
    ap.add_argument(
        "--api-key",
        default=os.getenv("LIA_API_KEY", ""),
        help="X-Api-Key value (default: LIA_API_KEY environment variable)",
    )
    ap.add_argument("--rate", type=float, default=0.0,
                    help="cap events per second (0 = unlimited)")
    ap.add_argument("--since", type=float, default=None,
                    help="only replay events with unix ts >= this value")
    ap.add_argument("--until", type=float, default=None,
                    help="only replay events with unix ts <= this value")
    ap.add_argument("--types", nargs="+", default=None,
                    help="only replay these event_types")
    ap.add_argument("--dry-run", action="store_true",
                    help="print events but don't POST")
    args = ap.parse_args()

    log_dir = Path(args.dir)
    if args.file:
        path = log_dir / args.file if not Path(args.file).is_absolute() else Path(args.file)
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = log_dir / f"{today}.jsonl"

    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2

    endpoint = args.url.rstrip("/") + "/api/camera/events"
    print(f"replaying {path} → {endpoint if not args.dry_run else '(dry-run)'}")

    min_gap = 1.0 / args.rate if args.rate > 0 else 0.0
    last_post = 0.0
    sent = 0
    skipped = 0
    failed = 0

    for env in _iter_envelopes(path):
        if _should_skip(env, args.since, args.until, args.types):
            skipped += 1
            continue

        if min_gap > 0:
            now = time.time()
            wait = min_gap - (now - last_post)
            if wait > 0:
                time.sleep(wait)
            last_post = time.time()

        if args.dry_run:
            print(f"  {env.get('event_type'):30s} {env.get('event_id')} "
                  f"ts={env.get('timestamp')}")
            sent += 1
            continue

        try:
            headers = {"X-Api-Key": args.api_key} if args.api_key else {}
            resp = requests.post(endpoint, json=env, headers=headers, timeout=5.0)
            if resp.status_code not in (200, 201, 202):
                print(f"  FAIL {env.get('event_type')} HTTP {resp.status_code}")
                failed += 1
            else:
                sent += 1
        except Exception as e:
            print(f"  FAIL {env.get('event_type')}: {e}")
            failed += 1

    print(f"done: sent={sent} failed={failed} skipped={skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
