"""Full benchmark runs against a FAKE OpenRouter/Ollama server (httpx.MockTransport). No real request is possible."""
import asyncio
import itertools
import json
import random

import httpx
import pytest

from backend.benchmark import create_run, execute_run
from backend.budget import BudgetLedger
from backend.cube_engine import MOVES, CubeEngine
from backend.experiment import generate_final_scramble, verify_exact_distance
from backend.models import build_adapters
from backend.models.base import StepContext
from backend.models.chat import ChatAdapter
from backend.models.prompts import chat_user_prompt, decisions_question, state_payload
from tests.helpers import (TEST_KEY, FakeAPI, chat_body, decisions_body, make_guard, never_solving, settings,
                           solver_move)

SCRAMBLE = generate_final_scramble(random.Random(2026))
OPUS_ID = "anthropic/claude-opus-5.5"


def run_all(tmp_path, fake=None, *, mode="real", keys=None, scramble=SCRAMBLE, s=None, guard=None, limit=20):
    s = s or settings(openrouter_api_key=TEST_KEY, state_dir=tmp_path)
    guard = guard or make_guard(tmp_path)
    kwargs = {} if mode == "replay" else {"client": fake.client}
    adapters = build_adapters(mode, s, guard=guard, keys=keys, **kwargs)
    run = create_run(category="final", scramble=scramble, mode=mode, move_limit=limit, seed=1, adapters=adapters)
    asyncio.run(execute_run(run, adapters, s))
    return run.to_dict()


def comp(run, key):
    return next(c for c in run["competitors"] if c["key"] == key)


# ------------------------------------------------------------------ the happy path and the accounting
def test_full_run_every_model_gets_the_same_start_and_everything_is_recorded(tmp_path):
    fake = FakeAPI(cost=0.00002)
    run = run_all(tmp_path, fake)
    assert len({c["start_facelets"] for c in run["competitors"]}) == 1
    assert run["start_facelets"] == CubeEngine.from_scramble(SCRAMBLE).facelets
    for c in run["competitors"]:
        m = c["metrics"]
        assert c["status"] == "solved" and m["moves"] == 3 and m["api_calls"] == 3 and m["cache_hits"] == 0
        assert m["optimality_gap"] == 0 and m["avg_latency_ms"] is not None and m["avg_cache_latency_ms"] is None
        assert m["cost_usd"] == pytest.approx(3 * 0.00002)
        assert CubeEngine.from_facelets(c["start_facelets"]).apply_moves(c["applied_moves"]).is_solved()
    assert len(fake.openrouter_requests) == 12
    paths = {p for p, _, _ in fake.requests}
    assert paths == {"/api/alpha/decisions", "/api/v1/chat/completions"}
    st = BudgetLedger(tmp_path).status()["openrouter"]
    assert st["requests"] == 12 and st["by_model"]["opus"]["requests"] == 3 and st["cost_usd"] == pytest.approx(12 * 0.00002)


def test_rerun_after_restart_is_all_cache_hits_zero_requests_zero_cost(tmp_path):
    run_all(tmp_path, FakeAPI())
    fake2 = FakeAPI()
    run = run_all(tmp_path, fake2, guard=make_guard(tmp_path))     # new guard objects = application restart
    assert fake2.requests == []                                    # not a single request reached the (fake) network
    for c in run["competitors"]:
        m = c["metrics"]
        assert c["status"] == "solved" and m["api_calls"] == 0 and m["cache_hits"] == 3
        assert m["avg_latency_ms"] is None and m["avg_cache_latency_ms"] is not None   # live vs cached kept apart
        assert m["cost_usd"] == 0.0
        assert all(s["cache_hit"] and s["source"] == "cache" for s in c["steps"])
    st = BudgetLedger(tmp_path).status()
    assert st["openrouter"]["requests"] == 12 and st["cache_hits"]["total"] == 12


