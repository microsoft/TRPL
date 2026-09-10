#!/usr/bin/env python3
"""Verify self-hosted LiveKit, avatar-free speech, and the camera VLM."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAMERA_ENV = (
    ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-CameraPlatform"
    / "camera_platform"
    / ".env.live.local"
)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name and not name.startswith("#"):
            values[name] = value
    return values


def _json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def _verify_vlm(camera: dict[str, str]) -> None:
    tiny_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    payload = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Reply with only: local vision ok"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{tiny_png}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 20,
        }
    ).encode()
    request = urllib.request.Request(
        camera["LLM_BASE_URL"],
        data=payload,
        method="POST",
        headers={
            "api-key": camera["LLM_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
    content = result["choices"][0]["message"]["content"].strip()
    if not content:
        raise RuntimeError("Camera VLM returned empty content.")


def main() -> int:
    brain = _json("http://127.0.0.1:8011/healthz")
    if not brain["providers"]["llm"] or not brain["providers"]["search"]:
        raise RuntimeError("Cloud brain providers are not ready.")
    token_health = _json("http://127.0.0.1:8003/healthz")
    if not token_health["providers"]["livekit"]:
        raise RuntimeError("Token server does not report LiveKit available.")

    query = urllib.parse.urlencode(
        {"identity": "layer4-local-acceptance"}
    )
    token_response = _json(f"http://127.0.0.1:8003/api/token?{query}")
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "Layer1_Data_foundations/.env.local",
            "--profile",
            "layer4-live",
            "exec",
            "-T",
            "layer4-live-worker",
            "python",
            "verify_room_audio.py",
            "--url",
            "ws://layer4-livekit:7880",
            "--token",
            token_response["token"],
        ],
        cwd=ROOT,
        check=True,
    )

    _verify_vlm(_read_env(CAMERA_ENV))
    print("Layer 4 live-media acceptance passed.")
    print("Manual browser URL: http://127.0.0.1:8003")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
