# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# for Azure App Service when deploying as code (instead of container)
# NOTE: App Service load-balances over the container network, so this MUST bind
# 0.0.0.0 and is therefore internet-facing. Ensure CLIENT_API_KEYS is set so the
# API-key auth on every endpoint is active before using this deployment path.
# Add src directory to Python path so imports work without src. prefix
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
gunicorn api.main:app -w ${GUNICORN_WORKERS:-1} -k ${GUNICORN_WORKER_CLASS:-uvicorn.workers.UvicornWorker} --bind ${HOST:-0.0.0.0}:${PORT:-8000} --timeout ${GUNICORN_TIMEOUT:-120} --keep-alive ${GUNICORN_KEEPALIVE:-2} --max-requests ${GUNICORN_MAX_REQUESTS:-1000} --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100} --preload --log-level ${LOG_LEVEL:-info}
