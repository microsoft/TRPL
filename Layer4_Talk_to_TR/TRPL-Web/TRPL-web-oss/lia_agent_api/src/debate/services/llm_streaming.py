# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Awaitable, Callable

from api.config import config
from debate.services.safety import StreamCancelled
from debate.services.shared import get_openai_client
from debate.utils import LLMError

logger = logging.getLogger(f"lia.{__name__}")


def _prepare_messages(messages: list[dict], system_message: str | None) -> list[dict]:
    if any(message.get("role") == "system" for message in messages):
        return messages
    if system_message:
        return [{"role": "system", "content": system_message}, *messages]
    return messages


class JsonFieldStreamExtractor:
    def __init__(self, field_name: str):
        self._field_name = field_name
        self._pattern = f"\"{field_name}\""
        self._buffer = ""
        self._value_started = False
        self._capturing = False
        self._done = False
        self._escape_mode: str | None = None
        self._unicode_buffer = ""

    def feed(self, text: str) -> str:
        if self._done or not text:
            return ""

        output: list[str] = []
        for ch in text:
            if not self._value_started:
                self._buffer += ch
                if len(self._buffer) > 400:
                    self._buffer = self._buffer[-400:]

                idx = self._buffer.find(self._pattern)
                if idx != -1:
                    remainder = self._buffer[idx + len(self._pattern) :]
                    colon_idx = remainder.find(":")
                    if colon_idx != -1:
                        after_colon = remainder[colon_idx + 1 :]
                        quote_idx = after_colon.find("\"")
                        if quote_idx != -1:
                            self._value_started = True
                            self._capturing = True
                            value_start = (
                                idx + len(self._pattern) + colon_idx + 1 + quote_idx + 1
                            )
                            existing_value = self._buffer[value_start:]
                            self._buffer = ""
                            output.extend(self._consume_value(existing_value))
            else:
                output.extend(self._consume_value(ch))

            if self._done:
                break

        return "".join(output)

    def _consume_value(self, text: str) -> list[str]:
        if not text or self._done:
            return []

        output: list[str] = []
        for ch in text:
            if self._escape_mode == "unicode":
                self._unicode_buffer += ch
                if len(self._unicode_buffer) == 4:
                    try:
                        output.append(chr(int(self._unicode_buffer, 16)))
                    except Exception:
                        output.append("")
                    self._unicode_buffer = ""
                    self._escape_mode = None
                continue

            if self._escape_mode == "start":
                if ch == "u":
                    self._escape_mode = "unicode"
                    self._unicode_buffer = ""
                    continue
                output.append(self._map_escape(ch))
                self._escape_mode = None
                continue

            if ch == "\\":
                self._escape_mode = "start"
                continue

            if ch == "\"":
                self._capturing = False
                self._done = True
                break

            output.append(ch)

        return output

    def _map_escape(self, ch: str) -> str:
        mapping = {
            "\"": "\"",
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        return mapping.get(ch, ch)


def _parse_json_payload(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            logger.debug("Failed to parse JSON payload from LLM output")
    return {}


async def _stream_chat_completion(
    user_messages: list[dict],
    llm_config: dict,
    system_message: str | None = None,
    cancel_event: threading.Event | None = None,
) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    logger.debug(f"LLM payload (streaming): {user_messages}")

    def run_stream() -> None:
        stream = None
        try:
            client = get_openai_client()
            instructions = system_message
            input_messages = user_messages
            payload: dict[str, Any] = {
                "model": llm_config.get("model"),
                "input": input_messages,
                "stream": True,
                "timeout": config.llm_timeout,
            }
            if instructions:
                payload["instructions"] = instructions
            reasoning_effort = llm_config.get("reasoning_effort")
            if reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}
            stream = client.responses.create(**payload)
            saw_delta = False
            for event in stream:
                # Safety / consumer asked us to stop. Closing the
                # OpenAI stream stops further token generation server-side.
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("LLM stream cancelled by consumer")
                    break
                event_type = getattr(event, "type", None)
                if event_type is None and isinstance(event, dict):
                    event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    token = getattr(event, "delta", None)
                    if token is None and isinstance(event, dict):
                        token = event.get("delta")
                    if token:
                        saw_delta = True
                        loop.call_soon_threadsafe(queue.put_nowait, token)
                    continue
                if event_type == "response.output_text.done" and not saw_delta:
                    token = getattr(event, "text", None)
                    if token is None and isinstance(event, dict):
                        token = event.get("text")
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, token)
                    continue
                if event_type == "error":
                    error = getattr(event, "error", None)
                    if error is None and isinstance(event, dict):
                        error = event.get("error")
                    raise RuntimeError(error or "Unknown streaming error")
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=run_stream, daemon=True).start()
    return queue


async def stream_json_field(
    user_messages: list[dict],
    llm_config: dict,
    field_name: str,
    on_field_chunk: Callable[[str], Awaitable[None]] | None,
    system_message: str | None = None,
) -> dict[str, Any]:
    extractor = JsonFieldStreamExtractor(field_name)
    full_text: list[str] = []
    if on_field_chunk is None:
        async def on_field_chunk(_chunk: str) -> None:
            return None

    cancel_event = threading.Event()
    queue = await _stream_chat_completion(
        user_messages, llm_config, system_message, cancel_event=cancel_event
    )
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            token = item
            full_text.append(token)
            extracted = extractor.feed(token)
            if extracted:
                try:
                    await on_field_chunk(extracted)
                except StreamCancelled:
                    logger.info("stream_json_field cancelled by safety")
                    cancel_event.set()
                    break
    except StreamCancelled:
        cancel_event.set()
    except Exception as exc:
        raise LLMError.from_exception(exc) from exc

    result = _parse_json_payload("".join(full_text))
    logger.debug("Parsed LLM response (streaming): %s", result)
    return result


async def stream_text_response(
    user_messages: list[dict],
    llm_config: dict,
    on_text_chunk: Callable[[str], Awaitable[None]] | None,
    system_message: str | None = None,
) -> str:
    full_text: list[str] = []
    if on_text_chunk is None:
        async def on_text_chunk(_chunk: str) -> None:
            return None
    cancel_event = threading.Event()
    queue = await _stream_chat_completion(
        user_messages, llm_config, system_message, cancel_event=cancel_event
    )

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            token = item
            full_text.append(token)
            try:
                await on_text_chunk(token)
            except StreamCancelled:
                logger.info("stream_text_response cancelled by safety")
                cancel_event.set()
                break
    except StreamCancelled:
        cancel_event.set()
    except Exception as exc:
        raise LLMError.from_exception(exc) from exc

    result = "".join(full_text).strip()
    logger.debug("LLM response (streaming): %s", result)
    return result