def test_replay_mode_needs_no_key_and_makes_no_requests(tmp_path):
    run_all(tmp_path, FakeAPI())
    s = settings(state_dir=tmp_path)                    # NO api key
    run = run_all(tmp_path, mode="replay", s=s)
    assert [c["mode"] for c in run["competitors"]] == ["replay"] * 4
    assert all(c["status"] == "solved" and c["metrics"]["api_calls"] == 0 for c in run["competitors"])
    assert BudgetLedger(tmp_path).status()["openrouter"]["requests"] == 12


def test_replay_with_empty_cache_fails_instead_of_calling_the_network(tmp_path):
    run = run_all(tmp_path, mode="replay", s=settings(state_dir=tmp_path))
    assert all(c["status"] == "failed" and c["outcome"] == "cache_miss" for c in run["competitors"])
    assert BudgetLedger(tmp_path).status()["openrouter"]["requests"] == 0


def test_worst_case_run_never_exceeds_65_requests_and_claude_never_exceeds_5(tmp_path):
    fake = FakeAPI(chooser=never_solving)
    run = run_all(tmp_path, fake)
    for k in ("jev", "luna", "clef"):
        c = comp(run, k)
        assert c["status"] == "failed" and c["outcome"] == "unsolved" and c["metrics"]["moves"] == 20
        assert c["metrics"]["optimality_gap"] is None                  # unsolved -> N/A, never "20 - 3"
        assert c["metrics"]["api_calls"] == 20
    o = comp(run, "opus")
    assert o["outcome"] == "budget_exhausted" and o["metrics"]["api_calls"] == 5 and o["metrics"]["moves"] == 5
    assert len(fake.openrouter_requests) == 65
    assert sum(1 for p, _, m in fake.requests if m == OPUS_ID) == 5
    # rerunning with fresh objects can't buy more: the 5 cached answers replay, request 6 is refused, nothing is sent
    fake2 = FakeAPI(chooser=never_solving)
    run_all(tmp_path, fake2, guard=make_guard(tmp_path))
    assert fake2.requests == [] and BudgetLedger(tmp_path).status()["openrouter"]["requests"] == 65


def test_a_competitor_stops_immediately_when_solved_and_at_20_moves(tmp_path):
    run = run_all(tmp_path, FakeAPI(), keys=["jev"])
    c = comp(run, "jev")
    assert len(c["steps"]) == 3 and c["steps"][-1]["solved_after"]            # no request after the solution
    run2 = run_all(tmp_path / "b", FakeAPI(chooser=never_solving), keys=["clef"])
    assert comp(run2, "clef")["metrics"]["moves"] == 20 and len(comp(run2, "clef")["steps"]) == 20


# ------------------------------------------------------------------ bad replies / errors: bounded, counted, harmless
@pytest.mark.parametrize("name,response", [
    ("empty content", lambda req: httpx.Response(200, json=chat_body(OPUS_ID, ""))),
    ("reasoning only", lambda req: httpx.Response(200, json=chat_body(OPUS_ID, None, extra_message={"reasoning": "I think U"}))),
    ("truncated", lambda req: httpx.Response(200, json=chat_body(OPUS_ID, "Let me think about the move: U", finish="length"))),
    ("illegal move", lambda req: httpx.Response(200, json=chat_body(OPUS_ID, '{"move": "R3"}'))),
    ("prose", lambda req: httpx.Response(200, json=chat_body(OPUS_ID, "I would turn the top face clockwise"))),
    ("malformed json", lambda req: httpx.Response(200, content=b"<html>not json")),
    ("200 with error body", lambda req: httpx.Response(200, json={"error": {"message": "provider exploded"}})),
    ("no choices", lambda req: httpx.Response(200, json={"model": OPUS_ID, "choices": []})),
    ("http 500", lambda req: httpx.Response(500, json={"error": {"message": "boom"}})),
    ("http 429", lambda req: httpx.Response(429, json={"error": {"message": "slow down"}})),
])
def test_bad_replies_never_touch_the_cube_and_retries_are_bounded_and_counted(tmp_path, name, response):
    fake = FakeAPI()
    fake.raw_override = response
    run = run_all(tmp_path, fake, keys=["opus"])
    c = comp(run, "opus")
    assert c["metrics"]["moves"] == 0 and c["applied_moves"] == []
    assert c["final_facelets"] == c["start_facelets"]                      # cube untouched
    assert c["status"] == "failed"
    assert len(fake.requests) == 2 == c["metrics"]["api_calls"]            # at most ONE retry, both counted
    assert BudgetLedger(tmp_path).status()["openrouter"]["by_model"]["opus"]["requests"] == 2
    assert not any(s["applied"] for s in c["steps"])
    assert ResponseCacheEntries(tmp_path) == 0                             # a bad reply is never cached


