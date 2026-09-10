#!/usr/bin/env python
"""
Open any UVC camera (RealSense color, FaceTime, external USB cams) via
OpenCV — no pyrealsense2, no depth, no SDK dependencies beyond cv2.

Use when pyrealsense2 refuses to enumerate the device ("failed to set
power state" and friends on macOS). D455's color sensor is a standard
UVC device and shows up alongside FaceTime.

Run from Terminal.app (needs Camera TCC permission):
    cd camera_platform
    source .venv/bin/activate
    python tools/uvc_preview.py                  # tries 0,1,2,3; opens first
    python tools/uvc_preview.py --device 1       # force a specific index
    python tools/uvc_preview.py --list           # probe all, print what opens

Controls in window:
    q  — quit
    n  — cycle to next device (only if --list found >1)
    s  — save snapshot
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2


def probe(indices=range(0, 6)) -> list[int]:
    """Return indices that open successfully."""
    ok: list[int] = []
    for i in indices:
        cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            # Grab a single frame to confirm it actually streams.
            success, _ = cap.read()
            if success:
                ok.append(i)
            cap.release()
    return ok


def open_cam(device: int, width: int, height: int) -> cv2.VideoCapture | None:
    cap = cv2.VideoCapture(device, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        print(f"[FAIL] could not open device {device}")
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[OK] device {device}: {actual_w}x{actual_h} (requested {width}x{height})")
    return cap


def run(device: int, width: int, height: int, cycle_devices: list[int]) -> int:
    cap = open_cam(device, width, height)
    if cap is None:
        return 1

    idx_in_cycle = cycle_devices.index(device) if device in cycle_devices else 0
    fps_smoothed = 0.0
    last = time.time()
    saved = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[WARN] read failed; sleeping")
                time.sleep(0.05)
                continue

            now = time.time()
            dt = now - last
            last = now
            inst = 1.0 / dt if dt > 0 else 0.0
            fps_smoothed = 0.9 * fps_smoothed + 0.1 * inst
            cv2.putText(
                frame,
                f"dev={device}  {frame.shape[1]}x{frame.shape[0]}  {fps_smoothed:5.1f} fps",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )

            cv2.imshow(f"UVC preview (device {device})", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                path = f"snapshot_{device}_{saved:03d}.jpg"
                cv2.imwrite(path, frame)
                print(f"saved {path}")
                saved += 1
            if key == ord("n") and len(cycle_devices) > 1:
                cap.release()
                cv2.destroyAllWindows()
                idx_in_cycle = (idx_in_cycle + 1) % len(cycle_devices)
                device = cycle_devices[idx_in_cycle]
                print(f"\n[cycle] switching to device {device}")
                cap = open_cam(device, width, height)
                if cap is None:
                    return 1
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--device", type=int, default=None,
                   help="force a specific device index (default: auto-pick)")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--list", action="store_true",
                   help="probe indices 0..5 and print which ones stream, then exit")
    args = p.parse_args()

    print("probing UVC devices 0..5 (this may prompt for camera permission)...")
    available = probe()
    if not available:
        print("[FAIL] no UVC devices streamed a frame. Check:")
        print("  - Terminal.app has Camera permission (System Settings → Privacy)")
        print("  - RealSense is physically connected via USB 3.0+ cable")
        print("  - No other app is holding the camera")
        return 2
    print(f"opened devices: {available}")

    if args.list:
        return 0

    dev = args.device if args.device is not None else available[0]
    if dev not in available and args.device is None:
        print(f"auto-picked device {dev}")
    return run(dev, args.width, args.height, available)


if __name__ == "__main__":
    sys.exit(main())
