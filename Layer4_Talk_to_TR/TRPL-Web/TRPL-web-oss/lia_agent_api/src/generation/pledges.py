# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import asyncio
from typing import Any

from api.config import config
from debate.utils import _a_generate_reply_threaded, LLMError, build_agent
from generation.generated_pledges_matrix import PLEDGES_BY_CONTRIBUTION_SPHERE_AGE
from generation.models import PledgeChoice, PledgeRequest

logger = logging.getLogger(f"lia.{__name__}")

_INPUT_COUNT = 6
_GENERATED_COUNT = 24
_TOTAL_COUNT = _INPUT_COUNT + _GENERATED_COUNT


ALL_POSTER_VALUES = [
    "Conservation",
    "Innovation",
    "Integrity",
    "Justice",
    "Propserity",
    "Reform",
    "Security",
    "Service",
]
example_values = {"values": ["Integrity", "Service", "Conservation"]}


def _build_alternatives_system_message(options_count: int) -> str:
    return f"""You are generating alternatives for pledge defaults.

You must output JSON with exactly this key:
- alternatives: array of exactly {options_count} objects, each with keys "less" and "more"

Input notes:
- pledges contains the final 30 default pledges in order.
- context fields may include sphere, need, and contribution.
- personalization and profile may be present; treat both as user profile context.

Requirements for alternatives:
- return exactly one less/more pair for each default pledge in order
- "less" means lower effort/investment while keeping the same intent
- "more" means higher effort/investment while keeping the same intent
- each pair should align semantically with its corresponding default pledge
- concrete and actionable
- concise and clear
- avoid social media or online posting suggestions
- broadly accessible and realistic

Return JSON only. No markdown. No extra keys.
"""


