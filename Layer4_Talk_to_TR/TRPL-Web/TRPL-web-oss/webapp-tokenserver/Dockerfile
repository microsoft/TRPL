# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# syntax=docker/dockerfile:1.7
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY webapp-tokenserver/requirements.local.txt ./
RUN --mount=type=secret,id=pip_index_url \
    PIP_INDEX_URL="$(cat /run/secrets/pip_index_url)" \
    pip install --no-cache-dir -r requirements.local.txt

COPY webapp-tokenserver/*.py webapp-tokenserver/*.html ./

EXPOSE 8002

CMD ["uvicorn", "token_server:app", "--host", "0.0.0.0", "--port", "8002"]
