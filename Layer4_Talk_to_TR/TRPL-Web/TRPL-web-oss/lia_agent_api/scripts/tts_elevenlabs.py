# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os, json, threading, time
from pathlib import Path
from collections import deque

import requests
import pyaudio
from dotenv import load_dotenv

# -----------------------------
# Configuration
# -----------------------------


load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.environ["ELEVEN_API_KEY"]
VOICE_ID = "ZOpCSiuZUemRwpvp3yXP"
TEXT = "Testing clean streaming playback."
MODEL_ID = "eleven_v3"

SAMPLE_RATE = 22050
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
BYTES_PER_FRAME = CHANNELS * SAMPLE_WIDTH

# Play in fixed blocks (power of two is nice). Must be multiple of BYTES_PER_FRAME.
PLAY_BLOCK_BYTES = 4096  # 4096 bytes = 2048 frames at 16-bit mono

url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream?output_format=pcm_22050"
headers = {"xi-api-key": API_KEY, "Content-Type": "application/json"}
payload = {"text": TEXT, "model_id": MODEL_ID}

# Thread-safe byte buffer
buf = deque()
buf_size = 0
buf_lock = threading.Lock()
done = False
remainder = b""

def producer():
    global done, remainder, buf_size
    try:
        with requests.post(url, headers=headers, data=json.dumps(payload), stream=True, timeout=30) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                # Preserve sample alignment (carry remainder instead of dropping a byte)
                chunk = remainder + chunk
                extra = len(chunk) % BYTES_PER_FRAME
                if extra:
                    remainder = chunk[-extra:]
                    chunk = chunk[:-extra]
                else:
                    remainder = b""

                if chunk:
                    with buf_lock:
                        buf.append(chunk)
                        buf_size += len(chunk)
    finally:
        done = True

def consumer(stream):
    global buf_size
    # Prebuffer ~200ms to avoid startup underflows
    target_prebuffer = int(SAMPLE_RATE * 0.2) * BYTES_PER_FRAME  # 200ms in bytes
    while True:
        with buf_lock:
            if buf_size >= target_prebuffer or (done and buf_size > 0):
                break
        if done:
            break
        time.sleep(0.005)

    local = bytearray()

    while True:
        with buf_lock:
            while buf and len(local) < PLAY_BLOCK_BYTES:
                chunk = buf.popleft()
                buf_size -= len(chunk)
                local.extend(chunk)

        if len(local) >= PLAY_BLOCK_BYTES:
            block = bytes(local[:PLAY_BLOCK_BYTES])
            del local[:PLAY_BLOCK_BYTES]
            stream.write(block)
            continue

        if done:
            # flush remaining
            if local:
                # pad with silence to full frame alignment
                pad = (-len(local)) % BYTES_PER_FRAME
                stream.write(bytes(local) + (b"\x00" * pad))
            break

        # If we’re short, wait a hair for more network data (avoids underflow noise)
        time.sleep(0.005)

def main():
    p = pyaudio.PyAudio()
    stream = p.open(
        format=p.get_format_from_width(SAMPLE_WIDTH),  # paInt16
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        output=True,
        frames_per_buffer=PLAY_BLOCK_BYTES // BYTES_PER_FRAME,
    )

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    try:
        consumer(stream)
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == "__main__":
    main()