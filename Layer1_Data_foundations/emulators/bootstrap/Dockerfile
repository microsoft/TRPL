# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

RUN --mount=type=secret,id=pip_index_url \
    PIP_INDEX_URL="$(cat /run/secrets/pip_index_url)" \
    pip install --no-cache-dir \
        azure-cosmos==4.16.3 \
        azure-storage-blob==12.30.0

COPY scripts/local_dev.py /opt/trpl/local_dev.py

ENTRYPOINT ["python", "/opt/trpl/local_dev.py", "bootstrap"]
