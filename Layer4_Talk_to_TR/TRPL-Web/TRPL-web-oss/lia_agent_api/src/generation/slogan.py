# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import random
import textwrap

from api.config import config
from debate.utils import _a_generate_reply_threaded, LLMError, build_agent
from generation.models import WordMeaningPair

logger = logging.getLogger(f"lia.{__name__}")


_EXAMPLES = [
    "Progress That Works for Land and Labor Alike",
    "Advancing Together, for People and Nature",
    "For Work, Wealth, and the Welfare of All",
    "Free the Future",
]

_SYSTEM_MESSAGE = f"""You generate concise campaign slogans for posters.
Write a single short slogan suitable for a poster, in title case.

Focus the message around the provided word meanings, but you do not need to include them verbatim.
Prefer a natural-sounding slogan over including all the given meanings.
It's fine to use only one or two of the provided meanings if that results in a better slogan.

Constraints:
- you MAY NOT just make a list of words; the slogan must be a coherent phrase or sentence
- it has to make sense
- no quotes
- no trailing punctuation
- no extra explanation

Output only json, and include only the short slogan:

{{"short_slogan": ""}}

Examples of desired tone, structure, and length:
{json.dumps(_EXAMPLES)}
"""


def _fallback_slogan(pairs: list[WordMeaningPair], max_width: int) -> list[str]:
    second_lines = [
        "A Better Tomorrow",
        "Shaping Tomorrow",
        "Looking Ahead",
        "Moving Forward",
        "The Path Forward",
        "Building the Future",
        "For a Stronger Nation",
        "Stronger Together",
        "United for Tomorrow",
        "Now Is the Time",
    ]
    eligible_second_lines = [line for line in second_lines if len(line) <= max_width]
    result = [random.choice(eligible_second_lines)]
    words = [pair.word.strip() for pair in pairs if pair.word.strip()]
    random.shuffle(words)
    line1 = ""
    while words:
        word = words.pop(0)
        candidate = f"{line1}, {word}" if line1 else word
        if len(candidate) + 1 > max_width:  # +1 for colon
            continue  # if this word is too long, try subsequent words
        line1 = candidate
    if line1:
        result.insert(0, f"{line1}:")
    return result


def _split_slogan(text: str, max_width: int, max_lines: int) -> list[str] | None:
    if not text.strip():
        return None
    text = text.strip()
    if len(text) <= max_width:
        return [text]

    words = text.split()
    if len(words) < 2:
        # single word longer than max width
        return None

    # if it fits in 2 lines, try to split near the center and avoid
    # orphans
    if max_lines >= 2:
        center = len(words) // 2
        offsets = [0]
        for i in range(1, len(words)):
            offsets.append(i)
            offsets.append(-i)

        for offset in offsets:
            split_at = center + offset
            if split_at <= 0 or split_at >= len(words):
                continue
            line1 = " ".join(words[:split_at])
            line2 = " ".join(words[split_at:])
            if len(line1) <= max_width and len(line2) <= max_width:
                return [line1, line2]

    lines = textwrap.wrap(text, width=max_width)
    if len(lines) <= max_lines and all(len(line) <= max_width for line in lines):
        return lines
    return None


async def generate_slogan(
    pairs: list[WordMeaningPair],
    max_line_length: int,
    max_lines: int,
    model_tier: str,
) -> list[str]:
    """Generate a slogan based on the provided word-meaning pairs.

    Args:
        pairs (list[WordMeaningPair]): List of word-meaning pairs.
        max_line_length (int): Maximum characters per line.
        max_lines (int): Maximum number of lines.
        model_tier (str): LLM tier to use.
    Returns:
        list[str]: List of lines in the generated slogan.
    """
    if max_line_length < 15:
        raise ValueError("max_line_length must be at least 15")
    agent = build_agent(
        name="SloganGenerator",
        sys_msg=_SYSTEM_MESSAGE,
        llm_cfg=config.slogan_llm_config_for(model_tier).model_dump(),
    )
    payload = {
        "word_meanings": [
            pair.meaning for pair in pairs
        ]
    }
    logger.info("Generating slogan [%s]: %s", model_tier, payload)
    try:
        reply = await _a_generate_reply_threaded(
            agent,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        logger.info("Received slogan reply: %s", reply)
        if reply:
            reply_json = json.loads(str(reply))
            short_lines = _split_slogan(
                reply_json.get("short_slogan", ""),
                max_width=max_line_length,
                max_lines=max_lines,
            )
            if short_lines:
                logger.info("Returning slogan lines: %s", short_lines)
                return short_lines
    except LLMError as exc:
        logger.error("LLM error generating slogan: %s", exc.message, exc_info=True)
    except Exception as exc:
        logger.error("Error generating slogan: %s", exc, exc_info=True)
    result = _fallback_slogan(pairs, max_line_length)
    logger.info("Returning slogan lines: %s", result)
    return result
