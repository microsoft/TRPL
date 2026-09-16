# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read/write prompt overrides used by agent_registry.

Internal PM tool. The override file is kept under the server-user's home
dir and is never baked into source. Restart a session for changes to
take effect — existing sessions keep the prompt they were built with.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from debate.services import prompt_overrides

logger = logging.getLogger(f"lia.{__name__}")
router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptEntry(BaseModel):
    key: str
    is_override: bool
    text: str
    default: str


class PromptUpdate(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)


@router.get("", response_model=list[PromptEntry])
async def list_prompts(user: dict = Depends(require_auth())):
    return prompt_overrides.list_all()


@router.get("/{key}", response_model=PromptEntry)
async def get_prompt(key: str, user: dict = Depends(require_auth())):
    if key not in prompt_overrides.known_slots():
        raise HTTPException(status_code=404, detail=f"Unknown prompt slot: {key}")
    text, is_override = prompt_overrides.get_effective(key)
    return PromptEntry(
        key=key,
        is_override=is_override,
        text=text,
        default=prompt_overrides.get_default(key),
    )


@router.put("/{key}", response_model=PromptEntry)
async def put_prompt(
    key: str,
    body: PromptUpdate,
    user: dict = Depends(require_auth()),
):
    try:
        prompt_overrides.set_override(key, body.text)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown prompt slot: {key}")
    return PromptEntry(
        key=key,
        is_override=True,
        text=body.text,
        default=prompt_overrides.get_default(key),
    )


@router.delete("/{key}", response_model=PromptEntry)
async def delete_prompt(key: str, user: dict = Depends(require_auth())):
    if key not in prompt_overrides.known_slots():
        raise HTTPException(status_code=404, detail=f"Unknown prompt slot: {key}")
    prompt_overrides.delete_override(key)
    default_text = prompt_overrides.get_default(key)
    return PromptEntry(
        key=key,
        is_override=False,
        text=default_text,
        default=default_text,
    )
