"""Competitor 3: Cloudflare Clef - an open-weight Decisions-API model on OpenRouter."""
from __future__ import annotations

from .base import ModelSpec
from .decisions import DecisionsAdapter
from .mock import MockAdapter

SPEC = ModelSpec(
    key="clef",
    display_name="Cloudflare Clef",
    openrouter_id="cloudflare/clef",
    provider="Cloudflare",
    protocol="decisions",
    summary="Cloudflare open-weight decision model (weights on Hugging Face as Cloudflare/clef).",
    sources=("https://openrouter.ai/cloudflare/clef",),
    notes=(
        "Runs on OpenRouter's Decisions API (POST /api/alpha/decisions), not chat completions. "
        "OpenRouter lists Prime Intellect, DeepInfra and Cloudflare (Workers AI) as providers; Workers AI truncates long "
        "text state to roughly the first 2K tokens (our state is far smaller than that)."
    ),
)


class ClefAdapter(DecisionsAdapter):
    """Real Cloudflare Clef via the Decisions API."""

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)


class ClefMockAdapter(MockAdapter):
    """Simulated 'quick but short-sighted' player: greedily maximises correctly placed stickers."""

    strategy = "greedy"
    delay_range = (0.06, 0.18)
    personality = "Quick but short-sighted: picks the move that fixes the most stickers right now, so it often stalls."

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)
