#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# Build antenv on the deploy runner (has PyPI egress). App Service Oryx build is disabled
# for this service — the zip must include a ready virtualenv (zero-trust blocks pypi.org).
#
# Use manylinux2014 wheels: GitHub runners have newer glibc than App Service; runner-native
# cryptography/rust extensions fail on the web app with GLIBC_2.33 not found.
set -euo pipefail

PYTHON="${PYTHON:-python3.11}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

rm -rf antenv
"$PYTHON" -m venv antenv --copies
# shellcheck disable=SC1091
source antenv/bin/activate
python -m pip install --upgrade pip

# Download manylinux2014 wheels first (--platform cannot be used on a plain venv install).
WHEEL_DIR="$(mktemp -d)"
trap 'rm -rf "$WHEEL_DIR"' EXIT
pip download -r requirements.txt -d "$WHEEL_DIR" \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --only-binary=:all:
pip install --no-index --find-links="$WHEEL_DIR" -r requirements.txt

python -c "from cryptography.hazmat.bindings._rust import x509 as _rust_x509; print('prepackage: cryptography OK')"
python -c "import azure.identity; print('prepackage: azure.identity OK')"
python -c "import fastapi; print('prepackage: antenv ready, fastapi', fastapi.__version__)"
