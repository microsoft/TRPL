#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# ----------------------------------------------------------------------------
# Watchdog wrapper for camera_platform.
#
# Restarts main.py automatically on any non-zero exit. Exits cleanly only on
# exit code 0 (graceful shutdown) or SIGTERM / SIGINT from the user. Designed
# for 10h+ continuous operation where a single unhandled crash should not
# kill the whole system.
#
# Usage:
#     ./run.sh                 # same as: python main.py --demo
#     ./run.sh --api --port 5000
#     ./run.sh --headless
#
# Tunables (environment variables):
#     CAMERA_PLATFORM_PY   — python interpreter (default: python)
#     RESTART_BACKOFF_SEC  — seconds to wait between restarts (default: 2)
#     MAX_RESTART_PER_MIN  — circuit breaker: abort if we restart more than
#                            this many times in the last 60s (default: 10)
# ----------------------------------------------------------------------------

set -u

PY="${CAMERA_PLATFORM_PY:-python}"
BACKOFF="${RESTART_BACKOFF_SEC:-2}"
MAX_RESTART_PER_MIN="${MAX_RESTART_PER_MIN:-10}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="${SCRIPT_DIR}/watchdog.log"

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${ts}] $*" | tee -a "$LOG_FILE"
}

# Forward SIGTERM / SIGINT to the child so graceful shutdown works
child_pid=0
cleanup() {
    log "watchdog: signal received, forwarding to child PID=${child_pid}"
    if [ "$child_pid" -ne 0 ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    log "watchdog: exiting"
    exit 0
}
trap cleanup TERM INT

log "watchdog: starting main.py with args: $*"
restart_times=()

while true; do
    "$PY" main.py "$@" &
    child_pid=$!
    wait "$child_pid"
    exit_code=$?
    child_pid=0

    if [ "$exit_code" -eq 0 ]; then
        log "watchdog: main.py exited cleanly (code 0), stopping"
        exit 0
    fi

    now=$(date +%s)
    restart_times+=("$now")

    # Drop entries older than 60s — simple circuit breaker window
    trimmed=()
    for t in "${restart_times[@]}"; do
        if [ "$((now - t))" -lt 60 ]; then
            trimmed+=("$t")
        fi
    done
    restart_times=("${trimmed[@]}")

    if [ "${#restart_times[@]}" -ge "$MAX_RESTART_PER_MIN" ]; then
        log "watchdog: ${#restart_times[@]} restarts in the last 60s — circuit breaker tripped, aborting"
        exit 1
    fi

    log "watchdog: main.py exited with code ${exit_code}, restarting in ${BACKOFF}s (restart #${#restart_times[@]} in last 60s)"
    sleep "$BACKOFF"
done
