#!/usr/bin/env python3
"""
OpenAI/Azure Realtime transcription demo (WAV file) over WebSocket.

- Streams WAV audio to OpenAI Realtime by default
- Shows partial transcription overwriting a single terminal line
- Logs VAD speech start/stop events
- Uses .env for OPENAI_API_KEY by default
- Supports Azure with --provider azure
- CLI arg for VAD: --vad server|semantic (default semantic)

Install:
  pip install websockets python-dotenv
"""

import argparse
import asyncio
import audioop
import base64
import inspect
import json
import os
import sys
import time
import wave
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK


@dataclass
class AudioPCM:
    pcm16_mono_24k: bytes
    sample_rate: int = 24000
    channels: int = 1
    sample_width: int = 2  # bytes


def _read_wav(path: str):
    with wave.open(path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        comptype = wf.getcomptype()
        if comptype != "NONE":
            raise ValueError(
                f"Unsupported WAV compression {comptype}; please provide PCM WAV."
            )
        raw = wf.readframes(nframes)

    return raw, sr, nch, sw


def _to_pcm16_mono(raw: bytes, nch: int, sw: int) -> bytes:
    if sw not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported sample width: {sw}")

    mono = raw if nch == 1 else audioop.tomono(raw, sw, 0.5, 0.5)
    if sw != 2:
        mono = audioop.lin2lin(mono, sw, 2)
    return mono


def _resample_pcm16(pcm16_mono: bytes, src_sr: int, dst_sr: int) -> bytes:
    if src_sr == dst_sr:
        return pcm16_mono
    converted, _state = audioop.ratecv(pcm16_mono, 2, 1, src_sr, dst_sr, None)
    return converted


def load_wav_as_pcm16_mono_24k(path: str) -> AudioPCM:
    raw, sr, nch, sw = _read_wav(path)
    mono_16 = _to_pcm16_mono(raw, nch, sw)
    mono_16_24k = _resample_pcm16(mono_16, sr, 24000)
    return AudioPCM(pcm16_mono_24k=mono_16_24k)


def _print_overwrite(line: str, width: int = 140):
    if len(line) > width:
        line = line[: width - 1] + "…"
    sys.stdout.write("\r" + line.ljust(width))
    sys.stdout.flush()


def _make_azure_ws_url(azure_endpoint: str, api_version: str, deployment: str) -> str:
    """
    Azure Realtime WebSocket URL (preview form):
      wss://{host}/openai/realtime?api-version=...&deployment=...&intent=transcription

    Microsoft Learn documents /openai/realtime with deployment= for preview. :contentReference[oaicite:1]{index=1}
    """
    ep = azure_endpoint.strip()
    if not ep.startswith("http"):
        ep = "https://" + ep

    u = urlparse(ep)
    host = u.netloc or u.path  # tolerate passing just hostname
    return (
        f"wss://{host}/openai/realtime"
        f"?api-version={api_version}"
        f"&deployment={deployment}"
        f"&intent=transcription"
    )


def _make_openai_ws_url(model: str) -> str:
    return "wss://api.openai.com/v1/realtime" f"?intent=transcription"


async def run(
    wav_path: str,
    provider: str,
    vad_mode: str,
    eagerness: str,
    no_sleep: bool,
    api_version: str,
    deployment: str,
    model: str,
    language: str,
    debug_events: bool,
    post_audio_wait: float,
):
    load_dotenv()

    if provider == "openai":
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            raise RuntimeError("Missing OPENAI_API_KEY in .env.")
        ws_url = _make_openai_ws_url(model)
        headers = {"Authorization": f"Bearer {openai_key}"}
    else:
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_key = os.getenv("AZURE_OPENAI_KEY")
        if not azure_endpoint:
            raise RuntimeError(
                "Missing AZURE_OPENAI_ENDPOINT in .env (e.g. https://<resource>.cognitiveservices.azure.com)."
            )
        if not azure_key:
            raise RuntimeError("Missing AZURE_OPENAI_KEY in .env.")
        if not deployment:
            deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""
        if not deployment:
            raise RuntimeError(
                "Missing deployment name. Provide --deployment or AZURE_OPENAI_DEPLOYMENT in .env."
            )
        ws_url = _make_azure_ws_url(azure_endpoint, api_version, deployment)
        headers = {"api-key": azure_key}

    pcm = load_wav_as_pcm16_mono_24k(wav_path)

    # 20ms chunks @ 24kHz => 480 samples => 960 bytes (mono int16)
    chunk_ms = 20
    bytes_per_ms = pcm.sample_rate * pcm.sample_width // 1000  # 48 bytes/ms
    chunk_size = bytes_per_ms * chunk_ms  # 960 bytes

    partial_by_item = {}
    speech_open_items = set()

    connect_sig = inspect.signature(websockets.connect)
    header_kwarg = (
        "additional_headers"
        if "additional_headers" in connect_sig.parameters
        else "extra_headers"
    )
    connect_kwargs = {header_kwarg: headers, "max_size": 8 * 1024 * 1024}

    async with websockets.connect(ws_url, **connect_kwargs) as ws:
        # Configure transcription session
        if vad_mode == "server":
            turn_detection = {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            }
        else:
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": eagerness,  # low|medium|high|auto (service-dependent)
            }

        if provider == "openai":
            session_update = {
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": pcm.sample_rate,
                            },
                            "transcription": {
                                "model": model,
                                "prompt": "",
                                "language": language,
                            },
                            "turn_detection": turn_detection,
                            "noise_reduction": {"type": "near_field"},
                        }
                    },
                },
            }
        else:
            session_update = {
                "type": "transcription_session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": model,  # often "gpt-4o-transcribe" (or mini)
                        "prompt": "",
                        "language": language,
                    },
                    "turn_detection": turn_detection,
                    "input_audio_noise_reduction": {"type": "near_field"},
                },
            }
        await ws.send(json.dumps(session_update))

        async def sender():
            data = pcm.pcm16_mono_24k
            t0 = time.perf_counter()

            for i in range(0, len(data), chunk_size):
                chunk = data[i : i + chunk_size]
                b64 = base64.b64encode(chunk).decode("ascii")
                await ws.send(
                    json.dumps({"type": "input_audio_buffer.append", "audio": b64})
                )

                if not no_sleep:
                    # pace roughly in realtime
                    elapsed_chunks = i / chunk_size
                    target = t0 + elapsed_chunks * (chunk_ms / 1000.0)
                    delay = target - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)

            # Ensure final segment flushes even if VAD doesn't close it.
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        async def receiver():
            while True:
                try:
                    msg = await ws.recv()
                except ConnectionClosedOK:
                    return
                except ConnectionClosedError as e:
                    raise RuntimeError(
                        f"Realtime connection closed with error: {e}"
                    ) from e
                evt = json.loads(msg)
                etype = evt.get("type", "")
                item_id = evt.get("item_id", "unknown")

                if etype == "input_audio_buffer.speech_started":
                    start_ms = evt.get("audio_start_ms")
                    for open_item_id in list(speech_open_items):
                        if open_item_id != item_id:
                            speech_open_items.discard(open_item_id)
                            print(
                                f"\n[VAD] speech_stopped  item_id={open_item_id} at {start_ms}ms (inferred)"
                            )
                    speech_open_items.add(item_id)
                    print(f"\n[VAD] speech_started item_id={item_id} at {start_ms}ms")

                elif etype == "input_audio_buffer.speech_stopped":
                    speech_open_items.discard(item_id)
                    print(
                        f"\n[VAD] speech_stopped  item_id={item_id} at {evt.get('audio_end_ms')}ms"
                    )

                elif etype in {
                    "conversation.item.input_audio_transcription.delta",
                    "conversation.item.input_audio_transcription.partial",
                    "input_audio_transcription.delta",
                    "input_audio_transcription.partial",
                    "transcription.delta",
                    "transcription.partial",
                    "response.audio_transcript.delta",
                    "response.audio_transcript.partial",
                }:
                    delta = (
                        evt.get("delta")
                        or evt.get("text_delta")
                        or evt.get("partial")
                        or evt.get("transcript")
                        or evt.get("text")
                        or ""
                    )
                    if delta:
                        partial_by_item[item_id] = (
                            partial_by_item.get(item_id, "") + delta
                        )
                        _print_overwrite(f"[partial] {partial_by_item[item_id]}")

                elif etype in {
                    "conversation.item.input_audio_transcription.completed",
                    "input_audio_transcription.completed",
                    "transcription.completed",
                    "response.audio_transcript.done",
                }:
                    text = evt.get("transcript", "") or evt.get("text", "") or ""
                    sys.stdout.write("\r" + (" " * 160) + "\r")
                    sys.stdout.flush()
                    print(f"[final  ] item_id={item_id}: {text}")
                    if item_id in speech_open_items:
                        speech_open_items.discard(item_id)
                        print(
                            f"[VAD] speech_stopped  item_id={item_id} at ?ms (inferred)"
                        )

                elif etype == "error":
                    raise RuntimeError(f"Realtime error: {evt}")

                elif debug_events:
                    print(
                        f"\n[debug ] unhandled event type={etype} keys={sorted(evt.keys())}"
                    )

        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())
        await sender_task

        # Let late VAD/final transcription events arrive, then close cleanly.
        await asyncio.sleep(max(0.0, post_audio_wait))
        if not receiver_task.done():
            await ws.close(code=1000, reason="audio_complete")

        await receiver_task


