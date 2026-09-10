#!/usr/bin/env python3
"""Emotion-label smoke test for the custom DragonHD V2.5 voice.

Synthesizes one short sentence per (emotion-label x feed-format) and saves each
to a WAV file so you can listen and decide which labelling convention the
trained model actually responds to.

The model was trained with custom emotion labels:
    Excitement, Awe, Serious, Grief, Happiness, Tenderness

Two feed formats are tried per label, because a custom-trained DragonHD voice
could accept the label either way and we don't yet know which one it was
trained on:

  * "bracket" -> inline plain-text marker, e.g. "[Excitement] <sentence>"
                 (Azure HD "plain-text style markers", streaming-compatible)
  * "style"   -> SpeechSynthesisRequest.style = "Excitement" set before the
                 text is streamed (the SDK request property)

A "neutral" baseline with no label is also produced for comparison.

Credentials/config are read from the environment (.env is loaded). Reuses the
exact TextStream + websocket-v2 path used in production (scripts/tts.py).

Usage:
    # set the new model's endpoint deployment id first:
    #   export TTS_AZURE_DEPLOYMENT_ID=<deployment-guid-from-the-endpoint-page>
    .venv/bin/python scripts/test_emotion_tts.py
    .venv/bin/python scripts/test_emotion_tts.py --labels Excitement Grief
    .venv/bin/python scripts/test_emotion_tts.py --formats bracket
"""

from __future__ import annotations

import argparse
import os
import threading
import time

import azure.cognitiveservices.speech as speechsdk
import dotenv

dotenv.load_dotenv()

# ── Config (override via env) ────────────────────────────────────────────────
VOICE = os.getenv("TTS_AZURE_VOICE_ID", "").strip()
REGION = os.getenv("TTS_AZURE_REGION", "").strip()
DEPLOYMENT = os.getenv("TTS_AZURE_DEPLOYMENT_ID", "").strip()
OUTPUT_DIR = os.getenv("EMOTION_OUT_DIR", "emotion_samples")

# The 6 labels the model was trained on.
LABELS = ["Excitement", "Awe", "Serious", "Grief", "Happiness", "Tenderness"]

# A single, emotionally-flexible sentence so the colouring is audible.
TEXT = (
    "So this is the moment we have waited for. "
    "After everything we have been through, here we finally stand together."
)

WRITE_CHUNK_SIZE = 180
DONE_TIMEOUT_SECONDS = 60
SAMPLE_RATE = 24000


def synthesize(key: str, text: str, style: str | None, out_path: str) -> str | None:
    """Run one TextStream synthesis straight to a WAV file (no audio device,
    so it works on a headless VM). Returns an error string or None on success.
    """
    endpoint = f"wss://{REGION}.tts.speech.microsoft.com/cognitiveservices/websocket/v2"
    speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=key)
    speech_config.speech_synthesis_voice_name = VOICE
    if DEPLOYMENT:
        speech_config.endpoint_id = DEPLOYMENT
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )

    # File sink avoids initialising the default speaker (SPXERR_AUDIO_SYS_LIBRARY_NOT_FOUND).
    audio_config = speechsdk.audio.AudioOutputConfig(filename=out_path)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )
    done = threading.Event()
    err: dict[str, str | None] = {"error": None}

    synthesizer.synthesis_completed.connect(lambda _evt: done.set())

    def on_canceled(evt) -> None:
        d = evt.result.cancellation_details
        err["error"] = f"{d.reason}; {d.error_details}"
        done.set()

    synthesizer.synthesis_canceled.connect(on_canceled)

    request = speechsdk.SpeechSynthesisRequest(
        speechsdk.SpeechSynthesisRequestInputType.TextStream
    )
    if style is not None:
        # Set the per-request style BEFORE streaming any text.
        request.style = style

    future = synthesizer.speak_async(request)
    for i in range(0, len(text), WRITE_CHUNK_SIZE):
        request.input_stream.write(text[i : i + WRITE_CHUNK_SIZE])
        time.sleep(0.01)
    request.input_stream.close()

    if not done.wait(DONE_TIMEOUT_SECONDS):
        synthesizer.stop_speaking_async()
        return f"timeout after {DONE_TIMEOUT_SECONDS}s"

    final = future.get()
    if final.reason == speechsdk.ResultReason.Canceled:
        d = final.cancellation_details
        return f"{d.reason}; {d.error_details}"
    return err["error"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", nargs="*", default=LABELS, help="subset of emotion labels")
    parser.add_argument(
        "--formats",
        nargs="*",
        choices=["bracket", "style", "neutral"],
        default=["neutral", "bracket", "style"],
        help="which feed formats to test",
    )
    args = parser.parse_args()

    key = os.getenv("TTS_AZURE_API_KEY")
    if not key:
        raise SystemExit("TTS_AZURE_API_KEY is not set (check the .env in this dir).")
    if not REGION:
        raise SystemExit("TTS_AZURE_REGION is not set (check the .env in this dir).")
    if not VOICE:
        raise SystemExit("TTS_AZURE_VOICE_ID is not set (check the .env in this dir).")
    if not DEPLOYMENT:
        print("WARNING: TTS_AZURE_DEPLOYMENT_ID is not set; custom voice synthesis "
              "may fail.\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"voice={VOICE}  region={REGION}  deployment={'set' if DEPLOYMENT else 'MISSING'}")
    print(f"output dir: {OUTPUT_DIR}\n")

    jobs: list[tuple[str, str, str | None]] = []  # (out_name, text, style)
    if "neutral" in args.formats:
        jobs.append(("neutral", TEXT, None))
    for label in args.labels:
        if "bracket" in args.formats:
            jobs.append((f"{label}_bracket", f"[{label}] {TEXT}", None))
        if "style" in args.formats:
            jobs.append((f"{label}_style", TEXT, label))

    results = []
    for name, text, style in jobs:
        path = os.path.join(OUTPUT_DIR, f"{name}.wav")
        error = synthesize(key, text, style, path)
        if error is None and os.path.exists(path):
            kb = os.path.getsize(path) / 1024
            secs = max(0.0, (os.path.getsize(path) - 44)) / (SAMPLE_RATE * 2)
            status = f"OK  {secs:5.1f}s  {kb:6.0f}KB  -> {path}"
        else:
            status = f"FAIL  ({error})"
        print(f"{name:22s} {status}")
        results.append((name, error is None, error))

    ok = sum(1 for _, good, _ in results if good)
    print(f"\n{ok}/{len(results)} succeeded. Listen and compare the WAVs in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
