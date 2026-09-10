#!/usr/bin/env bash
# Start ONLY the livekit_worker (token_server lives in the Azure Web App now).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

mkdir -p logs run

PY="$APP_DIR/.venv/bin/python"

# lia_agent_api runs on the same VM, internal only.
export LIA_AGENT_API_URL="${LIA_AGENT_API_URL:-http://127.0.0.1:8010}"
# Skip camera phase — web UI has no camera, go straight to storys.
export LIA_PHASES="${LIA_PHASES:-storys}"

WORKER_PID="$APP_DIR/run/livekit_worker.pid"
WORKER_LOG="$APP_DIR/logs/livekit_worker.log"

if [ -f "$WORKER_PID" ] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null; then
    echo "livekit_worker already running with PID $(cat "$WORKER_PID")"
    exit 0
fi

nohup "$PY" livekit_agent.py start >> "$WORKER_LOG" 2>&1 &
echo $! > "$WORKER_PID"
echo "livekit_worker PID $(cat "$WORKER_PID"), logs: $WORKER_LOG"
