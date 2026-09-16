#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Does a single leading [Label] survive a *streamed* multi-sentence reply?

Background
----------
test_emotion_tts.py proved the bracket marker works — but it writes the whole
(short) text to the TextStream in one go. Production is different: the brain
flushes the reply to TTS sentence-by-sentence (io.py `_should_flush`: every
`.!?\\n` or 60 chars), and the worker bridge prepends `[Label]` only to the
FIRST chunk of the utterance. If DragonHD re-applies its default style at each
synthesis segment boundary, only the first sentence would carry the emotion and
the rest would revert — the "后半段语音突变" the user reported.

This script reproduces that path and lets you hear the difference. For each
label it renders three WAVs from the SAME multi-sentence text:

  * <label>_oneshot      — "[L] <whole text>" written in one shot (baseline)
  * <label>_stream_first — text written sentence-by-sentence, [L] on the
                           FIRST sentence only (mimics production today)
  * <label>_stream_each  — text written sentence-by-sentence, [L] prepended to
                           EVERY sentence (the candidate fix)

If stream_first loses the emotion after sentence 1 but stream_each keeps it,
the diagnosis holds and the fix is "re-inject the label per sentence".

Usage:
    export TTS_AZURE_DEPLOYMENT_ID=<deployment-guid>
    .venv/bin/python scripts/test_emotion_streaming.py
    .venv/bin/python scripts/test_emotion_streaming.py --labels Grief Tenderness
"""
from __future__ import annotations

import argparse
import os
import re
import threading
import time

import azure.cognitiveservices.speech as speechsdk
import dotenv

dotenv.load_dotenv()

VOICE = os.getenv("TTS_AZURE_VOICE_ID", "").strip()
REGION = os.getenv("TTS_AZURE_REGION", "").strip()
DEPLOYMENT = os.getenv("TTS_AZURE_DEPLOYMENT_ID", "").strip()
OUTPUT_DIR = os.getenv("EMOTION_OUT_DIR", "emotion_streaming_samples")

LABELS = ["Excitement", "Awe", "Serious", "Grief", "Happiness", "Tenderness"]

# A multi-sentence reply, like the real Grief monologue that triggered the bug,
# so the loss-of-emotion is audible across several sentences.
TEXT = (
    "That day was darker than any storm I had known. "
    "My mother died upstairs, then hours later my wife passed, "
    "leaving our newborn daughter in my arms. "
    "I wrote a single cross in my diary for the date, and fled the city soon after. "
    "The silence of the Badlands did more for me than a room full of words."
)

# Mimic the brain's per-sentence flush. Inter-chunk delay approximates how the
# deltas dribble out of the LLM stream in production.
SENTENCE_GAP_SECONDS = 0.20
ONESHOT_CHUNK = 180
DONE_TIMEOUT_SECONDS = 60
SAMPLE_RATE = 24000


def _sentences(text: str) -> list[str]:
    """Split into sentence-ish chunks on . ! ? keeping the punctuation."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _new_synth(key: str, out_path: str):
    endpoint = f"wss://{REGION}.tts.speech.microsoft.com/cognitiveservices/websocket/v2"
    speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=key)
    speech_config.speech_synthesis_voice_name = VOICE
    if DEPLOYMENT:
        speech_config.endpoint_id = DEPLOYMENT
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
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
    return synthesizer, done, err


def _run(synthesizer, done, err, chunks: list[str]) -> str | None:
    """Stream the given chunks into one TextStream synthesis, one write each."""
    request = speechsdk.SpeechSynthesisRequest(
        speechsdk.SpeechSynthesisRequestInputType.TextStream
    )
    future = synthesizer.speak_async(request)
    for i, chunk in enumerate(chunks):
        request.input_stream.write(chunk)
        if i < len(chunks) - 1:
            time.sleep(SENTENCE_GAP_SECONDS)
    request.input_stream.close()
    if not done.wait(DONE_TIMEOUT_SECONDS):
        synthesizer.stop_speaking_async()
        return f"timeout after {DONE_TIMEOUT_SECONDS}s"
    final = future.get()
    if final.reason == speechsdk.ResultReason.Canceled:
        d = final.cancellation_details
        return f"{d.reason}; {d.error_details}"
    return err["error"]


def _chunks_for(mode: str, label: str) -> list[str]:
    sents = _sentences(TEXT)
    if mode == "oneshot":
        whole = f"[{label}] {TEXT}"
        return [whole[i : i + ONESHOT_CHUNK] for i in range(0, len(whole), ONESHOT_CHUNK)]
    if mode == "stream_first":
        return [f"[{label}] {sents[0]}" if i == 0 else f" {s}"
                for i, s in enumerate(sents)]
    if mode == "stream_each":
        return [f"[{label}] {s} " for s in sents]
    raise ValueError(mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", nargs="*", default=["Grief"], help="emotion labels")
    parser.add_argument(
        "--modes", nargs="*",
        choices=["oneshot", "stream_first", "stream_each"],
        default=["oneshot", "stream_first", "stream_each"],
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
    print(f"sentences={len(_sentences(TEXT))}  gap={SENTENCE_GAP_SECONDS}s  out={OUTPUT_DIR}/\n")

    results = []
    for label in args.labels:
        for mode in args.modes:
            name = f"{label}_{mode}"
            path = os.path.join(OUTPUT_DIR, f"{name}.wav")
            synthesizer, done, err = _new_synth(key, path)
            error = _run(synthesizer, done, err, _chunks_for(mode, label))
            if error is None and os.path.exists(path):
                secs = max(0.0, (os.path.getsize(path) - 44)) / (SAMPLE_RATE * 2)
                status = f"OK  {secs:5.1f}s  -> {path}"
            else:
                status = f"FAIL  ({error})"
            print(f"{name:24s} {status}")
            results.append((name, error is None))

    ok = sum(1 for _, good in results if good)
    print(f"\n{ok}/{len(results)} succeeded. Compare in {OUTPUT_DIR}/:")
    print("  *_oneshot      = baseline (emotion should span the whole clip)")
    print("  *_stream_first = production today (listen: does it fade after S1?)")
    print("  *_stream_each  = candidate fix (emotion should span the whole clip)")


if __name__ == "__main__":
    main()
