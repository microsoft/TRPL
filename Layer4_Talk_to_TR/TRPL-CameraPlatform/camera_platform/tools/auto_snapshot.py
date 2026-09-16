#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Auto-snapshot: run this, walk out of frame, it saves a clean empty-scene
shot. For first-time zone calibration.

How it works:
  1. Opens the camera (UVC device, default index 1).
  2. Runs YOLOv8n-pose on every frame.
  3. When it sees 0 people for N consecutive frames (default 3 seconds at
     30 fps ≈ 90 frames), it saves the frame to snapshot_YYYYmmdd_HHMMSS.jpg.
  4. Shows a live preview window with a countdown overlay so you know what's
     happening. Press q to abort.

Run from Terminal.app (needs Camera permission):
    cd camera_platform
    source .venv/bin/activate
    python tools/auto_snapshot.py
    python tools/auto_snapshot.py --device 0 --hold 5   # 5s empty-hold
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import cv2
from ultralytics import YOLO


def run(device: int, width: int, height: int, hold_sec: float,
        conf: float, out_prefix: str) -> int:
    cap = cv2.VideoCapture(device, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        print(f"[FAIL] cannot open device {device}")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    print("[INFO] loading YOLO model...")
    model = YOLO("yolov8n-pose.pt")
    print("[INFO] model ready. Walk out of frame.")

    empty_since: float | None = None
    saved_path: str | None = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.02)
                continue

            res = model.predict(frame, imgsz=960, conf=conf, verbose=False)
            r = res[0]
            n_persons = 0
            if r.boxes is not None:
                for b in r.boxes:
                    if int(b.cls.item()) == 0:   # 0 == person
                        n_persons += 1

            annotated = frame.copy()

            # Draw bboxes if any
            if r.boxes is not None and n_persons > 0:
                for b in r.boxes:
                    if int(b.cls.item()) != 0:
                        continue
                    x1, y1, x2, y2 = b.xyxy.cpu().numpy().astype(int)[0]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)

            # Countdown / status logic
            now = time.time()
            if n_persons == 0:
                if empty_since is None:
                    empty_since = now
                    print("[INFO] scene empty — starting countdown...")
                elapsed = now - empty_since
                remaining = max(0.0, hold_sec - elapsed)
                if remaining <= 0:
                    # Capture!
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    saved_path = f"{out_prefix}_{stamp}.jpg"
                    cv2.imwrite(saved_path, frame)
                    print(f"\n[OK] captured empty scene → {saved_path}")
                    # Hold the saved shot on screen briefly
                    cv2.putText(annotated, f"SAVED: {saved_path}", (20, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                    cv2.imshow("auto_snapshot", annotated)
                    cv2.waitKey(1200)
                    break
                else:
                    msg = f"Empty — capturing in {remaining:4.1f}s  (q=abort)"
                    cv2.putText(annotated, msg, (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            else:
                if empty_since is not None:
                    print(f"[INFO] {n_persons} person(s) detected — resetting countdown")
                empty_since = None
                cv2.putText(annotated,
                            f"{n_persons} person(s) in frame — please leave",
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                            (0, 0, 255), 2)

            cv2.imshow("auto_snapshot", annotated)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] aborted by user")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if saved_path:
        print()
        print(f"Next step — label zones on this image:")
        print(f"  python tools/zone_labeler.py {saved_path}")
        return 0
    return 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--device", type=int, default=1,
                   help="UVC device index (default 1 — RealSense color)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--hold", type=float, default=3.0,
                   help="Seconds the scene must stay empty before capture")
    p.add_argument("--conf", type=float, default=0.35,
                   help="YOLO person confidence threshold")
    p.add_argument("--out", type=str, default="snapshot",
                   help="Output filename prefix")
    args = p.parse_args()
    return run(args.device, args.width, args.height,
               args.hold, args.conf, args.out)


if __name__ == "__main__":
    sys.exit(main())
