# -*- coding: utf-8 -*-
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WordMeaningPair(BaseModel):
    word: str = Field(min_length=1)
    meaning: str = Field(min_length=1)


class SloganRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    words_and_meanings_pairs: list[WordMeaningPair] = Field(
        alias="wordsAndMeaningsPairs",
    )
    max_line_length: int | None = Field(
        default=None,
        alias="maxLineLength",
        ge=15,
    )
    max_lines: int | None = Field(
        default=None,
        alias="maxLines",
        ge=1,
    )
    model_tier: Literal["nano", "mini", "full"] | None = Field(
        default=None,
        alias="modelTier",
    )


class SloganResponse(BaseModel):
    slogan: str


class PledgeChoice(BaseModel):
    less: str = Field(min_length=1)
    default: str = Field(min_length=1)
    more: str = Field(min_length=1)


class PledgeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    input_pledges: list[str] = Field(alias="inputPledges", min_length=6, max_length=6)
    sphere: Literal["self", "family", "community", "nation", "world"] | None = None
    need: Literal["empathy", "justice", "service", "belonging", "accountability"] | None = None
    contribution: Literal["time", "money", "words", "creativity"] | None = None
    personalization: Any | None = None
    profile: Any | None = None
    model_tier: Literal["nano", "mini", "full"] | None = Field(
        default=None,
        alias="modelTier",
    )

    @field_validator("input_pledges")
    @classmethod
    def _validate_input_pledges(cls, pledges: list[str]) -> list[str]:
        cleaned = [p.strip() for p in pledges if isinstance(p, str) and p.strip()]
        if len(cleaned) != 6:
            raise ValueError("inputPledges must contain exactly 6 non-empty strings")
        return cleaned

class PledgeResponse(BaseModel):
    pledges: list[PledgeChoice] = Field(min_length=30, max_length=30)
