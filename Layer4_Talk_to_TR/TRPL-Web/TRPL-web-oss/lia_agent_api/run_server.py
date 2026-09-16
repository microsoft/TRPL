#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Run the debate API server
"""
import sys
from pathlib import Path

# Add src directory to Python path so imports work without src. prefix
project_root = Path(__file__).parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

import uvicorn
from api.config import config

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=config.host,
        port=config.port,
        log_level="info",
        reload=True,  # Set to True for development
        ws_ping_interval=20,  # Send ping every 20 seconds
        timeout_keep_alive=300,  # Keep connection alive for 5 minutes
    )
