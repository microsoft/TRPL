#!/bin/bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# Install Playwright Chromium browser + OS deps if not already present.
# Runs on each container cold start; skips if already installed.
BROWSER_DIR="${PLAYWRIGHT_BROWSERS_PATH:-/home/.playwright}"
mkdir -p "$BROWSER_DIR"
echo "Checking Playwright browsers in $BROWSER_DIR ..."
if ! ls "$BROWSER_DIR"/chromium-*/chrome-linux/chrome 2>/dev/null | grep -q .; then
    echo "Installing Playwright Chromium + OS deps..."
    python -m playwright install --with-deps chromium
    echo "Playwright install exit code: $?"
else
    echo "Playwright Chromium already installed, skipping."
fi