def main():
    p = argparse.ArgumentParser(
        description="OpenAI/Azure Realtime transcription demo for WAV files."
    )
    p.add_argument("wav_path", help="Path to a 16-bit PCM WAV file (mono or stereo).")
    p.add_argument(
        "--provider",
        choices=["openai", "azure"],
        default="openai",
        help="Realtime provider (default: openai).",
    )

    p.add_argument(
        "--vad",
        choices=["server", "semantic"],
        default="semantic",
        help="Turn detection mode (default: semantic).",
    )
    p.add_argument(
        "--eagerness",
        choices=["low", "medium", "high", "auto"],
        default="auto",
        help="Semantic VAD eagerness (default: auto). Ignored for server_vad.",
    )
    p.add_argument(
        "--no-sleep",
        action="store_true",
        help="Stream audio as fast as possible (not realtime-paced).",
    )
    p.add_argument(
        "--api-version",
        default="2025-04-01-preview",
        help="Azure Realtime API version for /openai/realtime (default: 2025-04-01-preview).",
    )
    p.add_argument(
        "--deployment",
        default="",
        help="Azure deployment name (required only with --provider azure).",
    )
    p.add_argument(
        "--model",
        default="gpt-4o-transcribe",
        help='Transcription model name inside the session (default: "gpt-4o-transcribe").',
    )
    p.add_argument(
        "--language",
        default="en",
        help='Language code like "en" or empty for auto-detect (default: "en").',
    )
    p.add_argument(
        "--debug-events",
        action="store_true",
        help="Log unhandled realtime event types for troubleshooting.",
    )
    p.add_argument(
        "--post-audio-wait",
        type=float,
        default=8.0,
        help="Seconds to wait for final transcript events after sending audio (default: 8.0).",
    )

    args = p.parse_args()

    try:
        asyncio.run(
            run(
                wav_path=args.wav_path,
                provider=args.provider,
                vad_mode=args.vad,
                eagerness=args.eagerness,
                no_sleep=args.no_sleep,
                api_version=args.api_version,
                deployment=args.deployment,
                model=args.model,
                language=args.language,
                debug_events=args.debug_events,
                post_audio_wait=args.post_audio_wait,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
