#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# Startup command for Azure Web App (Linux, Python 3.12)
# Azure injects $PORT; token_server.py reads TOKEN_SERVER_PORT from env, so bridge them.
export TOKEN_SERVER_PORT="${PORT:-8000}"
exec python token_server.py