def ResponseCacheEntries(tmp_path):
    from backend.cache import ResponseCache
    return ResponseCache(tmp_path).stats()["entries"]


def test_timeouts_are_counted_and_bounded(tmp_path):
    fake = FakeAPI(delay=1.0)
    s = settings(openrouter_api_key=TEST_KEY, state_dir=tmp_path, step_timeout_seconds=0.05)
    run = run_all(tmp_path, fake, keys=["luna"], s=s)
    c = comp(run, "luna")
    assert c["status"] == "failed" and c["metrics"]["moves"] == 0
    assert c["metrics"]["api_calls"] == 2 and BudgetLedger(tmp_path).status()["openrouter"]["requests"] == 2
    assert {s_["error_kind"] for s_ in c["steps"]} == {"timeout"}


def test_decisions_reply_must_contain_a_choice_and_the_choice_must_be_legal(tmp_path):
    fake = FakeAPI()
    fake.raw_override = lambda req: httpx.Response(200, json={**decisions_body("typesafe/jev-1.13", "X9"),})
    c = comp(run_all(tmp_path, fake, keys=["jev"]), "jev")
    assert c["metrics"]["moves"] == 0 and c["metrics"]["invalid_responses"] == 2 and c["applied_moves"] == []
    fake2 = FakeAPI()
    fake2.raw_override = lambda req: httpx.Response(200, json={"model": "typesafe/jev-1.13", "answers": {}})
    c2 = comp(run_all(tmp_path / "x", fake2, keys=["jev"]), "jev")
    assert c2["metrics"]["moves"] == 0 and c2["metrics"]["api_calls"] == 2


def test_provider_refusal_502_like_the_one_seen_with_luna_is_bounded(tmp_path):
    fake = FakeAPI()
    fake.fail_with = (502, {"error": {"message": 'OpenAI refused to answer question "next_move"'}})
    c = comp(run_all(tmp_path, fake, keys=["luna"]), "luna")
    assert c["status"] == "failed" and c["metrics"]["api_calls"] == 2 and "refused" in c["failure_reason"]


def test_missing_cost_is_reported_as_unreported_not_invented(tmp_path):
    c = comp(run_all(tmp_path, FakeAPI(cost=None), keys=["jev"]), "jev")
    assert c["metrics"]["cost_usd"] is None and c["metrics"]["cost_unreported_calls"] == 3


# ------------------------------------------------------------------ equivalent instructions
def _ctx(facelets, history=()):
    cube = CubeEngine.from_facelets(facelets)
    return StepContext(step=1, facelets=facelets, faces=cube.faces(), net=cube.net(), history=list(history),
                       move_limit=20, hidden={"scramble": SCRAMBLE})


def test_all_four_models_get_equivalent_information():
    start = CubeEngine.from_scramble(SCRAMBLE).facelets
    ctx = _ctx(start)
    s = settings(openrouter_api_key=TEST_KEY)
    adapters = build_adapters("real", s, guard=make_guard("/tmp/unused-guard-dir-for-payload-test"),
                              client=FakeAPI().client)
    payloads = {a.key: a.build_payload(ctx) for a in adapters}
    states = {k: p["state"] for k, p in payloads.items() if k != "opus"}
    assert states["jev"] == states["luna"] == states["clef"] == state_payload(ctx)
    chat_user = json.loads(payloads["opus"]["messages"][1]["content"].split("Current cube state:\n")[1].split("\n\nChoose")[0])
    assert chat_user == state_payload(ctx)                                  # identical state JSON for Claude
    # same instructions: notation + facelet help + 18 legal moves, nothing about the scramble, same remaining budget
    system = payloads["opus"]["messages"][0]["content"]
    assert state_payload(ctx)["notation"] in system and state_payload(ctx)["facelet_help"] in system
    assert set(decisions_question()["next_move"]["criteria"]) == set(MOVES)
    assert all(str(SCRAMBLE) not in json.dumps(p) and "hidden" not in json.dumps(p) for p in payloads.values())
    assert state_payload(ctx)["moves_remaining_before_limit"] == 20


