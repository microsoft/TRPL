FROM mcr.microsoft.com/azure-functions/python@sha256:4d233f5a48eecb5eea9077dc66f50f4593cb46c97472970e1f818312b80ca710

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/functions/requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=pip_index_url \
    PIP_INDEX_URL="$(cat /run/secrets/pip_index_url)" \
    pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/functions /home/site/wwwroot
COPY scripts/local_dev.py /opt/trpl/local_dev.py
