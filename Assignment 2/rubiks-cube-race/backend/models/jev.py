"""Competitor 1: Jev 1.13 (TypeSafe) - a Decisions-API model on OpenRouter."""
from __future__ import annotations

from .base import ModelSpec
from .decisions import DecisionsAdapter
from .mock import MockAdapter

SPEC = ModelSpec(
    key="jev",
    display_name="Jev 1.13",
    openrouter_id="typesafe/jev-1.13",
    provider="TypeSafe",
    protocol="decisions",
    summary="TypeSafe decision model. Returns a typed choice with probabilities instead of free-form text.",
    sources=(
        "https://openrouter.ai/typesafe/jev-1.13",
        "https://openrouter.ai/docs/guides/community/jev-tutorial",
    ),
    notes=(
        "Runs on OpenRouter's Decisions API (POST /api/alpha/decisions), not chat completions. "
        "OpenRouter's page also lists Jev 1.14 as the latest version; the assignment specifies 1.13, so 1.13 is used."
    ),
)


class JevAdapter(DecisionsAdapter):
    """Real Jev 1.13 via the Decisions API."""

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)


class JevMockAdapter(MockAdapter):
    """Simulated 'fast but a little sloppy' player."""

    strategy = "plan"
    delay_range = (0.04, 0.12)
    wrong_move_rate = 0.10
    personality = "Fast; follows a solution but picks a wrong legal move about 10% of the time."

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)
