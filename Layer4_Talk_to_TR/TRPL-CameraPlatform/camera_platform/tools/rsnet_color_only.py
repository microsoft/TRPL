#!/usr/bin/env python
"""
RealSense color-only preview — no depth stream.

Use this when depth pipeline init fails but you just want the RGB feed
working. Opens a cv2 window that displays frames live from the D455 color
sensor.

IMPORTANT — must be run from a real Terminal window (NOT Claude Code, NOT
any wrapper). macOS TCC grants camera permission to the process that owns
the TTY, which means Terminal.app (or iTerm) needs to have Camera access
ticked in System Settings → Privacy & Security → Camera.

Usage:
    cd camera_platform
    source .venv/bin/activate
    python tools/rsnet_color_only.py                 # 640x480 @ 30fps
    python tools/rsnet_color_only.py --width 1280 --height 720
    python tools/rsnet_color_only.py --serial 123456 # pick device by serial

Controls:
    q  — quit
    s  — save snapshot to `snapshot_<N>.jpg`
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np
import pyrealsense2 as rs


def list_devices() -> list[tuple[str, str]]:
    ctx = rs.context()
    out: list[tuple[str, str]] = []
    for d in ctx.devices:
        try:
            out.append((
                d.get_info(rs.camera_info.name),
                d.get_info(rs.camera_info.serial_number),
            ))
        except Exception:
            continue
    return out


def run(width: int, height: int, fps: int, serial: str | None) -> int:
    devs = list_devices()
    if not devs:
        print("[FAIL] no RealSense devices detected on USB")
        return 2
    for i, (name, ser) in enumerate(devs):
        marker = "  (selected)" if serial and serial == ser else ""
        print(f"[device {i}] {name}  serial={ser}{marker}")

    cfg = rs.config()
    if serial:
        cfg.enable_device(serial)
    # Color only. No depth, no IR, no infrared pairs — the whole point of this script.
    cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    pipeline = rs.pipeline()
    print(f"\nstarting pipeline  color={width}x{height}@{fps}fps (no depth)")
    profile = pipeline.start(cfg)
    print("pipeline running — press q in the window to quit")

    frame_count = 0
    last_tick = time.time()
    fps_smoothed = 0.0
    saved_count = 0

    try:
        while True:
            frames = pipeline.wait_for_frames(timeout_ms=5000)
            color = frames.get_color_frame()
            if not color:
                continue
            img = np.asanyarray(color.get_data())

            # Overlay FPS
            now = time.time()
            dt = now - last_tick
            last_tick = now
            inst = 1.0 / dt if dt > 0 else 0.0
            fps_smoothed = 0.9 * fps_smoothed + 0.1 * inst
            cv2.putText(
                img, f"{fps_smoothed:5.1f} fps  {img.shape[1]}x{img.shape[0]}",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )

            cv2.imshow("RealSense color (no depth)", img)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                path = f"snapshot_{saved_count:03d}.jpg"
                cv2.imwrite(path, img)
                print(f"saved {path}")
                saved_count += 1
            frame_count += 1
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        print(f"\nstopped — grabbed {frame_count} frames")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--serial", type=str, default=None,
                   help="pick a specific device serial (see device list above)")
    args = p.parse_args()
    return run(args.width, args.height, args.fps, args.serial)


if __name__ == "__main__":
    sys.exit(main())
