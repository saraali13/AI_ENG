"""Request validation models for the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["warmup", "primary", "extension", "final"]
Mode = Literal["mock", "ollama", "replay", "real"]


class ScrambleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category = "primary"
    length: Optional[int] = Field(default=None, ge=1, le=40, description="Optional exact length inside the category range")
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1, description="Seed for a reproducible scramble")


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category = "primary"
    moves: Optional[list[str]] = Field(
        default=None, max_length=40,
        description="Use this exact scramble (e.g. the one from /api/scramble). If omitted, one is generated.",
    )
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)
    mode: Mode = "mock"
    confirm_live: bool = Field(default=False, description="Must be true for mode=real. Live runs spend the restricted OpenRouter budget.")
    move_limit: Optional[int] = Field(default=None, ge=1, le=100, description="Defaults to the configured limit (20); may not exceed it")
    competitors: Optional[list[str]] = Field(default=None, description="Subset of jev, luna, clef, opus; default all four")
    wait: bool = Field(default=False, description="true = block until the run finishes and return it; false = return a run_id to poll")

    @field_validator("moves")
    @classmethod
    def _moves_not_empty(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is not None and not value:
            raise ValueError("moves must contain at least one move (or be omitted)")
        return value
