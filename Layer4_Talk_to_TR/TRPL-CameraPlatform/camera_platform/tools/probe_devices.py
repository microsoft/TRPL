#!/usr/bin/env python
"""Probe device indices using the SAME open() style as camera_manager.py
(no explicit backend), so the enumeration matches exactly what
camera_platform sees. Each working index opens a live preview so you
can eyeball which one is the RealSense.

    python tools/probe_devices.py        # shows 0,1,2,3,4 one at a time

Press 'n' to try the next index, 'q' to quit, 's' to save a snapshot
of the currently-shown device.
"""
import sys
import time
import cv2


def try_open(idx: int):
    # Mimic camera_manager._open_cv2: no backend flag.
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        return None
    # Grab one frame to confirm stream
    ok, frame = cap.read()
    if not ok:
        cap.release()
        return None
    return cap


def main() -> int:
    for idx in range(0, 6):
        print(f"[probe] trying index {idx} (no explicit backend)...")
        cap = try_open(idx)
        if cap is None:
            print(f"[probe] index {idx} does not stream, skipping")
            continue

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[probe] index {idx} OPENED — {w}x{h}")
        print("         Press n=next  s=save snapshot  q=quit")

        saved = False
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02)
                    continue
                label = f"index={idx}   {frame.shape[1]}x{frame.shape[0]}   press: n=next s=save q=quit"
                cv2.putText(frame, label, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (0, 255, 0), 2)
                cv2.imshow("probe_devices", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("n"):
                    break
                if key == ord("q"):
                    cap.release()
                    cv2.destroyAllWindows()
                    return 0
                if key == ord("s") and not saved:
                    path = f"probe_idx{idx}.jpg"
                    cv2.imwrite(path, frame)
                    print(f"[probe] saved {path}")
                    saved = True
        finally:
            cap.release()
            cv2.destroyAllWindows()

    print("[probe] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
