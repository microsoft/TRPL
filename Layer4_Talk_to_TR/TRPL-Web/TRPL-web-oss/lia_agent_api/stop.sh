#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$APP_DIR/run/server.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "No PID file. Nothing to stop."
    exit 0
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping lia_agent_api PID $PID..."
    pkill -TERM -P "$PID" 2>/dev/null || true
    kill -TERM "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    kill -0 "$PID" 2>/dev/null && kill -KILL "$PID" 2>/dev/null || true
fi
rm -f "$PIDFILE"
echo "lia_agent_api stopped."
