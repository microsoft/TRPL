# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
import subprocess
import sys


APP_ROOT = Path(__file__).resolve().parents[1]


def _base_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LLM_") and not key.startswith("LIA_")
    }
    env["PYTHONPATH"] = str(APP_ROOT / "src")
    for name in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_SMALL_MODEL",
        "LLM_SMALL_INPUT_PRICE_1K",
        "LLM_SMALL_OUTPUT_PRICE_1K",
        "LLM_MID_MODEL",
        "LLM_MID_INPUT_PRICE_1K",
        "LLM_MID_OUTPUT_PRICE_1K",
        "LLM_LARGE_MODEL",
        "LLM_LARGE_INPUT_PRICE_1K",
        "LLM_LARGE_OUTPUT_PRICE_1K",
    ):
        env[name] = ""
    return env


def test_deterministic_mode_runs_transport_without_providers():
    script = """
from fastapi.testclient import TestClient
from api.main import app
from debate.services.session_store import session_store

with TestClient(app) as client:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["providers"] == {
        "llm": False,
        "search": False,
        "speech": False,
    }
    assert client.post("/api/debate/start", json={}).status_code == 401
    started = client.post(
        "/api/debate/start",
        json={},
        headers={"X-Api-Key": "test-local-key"},
    )
    assert started.status_code == 200
    session = started.json()
    ws_url = f"{session['ws_url']}?token={session['controller_token']}"
    with client.websocket_connect(ws_url) as websocket:
        events = [websocket.receive_json(), websocket.receive_json()]
        assert any(event["type"] == "runtime_status" for event in events)
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}
        camera = client.post(
            "/api/camera/events",
            headers={"X-Api-Key": "test-local-key"},
            json={
                "event_id": "test-fictional-event",
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "test_fixture",
                "event_type": "BATCH_INVITE",
                "payload": {"fixture": "fictional-no-media"},
                "session_id": session["session_id"],
            },
        )
        assert camera.status_code == 200
        assert websocket.receive_json()["type"] == "camera_event_received"
        assert session_store.sessions[session["session_id"]].input_queue.empty()
        test_camera = client.post(
            "/api/camera/test/events",
            headers={"X-Api-Key": "test-local-key"},
            json={
                "event_id": "test-debug-event",
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "test_fixture",
                "event_type": "PERSON_ENTERED",
                "payload": {"fixture": "fictional-no-media"},
                "session_id": session["session_id"],
            },
        )
        assert test_camera.status_code == 200
        assert websocket.receive_json()["type"] == "camera_event_received"
        assert session_store.sessions[session["session_id"]].input_queue.empty()
    stored = session_store.sessions[session["session_id"]]
    buffered_before = len(stored.output_queue._message_buffer)
    detached_camera = client.post(
        "/api/camera/events",
        headers={"X-Api-Key": "test-local-key"},
        json={
            "event_id": "test-detached-event",
            "timestamp": "2026-01-01T00:00:00Z",
            "source": "test_fixture",
            "event_type": "PERSON_LEFT",
            "payload": {"fixture": "fictional-no-media"},
            "session_id": session["session_id"],
        },
    )
    assert detached_camera.status_code == 200
    assert stored.input_queue.empty()
    assert len(stored.output_queue._message_buffer) == buffered_before
"""
    env = _base_env()
    env.update(
        {
            "LIA_RUNTIME_MODE": "deterministic",
            "CLIENT_API_KEYS": "test-local-key",
            "WS_AUTH_ENABLED": "true",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cloud_mode_fails_fast_without_llm_configuration():
    env = _base_env()
    env["LIA_RUNTIME_MODE"] = "cloud"
    result = subprocess.run(
        [sys.executable, "-c", "import api.config"],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "LLM_BASE_URL is required when LIA_RUNTIME_MODE=cloud" in result.stderr


def test_deterministic_mode_ignores_present_provider_credentials():
    env = _base_env()
    env.update(
        {
            "LIA_RUNTIME_MODE": "deterministic",
            "LLM_BASE_URL": "https://must-not-be-used.invalid",
            "LLM_API_KEY": "must-not-be-used",
            "LLM_SMALL_MODEL": "must-not-be-used",
            "LLM_MID_MODEL": "must-not-be-used",
            "LLM_LARGE_MODEL": "must-not-be-used",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from api.config import config; "
                "assert config.provider_status == "
                "{'llm': False, 'search': False, 'speech': False}"
            ),
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cloud_provider_status_requires_complete_search_configuration():
    env = _base_env()
    env.update(
        {
            "LIA_RUNTIME_MODE": "cloud",
            "LLM_BASE_URL": "https://ai.example/openai/v1/",
            "LLM_API_KEY": "test-key",
            "LLM_SMALL_MODEL": "small",
            "LLM_SMALL_INPUT_PRICE_1K": "0",
            "LLM_SMALL_OUTPUT_PRICE_1K": "0",
            "LLM_MID_MODEL": "mid",
            "LLM_MID_INPUT_PRICE_1K": "0",
            "LLM_MID_OUTPUT_PRICE_1K": "0",
            "LLM_LARGE_MODEL": "large",
            "LLM_LARGE_INPUT_PRICE_1K": "0",
            "LLM_LARGE_OUTPUT_PRICE_1K": "0",
            "SEARCH_ARCHIVES_QUERY_KEY": "search-key",
            "SEARCH_ARCHIVES_URL": "https://search.example",
            "SEARCH_ARCHIVES_INDEX_NAME": "letters",
            "SEARCH_BOOKS_QUERY_KEY": "search-key",
            "SEARCH_BOOKS_URL": "https://search.example",
            "SEARCH_BOOKS_INDEX_NAME": "books",
        }
    )
    incomplete = subprocess.run(
        [
            sys.executable,
            "-c",
            "from api.config import config; assert config.provider_status['search'] is False",
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert incomplete.returncode == 0, incomplete.stderr

    env.update(
        {
            "SEARCH_ARCHIVES_EMBEDDING_MODEL": "embedding",
            "SEARCH_ARCHIVES_EMBEDDING_KEY": "embedding-key",
            "SEARCH_ARCHIVES_EMBEDDING_ENDPOINT": "https://ai.example/embeddings",
            "SEARCH_BOOKS_EMBEDDING_MODEL": "embedding",
            "SEARCH_BOOKS_EMBEDDING_KEY": "embedding-key",
            "SEARCH_BOOKS_EMBEDDING_ENDPOINT": "https://ai.example/embeddings",
        }
    )
    complete = subprocess.run(
        [
            sys.executable,
            "-c",
            "from api.config import config; assert config.provider_status['search'] is True",
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert complete.returncode == 0, complete.stderr


def test_cloud_provider_status_respects_disabled_speech():
    env = _base_env()
    env.update(
        {
            "LIA_RUNTIME_MODE": "cloud",
            "LLM_BASE_URL": "https://ai.example/openai/v1/",
            "LLM_API_KEY": "test-key",
            "LLM_SMALL_MODEL": "small",
            "LLM_SMALL_INPUT_PRICE_1K": "0",
            "LLM_SMALL_OUTPUT_PRICE_1K": "0",
            "LLM_MID_MODEL": "mid",
            "LLM_MID_INPUT_PRICE_1K": "0",
            "LLM_MID_OUTPUT_PRICE_1K": "0",
            "LLM_LARGE_MODEL": "large",
            "LLM_LARGE_INPUT_PRICE_1K": "0",
            "LLM_LARGE_OUTPUT_PRICE_1K": "0",
            "TTS_ENABLED": "false",
            "TTS_AZURE_API_KEY": "speech-key",
            "TTS_AZURE_REGION": "eastus",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from api.config import config; assert config.provider_status['speech'] is False",
        ],
        cwd=APP_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
