"""Benchmark runner: one shared starting cube, an independent copy for each competitor.

Rules for every competitor (documented in the README):

1. It is given a clone of the SAME start state and asked for one move at a time.
2. Every proposed move is validated. Only one of the 18 legal moves is ever applied to the cube.
3. After every applied move the real cube (pycuber) is checked for "solved"; model claims are never trusted.
4. It stops IMMEDIATELY when the cube is solved or when it has applied ``move_limit`` (20) valid moves without
   solving (outcome "unsolved"), after too many bad replies/errors in a row (fewer for live OpenRouter requests,
   because every retry costs budget), on a fatal API error, or when its request budget is exhausted.
5. Everything is recorded: each attempt, live latency, cache latency, validity, errors, API calls, cache hits,
   provider-reported cost, state after each step, final result.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .config import Settings
from .cube_engine import COUNTING_CONVENTION, CubeEngine, InvalidMoveError, validate_move
from .metrics import competitor_metrics
from .models.base import AdapterError, BaseAdapter, StepContext
from .optimal import not_computed, find_optimal

RAW_REPLY_LIMIT = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepRecord:
    step: int                       # attempt number (1-based), counts invalid replies and errors too
    proposed: Optional[str] = None  # the move candidate the competitor returned (may be illegal)
    move: Optional[str] = None      # canonical move that was applied (None if nothing was applied)
    move_number: Optional[int] = None  # position among the applied moves (1-based)
    raw: str = ""                   # the raw reply (truncated)
    responded: bool = False         # the model returned a reply (valid or not)
    valid: bool = False             # the proposed move was legal
    applied: bool = False           # the move was applied to the cube
    error_kind: Optional[str] = None  # "invalid_response" or an API error kind (timeout, auth, ...)
    error: Optional[str] = None
    latency_ms: Optional[float] = None  # wall-clock time of this step's call (live OR cache OR mock)
    source: Optional[str] = None        # "live" | "cache" | "shared" | "mock" | None (no reply)
    api_calls: int = 0                  # live requests attempted for this step; counted even if they failed/timed out
    cache_hit: bool = False
    live_latency_ms: Optional[float] = None   # network round trip (live/mock only)
    cache_latency_ms: Optional[float] = None  # time to read the cache (cache hits only); never mixed with live
    cost_usd: Optional[float] = None    # provider-reported cost of this step (0.0 for cache hits, None = not reported)
    facelets_after: str = ""
    solved_after: bool = False
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompetitorRun:
    key: str
    name: str
    openrouter_id: str
    provider: str
    protocol: str
    mode: str
    personality: str = ""
    status: str = "queued"  # queued | running | solved | failed
    outcome: Optional[str] = None  # solved | unsolved (move limit) | budget_exhausted | cache_miss | aborted
    failure_reason: Optional[str] = None
    steps: list[StepRecord] = field(default_factory=list)
    applied_moves: list[str] = field(default_factory=list)
    start_facelets: str = ""
    current_facelets: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_time_ms: Optional[float] = None
    optimal_length: Optional[int] = None
    _t0: Optional[float] = field(default=None, repr=False)

    def elapsed_ms(self) -> Optional[float]:
        if self.total_time_ms is not None:
            return self.total_time_ms
        if self._t0 is None:
            return None
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def to_dict(self) -> dict:
        data = {
            "key": self.key,
            "name": self.name,
            "openrouter_id": self.openrouter_id,
            "provider": self.provider,
            "protocol": self.protocol,
            "mode": self.mode,
            "personality": self.personality,
            "status": self.status,
            "outcome": self.outcome,
            "failure_reason": self.failure_reason,
            "applied_moves": list(self.applied_moves),
            "start_facelets": self.start_facelets,
            "final_facelets": self.current_facelets,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_time_ms": self.elapsed_ms(),
            "steps": [s.to_dict() for s in self.steps],
        }
        data["metrics"] = competitor_metrics(data, self.optimal_length)
        return data


@dataclass
class BenchmarkRun:
    run_id: str
    created_at: str
    category: str
    scramble: list[str]
    mode: str
    move_limit: int
    seed: int
    start_facelets: str
    competitors: list[CompetitorRun] = field(default_factory=list)
    status: str = "queued"  # queued | running | completed | error | cancelled
    optimal: dict = field(default_factory=dict)
    completed_at: Optional[str] = None
    error: Optional[str] = None
    profile: str = "final"
    start_verification: dict = field(default_factory=dict)  # proof of how far the start state is from solved
    budget_before: Optional[dict] = None   # ledger snapshot when the run started (non-mock modes)
    budget_after: Optional[dict] = None

    def totals(self) -> dict:
        comps = [c.to_dict()["metrics"] for c in self.competitors]
        costs = [m["cost_usd"] for m in comps if m["cost_usd"] is not None]
        return {
            "api_calls": sum(m["api_calls"] for m in comps),
            "cache_hits": sum(m["cache_hits"] for m in comps),
            "cost_usd": round(sum(costs), 10) if costs else None,
            "cost_unreported_calls": sum(m["cost_unreported_calls"] for m in comps),
        }

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "start_verification": self.start_verification,
            "budget_before": self.budget_before,
            "budget_after": self.budget_after,
            "totals": self.totals(),
            "run_id": self.run_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "error": self.error,
            "category": self.category,
            "mode": self.mode,
            "seed": self.seed,
            "move_limit": self.move_limit,
            "scramble_moves": list(self.scramble),
            "scramble": " ".join(self.scramble),
            "scramble_length": len(self.scramble),
            "start_facelets": self.start_facelets,
            "counting_convention": COUNTING_CONVENTION,
            "optimal": self.optimal,
            "competitors": [c.to_dict() for c in self.competitors],
        }


def create_run(*, category: str, scramble: list[str], mode: str, move_limit: int, seed: int,
               adapters: list[BaseAdapter]) -> BenchmarkRun:
    """Build a run object (status ``queued``) with one entry per competitor and the single shared start state."""
    start = CubeEngine.from_scramble(scramble)
    run = BenchmarkRun(
        run_id=uuid.uuid4().hex[:12],
        created_at=now_iso(),
        category=category,
        scramble=list(scramble),
        mode=mode,
        move_limit=move_limit,
        seed=seed,
        start_facelets=start.facelets,
        optimal=not_computed("Not computed yet").to_dict(),
    )
    for adapter in adapters:
        info = adapter.describe()
        run.competitors.append(
            CompetitorRun(
                key=adapter.key,
                name=adapter.display_name,
                openrouter_id=adapter.spec.openrouter_id,
                provider=adapter.spec.provider,
                protocol=adapter.spec.protocol,
                mode=adapter.mode,
                personality=info.get("mock_personality", ""),
                start_facelets=start.facelets,
                current_facelets=start.facelets,
            )
        )
    return run


async def run_competitor(comp: CompetitorRun, adapter: BaseAdapter, start_cube: CubeEngine, *,
                         scramble: list[str], move_limit: int, settings: Settings) -> None:
    """Play one competitor to the end. Never raises for model problems; they are recorded instead."""
    cube = start_cube.clone()  # independent copy of the exact same starting state
    comp.status = "running"
    comp.started_at = now_iso()
    comp._t0 = time.perf_counter()
    consecutive_failures = 0
    attempts = 0
    # Live OpenRouter requests cost budget, so a competitor gets fewer consecutive failures there (at most one retry).
    fail_limit = settings.live_max_consecutive_failures if adapter.provider == "openrouter" else settings.max_consecutive_failures
    max_attempts = move_limit * 2 + fail_limit
    last_problem: Optional[str] = None

    def finish(status: str, reason: Optional[str] = None, outcome: Optional[str] = None) -> None:
        comp.status = status
        comp.outcome = outcome or ("solved" if status == "solved" else "aborted")
        comp.failure_reason = reason
        comp.total_time_ms = round((time.perf_counter() - comp._t0) * 1000, 1)  # type: ignore[operator]
        comp.finished_at = now_iso()
        comp.current_facelets = cube.facelets

    while True:
        if cube.is_solved():
            finish("solved")
            return
        if len(comp.applied_moves) >= move_limit:
            finish("failed", f"Move limit reached: {move_limit} valid moves applied and the cube is not solved",
                   "unsolved")
            return
        if attempts >= max_attempts:
            finish("failed", f"Gave up after {attempts} attempts")
            return

        attempts += 1
        ctx = StepContext(
            step=attempts,
            facelets=cube.facelets,
            faces=cube.faces(),
            net=cube.net(),
            history=list(comp.applied_moves),
            move_limit=move_limit,
            last_error=last_problem,
            hidden={"scramble": list(scramble)},  # only mock adapters read this
        )
        record = StepRecord(step=attempts)

        error: Optional[AdapterError] = None
        decision = None
        calls_before = adapter.api_calls_started
        started = time.perf_counter()
        try:
            decision = await asyncio.wait_for(adapter.choose_move(ctx), timeout=settings.step_timeout_seconds)
        except asyncio.TimeoutError:
            error = AdapterError(f"No reply within {settings.step_timeout_seconds:g} seconds", kind="timeout")
        except AdapterError as exc:
            error = exc
        except Exception as exc:  # a bug in an adapter must not take the whole benchmark down
            error = AdapterError(f"Unexpected adapter error: {type(exc).__name__}: {exc}", fatal=True, kind="internal")
        record.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        # Any live request that passed the budget check counts, even if it then timed out, failed or was cancelled.
        record.api_calls = adapter.api_calls_started - calls_before
        record.facelets_after = cube.facelets

        if error is not None:
            record.error_kind = error.kind
            record.error = error.message
            comp.steps.append(record)
            consecutive_failures += 1
            last_problem = None
            if error.fatal:
                outcome = {"budget": "budget_exhausted", "cache_miss": "cache_miss"}.get(error.kind, "aborted")
                finish("failed", error.message, outcome)
                return
            if consecutive_failures >= fail_limit:
                finish("failed", f"{consecutive_failures} failures in a row; last: {error.message}")
                return
            await asyncio.sleep(min(error.retry_after or settings.retry_backoff_seconds, 30.0))
            continue

        assert decision is not None
        record.responded = True
        record.raw = (decision.raw or "")[:RAW_REPLY_LIMIT]
        record.proposed = None if decision.move is None else str(decision.move)[:40]
        record.meta = dict(decision.meta or {})
        record.source = record.meta.get("source") or ("mock" if adapter.mode == "mock" else None)
        record.cache_hit = bool(record.meta.get("cache_hit"))
        if record.source == "cache":
            record.cache_latency_ms = record.meta.get("cache_latency_ms")
        elif record.source in ("live", "mock"):
            record.live_latency_ms = record.meta.get("live_latency_ms", record.latency_ms)
        record.cost_usd = record.meta.get("cost_usd")

        # Validate the proposed move before it can touch the cube.
        problem: Optional[str] = None
        canonical: Optional[str] = None
        if decision.move is None or not str(decision.move).strip():
            problem = "The reply did not contain a move"
        else:
            try:
                canonical = validate_move(str(decision.move))
            except InvalidMoveError as exc:
                problem = str(exc)

        if problem is not None or canonical is None:
            record.error_kind = "invalid_response"
            record.error = problem
            comp.steps.append(record)
            consecutive_failures += 1
            last_problem = problem
            if consecutive_failures >= fail_limit:
                finish("failed", f"{consecutive_failures} invalid or failed replies in a row; last: {problem}")
                return
            continue

        cube.apply_move(canonical)
        comp.applied_moves.append(canonical)
        record.valid = True
        record.applied = True
        record.move = canonical
        record.move_number = len(comp.applied_moves)
        record.facelets_after = cube.facelets
        record.solved_after = cube.is_solved()  # the real cube decides, not the model
        comp.current_facelets = cube.facelets
        comp.steps.append(record)
        consecutive_failures = 0
        last_problem = None


async def _guarded(comp: CompetitorRun, adapter: BaseAdapter, start: CubeEngine, run: BenchmarkRun,
                   settings: Settings) -> None:
    try:
        await run_competitor(comp, adapter, start, scramble=run.scramble, move_limit=run.move_limit,
                             settings=settings)
    except asyncio.CancelledError:
        _abort(comp, "Run was cancelled")
        raise
    except Exception as exc:  # pragma: no cover - run_competitor handles expected errors itself
        _abort(comp, f"Internal error: {type(exc).__name__}: {exc}")


def _abort(comp: CompetitorRun, reason: str) -> None:
    if comp.status in ("solved", "failed"):
        return
    comp.status = "failed"
    comp.failure_reason = reason
    if comp._t0 is not None:
        comp.total_time_ms = round((time.perf_counter() - comp._t0) * 1000, 1)
    comp.finished_at = now_iso()


async def execute_run(run: BenchmarkRun, adapters: list[BaseAdapter], settings: Settings) -> BenchmarkRun:
    """Run all competitors concurrently on independent copies of the same cube, updating ``run`` as it goes."""
    run.status = "running"
    try:
        if len(run.scramble) <= settings.optimal_max_scramble:
            optimal = await asyncio.to_thread(find_optimal, run.start_facelets)
        else:
            optimal = not_computed(
                f"Scramble has {len(run.scramble)} moves. Exact search only proves optimal lengths up to 8 moves, "
                "so no trustworthy optimum is available (N/A)."
            )
        run.optimal = optimal.to_dict()
        for comp in run.competitors:
            comp.optimal_length = optimal.length if optimal.exact else None

        master = CubeEngine.from_scramble(run.scramble)
        if master.facelets != run.start_facelets:  # pragma: no cover - would indicate an engine bug
            raise RuntimeError("Start state mismatch")

        await asyncio.gather(
            *(_guarded(comp, adapter, master, run, settings) for comp, adapter in zip(run.competitors, adapters))
        )
        run.status = "completed"
    except asyncio.CancelledError:
        run.status = "cancelled"
        raise
    except Exception as exc:
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        for comp in run.competitors:
            _abort(comp, run.error)
    finally:
        run.completed_at = now_iso()
        for adapter in adapters:
            try:
                await adapter.aclose()
            except Exception:  # pragma: no cover
                pass
    return run
