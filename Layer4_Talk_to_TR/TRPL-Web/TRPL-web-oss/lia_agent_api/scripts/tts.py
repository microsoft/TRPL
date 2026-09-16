#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import struct
import threading
import time

import azure.cognitiveservices.speech as speechsdk
import dotenv


dotenv.load_dotenv()

# Supply deployment values through a local environment file for one-off runs.
OUTPUT_PATH = "output_audio.wav"
VOICE = os.getenv("TTS_AZURE_VOICE_ID", "").strip()
REGION = os.getenv("TTS_AZURE_REGION", "").strip()
DEPLOYMENT = os.getenv("TTS_AZURE_DEPLOYMENT_ID", "").strip()
WRITE_CHUNK_SIZE = 180
DONE_TIMEOUT_SECONDS = 180

TEXT = (
    "This is synthetic sample text for a local text-to-speech diagnostic. "
    "It does not contain archive or visitor content."
)


def write_wav_file(
    filename: str,
    audio_data: bytes,
    sample_rate: int,
    bits_per_sample: int,
    channels: int,
) -> None:
    """Write a WAV file with a proper RIFF header for raw PCM audio."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(audio_data)

    with open(filename, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", data_size + 36))
        f.write(b"WAVE")

        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))

        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(audio_data)


def main() -> None:
    tts_key = os.getenv("TTS_AZURE_API_KEY")
    if not tts_key:
        raise ValueError("TTS_AZURE_API_KEY is not set")
    if not REGION:
        raise ValueError("TTS_AZURE_REGION is not set")
    if not VOICE:
        raise ValueError("TTS_AZURE_VOICE_ID is not set")

    endpoint = f"wss://{REGION}.tts.speech.microsoft.com/cognitiveservices/websocket/v2"
    speech_config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=tts_key)
    speech_config.speech_synthesis_voice_name = VOICE

    if DEPLOYMENT:
        speech_config.endpoint_id = DEPLOYMENT

    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
    )

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
    done_event = threading.Event()
    audio_chunks: list[bytes] = []
    cancellation_error: dict[str, str | None] = {"error": None}

    def on_synthesizing(evt) -> None:
        if evt.result.audio_data:
            audio_chunks.append(evt.result.audio_data)

    def on_completed(_evt) -> None:
        done_event.set()

    def on_canceled(evt) -> None:
        details = evt.result.cancellation_details
        cancellation_error["error"] = (
            f"{details.reason}; error={details.error_details}"
        )
        done_event.set()

    synthesizer.synthesizing.connect(on_synthesizing)
    synthesizer.synthesis_completed.connect(on_completed)
    synthesizer.synthesis_canceled.connect(on_canceled)

    request = speechsdk.SpeechSynthesisRequest(
        speechsdk.SpeechSynthesisRequestInputType.TextStream
    )
    synth_future = synthesizer.speak_async(request)

    # Feed text in small chunks to avoid large single writes and keep stream behavior.
    for i in range(0, len(TEXT), WRITE_CHUNK_SIZE):
        request.input_stream.write(TEXT[i : i + WRITE_CHUNK_SIZE])
        time.sleep(0.01)
    request.input_stream.close()

    if not done_event.wait(DONE_TIMEOUT_SECONDS):
        synthesizer.stop_speaking_async()
        raise TimeoutError(
            f"TTS did not complete within {DONE_TIMEOUT_SECONDS} seconds."
        )

    # Resolve future for completeness and surface SDK-level failures.
    final_result = synth_future.get()
    if final_result.reason == speechsdk.ResultReason.Canceled:
        details = final_result.cancellation_details
        raise RuntimeError(f"TTS canceled: {details.reason}; error={details.error_details}")
    if cancellation_error["error"]:
        raise RuntimeError(f"TTS canceled: {cancellation_error['error']}")

    audio_data = b"".join(audio_chunks)
    if not audio_data:
        raise RuntimeError("No audio received from TTS stream.")

    write_wav_file(
        filename=OUTPUT_PATH,
        audio_data=audio_data,
        sample_rate=24000,
        bits_per_sample=16,
        channels=1,
    )
    print(f"Saved WAV: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
