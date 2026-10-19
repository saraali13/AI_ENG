import json

import pytest
from fastapi.testclient import TestClient

from backend.cube_engine import CubeEngine
from backend.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_root_serves_the_website(client):
    r = client.get("/")
    assert r.status_code == 200 and "Rubik" in r.text and "text/html" in r.headers["content-type"]


def test_static_assets_are_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/vendor/chart.umd.js").status_code == 200


def test_health(client):
    d = client.get("/api/health").json()
    assert d["status"] == "ok" and d["mock_mode_available"] is True and d["real_mode_available"] is False
    assert d["move_limit"] == 20


def test_config_has_student_info_and_never_the_key(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-supersecret")
    text = client.get("/api/config").text + client.get("/api/health").text + client.get("/api/models").text
    assert "sk-or-supersecret" not in text
    d = client.get("/api/config").json()
    assert d["student_name"] == "Test Student" and d["roll_number"] == "00-TEST" and d["real_mode_available"] is True


def test_models_lists_four_competitors_in_order(client):
    d = client.get("/api/models").json()
    assert [m["name"] for m in d["models"]] == ["Jev 1.13", "GPT-6 Luna Decisions", "Cloudflare Clef", "Claude Opus 5.5"]
    assert [m["openrouter_id"] for m in d["models"]] == [
        "typesafe/jev-1.13", "openai/gpt-6-luna-decisions", "cloudflare/clef", "anthropic/claude-opus-5.5"]
    assert [m["protocol"] for m in d["models"]] == ["decisions", "decisions", "decisions", "chat"]


@pytest.mark.parametrize("category,low,high", [("warmup", 3, 4), ("primary", 4, 5), ("extension", 15, 20)])
def test_scramble_endpoint(client, category, low, high):
    d = client.post("/api/scramble", json={"category": category}).json()
    assert low <= d["scramble_length"] <= high == d["category_range"]["max"]
    cube = CubeEngine.from_scramble(d["scramble_moves"])
    assert d["cube"]["facelets"] == cube.facelets and d["cube"]["solved"] is False
    assert (d["optimal"]["exact"] is False) if category == "extension" else d["optimal"]["length"] <= d["scramble_length"]


def test_scramble_is_reproducible_with_seed_and_exact_length(client):
    a = client.post("/api/scramble", json={"category": "extension", "seed": 3, "length": 17}).json()
    b = client.post("/api/scramble", json={"category": "extension", "seed": 3, "length": 17}).json()
    assert a["scramble_moves"] == b["scramble_moves"] and a["scramble_length"] == 17


@pytest.mark.parametrize("body", [{"category": "huge"}, {"category": "warmup", "length": 9}, {"length": -1}, {"bogus": 1}])
def test_scramble_validation_errors(client, body):
    r = client.post("/api/scramble", json=body)
    assert r.status_code == 422 and r.json()["error"]["message"]


def test_reset_returns_solved_cube(client):
    d = client.post("/api/reset").json()["cube"]
    assert d["solved"] and d["history"] == []


def benchmark(client, **body):
    return client.post("/api/benchmark", json={"mode": "mock", "wait": True, **body})


def test_mock_benchmark_end_to_end_and_saved(client, tmp_path):
    r = benchmark(client, category="primary", seed=1)
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "completed" and len(run["competitors"]) == 4
    assert 4 <= run["scramble_length"] <= 5
    assert len({c["start_facelets"] for c in run["competitors"]}) == 1
    saved = list((tmp_path / "results").glob("run_*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["run_id"] == run["run_id"]

    res = client.get("/api/results").json()
    assert res["count"] == 1 and res["runs"][0]["run_id"] == run["run_id"]
    assert {row["key"] for row in res["aggregate"]} == {"jev", "luna", "clef", "opus"}
    assert client.get(f"/api/results/{run['run_id']}").json()["run_id"] == run["run_id"]


def test_benchmark_with_given_scramble_subset_and_limit(client):
    run = benchmark(client, category="warmup", moves=["R", "U", "F"], competitors=["opus", "jev"], move_limit=7).json()
    assert run["scramble_moves"] == ["R", "U", "F"] and run["move_limit"] == 7
    assert [c["key"] for c in run["competitors"]] == ["opus", "jev"]
    assert all(c["metrics"]["moves"] <= 7 for c in run["competitors"])


def test_async_benchmark_can_be_polled(client):
    r = client.post("/api/benchmark", json={"mode": "mock", "category": "warmup", "seed": 2})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    import time
    for _ in range(100):
        d = client.get(f"/api/benchmark/{run_id}").json()
        if d["status"] == "completed":
            break
        time.sleep(0.05)
    assert d["status"] == "completed"


@pytest.mark.parametrize("body,code", [
    ({"moves": ["R", "Q"]}, 422),                    # illegal move
    ({"category": "warmup", "moves": ["R"]}, 422),   # too short for category
    ({"category": "warmup", "moves": ["R", "R'", "U", "U'"]}, 422),  # leaves the cube solved
    ({"competitors": ["gpt4"]}, 422), ({"competitors": ["jev", "jev"]}, 422),
    ({"mode": "banana"}, 422), ({"move_limit": 0}, 422), ({"extra": True}, 422),
])
def test_benchmark_validation(client, body, code):
    r = client.post("/api/benchmark", json={"mode": "mock", **body} if "mode" not in body else body)
    assert r.status_code == code and "error" in r.json()


def test_real_mode_without_key_is_a_clear_400_never_fake_results(client, tmp_path):
    r = client.post("/api/benchmark", json={"mode": "real", "category": "warmup"})
    assert r.status_code == 400 and "OPENROUTER_API_KEY" in r.json()["error"]["message"]
    results_dir = tmp_path / "results"
    assert not results_dir.exists() or not list(results_dir.glob("*.json"))


def test_unknown_run_and_results_filters(client):
    assert client.get("/api/benchmark/doesnotexist").status_code == 404
    assert client.get("/api/results/doesnotexist").status_code == 404
    assert client.get("/api/results/../../etc/passwd").status_code in (404, 422)
    assert client.get("/api/results?category=bogus").status_code == 422
    empty = client.get("/api/results").json()
    assert empty["count"] == 0 and empty["runs"] == [] and empty["aggregate"] == []


def test_results_filter_by_mode(client):
    benchmark(client, category="warmup", seed=1)
    assert client.get("/api/results?mode=mock").json()["count"] == 1
    assert client.get("/api/results?mode=real").json()["count"] == 0
    assert client.get("/api/results?category=primary").json()["count"] == 0
