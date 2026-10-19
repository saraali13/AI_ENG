"""The ONE gate every real (OpenRouter / Ollama) request must pass through.

Order of operations for every request (this is the lifecycle the professor asked for):

1. Build the effective request payload and its cache key (model id + complete payload).
2. CACHE: if a usable cached response exists, reuse it. No network, no budget used. Counted as a cache hit.
3. If the exact same request is already in flight, wait for it instead of sending a duplicate.
4. If network access is disabled (replay mode), stop: a cache miss is an error, never a silent live call.
5. BUDGET: ``ledger.reserve()`` counts the request and writes the counter to disk. If a limit is hit, nothing is sent.
6. NETWORK: the request is sent. From here on it is already counted, whatever happens (error, timeout, cancel).
7. PARSE: the reply is parsed into a move candidate. A reply that is not a usable legal move is NOT cached.
8. The outcome (ok / failed, cost, tokens) is added to the ledger.

``send`` is never called without step 5 having succeeded; it is only called from ``_live``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from ..budget import CLAUDE_KEY, BudgetExceeded, BudgetLedger, LedgerError, Limits
from ..cache import ResponseCache
from ..config import Settings
from ..cube_engine import is_valid_move
from .base import AdapterError, MoveDecision

log = logging.getLogger(__name__)
_INFLIGHT: dict[str, dict] = {}


@dataclass
class CallInfo:
    source: str                         # "live" | "cache" | "shared"
    live_latency_ms: Optional[float]    # network round trip (live only)
    cache_latency_ms: Optional[float]   # time to read the cache (cache hits only)
    original_latency_ms: Optional[float]  # live latency of the call that produced the cached answer
    cost_usd: Optional[float]           # provider-reported cost (None = not reported); 0.0 for cache hits
    usage: Optional[dict]
    request_number: Optional[int]       # the ledger's running count when this request was reserved
    cache_key: str
    api_calls: int                      # 1 for a live attempt, 0 otherwise


def usable(decision: MoveDecision) -> bool:
    """A reply is usable only if it names one of the 18 legal moves."""
    return decision.move is not None and is_valid_move(decision.move)


def is_claude_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return mid.startswith("anthropic/") or "claude" in mid


def reported_cost(data: dict) -> Optional[float]:
    usage = data.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        return float(cost)
    return None


class RequestGuard:
    def __init__(self, ledger: BudgetLedger, cache: ResponseCache, inflight: Optional[dict] = None):
        self.ledger = ledger
        self.cache = cache
        self._inflight = inflight if inflight is not None else {}

    async def execute(
        self, *, ledger_key: str, provider: str, endpoint: str, model_id: str, payload: dict,
        send: Callable[[], Awaitable[dict]], parse: Callable[[dict], MoveDecision],
        on_reserved: Optional[Callable[[], None]] = None, allow_network: bool = True,
    ) -> tuple[MoveDecision, CallInfo]:
        key = ResponseCache.make_key(provider, endpoint, model_id, payload)
        if provider == "openrouter" and is_claude_model(model_id):
            ledger_key = CLAUDE_KEY  # Claude's 5-request cap follows the MODEL, even if a slot is relabelled

        # 2. cache first
        started = time.perf_counter()
        entry = self.cache.get(key)
        if entry is not None:
            try:
                decision = parse(entry["response"])
            except (AdapterError, KeyError, TypeError):
                decision = None
            if decision is not None and usable(decision):
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                try:
                    self.ledger.record_cache_hit(ledger_key)
                except (LedgerError, TimeoutError, OSError) as exc:  # statistics only; nothing was spent
                    log.warning("could not record cache hit: %s", exc)
                return decision, CallInfo("cache", None, elapsed, entry.get("original_latency_ms"), 0.0, None,
                                          None, key, 0)

        # 3. identical request already running -> wait for it, never send a duplicate
        running = self._inflight.get(key)
        if running is not None:
            try:
                decision, info = await asyncio.shield(running)
            except asyncio.CancelledError:
                if running.cancelled():
                    raise AdapterError("The identical in-flight request was cancelled", kind="timeout") from None
                raise
            return decision, CallInfo("shared", None, None, None, 0.0, None, None, key, 0)

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result = await self._live(key, ledger_key, provider, endpoint, model_id, payload, send, parse,
                                      on_reserved, allow_network)
            future.set_result(result)
            return result
        except asyncio.CancelledError:
            future.cancel()
            raise
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark as retrieved so asyncio does not log "never retrieved"
            raise
        finally:
            self._inflight.pop(key, None)

    async def _live(self, key, ledger_key, provider, endpoint, model_id, payload, send, parse, on_reserved,
                    allow_network):
        # 4. replay mode: cache only
        if not allow_network:
            raise AdapterError(
                "Replay mode: this exact request is not in the response cache, and network calls are disabled.",
                fatal=True, kind="cache_miss")

        # 5. budget: count BEFORE the network attempt, persisted to disk
        try:
            number = self.ledger.reserve(ledger_key, provider=provider, model_id=model_id)
        except BudgetExceeded as exc:
            raise AdapterError(exc.message, fatal=True, kind="budget") from None
        except (LedgerError, TimeoutError, OSError) as exc:
            raise AdapterError(f"Request ledger unavailable, request blocked: {exc}", fatal=True, kind="budget") from None
        if on_reserved:
            on_reserved()

        # 6. network (already counted)
        t0 = time.perf_counter()
        ok, kind, cost, usage = False, None, None, None
        try:
            data = await send()
            latency = round((time.perf_counter() - t0) * 1000, 2)
            cost = reported_cost(data)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            decision = parse(data)           # 7. may raise AdapterError(bad_response / model_mismatch)
            ok = True
            if usable(decision):
                try:
                    self.cache.put(key, {"provider": provider, "endpoint": endpoint, "model": model_id,
                                         "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         "original_latency_ms": latency, "cost_usd": cost, "usage": usage,
                                         "response": data})
                except (OSError, TimeoutError) as exc:
                    log.warning("could not write response cache: %s", exc)
            return decision, CallInfo("live", latency, None, None, cost, usage, number, key, 1)
        except AdapterError as exc:
            kind = exc.kind
            raise
        except asyncio.CancelledError:
            kind = "cancelled"
            raise
        except Exception as exc:
            kind = "internal"
            log.exception("unexpected %s while handling a reply for %s (the request was already counted)",
                          type(exc).__name__, ledger_key)   # traceback only; headers/keys are never part of it
            raise AdapterError(f"Unexpected error while handling the reply: {type(exc).__name__}", fatal=True,
                               kind="internal") from None
        finally:
            try:  # 8. bookkeeping only; the request was already counted
                self.ledger.finish(ledger_key, ok=ok, kind=kind, cost=cost, usage=usage, provider=provider)
            except (LedgerError, TimeoutError, OSError) as exc:
                log.warning("could not record request outcome: %s", exc)


def get_guard(settings: Settings) -> RequestGuard:
    limits = Limits(global_limit=settings.openrouter_global_limit,
                    default_model_limit=settings.model_request_limit,
                    per_model={"opus": settings.claude_request_limit})
    inflight = _INFLIGHT.setdefault(str(settings.state_dir.resolve()), {})
    return RequestGuard(BudgetLedger(settings.state_dir, limits), ResponseCache(settings.state_dir), inflight)


def call_meta(info: CallInfo) -> dict:
    """Per-step accounting facts attached to every decision (None values are dropped)."""
    meta = {"source": info.source, "api_calls": info.api_calls, "cache_hit": info.source == "cache",
            "live_latency_ms": info.live_latency_ms, "cache_latency_ms": info.cache_latency_ms,
            "original_latency_ms": info.original_latency_ms, "cost_usd": info.cost_usd,
            "request_number": info.request_number, "cache_key": info.cache_key[:12]}
    return {k: v for k, v in meta.items() if v is not None}