def _sanitize_alternatives(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        less = str(item.get("less", "")).strip()
        more = str(item.get("more", "")).strip()
        if not less and not more:
            continue
        cleaned.append({"less": less, "more": more})
    return cleaned


def _fallback_default(index: int, request: PledgeRequest) -> str:
    contribution = request.contribution or "effort"
    sphere = request.sphere or "community"
    return f"Take one concrete {contribution} step for my {sphere} ({index + 1})."


def _fallback_less(default_text: str) -> str:
    return f"Take a smaller step: {default_text}"


def _fallback_more(default_text: str) -> str:
    return f"Take a bigger step: {default_text}"


def _build_context_payload(request: PledgeRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "context": {
            "sphere": request.sphere,
            "need": request.need,
            "contribution": request.contribution,
        },
    }
    if request.personalization is not None:
        payload["personalization"] = request.personalization
    if request.profile is not None:
        payload["profile"] = request.profile
    return payload


def _extract_age_bucket(request: PledgeRequest) -> str:
    """Infer age bucket for matrix lookup. Defaults to adult."""

    def from_number(age_number: int) -> str:
        return "kid" if age_number < 18 else "adult"

    sources: list[Any] = [request.profile, request.personalization]
    for src in sources:
        if src is None:
            continue

        if isinstance(src, dict):
            for key in ("age_bucket", "ageBucket", "age_group", "ageGroup", "age"):
                value = src.get(key)
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    return from_number(int(value))
                text = str(value).strip().lower()
                if text in {"kid", "child", "children", "youth", "teen"}:
                    return "kid"
                if text in {"adult", "grownup"}:
                    return "adult"
                digits = "".join(ch for ch in text if ch.isdigit())
                if digits:
                    return from_number(int(digits))

        if isinstance(src, str):
            text = src.strip().lower()
            if any(
                word in text for word in ("kid", "child", "children", "youth", "teen")
            ):
                return "kid"
            if "adult" in text:
                return "adult"
            if "age" in text:
                digits = "".join(ch for ch in text if ch.isdigit())
                if digits:
                    return from_number(int(digits))

    return "adult"


def _get_matrix_defaults(request: PledgeRequest) -> list[str]:
    """Load defaults from precomputed matrix for contribution/sphere/age."""
    contribution = request.contribution
    sphere = request.sphere
    if not contribution or not sphere:
        return []

    age_bucket = _extract_age_bucket(request)
    entries = (
        PLEDGES_BY_CONTRIBUTION_SPHERE_AGE.get(contribution, {})
        .get(sphere, {})
        .get(age_bucket, [])
    )
    defaults: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            text = entry.strip()
        elif isinstance(entry, dict):
            text = str(entry.get("pledge", "")).strip()
        else:
            text = ""
        if text:
            defaults.append(text)
    if defaults:
        logger.info(
            "Loaded %s matrix defaults for contribution=%s sphere=%s age=%s",
            len(defaults),
            contribution,
            sphere,
            age_bucket,
        )
    return defaults


def _build_alternatives_payload(
    request: PledgeRequest, pledges: list[str]
) -> dict[str, Any]:
    payload = _build_context_payload(request)
    payload["pledges"] = pledges
    return payload


async def _generate_alternatives_chunk(
    request: PledgeRequest,
    llm_cfg: dict[str, Any],
    pledges_chunk: list[str],
    chunk_index: int,
) -> list[dict[str, str]]:
    alternatives_agent = build_agent(
        name=f"PledgeAlternativesGenerator{chunk_index}",
        sys_msg=_build_alternatives_system_message(len(pledges_chunk)),
        llm_cfg=llm_cfg,
    )
    alternatives_payload = _build_alternatives_payload(request, pledges_chunk)
    logger.info(
        "Generating pledge alternatives (pass 2, chunk=%s) with payload: %s",
        chunk_index,
        alternatives_payload,
    )

    alternatives: list[dict[str, str]] = []
    try:
        reply = await _a_generate_reply_threaded(
            alternatives_agent,
            messages=[{"role": "user", "content": json.dumps(alternatives_payload)}],
        )
        logger.info(
            "Received pledge alternatives reply (pass 2, chunk=%s): %s",
            chunk_index,
            reply,
        )
        if reply:
            reply_json = reply if isinstance(reply, dict) else json.loads(str(reply))
            if isinstance(reply_json, dict):
                alternatives = _sanitize_alternatives(reply_json.get("alternatives"))
    except LLMError as exc:
        logger.error(
            "LLM error generating pledge alternatives (chunk=%s): %s",
            chunk_index,
            exc.message,
            exc_info=True,
        )
    except Exception as exc:
        logger.error(
            "Error generating pledge alternatives (chunk=%s): %s",
            chunk_index,
            exc,
            exc_info=True,
        )
    return alternatives


async def generate_pledges(request: PledgeRequest) -> list[PledgeChoice]:
    model_tier = request.model_tier or config.pledge_model_tier
    llm_cfg = config.pledge_llm_config_for(model_tier).model_dump()

    generated_defaults = _get_matrix_defaults(request)

    defaults = list(request.input_pledges)
    for pledge in generated_defaults:
        if len(defaults) >= _TOTAL_COUNT:
            break
        if pledge in defaults:
            continue
        defaults.append(pledge)
    while len(defaults) < _TOTAL_COUNT:
        defaults.append(_fallback_default(len(defaults), request))

    return [PledgeChoice(less="foo", default=pledge, more="foo") for pledge in defaults]

    chunk_size = 6
    chunks = [
        defaults[start : start + chunk_size]
        for start in range(0, _TOTAL_COUNT, chunk_size)
    ]
    chunk_tasks = [
        _generate_alternatives_chunk(
            request=request,
            llm_cfg=llm_cfg,
            pledges_chunk=chunk,
            chunk_index=chunk_index,
        )
        for chunk_index, chunk in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

    normalized_alternatives: list[dict[str, str]] = []
    for chunk_index, chunk_result in enumerate(chunk_results):
        if isinstance(chunk_result, Exception):
            logger.error(
                "Unhandled error generating alternatives chunk=%s: %s",
                chunk_index,
                chunk_result,
                exc_info=True,
            )
            chunk_alternatives: list[dict[str, str]] = []
        else:
            chunk_alternatives = chunk_result

        chunk_target = len(chunks[chunk_index])
        chunk_alternatives = chunk_alternatives[:chunk_target]
        while len(chunk_alternatives) < chunk_target:
            chunk_alternatives.append({})
        normalized_alternatives.extend(chunk_alternatives)

    pledge_triplets: list[PledgeChoice] = []
    for index, default_text in enumerate(defaults):
        alternative = normalized_alternatives[index]
        less = alternative.get("less", "") or _fallback_less(default_text)
        more = alternative.get("more", "") or _fallback_more(default_text)
        pledge_triplets.append(
            PledgeChoice(
                less=less,
                default=default_text,
                more=more,
            )
        )

    return pledge_triplets
