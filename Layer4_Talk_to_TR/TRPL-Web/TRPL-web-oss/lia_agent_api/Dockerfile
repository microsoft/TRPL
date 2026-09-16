# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# syntax=docker/dockerfile:1.7
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

ARG REQUIREMENTS_FILE=requirements.local.txt
COPY lia_agent_api/requirements*.txt ./
RUN --mount=type=secret,id=pip_index_url \
    PIP_INDEX_URL="$(cat /run/secrets/pip_index_url)" \
    pip install --no-cache-dir -r "${REQUIREMENTS_FILE}"

COPY lia_agent_api/src/ ./src/
COPY lia_agent_api/static/ ./static/
COPY lia_agent_api/scripts/verify_cloud_data.py ./scripts/verify_cloud_data.py

EXPOSE 8010

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8010"]
