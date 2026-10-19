import asyncio

import pytest

from backend.benchmark import create_run, execute_run, run_competitor, CompetitorRun
from backend.config import Settings, get_settings
from backend.cube_engine import CubeEngine
from backend.models import SPECS, build_adapters
from backend.models.base import AdapterError, BaseAdapter, MoveDecision, StepContext

SETTINGS = Settings(max_consecutive_failures=3, retry_backoff_seconds=0, step_timeout_seconds=5)


class Scripted(BaseAdapter):
    """Test double: replies from a list (then repeats the last one) or via a function."""
    mode = "mock"

    def __init__(self, key="jev", replies=None, fn=None):
        self.spec = SPECS[key]
        self.replies = list(replies or [])
        self.fn = fn
        self.seen_states = []
        self.calls = 0

    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        self.calls += 1
        self.seen_states.append(ctx.facelets)
        if self.fn:
            r = self.fn(ctx)
        else:
            r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return MoveDecision(raw=str(r), move=r)


def play(adapter, scramble, limit=20):
    start = CubeEngine.from_scramble(scramble)
    comp = CompetitorRun(key=adapter.key, name=adapter.display_name, openrouter_id="x", provider="p",
                         protocol="decisions", mode="mock")
    asyncio.run(run_competitor(comp, adapter, start, scramble=scramble, move_limit=limit, settings=SETTINGS))
    return comp, start


def test_solves_when_given_the_inverse():
    comp, _ = play(Scripted(replies=["R'"]), ["R"])
    assert comp.status == "solved" and comp.applied_moves == ["R'"]
    assert comp.steps[-1].solved_after and comp.total_time_ms is not None


def test_model_claims_are_not_trusted_the_real_cube_decides():
    # It "answers" a legal move that does NOT solve; status must not be solved.
    comp, _ = play(Scripted(replies=["U", "U'"]), ["R"], limit=6)
    assert comp.status == "failed"


def test_move_limit_counts_valid_moves_and_stops():
    adapter = Scripted(replies=["U", "U'"])  # never solves a cube scrambled with R
    comp, _ = play(adapter, ["R"], limit=20)
    assert comp.status == "failed" and "Move limit" in comp.failure_reason
    assert comp.outcome == "unsolved"
    assert len(comp.applied_moves) == 20 and adapter.calls == 20


def test_default_move_limit_is_20(monkeypatch):
    assert get_settings().move_limit == 20
    monkeypatch.setenv("MOVE_LIMIT", "30")  # the professor's limit is a ceiling: it cannot be raised
    assert get_settings().move_limit == 20


def test_invalid_moves_are_recorded_not_applied_and_cube_unchanged():
    comp, start = play(Scripted(replies=["Z9", "R prime", "banana"]), ["R"])
    assert comp.status == "failed"
    assert comp.applied_moves == []
    assert comp.current_facelets == start.facelets
    assert [s.error_kind for s in comp.steps] == ["invalid_response"] * 3
    assert all(not s.valid and not s.applied and s.responded for s in comp.steps)
    assert "3 invalid" in comp.failure_reason


def test_recovers_after_an_invalid_reply():
    comp, _ = play(Scripted(replies=["nonsense", "R'"]), ["R"])
    assert comp.status == "solved"
    assert [s.error_kind for s in comp.steps] == ["invalid_response", None]
    assert comp.applied_moves == ["R'"]


def test_non_fatal_api_error_is_retried_and_fatal_one_stops():
    comp, _ = play(Scripted(replies=[AdapterError("boom", kind="server_error"), "R'"]), ["R"])
    assert comp.status == "solved" and comp.steps[0].error_kind == "server_error" and comp.steps[0].latency_ms is not None
    comp, _ = play(Scripted(replies=[AdapterError("bad key", fatal=True, kind="auth")]), ["R"])
    assert comp.status == "failed" and comp.failure_reason == "bad key" and len(comp.steps) == 1


def test_unexpected_exception_in_adapter_is_contained():
    comp, _ = play(Scripted(replies=[RuntimeError("bug")]), ["R"])
    assert comp.status == "failed" and comp.steps[0].error_kind == "internal"


def test_timeout_is_recorded():
    class Slow(Scripted):
        async def choose_move(self, ctx):
            await asyncio.sleep(1)
    start = CubeEngine.from_scramble(["R"])
    comp = CompetitorRun(key="jev", name="J", openrouter_id="x", provider="p", protocol="decisions", mode="mock")
    s = Settings(max_consecutive_failures=1, retry_backoff_seconds=0, step_timeout_seconds=1.0)
    s = Settings(**{**s.__dict__, "step_timeout_seconds": 0.05})
    asyncio.run(run_competitor(comp, Slow(), start, scramble=["R"], move_limit=5, settings=s))
    assert comp.status == "failed" and comp.steps[0].error_kind == "timeout"


