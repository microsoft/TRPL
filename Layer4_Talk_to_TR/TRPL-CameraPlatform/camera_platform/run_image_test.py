#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Image-based pipeline test
─────────────────────────
Feeds static images one-by-one through the full detection pipeline
(YOLO → EventPublisher → agent_server → VLM) without needing real cameras.

Usage
─────
# Put your test images in test_images/ (JPG/PNG), then:
python run_image_test.py

# Custom folder and delay
python run_image_test.py --images path/to/folder --delay 2.0

# Don't show OpenCV windows
python run_image_test.py --no-display

# Against a remote agent server
python run_image_test.py --agent-url http://<agent-host>:8000

Prerequisites
─────────────
1. Agent server must be running:
       python -m uvicorn agent_server.main:app --port 8000 --reload
2. ANTHROPIC_API_KEY must be set in the agent server's environment
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import cv2
import requests

# ── project path ──────────────────────────────────────────────────────────────
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

from src.core.event_manager import EventManager, Event, EventType
from src.services.event_publisher import EventPublisher
from src.utils.image import encode_crop
import src.utils.config as _cfg
from ultralytics import YOLO

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("image_test")

# ── supported image extensions ────────────────────────────────────────────────
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_images(folder: str) -> list[Path]:
    """Return sorted list of image paths from folder."""
    p = Path(folder)
    if not p.exists():
        logger.error(f"Folder not found: {folder}")
        sys.exit(1)
    paths = sorted(f for f in p.iterdir() if f.suffix.lower() in IMG_EXTS)
    if not paths:
        logger.error(f"No images found in {folder} (supported: {IMG_EXTS})")
        sys.exit(1)
    logger.info(f"Found {len(paths)} images in {folder}")
    return paths


