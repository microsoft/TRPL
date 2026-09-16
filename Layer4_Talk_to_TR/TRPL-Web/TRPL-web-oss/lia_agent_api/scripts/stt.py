# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import threading
from datetime import datetime
from time import monotonic

import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv

load_dotenv()

INITIAL_SILENCE_TIMEOUT_MS = 5000
END_SILENCE_TIMEOUT_MS = 1200
SEGMENTATION_SILENCE_TIMEOUT_MS = 1200


def _now_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def recognize_from_microphone():
    # This example requires environment variables named "SPEECH_KEY" and "ENDPOINT"
    # Replace with your own subscription key and endpoint, the endpoint is like : "https://YourServiceRegion.api.cognitive.microsoft.com"
    speech_config = speechsdk.SpeechConfig(
        subscription=os.environ.get("SPEECH_KEY"), endpoint=os.environ.get("ENDPOINT")
    )
    speech_config.speech_recognition_language = "en-US"
    # speech_config.set_property(
    #     speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
    #     str(INITIAL_SILENCE_TIMEOUT_MS),
    # )
    # speech_config.set_property(
    #     # Note: deprecated
    #     speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs,
    #     str(END_SILENCE_TIMEOUT_MS),
    # )
    # speech_config.set_property(
    #     speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
    #     str(SEGMENTATION_SILENCE_TIMEOUT_MS),
    # )

    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config
    )

    done = threading.Event()
    state = {"current_utterance_id": 0, "utterance_end_times": {}}

    def on_recognizing(evt: speechsdk.SpeechRecognitionEventArgs):
        if evt.result.text:
            print(f"[{_now_ts()}] Partial: {evt.result.text}", flush=True)

    def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            utterance_id = state["current_utterance_id"]
            end_ts = state["utterance_end_times"].get(utterance_id)
            if end_ts is not None:
                latency_ms = int((monotonic() - end_ts) * 1000)
                print(
                    f"[{_now_ts()}] Utterance {utterance_id} end->transcription latency: {latency_ms} ms",
                    flush=True,
                )
            print(f"[{_now_ts()}] Final: {evt.result.text}", flush=True)
            print(
                f"[{_now_ts()}] Utterance {utterance_id} transcription done",
                flush=True,
            )
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            print(
                f"[{_now_ts()}] No speech could be recognized: {evt.result.no_match_details}",
                flush=True,
            )

    def on_speech_start(evt: speechsdk.RecognitionEventArgs):
        state["current_utterance_id"] += 1

    def on_speech_end(evt: speechsdk.RecognitionEventArgs):
        utterance_id = state["current_utterance_id"]
        state["utterance_end_times"][utterance_id] = monotonic()
        print(f"[{_now_ts()}] Utterance {utterance_id} finished", flush=True)

    def on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs):
        print(f"[{_now_ts()}] Speech Recognition canceled: {evt.reason}", flush=True)
        if evt.reason == speechsdk.CancellationReason.Error:
            print(f"[{_now_ts()}] Error details: {evt.error_details}", flush=True)
            print(
                f"[{_now_ts()}] Did you set the speech resource key and endpoint values?",
                flush=True,
            )
        done.set()

    def on_session_stopped(evt: speechsdk.SessionEventArgs):
        print(f"[{_now_ts()}] Session stopped.", flush=True)
        done.set()

    speech_recognizer.recognizing.connect(on_recognizing)
    speech_recognizer.recognized.connect(on_recognized)
    speech_recognizer.speech_start_detected.connect(on_speech_start)
    speech_recognizer.speech_end_detected.connect(on_speech_end)
    speech_recognizer.canceled.connect(on_canceled)
    speech_recognizer.session_stopped.connect(on_session_stopped)

    print("Speak into your microphone (Ctrl+C to stop).")
    speech_recognizer.start_continuous_recognition()
    try:
        done.wait()
    except KeyboardInterrupt:
        print(f"[{_now_ts()}] Stopping...", flush=True)
    finally:
        speech_recognizer.stop_continuous_recognition()


recognize_from_microphone()
