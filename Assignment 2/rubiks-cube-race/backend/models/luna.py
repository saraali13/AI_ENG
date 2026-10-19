"""Competitor 2: GPT-6 Luna Decisions (OpenAI) - a Decisions-API model on OpenRouter."""
from __future__ import annotations

from .base import ModelSpec
from .decisions import DecisionsAdapter
from .mock import MockAdapter

SPEC = ModelSpec(
    key="luna",
    display_name="GPT-6 Luna Decisions",
    openrouter_id="openai/gpt-6-luna-decisions",
    provider="OpenAI",
    protocol="decisions",
    summary="OpenAI decision model. Answers typed questions (yes/no, choice, score) with probabilities.",
    sources=("https://openrouter.ai/openai/gpt-6-luna-decisions",),
    notes=(
        "Runs on OpenRouter's Decisions API (POST /api/alpha/decisions), not chat completions or the Responses API. "
        "OpenRouter also lists a dated snapshot slug (openai/gpt-6-luna-decisions-20261006); the undated slug is used."
    ),
)


class LunaAdapter(DecisionsAdapter):
    """Real GPT-6 Luna Decisions via the Decisions API."""

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)


class LunaMockAdapter(MockAdapter):
    """Simulated 'medium speed, occasionally garbles its answer' player."""

    strategy = "plan"
    delay_range = (0.15, 0.35)
    wrong_move_rate = 0.04
    garbage_reply_rate = 0.08
    personality = "Medium speed; sometimes answers with an illegal move or free text (invalid response)."

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)
