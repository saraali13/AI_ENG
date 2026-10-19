"""The four competitors and a factory that builds their adapters.

Order matches the assignment: Jev 1.13, GPT-6 Luna Decisions, Cloudflare Clef, Claude Opus 5.5.

Modes
-----
* ``mock``    simulated players, no network, no key.
* ``ollama``  local Ollama models stand in for the four slots. Never contacts OpenRouter.
* ``replay``  the REAL adapters in cache-only mode: answers come from ``state/response_cache.json``; a cache miss is
              an error. Makes zero network requests and needs no API key. Use it to demo a finished experiment.
* ``real``    the REAL adapters against OpenRouter, protected by the persistent budget (see ``guard.py``).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

import httpx

from ..config import Settings
from .base import AdapterError, BaseAdapter, ModelSpec, MoveDecision, StepContext
from .clef import SPEC as CLEF_SPEC, ClefAdapter, ClefMockAdapter
from .guard import RequestGuard, get_guard
from .jev import SPEC as JEV_SPEC, JevAdapter, JevMockAdapter
from .luna import SPEC as LUNA_SPEC, LunaAdapter, LunaMockAdapter
from .ollama import OllamaAdapter
from .opus import SPEC as OPUS_SPEC, OpusAdapter, OpusMockAdapter

SPECS: dict[str, ModelSpec] = {s.key: s for s in (JEV_SPEC, LUNA_SPEC, CLEF_SPEC, OPUS_SPEC)}
COMPETITOR_KEYS: tuple[str, ...] = tuple(SPECS)
MODES = ("mock", "ollama", "replay", "real")

_MOCK_CLASSES = {"jev": JevMockAdapter, "luna": LunaMockAdapter, "clef": ClefMockAdapter, "opus": OpusMockAdapter}
_REAL_CLASSES = {"jev": JevAdapter, "luna": LunaAdapter, "clef": ClefAdapter, "opus": OpusAdapter}

__all__ = ["AdapterError", "BaseAdapter", "ModelSpec", "MoveDecision", "StepContext", "SPECS", "COMPETITOR_KEYS",
           "MODES", "build_adapters", "effective_spec"]


def effective_spec(key: str, settings: Settings) -> ModelSpec:
    """The spec used for OpenRouter requests. ``final`` profile = the assignment model, always.
    ``free_test`` profile swaps ONLY the Claude slot for a free chat model (separate ledger key, clearly labelled)."""
    spec = SPECS[key]
    if key == "opus" and settings.openrouter_profile == "free_test":
        return replace(spec, display_name="Free test model (stand-in for Claude Opus 5.5)",
                       openrouter_id=settings.free_test_chat_model, provider="OpenRouter (free test)",
                       summary="A free chat model used ONLY to test the chat integration. Not Claude.",
                       notes="Profile 'free_test'. Counted under its own ledger key, never against Claude's 5 requests.")
    return spec


def build_adapters(mode: str, settings: Settings, *, seed: int | str = 0, keys: Optional[list[str]] = None,
                   client: Optional[httpx.AsyncClient] = None,
                   guard: Optional[RequestGuard] = None) -> list[BaseAdapter]:
    """Create one adapter per requested competitor (all four by default).

    ``real`` needs ``OPENROUTER_API_KEY`` and raises ``AdapterError`` otherwise. Nothing silently falls back to mock.
    """
    keys = list(keys) if keys else list(COMPETITOR_KEYS)
    unknown = [k for k in keys if k not in SPECS]
    if unknown:
        raise ValueError(f"Unknown competitor key(s): {', '.join(unknown)}")
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; use one of {', '.join(MODES)}")

    if mode == "mock":
        return [_MOCK_CLASSES[k](seed=seed, delay_scale=settings.mock_delay_scale) for k in keys]

    guard = guard or get_guard(settings)

    if mode == "ollama":
        adapters = []
        for k in keys:
            tag = settings.ollama_models[k]
            spec = replace(SPECS[k], openrouter_id=f"ollama:{tag}", provider="Ollama (local)", protocol="ollama-chat",
                           summary=f"Local Ollama model '{tag}' standing in for {SPECS[k].display_name}.",
                           notes="Local stand-in for testing. These are NOT results for the real model.")
            adapters.append(OllamaAdapter(spec, guard=guard, model_tag=tag, base_url=settings.ollama_base_url,
                                          timeout=settings.step_timeout_seconds, client=client))
        return adapters

    allow_network = mode == "real"
    if allow_network and not settings.api_key_configured:
        raise AdapterError("Real mode needs OPENROUTER_API_KEY. Put it in your .env file (see .env.example) and restart.",
                           fatal=True, kind="auth")
    adapters = []
    for k in keys:
        kwargs = dict(guard=guard, api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url,
                      timeout=settings.step_timeout_seconds, client=client, allow_network=allow_network)
        if k == "opus":
            spec = effective_spec(k, settings)
            free = spec is not SPECS[k]
            adapters.append(OpusAdapter(spec=spec, max_tokens=settings.chat_max_tokens,
                                        ledger_key="free_test_chat" if free else None, check_model=not free, **kwargs))
        else:
            adapters.append(_REAL_CLASSES[k](**kwargs))
    return adapters
