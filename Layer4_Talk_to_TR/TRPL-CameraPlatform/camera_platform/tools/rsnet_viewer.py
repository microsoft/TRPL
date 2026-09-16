#!/usr/bin/env python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
RSNet Viewer — inspect what the remote RealSense HTTP API is serving.

Polls GET /info once, then proxies GET /snapshot at ~1Hz to render color +
depth in the browser. No WebSocket, no protocol guessing — just HTTP.

Usage:
    pip install flask opencv-python numpy requests
    python tools/rsnet_viewer.py --server <remote-host>:8765
    # open http://127.0.0.1:7000/
"""
import argparse
import base64
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify, render_template_string

app = Flask(__name__)
REMOTE = ""  # set in main()
_INFO_CACHE = {"data": None}

# Shared latest frame (produced by _poll_loop, consumed by /stream/*.mjpg)
_FRAME_LOCK = threading.Lock()
_LATEST = {
    "color_jpeg": None,   # bytes — raw JPEG from server, forwarded as-is
    "depth_jpeg": None,   # bytes — colormap JPEG (we encode locally)
    "seq": 0,
    "ts": 0.0,
    "stats": {},          # depth min/median/max, color wxh, etc.
    "fps": 0.0,
    "last_error": None,
}

INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>RSNet Viewer — {{ remote }}</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #111; color: #eee; margin: 16px; }
  h1 { font-size: 16px; margin: 0 0 8px; }
  h1 small { color: #888; font-weight: normal; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .card { background: #1c1c1c; border: 1px solid #333; border-radius: 6px; padding: 8px; }
  .card h2 { font-size: 13px; margin: 0 0 6px; color: #aaa; font-weight: normal; }
  img { width: 100%; display: block; background: #000; }
  pre { font-size: 11px; overflow: auto; background: #000; padding: 8px; border-radius: 4px; margin: 0; max-height: 360px; color: #9cdcfe; }
  .meta { font-size: 11px; color: #888; margin-top: 4px; font-family: monospace; }
</style>
</head>
<body>
  <h1>RSNet Viewer <small>— {{ remote }}</small> <small id="fps-badge" style="margin-left:12px;color:#4ec9b0">— fps</small></h1>
  <div class="grid">
    <div class="card">
      <h2>color (MJPEG stream)</h2>
      <img id="color" src="/stream/color.mjpg" alt="color">
      <div class="meta" id="color-meta">—</div>
    </div>
    <div class="card">
      <h2>depth (MJPEG, JET 0.3–6.0 m)</h2>
      <img id="depth" src="/stream/depth.mjpg" alt="depth">
      <div class="meta" id="depth-meta">—</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="card">
      <h2>GET /info</h2>
      <pre id="info">loading…</pre>
    </div>
    <div class="card">
      <h2>GET /snapshot — keys &amp; derived stats</h2>
      <pre id="snapmeta">loading…</pre>
    </div>
  </div>

<script>
async function refreshInfo() {
  try {
    const j = await (await fetch('/api/info')).json();
    document.getElementById('info').textContent = JSON.stringify(j, null, 2);
  } catch (e) { document.getElementById('info').textContent = 'error: ' + e; }
}
async function refreshStats() {
  try {
    const j = await (await fetch('/api/stats')).json();
    document.getElementById('snapmeta').textContent = JSON.stringify(j, null, 2);
    if (j.depth && j.depth.median_m !== undefined) {
      document.getElementById('depth-meta').textContent =
        `${j.depth.width}x${j.depth.height}  min=${j.depth.min_m.toFixed(2)}m  median=${j.depth.median_m.toFixed(2)}m  max=${j.depth.max_m.toFixed(2)}m  valid=${(j.depth.valid_ratio*100).toFixed(1)}%`;
    }
    if (j.color) {
      document.getElementById('color-meta').textContent = `jpeg=${j.color.jpeg_bytes}B`;
    }
    if (j.fps !== undefined) {
      document.getElementById('fps-badge').textContent =
        `${j.fps.toFixed(1)} fps  seq=${j.seq}` + (j.last_error ? `  err: ${j.last_error}` : '');
    }
  } catch (e) { document.getElementById('snapmeta').textContent = 'error: ' + e; }
}
refreshInfo();
refreshStats();
setInterval(refreshStats, 500);
</script>
</body>
</html>
"""


def _fetch_info() -> dict:
    r = requests.get(f"http://{REMOTE}/info", timeout=5)
    r.raise_for_status()
    return r.json()


def _fetch_snapshot() -> dict:
    r = requests.get(f"http://{REMOTE}/snapshot", timeout=5)
    r.raise_for_status()
    return r.json()


def _ensure_info() -> dict:
    if _INFO_CACHE["data"] is None:
        _INFO_CACHE["data"] = _fetch_info()
    return _INFO_CACHE["data"]


