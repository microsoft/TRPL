#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Camera MJPEG Streaming Service
Run this script on each Mac to stream the local camera feed via HTTP to the main monitoring system

Usage:
    python stream_server.py                          # Default port 9090
    python stream_server.py --port 9090              # Specify port
    python stream_server.py --device 1               # Specify camera device number
    python stream_server.py --width 1280 --height 720

Dependencies (also needed on remote Mac):
    pip install flask opencv-python
"""
import argparse
import cv2
import time
import threading
from flask import Flask, Response

app = Flask(__name__)

# Global variables
camera = None
lock = threading.Lock()
latest_frame = None


def capture_loop(device, width, height):
    """Background thread: continuously capture camera frames"""
    global camera, latest_frame

    try:
        camera = cv2.VideoCapture(device)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    except Exception as e:
        print(f"[ERROR] Camera initialization failed: {e}")
        return

    if not camera.isOpened():
        print(f"[ERROR] Cannot open camera {device}")
        return

    print(f"[INFO] Camera opened: device {device}, resolution {width}x{height}")

    fail_count = 0
    while True:
        try:
            ok, frame = camera.read()
            if not ok:
                fail_count += 1
                if fail_count > 100:
                    print(f"[ERROR] Camera read failed {fail_count} consecutive times, stopping capture")
                    break
                time.sleep(0.01)
                continue
            fail_count = 0
            with lock:
                latest_frame = frame
        except Exception as e:
            print(f"[ERROR] Camera read exception: {e}")
            time.sleep(0.5)


def generate_mjpeg():
    """Generate MJPEG stream"""
    while True:
        with lock:
            frame = latest_frame

        if frame is None:
            time.sleep(0.01)
            continue

        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
        )


@app.route("/")
def index():
    """Preview page"""
    return "<h1>Camera Stream</h1><img src='/video_feed' width='640'>"


@app.route("/video_feed")
def video_feed():
    """MJPEG video stream endpoint"""
    return Response(
        generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/health")
def health():
    """Health check"""
    return {"status": "ok", "camera_open": camera is not None and camera.isOpened()}


def main():
    parser = argparse.ArgumentParser(description="Camera MJPEG Streaming Service")
    parser.add_argument("--port", type=int, default=9090, help="Service port (default 9090)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    parser.add_argument("--device", type=int, default=0, help="Camera device number (default 0)")
    parser.add_argument("--width", type=int, default=1280, help="Resolution width")
    parser.add_argument("--height", type=int, default=720, help="Resolution height")
    args = parser.parse_args()

    # Start capture thread
    t = threading.Thread(
        target=capture_loop,
        args=(args.device, args.width, args.height),
        daemon=True,
    )
    t.start()
    time.sleep(1)

    print(f"[INFO] MJPEG stream URL: http://0.0.0.0:{args.port}/video_feed")
    print(f"[INFO] Use in main system config: http://<this-machine-IP>:{args.port}/video_feed")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
