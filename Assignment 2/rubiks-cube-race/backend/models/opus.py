"""Competitor 4: Claude Opus 5.5 (Anthropic) - an ordinary chat model on OpenRouter."""
from __future__ import annotations

from .base import ModelSpec
from .chat import ChatAdapter
from .mock import MockAdapter

SPEC = ModelSpec(
    key="opus",
    display_name="Claude Opus 5.5",
    openrouter_id="anthropic/claude-opus-5.5",
    provider="Anthropic",
    protocol="chat",
    summary="Anthropic's Claude Opus 5.5, a general chat model, reached through OpenRouter chat completions.",
    sources=("https://openrouter.ai/compare/anthropic/claude-opus-5/anthropic/claude-opus-5.5",),
    notes=(
        "Uses the standard OpenAI-compatible chat completions endpoint (POST /api/v1/chat/completions). "
        "Not the same model as anthropic/claude-opus-5 (a separate OpenRouter entry)."
    ),
)


class OpusAdapter(ChatAdapter):
    """Real Claude Opus 5.5 via chat completions."""

    def __init__(self, *, spec=None, **kwargs):
        super().__init__(spec or SPEC, **kwargs)


class OpusMockAdapter(MockAdapter):
    """Simulated 'slow, careful, near-perfect' player: plays the exact shortest solution when it is known."""

    strategy = "optimal"
    delay_range = (0.25, 0.6)
    wrong_move_rate = 0.02
    api_error_rate = 0.04
    personality = "Slow and careful; plays the shortest solution, rarely slips, occasionally hits a simulated API error."

    def __init__(self, **kwargs):
        super().__init__(SPEC, **kwargs)
