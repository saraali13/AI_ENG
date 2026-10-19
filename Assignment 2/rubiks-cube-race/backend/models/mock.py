"""Mock competitors: simulated players so the whole app works without any API key.

IMPORTANT: mock results are SIMULATIONS. They say nothing about how the real models perform, and every
result is labelled ``mode: "mock"`` in the API, the saved JSON and the website.

Each mock has a "personality" (speed, how often it picks a wrong move, how often it answers with garbage,
how often its "API" fails) so that every code path of the benchmark - invalid responses, API errors, move
limit, failures - is exercised. Mocks may read ``ctx.hidden["scramble"]`` (real adapters never do).
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from ..cube_engine import MOVES, invert_sequence, simplify_sequence
from ..fast_cube import SOLVED_STATE, apply_move, to_state
from ..optimal import find_optimal
from .base import AdapterError, BaseAdapter, ModelSpec, MoveDecision, StepContext
from .parsing import extract_move_candidate

_GARBAGE_REPLIES = ("U3", "R prime", "turn the top layer clockwise", "M", "x", "Rw", "I think the cube is hard")


class MockAdapter(BaseAdapter):
    """Base class; subclasses set the personality class attributes."""

    mode = "mock"
    strategy = "plan"  # "plan" = undo what was done; "optimal" = exact shortest path; "greedy" = 1-move lookahead
    delay_range: tuple[float, float] = (0.05, 0.15)  # seconds of simulated API latency (before delay_scale)
    wrong_move_rate = 0.0
    garbage_reply_rate = 0.0
    api_error_rate = 0.0
    personality = ""

    def __init__(self, spec: ModelSpec, *, seed: int | str = 0, delay_scale: float = 1.0):
        self.spec = spec
        self.delay_scale = delay_scale
        self.rng = random.Random(f"{seed}:{spec.key}")

    # -- strategies ----------------------------------------------------------------------------
    def _plan_move(self, ctx: StepContext) -> Optional[str]:
        scramble = list(ctx.hidden.get("scramble", []))
        plan = simplify_sequence(invert_sequence(scramble + list(ctx.history)))
        return plan[0] if plan else None

    async def _optimal_move(self, ctx: StepContext) -> Optional[str]:
        result = await asyncio.to_thread(find_optimal, ctx.facelets)
        if result.moves:
            return result.moves[0]
        return self._plan_move(ctx)

    def _greedy_move(self, ctx: StepContext) -> str:
        state = to_state(ctx.facelets)
        scored = [(sum(a == b for a, b in zip(apply_move(state, m), SOLVED_STATE)), m) for m in MOVES]
        top = max(score for score, _ in scored)
        return self.rng.choice([m for score, m in scored if score == top])

    async def _intended_move(self, ctx: StepContext) -> Optional[str]:
        if self.strategy == "optimal":
            return await self._optimal_move(ctx)
        if self.strategy == "greedy":
            return self._greedy_move(ctx)
        return self._plan_move(ctx)

    # -- adapter interface ---------------------------------------------------------------------
    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        # Draw all random numbers first so a run is reproducible for a given seed.
        r_error, r_garbage, r_wrong = self.rng.random(), self.rng.random(), self.rng.random()
        delay = self.rng.uniform(*self.delay_range) * self.delay_scale
        garbage = self.rng.choice(_GARBAGE_REPLIES)
        wrong = self.rng.choice(MOVES)

        if delay > 0:
            await asyncio.sleep(delay)
        if r_error < self.api_error_rate:
            raise AdapterError("Simulated transient API error (mock)", kind="server_error")
        if r_garbage < self.garbage_reply_rate:
            return MoveDecision(raw=garbage, move=extract_move_candidate(garbage), meta={"mock": True})

        move = await self._intended_move(ctx)
        if move is None or r_wrong < self.wrong_move_rate:
            move = wrong
        return MoveDecision(raw=move, move=move, meta={"mock": True})

    def describe(self) -> dict:
        info = super().describe()
        info["mock_personality"] = self.personality
        return info
