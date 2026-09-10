#!/usr/bin/env bash
# Build antenv on the deploy runner (has PyPI egress). App Service Oryx build is disabled
# under ZERO_TRUST — the zip must include a ready virtualenv (route-all blocks pypi.org).
#
# Use manylinux2014 wheels: GitHub runners have newer glibc than App Service; runner-native
# cryptography/rust extensions fail on the web app with GLIBC_2.33 not found.
set -euo pipefail

resolve_zero_trust() {
  if [[ -n "${ZERO_TRUST:-}" ]]; then
    echo "$ZERO_TRUST"
    return
  fi
  if command -v azd >/dev/null 2>&1; then
    azd env get-values 2>/dev/null | sed -n 's/^ZERO_TRUST="\(.*\)"$/\1/p' | head -1
  fi
}

zt="$(echo "$(resolve_zero_trust)" | tr '[:upper:]' '[:lower:]')"
if [[ "$zt" != "true" && "$zt" != "1" && "$zt" != "yes" ]]; then
  echo "ZERO_TRUST not enabled — skipping antenv prepackage (App Service Oryx build will run)."
  exit 0
fi

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
# With --platform, pip requires --only-binary=:all:. ebooklib is fetched separately (pin may lack a manylinux wheel).
WHEEL_DIR="$(mktemp -d)"
REQ_WHEELS="$(mktemp)"
trap 'rm -rf "$WHEEL_DIR" "$REQ_WHEELS"' EXIT
EBOOKLIB_PIN="$(grep -E '^ebooklib==' requirements.txt || true)"
grep -v '^ebooklib' requirements.txt > "$REQ_WHEELS"
pip download -r "$REQ_WHEELS" -d "$WHEEL_DIR" \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --only-binary=:all:
if [ -n "$EBOOKLIB_PIN" ]; then
  pip download "$EBOOKLIB_PIN" -d "$WHEEL_DIR"
fi
pip install --no-index --find-links="$WHEEL_DIR" -r requirements.txt

python -c "from cryptography.hazmat.bindings._rust import x509 as _rust_x509; print('prepackage: cryptography OK')"
python -c "import azure.identity; print('prepackage: azure.identity OK')"
python -c "import fastapi; print('prepackage: antenv ready, fastapi', fastapi.__version__)"
