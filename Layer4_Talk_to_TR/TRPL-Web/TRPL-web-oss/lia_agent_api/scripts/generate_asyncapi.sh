#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

force_write_flag=()
if [[ "$*" == *"--force-write"* ]]; then
    force_write_flag=(--force-write)
fi

if [[ ! -x node_modules/.bin/asyncapi ]]; then
    echo "AsyncAPI dependencies are missing. Run 'npm install' first." >&2
    exit 1
fi

node_modules/.bin/asyncapi generate fromTemplate \
    static/asyncapi.yaml \
    ./node_modules/@asyncapi/html-template \
    -o static/asyncapi \
    --use-new-generator \
    "${force_write_flag[@]}"

cp \
    node_modules/@asyncapi/react-component/browser/standalone/without-parser.js.LICENSE.txt \
    static/asyncapi/js/without-parser.js.LICENSE.txt
cp node_modules/@asyncapi/html-template/LICENSE static/asyncapi/LICENSE
cp node_modules/@asyncapi/html-template/NOTICE static/asyncapi/NOTICE

echo "AsyncAPI docs generated successfully"
