from backend.metrics import (
    aggregate_runs, api_error_count, average_latency_ms, competitor_metrics, invalid_response_count,
    move_count, optimality_gap, success_rate,
)

STEPS = [
    {"responded": True, "latency_ms": 100, "applied": True},
    {"responded": True, "latency_ms": 300, "applied": False, "error_kind": "invalid_response"},
    {"responded": False, "latency_ms": 9000, "applied": False, "error_kind": "timeout"},
    {"responded": True, "latency_ms": 200, "applied": True},
]


def test_average_latency_only_counts_replies():
    assert average_latency_ms(STEPS) == 200.0
    assert average_latency_ms([]) is None


def test_counts():
    assert move_count(STEPS) == 2
    assert invalid_response_count(STEPS) == 1
    assert api_error_count(STEPS) == 1


def test_success_rate():
    assert success_rate([True, False, True, True]) == 0.75
    assert success_rate([]) is None


def test_optimality_gap_only_when_solved_and_known():
    assert optimality_gap(7, 5, True) == 2
    assert optimality_gap(5, 5, True) == 0
    assert optimality_gap(7, None, True) is None
    assert optimality_gap(30, 5, False) is None


def test_competitor_metrics():
    m = competitor_metrics({"steps": STEPS, "status": "solved", "total_time_ms": 1234.5}, 1)
    assert m["moves"] == 2 and m["solved"] and m["optimality_gap"] == 1 and m["total_time_ms"] == 1234.5
    assert m["attempts"] == 4


def _run(mode, category, status, lat):
    steps = [{"responded": True, "latency_ms": lat, "applied": True}]
    return {"status": "completed", "mode": mode, "category": category, "optimal": {"length": None},
            "competitors": [{"key": "jev", "name": "Jev", "mode": mode, "status": status, "steps": steps, "total_time_ms": 10}]}


def test_aggregate_weights_and_separates_modes():
    runs = [_run("mock", "primary", "solved", 100), _run("mock", "primary", "failed", 300), _run("real", "primary", "solved", 5)]
    rows = {r["mode"]: r for r in aggregate_runs(runs)}
    assert rows["mock"]["runs"] == 2 and rows["mock"]["success_rate"] == 0.5 and rows["mock"]["avg_latency_ms"] == 200
    assert rows["real"]["runs"] == 1 and rows["real"]["avg_latency_ms"] == 5
    assert aggregate_runs(runs, "warmup") == []
