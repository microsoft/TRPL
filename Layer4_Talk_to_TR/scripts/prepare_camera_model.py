#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Download the pinned YOLO pose weight into the ignored local model cache."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = (
    ROOT
    / "Layer4_Talk_to_TR"
    / "TRPL-CameraPlatform"
    / "camera_platform"
    / "private"
    / "models"
    / "yolov8n-pose.pt"
)
URL = (
    "https://github.com/ultralytics/assets/releases/download/"
    "v8.3.0/yolov8n-pose.pt"
)
SHA256 = "c6fa93dd1ee4a2c18c900a45c1d864a1c6f7aba75d84f91648a30b7fb641d212"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    if TARGET.is_file() and _digest(TARGET) == SHA256:
        print(f"Pinned camera model is ready: {TARGET}")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="yolov8n-pose-", suffix=".pt", dir=TARGET.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        urllib.request.urlretrieve(URL, temporary)
        actual = _digest(temporary)
        if actual != SHA256:
            raise RuntimeError(
                f"YOLO model checksum mismatch: expected {SHA256}, got {actual}"
            )
        temporary.replace(TARGET)
        TARGET.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)

    print(f"Downloaded pinned camera model: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