def _decode_bytes(value) -> bytes:
    """/snapshot is JSON so binary payloads are base64-encoded strings."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return base64.b64decode(value)
    raise ValueError(f"unexpected value type for binary field: {type(value)}")


def _find_key(obj: dict, candidates: Tuple[str, ...]) -> Optional[str]:
    for k in candidates:
        if k in obj:
            return k
    return None


def _depth_shape() -> Tuple[Optional[int], Optional[int]]:
    info = _INFO_CACHE["data"] or {}
    # D455 server exposes depth W/H at top level of /info as `width`/`height`.
    if "width" in info and "height" in info:
        return int(info["width"]), int(info["height"])
    if "depth_width" in info and "depth_height" in info:
        return int(info["depth_width"]), int(info["depth_height"])
    d = info.get("depth")
    if isinstance(d, dict) and "width" in d and "height" in d:
        return int(d["width"]), int(d["height"])
    intr = info.get("intrinsics")
    if isinstance(intr, dict) and "width" in intr and "height" in intr:
        return int(intr["width"]), int(intr["height"])
    return None, None


def _depth_scale() -> float:
    info = _INFO_CACHE["data"] or {}
    # Flat field or nested under "depth"
    if "depth_scale" in info:
        return float(info["depth_scale"])
    d = info.get("depth")
    if isinstance(d, dict) and "scale" in d:
        return float(d["scale"])
    return 0.001  # D455 default


def _infer_shape_from_bytes(n_samples: int) -> Tuple[Optional[int], Optional[int]]:
    for w, h in [(848, 480), (1280, 720), (640, 480), (1280, 800), (424, 240)]:
        if w * h == n_samples:
            return w, h
    return None, None


def _encode_depth_colormap(depth_bytes: bytes, w: int, h: int, scale: float) -> Tuple[bytes, dict]:
    depth = np.frombuffer(depth_bytes, dtype=np.uint16).reshape(h, w)
    meters = depth.astype(np.float32) * scale
    vis = np.clip(meters, 0.3, 6.0)
    vis = ((vis - 0.3) / (6.0 - 0.3) * 255.0).astype(np.uint8)
    vis = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
    vis[depth == 0] = (0, 0, 0)
    ok, jpeg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
    valid = meters[(meters > 0.3) & (meters < 6.0)]
    stats = {"width": w, "height": h, "raw_bytes": len(depth_bytes), "scale": scale}
    if valid.size > 0:
        stats.update({
            "min_m": float(valid.min()),
            "median_m": float(np.median(valid)),
            "max_m": float(valid.max()),
            "valid_ratio": float(valid.size) / depth.size,
        })
    else:
        stats["valid_ratio"] = 0.0
    return (jpeg.tobytes() if ok else b""), stats


def _poll_loop():
    """Background thread — fetches /snapshot as fast as remote will allow,
    decodes depth, and writes both JPEGs into _LATEST so the MJPEG endpoints
    can fan them out to the browser at full rate."""
    session = requests.Session()
    frame_times = []  # for FPS smoothing
    while True:
        try:
            _ensure_info()
            r = session.get(f"http://{REMOTE}/snapshot", timeout=5)
            r.raise_for_status()
            snap = r.json()

            color_key = _find_key(snap, ("color_jpeg_b64", "color_jpeg", "color", "rgb_jpeg", "jpeg"))
            depth_key = _find_key(snap, ("depth_z16_b64", "depth_z16", "depth", "depth_raw"))
            if color_key is None or depth_key is None:
                _LATEST["last_error"] = f"missing keys; saw {list(snap.keys())}"
                time.sleep(0.5)
                continue

            color_jpeg = _decode_bytes(snap[color_key])
            depth_bytes = _decode_bytes(snap[depth_key])
            w = int(snap.get("w") or snap.get("width") or (_depth_shape()[0] or 0))
            h = int(snap.get("h") or snap.get("height") or (_depth_shape()[1] or 0))
            if not w or not h:
                w, h = _infer_shape_from_bytes(len(depth_bytes) // 2)
            scale = float(snap.get("depth_scale") or _depth_scale())

            depth_jpeg, depth_stats = _encode_depth_colormap(depth_bytes, w, h, scale)

            now = time.time()
            frame_times.append(now)
            frame_times = [t for t in frame_times if now - t < 2.0]
            fps = len(frame_times) / 2.0 if len(frame_times) > 1 else 0.0

            with _FRAME_LOCK:
                _LATEST["color_jpeg"] = color_jpeg
                _LATEST["depth_jpeg"] = depth_jpeg
                _LATEST["seq"] = int(snap.get("seq", 0))
                _LATEST["ts"] = float(snap.get("ts", now))
                _LATEST["fps"] = fps
                _LATEST["stats"] = {
                    "color": {"jpeg_bytes": len(color_jpeg)},
                    "depth": depth_stats,
                    "seq": _LATEST["seq"],
                    "ts": _LATEST["ts"],
                    "fps": round(fps, 1),
                }
                _LATEST["last_error"] = None
        except Exception as e:
            _LATEST["last_error"] = f"{type(e).__name__}: {e}"
            time.sleep(0.5)


def _mjpeg_generator(key: str):
    """Yield the shared latest frame as multipart/x-mixed-replace at ~30Hz cap."""
    boundary = b"--frame"
    last_seq = -1
    while True:
        with _FRAME_LOCK:
            payload = _LATEST.get(key)
            seq = _LATEST["seq"]
        if payload is None or seq == last_seq:
            time.sleep(0.01)
            continue
        last_seq = seq
        yield (
            boundary + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
            + payload + b"\r\n"
        )


@app.route("/")
def index():
    return render_template_string(INDEX_HTML, remote=REMOTE)


@app.route("/stream/color.mjpg")
def stream_color():
    return Response(_mjpeg_generator("color_jpeg"),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/stream/depth.mjpg")
def stream_depth():
    return Response(_mjpeg_generator("depth_jpeg"),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/stats")
def api_stats():
    with _FRAME_LOCK:
        return jsonify({**_LATEST["stats"], "last_error": _LATEST["last_error"]})


@app.route("/api/info")
def api_info():
    try:
        data = _fetch_info()
        _INFO_CACHE["data"] = data
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/snapshot/color.jpg")
def api_color():
    try:
        snap = _fetch_snapshot()
        k = _find_key(snap, ("color_jpeg_b64", "color_jpeg", "color", "rgb_jpeg", "jpeg"))
        if k is None:
            return Response(f"no color key; saw: {list(snap.keys())}".encode(), status=500)
        return Response(_decode_bytes(snap[k]), mimetype="image/jpeg")
    except Exception as e:
        return Response(f"error: {e}".encode(), status=502)


@app.route("/api/snapshot/depth.jpg")
def api_depth():
    try:
        snap = _fetch_snapshot()
        k = _find_key(snap, ("depth_z16_b64", "depth_z16", "depth", "depth_raw"))
        if k is None:
            return Response(f"no depth key; saw: {list(snap.keys())}".encode(), status=500)
        depth_bytes = _decode_bytes(snap[k])
        _ensure_info()
        w, h = _depth_shape()
        if not w or not h:
            w, h = _infer_shape_from_bytes(len(depth_bytes) // 2)
        if not w or not h:
            return Response(
                f"unknown depth shape, {len(depth_bytes)//2} uint16 samples".encode(), status=500
            )
        depth = np.frombuffer(depth_bytes, dtype=np.uint16).reshape(h, w)
        meters = depth.astype(np.float32) * _depth_scale()
        vis = np.clip(meters, 0.3, 6.0)
        vis = ((vis - 0.3) / (6.0 - 0.3) * 255.0).astype(np.uint8)
        vis = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
        vis[depth == 0] = (0, 0, 0)  # invalid/no-return pixels
        ok, jpeg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return Response(b"jpeg encode failed", status=500)
        return Response(jpeg.tobytes(), mimetype="image/jpeg")
    except Exception as e:
        return Response(f"error: {e}".encode(), status=502)


@app.route("/api/snapshot_meta")
def api_snapshot_meta():
    try:
        snap = _fetch_snapshot()
        _ensure_info()
        out = {"keys": list(snap.keys())}

        color_key = _find_key(snap, ("color_jpeg_b64", "color_jpeg", "color", "rgb_jpeg", "jpeg"))
        if color_key:
            jpeg = _decode_bytes(snap[color_key])
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out["color"] = {
                    "key": color_key,
                    "width": int(img.shape[1]),
                    "height": int(img.shape[0]),
                    "jpeg_bytes": len(jpeg),
                }

        depth_key = _find_key(snap, ("depth_z16_b64", "depth_z16", "depth", "depth_raw"))
        if depth_key:
            depth_bytes = _decode_bytes(snap[depth_key])
            w, h = _depth_shape()
            if not w or not h:
                w, h = _infer_shape_from_bytes(len(depth_bytes) // 2)
            d = {"key": depth_key, "raw_bytes": len(depth_bytes), "width": w, "height": h}
            if w and h:
                depth = np.frombuffer(depth_bytes, dtype=np.uint16).reshape(h, w)
                meters = depth.astype(np.float32) * _depth_scale()
                valid = meters[(meters > 0.3) & (meters < 6.0)]
                d["scale"] = _depth_scale()
                if valid.size > 0:
                    d.update({
                        "min_m": float(valid.min()),
                        "median_m": float(np.median(valid)),
                        "max_m": float(valid.max()),
                        "valid_ratio": float(valid.size) / depth.size,
                    })
                else:
                    d["valid_ratio"] = 0.0
            out["depth"] = d

        # Echo remaining scalar fields (timestamp, frame_id, etc.)
        for k, v in snap.items():
            if k in (color_key, depth_key):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True, help="remote host:port e.g. <remote-host>:8765")
    p.add_argument("--port", type=int, default=7000, help="local port (default 7000)")
    args = p.parse_args()
    global REMOTE
    REMOTE = args.server
    print(f"[INFO] remote = http://{REMOTE}")
    print(f"[INFO] open   http://127.0.0.1:{args.port}/  in your browser")
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
