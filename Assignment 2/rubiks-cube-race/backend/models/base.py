"""Shared interface that every competitor adapter implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class AdapterError(Exception):
    """A problem talking to a model (network, auth, rate limit, malformed API reply, ...).

    ``fatal`` errors (bad API key, unknown model, bad request) will not fix themselves, so the
    benchmark stops that competitor immediately. Non-fatal errors are retried up to the
    configured number of consecutive failures.
    """

    def __init__(self, message: str, *, fatal: bool = False, kind: str = "api_error",
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.message = message
        self.fatal = fatal
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True)
class ModelSpec:
    """What we verified about one competitor (see docs/MODEL_VERIFICATION.md)."""

    key: str
    display_name: str
    openrouter_id: str
    provider: str
    protocol: str  # "decisions" (POST /api/alpha/decisions) or "chat" (POST /api/v1/chat/completions)
    summary: str
    sources: tuple[str, ...]
    notes: str = ""
    verified_on: str = "2026-10-16"

    def to_public_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.display_name,
            "openrouter_id": self.openrouter_id,
            "provider": self.provider,
            "protocol": self.protocol,
            "summary": self.summary,
            "sources": list(self.sources),
            "notes": self.notes,
            "verified_on": self.verified_on,
        }


@dataclass
class StepContext:
    """Everything an adapter may use to choose the next move.

    ``hidden`` is information only the MOCK adapters may read (the true scramble, so they can simulate a
    solver). Real adapters must never use it: real models only see the cube state and their own history.
    """

    step: int
    facelets: str
    faces: dict[str, list[str]]
    net: str
    history: list[str]
    move_limit: int
    last_error: Optional[str] = None
    hidden: dict = field(default_factory=dict)

    @property
    def moves_remaining(self) -> int:
        return max(0, self.move_limit - len(self.history))


@dataclass
class MoveDecision:
    """An adapter's answer: the raw reply, and the move candidate extracted from it (not yet validated)."""

    raw: str
    move: Optional[str]
    meta: dict = field(default_factory=dict)


class BaseAdapter(ABC):
    spec: ModelSpec
    mode: str = "mock"      # "mock", "real" (OpenRouter), "ollama" (local) or "replay" (cache only)
    provider: str = "mock"  # "openrouter", "ollama" or "mock"; only "openrouter" uses the request budget
    api_calls_started: int = 0  # live requests that passed the budget check (counts even if they later fail)

    def _mark_reserved(self) -> None:
        self.api_calls_started += 1

    @property
    def ledger_key(self) -> str:
        return self.spec.key

    @abstractmethod
    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        """Return the next move. Raise ``AdapterError`` for transport/API problems."""

    async def aclose(self) -> None:  # pragma: no cover - default does nothing
        return None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def display_name(self) -> str:
        return self.spec.display_name

    def describe(self) -> dict:
        info = self.spec.to_public_dict()
        info["mode"] = self.mode
        return info