# ------------------------------------------------------------------ the exact-3-move start state
def test_final_start_is_exactly_three_moves_proven_two_independent_ways():
    for seed in range(12):
        moves = generate_final_scramble(random.Random(seed))
        facelets = CubeEngine.from_scramble(moves).facelets
        check = verify_exact_distance(facelets)
        assert check.verified and check.optimal_length == 3 and len(check.solution) == 3
        assert CubeEngine.from_facelets(facelets).apply_moves(check.solution).is_solved()
        # independent re-check with nothing but the pycuber-backed engine: no sequence of <= 2 moves solves it
        for k in range(3):
            for seq in itertools.product(MOVES, repeat=k):
                assert CubeEngine.from_facelets(facelets).apply_moves(seq).is_solved() is False


@pytest.mark.parametrize("moves,distance", [(["R", "U", "U'"], 1), (["R", "R"], 1), (["R", "R", "R"], 1),
                                            (["U", "D", "U'", "D'"], 0), (["R", "L", "R'"], 1), (["R", "U"], 2)])
def test_scrambles_that_merely_apply_three_moves_are_rejected(moves, distance):
    check = verify_exact_distance(CubeEngine.from_scramble(moves).facelets)
    assert not check.verified and check.optimal_length == distance


def test_a_genuine_three_move_state_is_accepted():
    assert verify_exact_distance(CubeEngine.from_scramble(["R", "U", "F"]).facelets).verified


def test_official_start_state_is_fixed_across_calls_and_restarts():
    assert generate_final_scramble(random.Random(2026)) == SCRAMBLE == generate_final_scramble(random.Random(2026))


# ------------------------------------------------------------------ local Ollama experiments
def test_ollama_mode_never_contacts_openrouter_or_spends_its_budget(tmp_path):
    fake = FakeAPI()
    s = settings(state_dir=tmp_path, ollama_models={k: "llama3.2" for k in ("jev", "luna", "clef", "opus")})
    run = run_all(tmp_path, fake, mode="ollama", s=s)                       # note: no API key at all
    assert [c["mode"] for c in run["competitors"]] == ["ollama"] * 4
    assert fake.requests and all(h != "openrouter.ai" for _, h, _ in fake.requests)
    assert all(p == "/api/chat" for p, _, _ in fake.requests)
    st = BudgetLedger(tmp_path).status()
    assert st["openrouter"]["requests"] == 0 and st["local"]["requests"] == len(fake.requests)
    assert all(c["status"] == "solved" for c in run["competitors"])


def test_ollama_base_url_pointing_at_openrouter_is_refused(tmp_path):
    from backend.models.ollama import OllamaAdapter
    from backend.models import SPECS
    with pytest.raises(ValueError):
        OllamaAdapter(SPECS["jev"], guard=make_guard(tmp_path), model_tag="x", base_url="https://openrouter.ai/api")
    s = settings(state_dir=tmp_path, ollama_base_url="https://openrouter.ai/api",
                 ollama_models={k: "m" for k in ("jev", "luna", "clef", "opus")})
    with pytest.raises(ValueError):
        build_adapters("ollama", s, guard=make_guard(tmp_path))


def test_ollama_unreachable_is_a_clean_fatal_error_not_a_loop(tmp_path):
    fake = FakeAPI()
    fake.fail_with = httpx.ConnectError("refused")
    s = settings(state_dir=tmp_path, ollama_models={k: "m" for k in ("jev", "luna", "clef", "opus")})
    run = run_all(tmp_path, fake, mode="ollama", s=s, keys=["jev"])
    c = comp(run, "jev")
    assert c["status"] == "failed" and len(fake.requests) == 1 and "Ollama" in c["failure_reason"]
