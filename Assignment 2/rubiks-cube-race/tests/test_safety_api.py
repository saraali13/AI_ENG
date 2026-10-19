"""API-level guards: a UI/API action can never spend or reset the OpenRouter budget by accident."""
import random

import pytest
from fastapi.testclient import TestClient

from backend.budget import BudgetLedger
from backend.config import get_settings
from backend.experiment import generate_final_scramble
from backend.main import app
from tests.helpers import FakeAPI
from tests.test_safety_lifecycle import run_all


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def ledger():
    return BudgetLedger(get_settings().state_dir)


def spent():
    return ledger().status()["openrouter"]["requests"]


def test_budget_endpoint_reports_limits_counts_and_no_secrets(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-supersecretvalue")
    ledger().reserve("opus")
    text = client.get("/api/budget").text
    assert "supersecretvalue" not in text
    d = client.get("/api/budget").json()
    assert d["ledger"]["limits"] == {"global_limit": 65, "global_target": 65, "hard_ceiling": 100,
                                      "claude_limit": 5, "default_model_limit": 20}
    assert d["ledger"]["openrouter"]["requests"] == 1 and d["ledger"]["openrouter"]["by_model"]["opus"]["remaining"] == 4
    assert d["planned_max_final_run"]["total"] == 65 and d["ledger"]["healthy"] is True


def test_nothing_in_the_ui_api_can_reset_counters_or_the_cache(client):
    for _ in range(3):
        ledger().reserve("jev")
    client.post("/api/reset")
    client.post("/api/scramble", json={"category": "final"})
    client.post("/api/benchmark", json={"mode": "mock", "category": "warmup", "wait": True})
    client.get("/api/health"), client.get("/api/results")
    assert spent() == 3
    assert client.delete("/api/budget").status_code in (404, 405)
    assert client.post("/api/budget", json={}).status_code in (404, 405)


def test_real_mode_gates_each_leave_the_budget_untouched(client, monkeypatch):
    body = {"mode": "real", "category": "final"}
    r = client.post("/api/benchmark", json=body)                       # no key
    assert r.status_code == 400 and "OPENROUTER_API_KEY" in r.json()["error"]["message"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-only-not-real")
    r = client.post("/api/benchmark", json=body)                       # key but no explicit confirmation
    assert r.status_code == 400 and "confirm" in r.json()["error"]["message"].lower()
    r = client.post("/api/benchmark", json={**body, "category": "primary", "confirm_live": True})
    assert r.status_code == 400 and "final" in r.json()["error"]["message"]
    r = client.post("/api/benchmark", json={**body, "confirm_live": True, "moves": ["R", "U", "F"]})
    assert r.status_code == 422 and "official" in r.json()["error"]["message"]
    r = client.post("/api/benchmark", json={**body, "confirm_live": True, "move_limit": 21})
    assert r.status_code == 422
    assert spent() == 0


def test_final_category_start_state_is_verified_and_fixed(client):
    a = client.post("/api/scramble", json={"category": "final"}).json()
    b = client.post("/api/scramble", json={"category": "final"}).json()
    assert a["scramble_moves"] == b["scramble_moves"] == generate_final_scramble(random.Random(2026))
    v = a["start_verification"]
    assert a["scramble_length"] == 3 and v["verified"] and v["optimal_length"] == 3
    assert v["independent_lower_bound_holds"] is True and v["solution_replay_solves"] is True
    assert a["optimal"]["length"] == 3


def test_final_benchmark_rejects_scrambles_that_are_not_exactly_three_moves_away(client):
    for moves in (["R", "U", "U'"], ["R", "R", "R"], ["R", "L", "R'"]):
        r = client.post("/api/benchmark", json={"mode": "mock", "category": "final", "moves": moves})
        assert r.status_code == 422 and "exactly 3" in r.json()["error"]["message"]


def test_mock_final_run_records_verification_and_never_touches_openrouter(client):
    run = client.post("/api/benchmark", json={"mode": "mock", "category": "final", "wait": True}).json()
    assert run["status"] == "completed" and run["move_limit"] == 20 and run["scramble_length"] == 3
    assert run["start_verification"]["verified"] is True and run["optimal"]["length"] == 3
    assert spent() == 0
    for c in run["competitors"]:
        assert c["metrics"]["api_calls"] == 0 and c["metrics"]["cache_hits"] == 0


def test_replay_demo_through_the_api_uses_only_the_cache(client):
    s = get_settings()
    run_all(s.state_dir, FakeAPI(), scramble=generate_final_scramble(random.Random(s.official_seed)))
    before = spent()
    run = client.post("/api/benchmark", json={"mode": "replay", "category": "final", "wait": True}).json()
    assert run["mode"] == "replay" and all(c["status"] == "solved" for c in run["competitors"])
    assert all(c["metrics"]["api_calls"] == 0 and c["metrics"]["cache_hits"] == 3 for c in run["competitors"])
    assert spent() == before == 12
    assert run["budget_before"]["openrouter"]["requests"] == run["budget_after"]["openrouter"]["requests"] == 12


def test_ollama_mode_via_api_cannot_spend_openrouter_requests(client):
    run = client.post("/api/benchmark", json={"mode": "ollama", "category": "final", "wait": True}).json()
    assert run["mode"] == "ollama" and spent() == 0     # no Ollama in the test sandbox: runs fail cleanly
    assert all(c["status"] == "failed" for c in run["competitors"])


def test_only_one_benchmark_runs_at_a_time(client, monkeypatch):
    monkeypatch.setenv("MOCK_DELAY_SCALE", "3")
    first = client.post("/api/benchmark", json={"mode": "mock", "category": "warmup"})
    assert first.status_code == 202
    second = client.post("/api/benchmark", json={"mode": "mock", "category": "warmup"})
    assert second.status_code == 409
