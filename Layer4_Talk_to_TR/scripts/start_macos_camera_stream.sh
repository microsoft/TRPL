#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This publisher requires macOS AVFoundation." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required. Install it with: brew install ffmpeg" >&2
  exit 1
fi

device="${1:-0}"
port="${CAMERA_STREAM_PORT:-8090}"
fps="${CAMERA_STREAM_FPS:-30}"
size="${CAMERA_STREAM_SIZE:-1280x720}"
bind="${CAMERA_STREAM_BIND:-127.0.0.1}"
bind_url="${bind}"

if [[ "${bind}" != "127.0.0.1" && "${bind}" != "::1" \
      && "${CAMERA_STREAM_ALLOW_NETWORK:-false}" != "true" ]]; then
  echo "Refusing a network-visible bind without CAMERA_STREAM_ALLOW_NETWORK=true." >&2
  exit 1
fi

if [[ "${bind}" == "::1" ]]; then
  bind_url="[${bind}]"
fi

echo "Publishing macOS camera ${device} at http://${bind_url}:${port}/stream.mjpg"
echo "Keep this process running while using the camera dashboard. Press Ctrl-C to stop."

exec ffmpeg \
  -hide_banner \
  -f avfoundation \
  -framerate "${fps}" \
  -video_size "${size}" \
  -pixel_format nv12 \
  -i "${device}:none" \
  -c:v mjpeg \
  -q:v 5 \
  -f mpjpeg \
  -listen 1 \
  "http://${bind_url}:${port}/stream.mjpg"