def poll_person_descriptions(agent_url: str, person_ids: set, timeout: float = 15.0):
    """
    Poll agent server until all person_ids have a description (or timeout).
    Returns dict {person_id: description_str}.
    """
    deadline = time.time() + timeout
    results = {}
    url = f"{agent_url.rstrip('/')}/persons"

    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=3)
            if resp.ok:
                for p in resp.json():
                    pid = p["person_id"]
                    if pid in person_ids and pid not in results:
                        desc = p.get("description", "")
                        if desc and desc != "(no image available)":
                            results[pid] = desc
            if results.keys() >= person_ids:
                break
        except Exception:
            pass
        time.sleep(1.0)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Feed images through the camera pipeline")
    parser.add_argument("--images", default="test_images",
                        help="Folder containing test images (default: test_images/)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds to wait between images (default: 1.5)")
    parser.add_argument("--agent-url", default=None,
                        help="Agent server URL (overrides config)")
    parser.add_argument("--no-display", action="store_true",
                        help="Skip OpenCV imshow windows")
    parser.add_argument("--conf", type=float, default=0.30,
                        help="YOLO detection confidence threshold (default: 0.30)")
    args = parser.parse_args()

    # Apply agent URL override
    if args.agent_url:
        _cfg.AGENT_SERVER_CONFIG["url"] = args.agent_url
    agent_url = _cfg.AGENT_SERVER_CONFIG["url"]

    # ── check agent server reachable ──────────────────────────────────────────
    logger.info(f"Checking agent server at {agent_url} …")
    try:
        r = requests.get(f"{agent_url}/health", timeout=3)
        info = r.json()
        logger.info(
            f"Agent server OK — events={info.get('event_count', 0)}, "
            f"vlm={'yes' if info.get('vlm_available') else 'NO (check ANTHROPIC_API_KEY)'}"
        )
        if not info.get("vlm_available"):
            logger.warning("VLM not available — person descriptions will be skipped")
    except Exception as e:
        logger.error(f"Cannot reach agent server: {e}")
        logger.error("Start it with:  python -m uvicorn agent_server.main:app --port 8000")
        sys.exit(1)

    # ── load YOLO ─────────────────────────────────────────────────────────────
    model_path = _cfg.YOLO_CONFIG.get("model_path", "yolov8n-pose.pt")
    logger.info(f"Loading YOLO model: {model_path}")
    model = YOLO(model_path)

    # ── event publisher ─────────────────────────────────────────────────────
    publisher = EventPublisher()

    # simple in-memory event manager (no cameras needed)
    event_manager = EventManager(max_history=500)
    event_manager.register_callback(
        name="agent_server_forwarder",
        callback=publisher.forward,
        events=None,
    )

    # ── process images ────────────────────────────────────────────────────────
    image_paths = load_images(args.images)
    discovered_person_ids: set = set()
    # next_id mimics room_monitor's deferred id assignment per image
    next_person_id = 0

    print("\n" + "═" * 60)
    print(f"  Image pipeline test  ({len(image_paths)} images)")
    print("═" * 60)

    for idx, img_path in enumerate(image_paths):
        frame = cv2.imread(str(img_path))
        if frame is None:
            logger.warning(f"Cannot read {img_path}, skipping")
            continue

        print(f"\n[{idx+1}/{len(image_paths)}] {img_path.name}")

        # Run YOLO detection (no tracking — each image is independent)
        results = model.predict(
            source=frame,
            imgsz=_cfg.YOLO_CONFIG.get("imgsz", 960),
            conf=args.conf,
            verbose=False,
        )

        annotated = frame.copy()
        persons_in_frame = 0

        if results and len(results) > 0:
            r = results[0]
            boxes = r.boxes

            if boxes is not None and len(boxes) > 0:
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()

                for i in range(len(xyxy)):
                    box = xyxy[i].astype(int)
                    det_conf = float(confs[i])

                    # Assign a fresh person_id per detected person per image
                    person_id = next_person_id
                    next_person_id += 1
                    discovered_person_ids.add(person_id)
                    persons_in_frame += 1

                    # Build event data with crop
                    h_img, w_img = frame.shape[:2]
                    event_data = {
                        "bbox": box.tolist(),
                        "confidence": det_conf,
                        "frame_size": [w_img, h_img],
                    }
                    crop_b64 = encode_crop(frame, box)
                    if crop_b64:
                        event_data["person_crop_b64"] = crop_b64

                    # Fire PERSON_ENTERED
                    event_manager.record_event(Event(
                        event_type=EventType.PERSON_ENTERED,
                        camera_id=f"test_image_{img_path.name}",
                        person_id=person_id,
                        data=event_data,
                    ))
                    print(f"  ↳ Person {person_id} detected  conf={det_conf:.2f}  bbox={box.tolist()}")

                    # Annotate frame
                    cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)
                    cv2.putText(
                        annotated,
                        f"Person {person_id}  {det_conf:.2f}",
                        (box[0], max(20, box[1] - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2,
                    )

        if persons_in_frame == 0:
            print("  (no persons detected — try lowering --conf)")

        # Show annotated image
        if not args.no_display:
            label = f"{img_path.name}  [{idx+1}/{len(image_paths)}]"
            cv2.putText(annotated, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 220, 255), 2)
            cv2.imshow("Image Test", annotated)
            key = cv2.waitKey(max(1, int(args.delay * 1000))) & 0xFF
            if key == ord("q"):
                print("\nAborted by user.")
                break
        else:
            time.sleep(args.delay)

    if not args.no_display:
        cv2.destroyAllWindows()

    # ── wait for VLM responses ────────────────────────────────────────────────
    if not discovered_person_ids:
        print("\nNo persons were detected in any image. Done.")
        return

    print(f"\n{'─'*60}")
    print(f"Waiting for VLM descriptions ({len(discovered_person_ids)} persons) …")
    # Allow extra time for group-window flush (GROUP_WINDOW_SEC) + VLM latency
    descriptions = poll_person_descriptions(agent_url, discovered_person_ids, timeout=45.0)

    print("\n" + "═" * 60)
    print("  VLM Results")
    print("═" * 60)

    if not descriptions:
        print("  (no descriptions received — check ANTHROPIC_API_KEY on agent server)")
    else:
        for pid, desc in sorted(descriptions.items()):
            print(f"\n  Person {pid}:")
            # Wrap description at 70 chars
            words = desc.split()
            line, lines = [], []
            for w in words:
                line.append(w)
                if len(" ".join(line)) > 70:
                    lines.append("    " + " ".join(line[:-1]))
                    line = [w]
            if line:
                lines.append("    " + " ".join(line))
            print("\n".join(lines))

    # Show remaining persons that didn't get a description
    missing = discovered_person_ids - descriptions.keys()
    if missing:
        print(f"\n  (no description for persons: {sorted(missing)})")

    # ── greetings generated by agent ──────────────────────────────────────────
    try:
        resp = requests.get(f"{agent_url}/greetings", timeout=3)
        if resp.ok:
            greetings = resp.json()
            if greetings:
                print(f"\n{'─'*60}")
                print("  Greetings generated")
                print(f"{'─'*60}")
                for g in greetings:
                    pids = g.get("person_ids", [])
                    print(f"  Persons {pids}:  {g['greeting']}")
    except Exception:
        pass

    # ── final event log ───────────────────────────────────────────────────────
    try:
        resp = requests.get(f"{agent_url}/events?limit=50", timeout=3)
        if resp.ok:
            events = resp.json()
            print(f"\n{'─'*60}")
            print(f"  Agent server received {len(events)} total events")
    except Exception:
        pass

    print("\n" + "═" * 60)
    print("  Test complete")
    print("═" * 60)


if __name__ == "__main__":
    main()
