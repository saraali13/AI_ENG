"""The professor's API-budget rules, proven with fake HTTP only (no real request is ever sent).

Rules covered: Claude <= 5 requests ever, global target 65 / hard ceiling 100, counted BEFORE the network attempt,
failures/timeouts/cancellations still counted, cache hits are free, persistence across restarts, no duplicate
concurrent calls, cross-process safety, fail-closed ledger, no key leaks.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.budget import (CLAUDE_HARD_LIMIT, HARD_CEILING, TARGET_GLOBAL, BudgetExceeded, BudgetLedger,
                            LedgerError, Limits)
from backend.cache import ResponseCache
from backend.config import get_settings
from backend.models.base import AdapterError, MoveDecision
from tests.helpers import make_guard

ROOT = Path(__file__).resolve().parent.parent


def execute(guard, *, key="opus", model="anthropic/claude-opus-5.5", payload=None, send=None, parse=None,
            allow_network=True, provider="openrouter"):
    async def default_send():
        return {"ok": 1, "usage": {"cost": 0.5, "prompt_tokens": 3, "completion_tokens": 1}}
    return guard.execute(
        ledger_key=key, provider=provider, endpoint="POST /x", model_id=model, payload=payload or {"n": 1},
        send=send or default_send, parse=parse or (lambda d: MoveDecision(raw="R", move="R")),
        allow_network=allow_network)


def requests_used(directory):
    return BudgetLedger(directory).status()["openrouter"]["requests"]


# ------------------------------------------------------------------ limits are constants, not settings
def test_limits_are_the_professors_numbers():
    assert (TARGET_GLOBAL, HARD_CEILING, CLAUDE_HARD_LIMIT) == (65, 100, 5)
    assert Limits().effective_global == 65 and Limits().model_limit("opus") == 5
    assert 3 * Limits().model_limit("jev") + Limits().model_limit("opus") == 65  # 3 x 20 + 5


def test_configuration_cannot_raise_the_hard_limits(monkeypatch):
    assert Limits(global_limit=10**6).effective_global == 100
    assert Limits(per_model={"opus": 99}).model_limit("opus") == 5
    monkeypatch.setenv("OPENROUTER_GLOBAL_LIMIT", "999")
    monkeypatch.setenv("CLAUDE_REQUEST_LIMIT", "50")
    s = get_settings()
    assert s.openrouter_global_limit == 100 and s.claude_request_limit == 5
    monkeypatch.setenv("MOVE_LIMIT", "99")
    assert get_settings().move_limit == 20


def test_claude_gets_exactly_five_requests_even_after_restarts(tmp_path):
    for _ in range(5):
        BudgetLedger(tmp_path, Limits(per_model={"opus": 99})).reserve("opus")   # a new object = a restart
    with pytest.raises(BudgetExceeded) as e:
        BudgetLedger(tmp_path, Limits(per_model={"opus": 99})).reserve("opus")
    assert e.value.scope == "model"
    assert requests_used(tmp_path) == 5


def test_global_target_65_is_enforced_and_matches_3x20_plus_5(tmp_path):
    led = BudgetLedger(tmp_path)
    for key in ("jev", "luna", "clef"):
        for _ in range(20):
            led.reserve(key)
        with pytest.raises(BudgetExceeded):
            led.reserve(key)                      # 21st request of one model
    for _ in range(5):
        led.reserve("opus")
    assert led.status()["openrouter"]["requests"] == 65
    with pytest.raises(BudgetExceeded) as e:
        led.reserve("jev")
    assert e.value.scope == "global" and led.status()["remaining_global"] == 0


def test_hard_ceiling_100_cannot_be_bypassed(tmp_path):
    led = BudgetLedger(tmp_path, Limits(global_limit=10**6, default_model_limit=10**6))
    for i in range(100):
        led.reserve(f"m{i % 7}")
    with pytest.raises(BudgetExceeded):
        led.reserve("m1")
    assert requests_used(tmp_path) == 100


def test_claude_cap_follows_the_model_id_not_the_slot_name(tmp_path):
    g = make_guard(tmp_path)
    async def go():
        out = []
        for i in range(8):   # a Claude model hiding behind another slot name is still capped at 5
            out.append(await asyncio.gather(execute(g, key="jev", model="anthropic/claude-opus-5.5", payload={"n": i}),
                                            return_exceptions=True))
        return out
    results = [r[0] for r in asyncio.run(go())]
    assert sum(isinstance(r, AdapterError) and r.kind == "budget" for r in results) == 3
    assert g.ledger.status()["openrouter"]["by_model"]["opus"]["requests"] == 5


# ------------------------------------------------------------------ counted BEFORE the network attempt
def test_request_is_on_disk_before_the_network_call_starts(tmp_path):
    g = make_guard(tmp_path)
    seen = {}

    async def send():
        seen["during"] = requests_used(tmp_path)    # a separate ledger object reading the file
        return {"ok": 1}
    asyncio.run(execute(g, send=send))
    assert seen["during"] == 1


def test_failed_timed_out_and_cancelled_requests_still_count(tmp_path):
    g = make_guard(tmp_path)

    async def failing():
        raise AdapterError("HTTP 500", kind="server_error")

    async def hanging():
        await asyncio.sleep(5)
        return {"ok": 1}

    async def go():
        with pytest.raises(AdapterError):
            await execute(g, payload={"n": 1}, send=failing)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(execute(g, payload={"n": 2}, send=hanging), 0.05)   # cancelled mid-flight
    asyncio.run(go())
    s = g.ledger.status()["openrouter"]
    assert s["requests"] == 2 and s["failed"] == 2 and s["succeeded"] == 0


def test_over_budget_request_sends_nothing(tmp_path):
    g = make_guard(tmp_path, opus=1)
    sent = []

    async def send():
        sent.append(1)
        return {"ok": 1}

    async def go():
        await execute(g, payload={"n": 1}, send=send)
        with pytest.raises(AdapterError) as e:
            await execute(g, payload={"n": 2}, send=send)
        assert e.value.kind == "budget" and e.value.fatal
    asyncio.run(go())
    assert len(sent) == 1 and requests_used(tmp_path) == 1


# ------------------------------------------------------------------ cache
def test_cache_hit_is_free_distinct_and_persistent(tmp_path):
    sent = []

    async def send():
        sent.append(1)
        return {"ok": 1, "usage": {"cost": 0.25}}

    async def go(guard):
        return await execute(guard, send=send)
    d1, i1 = asyncio.run(go(make_guard(tmp_path)))
    assert i1.source == "live" and i1.api_calls == 1 and i1.live_latency_ms is not None and i1.cache_latency_ms is None
    assert i1.cost_usd == 0.25
    # "restart": a brand-new guard on the same directory
    d2, i2 = asyncio.run(go(make_guard(tmp_path)))
    assert i2.source == "cache" and i2.api_calls == 0 and i2.cost_usd == 0.0
    assert i2.live_latency_ms is None and i2.cache_latency_ms is not None      # separate metrics
    assert len(sent) == 1
    st = BudgetLedger(tmp_path).status()
    assert st["openrouter"]["requests"] == 1 and st["cache_hits"]["total"] == 1   # a hit is not an API call
    assert d2.move == d1.move


def test_cache_key_covers_model_endpoint_and_full_payload():
    k = ResponseCache.make_key
    base = k("openrouter", "POST /a", "m1", {"x": 1, "y": [1, 2]})
    assert base == k("openrouter", "POST /a", "m1", {"y": [1, 2], "x": 1})      # key order does not matter
    for other in (k("openrouter", "POST /a", "m2", {"x": 1, "y": [1, 2]}), k("openrouter", "POST /b", "m1", {"x": 1, "y": [1, 2]}),
                  k("ollama", "POST /a", "m1", {"x": 1, "y": [1, 2]}), k("openrouter", "POST /a", "m1", {"x": 1, "y": [1, 3]}),
                  k("openrouter", "POST /a", "m1", {"x": 1, "y": [1, 2], "max_tokens": 5})):
        assert other != base


def test_unusable_replies_are_never_cached(tmp_path):
    g = make_guard(tmp_path)
    bad = lambda d: MoveDecision(raw="U3", move="U3")     # not one of the 18 moves
    for _ in range(2):
        asyncio.run(execute(g, parse=bad))
    st = g.ledger.status()
    assert st["openrouter"]["requests"] == 2 and g.cache.stats()["entries"] == 0 and st["cache_hits"]["total"] == 0


def test_replay_mode_never_sends_and_a_miss_is_an_error(tmp_path):
    g = make_guard(tmp_path)
    async def boom():
        raise AssertionError("network must not be used")
    with pytest.raises(AdapterError) as e:
        asyncio.run(execute(g, send=boom, allow_network=False))
    assert e.value.kind == "cache_miss" and requests_used(tmp_path) == 0


# ------------------------------------------------------------------ concurrency
def test_identical_concurrent_requests_send_once(tmp_path):
    g = make_guard(tmp_path)
    sent = []

    async def send():
        sent.append(1)
        await asyncio.sleep(0.05)
        return {"ok": 1}

    async def go():
        return await asyncio.gather(*(execute(g, send=send) for _ in range(10)))
    res = asyncio.run(go())
    assert len(sent) == 1 and requests_used(tmp_path) == 1
    assert sorted(i.source for _, i in res).count("live") == 1


def test_many_concurrent_different_requests_cannot_exceed_claudes_five(tmp_path):
    g = make_guard(tmp_path)
    sent = []

    async def send():
        sent.append(1)
        await asyncio.sleep(0.01)
        return {"ok": 1}

    async def go():
        return await asyncio.gather(*(execute(g, payload={"n": i}, send=send) for i in range(40)), return_exceptions=True)
    res = asyncio.run(go())
    assert len(sent) == 5 and requests_used(tmp_path) == 5
    assert sum(isinstance(r, AdapterError) and r.kind == "budget" for r in res) == 35


def test_separate_processes_share_one_budget(tmp_path):
    code = ("import sys\nfrom backend.budget import BudgetLedger, Limits, BudgetExceeded\n"
            "led = BudgetLedger(sys.argv[1], Limits(per_model={'opus': 5}))\nok = 0\n"
            "for _ in range(4):\n    try:\n        led.reserve('opus'); ok += 1\n    except BudgetExceeded:\n        pass\nprint(ok)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
             for _ in range(8)]
    granted = sum(int(p.communicate()[0].strip()) for p in procs)
    assert granted == 5 and requests_used(tmp_path) == 5


# ------------------------------------------------------------------ durability
def test_ledger_survives_a_restart_and_is_never_reset_by_reading(tmp_path):
    led = BudgetLedger(tmp_path)
    for _ in range(3):
        led.reserve("jev")
    again = BudgetLedger(tmp_path)
    assert again.status()["openrouter"]["requests"] == 3
    again.status(), again.record_cache_hit("jev")
    assert BudgetLedger(tmp_path).status()["openrouter"]["requests"] == 3


def test_corrupt_ledger_fails_closed_never_restarts_from_zero(tmp_path):
    led = BudgetLedger(tmp_path)
    led.reserve("opus")
    led.path.write_text("{not json")
    led.backup.write_text("also broken")
    with pytest.raises(LedgerError):
        led.reserve("opus")
    g = make_guard(tmp_path)
    sent = []

    async def send():
        sent.append(1)
        return {"ok": 1}
    with pytest.raises(AdapterError) as e:
        asyncio.run(execute(g, send=send, payload={"n": 9}))
    assert e.value.kind == "budget" and not sent


def test_one_corrupt_copy_is_recovered_from_the_other_without_losing_counts(tmp_path):
    led = BudgetLedger(tmp_path)
    for _ in range(3):
        led.reserve("opus")
    led.path.write_text("garbage")
    assert BudgetLedger(tmp_path).reserve("opus") == 4


def test_cache_survives_corruption_by_starting_empty_without_touching_the_ledger(tmp_path):
    g = make_guard(tmp_path)
    asyncio.run(execute(g))
    g.cache.path.write_text("{broken")
    asyncio.run(execute(make_guard(tmp_path)))     # miss -> live again, counted
    assert requests_used(tmp_path) == 2


# ------------------------------------------------------------------ secrets
def test_api_key_never_reaches_logs_state_files_or_error_messages(tmp_path, caplog):
    import httpx
    from backend.models.jev import JevAdapter
    from tests.helpers import TEST_KEY, FakeAPI, play, settings
    fake = FakeAPI()
    fake.fail_with = (401, {"error": {"message": f"bad key {TEST_KEY}"}})
    caplog.set_level("DEBUG")
    a = JevAdapter(guard=make_guard(tmp_path), api_key=TEST_KEY, client=fake.client)
    comp = play(a, ["R"], s=settings())
    blob = json.dumps(comp.to_dict()) + caplog.text + "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert TEST_KEY not in blob and comp.status == "failed"
