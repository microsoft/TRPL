#!/usr/bin/env python3
"""Join a local LiveKit room and require agent-published audio."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

from livekit import rtc


async def _verify(url: str, token: str, timeout: float) -> None:
    room = rtc.Room()
    agent_connected = asyncio.Event()
    audio_frames: asyncio.Queue[float] = asyncio.Queue()
    audio_tasks: set[asyncio.Task] = set()

    def is_non_silent(frame_event) -> bool:  # noqa: ANN001
        data = memoryview(frame_event.frame.data)
        samples = data.cast("h") if data.format in {"B", "b", "c"} else data
        if not samples:
            return False
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        return rms >= 64

    async def consume_audio(track: rtc.AudioTrack) -> None:
        stream = rtc.AudioStream(track)
        try:
            async for frame_event in stream:
                if is_non_silent(frame_event):
                    audio_frames.put_nowait(time.monotonic())
        finally:
            await stream.aclose()

    @room.on("participant_connected")
    def on_participant_connected(_participant):  # noqa: ANN001
        agent_connected.set()

    @room.on("track_subscribed")
    def on_track_subscribed(track, _publication, _participant):  # noqa: ANN001
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            task = asyncio.create_task(consume_audio(track))
            audio_tasks.add(task)
            task.add_done_callback(audio_tasks.discard)

    await room.connect(url, token)
    try:
        if room.remote_participants:
            agent_connected.set()
        await asyncio.wait_for(agent_connected.wait(), timeout=timeout)

        # Drain the initial greeting through one second of audio silence so the
        # later assertion cannot pass on greeting frames.
        await asyncio.wait_for(audio_frames.get(), timeout=timeout)
        greeting_deadline = time.monotonic() + timeout
        while True:
            remaining = greeting_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Agent greeting did not reach audio silence")
            try:
                await asyncio.wait_for(
                    audio_frames.get(),
                    timeout=min(1.0, remaining),
                )
            except TimeoutError:
                break

        query_sent_at = time.monotonic()
        await room.local_participant.publish_data(
            json.dumps(
                {"text": "What happened at the fictional Cedar Station?"}
            ).encode(),
            reliable=True,
            topic="lk-chat-topic",
        )
        while True:
            frame_at = await asyncio.wait_for(audio_frames.get(), timeout=timeout)
            if frame_at >= query_sent_at:
                break
    finally:
        await room.disconnect()
        for task in audio_tasks:
            task.cancel()
        if audio_tasks:
            await asyncio.gather(*audio_tasks, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    asyncio.run(_verify(args.url, args.token, args.timeout))
    print("LiveKit participant received agent audio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
