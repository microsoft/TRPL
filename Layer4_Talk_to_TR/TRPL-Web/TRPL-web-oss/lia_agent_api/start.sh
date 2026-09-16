#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

mkdir -p logs run
export PYTHONPATH="${PYTHONPATH:-}:$APP_DIR/src"

# Prompt-injection enforce mode — actually sanitizes injected visitor
# input + injects a directive to the main agent. Set to "observe" to
# revert to detect-only behavior (audit + auto-report, no input change).
export PROMPT_INJECTION_MODE="${PROMPT_INJECTION_MODE:-enforce}"

# L3 output reviewer — parallel LLM audit of TR's response stream. OFF
# by default. To opt in:
#     OUTPUT_REVIEWER_ENABLED=true OUTPUT_REVIEWER_MODE=observe ./start.sh
# Once you trust observe verdicts, flip MODE to enforce to kill mid-
# stream on detection.
export OUTPUT_REVIEWER_ENABLED="${OUTPUT_REVIEWER_ENABLED:-true}"
export OUTPUT_REVIEWER_MODE="${OUTPUT_REVIEWER_MODE:-observe}"

PIDFILE="$APP_DIR/run/server.pid"
LOGFILE="$APP_DIR/logs/server.log"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Server already running with PID $(cat "$PIDFILE")"
    exit 0
fi

# Bind to loopback by default — this brain service is meant to sit behind the
# token-server edge / a firewall, not face the internet. Export HOST=0.0.0.0
# explicitly (with CLIENT_API_KEYS set) to expose it.
nohup "$APP_DIR/.venv/bin/gunicorn" api.main:app \
    -w "${GUNICORN_WORKERS:-4}" \
    -k "${GUNICORN_WORKER_CLASS:-uvicorn.workers.UvicornWorker}" \
    --bind "${HOST:-127.0.0.1}:${PORT:-8010}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-2}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}" \
    --preload \
    --log-level "${LOG_LEVEL:-info}" \
    >> "$LOGFILE" 2>&1 &

echo $! > "$PIDFILE"
echo "lia_agent_api PID $(cat "$PIDFILE"), logs: $LOGFILE"