def test_equivalent_spelling_is_applied_in_canonical_form():
    comp, _ = play(Scripted(replies=["R’"]), ["R"])
    assert comp.status == "solved" and comp.applied_moves == ["R'"]


def test_all_competitors_get_independent_copies_of_the_same_start_state():
    scramble = ["R", "U", "F"]
    adapters = [Scripted(k, fn=lambda ctx: "D") for k in ("jev", "luna", "clef", "opus")]
    run = create_run(category="warmup", scramble=scramble, mode="mock", move_limit=3, seed=1, adapters=adapters)
    asyncio.run(execute_run(run, adapters, SETTINGS))
    expected = CubeEngine.from_scramble(scramble).facelets
    assert run.start_facelets == expected
    for a in adapters:
        assert a.seen_states[0] == expected  # identical first view
    # each competitor played its own cube: all applied exactly D,D,D and ended identically
    finals = {c.current_facelets for c in run.competitors}
    assert len(finals) == 1 and finals != {expected}
    assert all(c.applied_moves == ["D", "D", "D"] for c in run.competitors)


def test_one_competitors_moves_do_not_leak_into_another():
    a, b = Scripted("jev", fn=lambda ctx: "R'"), Scripted("luna", fn=lambda ctx: "U")
    run = create_run(category="warmup", scramble=["R"], mode="mock", move_limit=4, seed=1, adapters=[a, b])
    asyncio.run(execute_run(run, [a, b], SETTINGS))
    ja, lb = run.competitors
    assert ja.status == "solved" and lb.status == "failed"
    assert b.seen_states[0] == a.seen_states[0]
    assert lb.applied_moves == ["U"] * 4


def test_mock_run_of_all_four_records_everything():
    adapters = build_adapters("mock", get_settings(), seed=5)
    assert [a.key for a in adapters] == ["jev", "luna", "clef", "opus"]
    run = create_run(category="primary", scramble=["R", "U", "F2", "L'"], mode="mock", move_limit=20, seed=5, adapters=adapters)
    asyncio.run(execute_run(run, adapters, get_settings()))
    d = run.to_dict()
    assert d["status"] == "completed" and d["mode"] == "mock"
    assert d["scramble_moves"] == ["R", "U", "F2", "L'"] and len(d["start_facelets"]) == 54
    assert d["optimal"]["exact"] is True
    for c in d["competitors"]:
        assert c["status"] in ("solved", "failed") and c["mode"] == "mock"
        assert c["total_time_ms"] is not None and c["final_facelets"] and c["steps"]
        assert c["metrics"]["moves"] == len(c["applied_moves"]) <= 20
        # replaying the applied moves on the start state really gives the recorded final state
        replay = CubeEngine.from_facelets(d["start_facelets"]).apply_moves(c["applied_moves"])
        assert replay.facelets == c["final_facelets"]
        assert (c["status"] == "solved") == replay.is_solved()


def test_mock_runs_are_reproducible_for_a_seed():
    def go():
        ad = build_adapters("mock", get_settings(), seed=42)
        run = create_run(category="primary", scramble=["R", "U", "F2", "L'"], mode="mock", move_limit=20, seed=42, adapters=ad)
        asyncio.run(execute_run(run, ad, get_settings()))
        return [(c.key, c.status, c.applied_moves) for c in run.competitors]
    assert go() == go()


def test_extension_scramble_has_no_optimal_and_gap_is_none():
    from backend.cube_engine import generate_scramble
    import random
    sc = generate_scramble(18, random.Random(1))
    ad = build_adapters("mock", get_settings(), seed=1)
    run = create_run(category="extension", scramble=sc, mode="mock", move_limit=20, seed=1, adapters=ad)
    asyncio.run(execute_run(run, ad, get_settings()))
    d = run.to_dict()
    assert d["optimal"]["exact"] is False and d["optimal"]["length"] is None
    assert all(c["metrics"]["optimality_gap"] is None for c in d["competitors"])


def test_real_mode_without_key_refuses_instead_of_faking():
    with pytest.raises(AdapterError) as e:
        build_adapters("real", get_settings())
    assert "OPENROUTER_API_KEY" in e.value.message
