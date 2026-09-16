# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_auth
from api.config import config
from generation.models import PledgeRequest, PledgeResponse, SloganRequest, SloganResponse
from generation.pledges import generate_pledges
from generation.slogan import generate_slogan

logger = logging.getLogger(f"lia.{__name__}")

router = APIRouter(prefix="/api/generate", tags=["generate"])


@router.post("/slogan", response_model=SloganResponse)
async def generate_slogan_endpoint(
    request: SloganRequest,
    user: dict = Depends(require_auth()),
):
    try:
        max_line_length = request.max_line_length or config.slogan_max_line_length
        max_lines = request.max_lines or config.slogan_max_lines
        model_tier = request.model_tier or config.slogan_model_tier
        lines = await generate_slogan(
            request.words_and_meanings_pairs,
            max_line_length=max_line_length,
            max_lines=max_lines,
            model_tier=model_tier,
        )
    except Exception as exc:
        logger.error("Failed to generate slogan: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate slogan") from exc
    return SloganResponse(slogan="\n".join(lines))


@router.post("/pledges", response_model=PledgeResponse)
async def generate_pledges_endpoint(
    request: PledgeRequest,
    user: dict = Depends(require_auth()),
):
    logger.info(
        "Generating pledge triplets for input_count=%s contribution=%s need=%s sphere=%s",
        len(request.input_pledges),
        request.contribution or "none",
        request.need or "none",
        request.sphere or "none",
    )
    try:
        pledges = await generate_pledges(request)
    except Exception as exc:
        logger.error("Failed to generate pledges: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate pledges") from exc
    return PledgeResponse(pledges=pledges)
