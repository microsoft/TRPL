# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
import subprocess
import sys


APP_ROOT = Path(__file__).resolve().parent


def _base_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "TALK_TO_TR_RUNTIME_MODE",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        }
    }
    env["LIVEKIT_API_KEY"] = ""
    env["LIVEKIT_API_SECRET"] = ""
    return env


def test_deterministic_mode_serves_static_ui_and_reports_livekit_unavailable():
    script = """
from fastapi.testclient import TestClient
from token_server import app

with TestClient(app) as client:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "runtime_mode": "deterministic",
        "providers": {"livekit": False},
    }
    assert client.get("/").status_code == 200
    unavailable = client.get("/api/token")
    assert unavailable.status_code == 503
    assert unavailable.json()["runtime_mode"] == "deterministic"
"""
    env = _base_env()
    env["TALK_TO_TR_RUNTIME_MODE"] = "deterministic"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_deterministic_mode_ignores_present_livekit_credentials():
    env = _base_env()
    env.update(
        {
            "TALK_TO_TR_RUNTIME_MODE": "deterministic",
            "LIVEKIT_API_KEY": "must-not-be-used",
            "LIVEKIT_API_SECRET": "must-not-be-used",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import token_server; "
                "assert token_server.LIVEKIT_CONFIGURED is True; "
                "assert token_server.LIVEKIT_AVAILABLE is False"
            ),
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cloud_mode_fails_fast_without_livekit_credentials():
    env = _base_env()
    env["TALK_TO_TR_RUNTIME_MODE"] = "cloud"
    result = subprocess.run(
        [sys.executable, "-c", "import token_server"],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required" in result.stderr


def test_internal_livekit_url_defaults_to_public_url():
    env = _base_env()
    env.update(
        {
            "TALK_TO_TR_RUNTIME_MODE": "cloud",
            "LIVEKIT_URL": "ws://localhost:7880",
            "LIVEKIT_API_KEY": "local-key",
            "LIVEKIT_API_SECRET": "local-secret",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import token_server; "
                "assert token_server.LIVEKIT_INTERNAL_URL == "
                "'ws://localhost:7880'"
            ),
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
