"""Metric calculations. Every function works on plain dicts (the saved JSON form), so the same code
computes metrics for a live run and for runs loaded back from ``results/``.

Definitions (also in the README):

* average API latency  - mean wall-clock time of LIVE calls that returned a reply (valid or invalid move).
                         Cache hits are NOT included (they are reported separately as cache latency).
                         Calls that failed with a timeout/network/API error are excluded and counted as errors.
* API calls            - live requests attempted (a failed or timed-out attempt still counts). Cache hits do not.
* cache hits           - replies served from the persistent cache (no network, no budget).
* cost                 - sum of provider-reported costs only. Never estimated. ``None`` if nothing was reported.
* total solve time     - wall-clock time from a competitor's first request until it solved the cube,
                         hit the move limit or failed. For failed runs it is the time spent, not a "solve time".
* move count           - number of valid moves actually applied to the cube.
* success rate         - solved runs / runs.
* optimality gap       - moves used minus the PROVEN shortest solution length. Only defined for solved runs
                         whose exact optimum is known; otherwise ``None`` (shown as N/A).
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Iterable, Optional


def average_latency_ms(steps: Iterable[dict]) -> Optional[float]:
    """Average LIVE latency. Steps answered from the cache are excluded; their time is ``average_cache_latency_ms``."""
    values = [s["latency_ms"] for s in steps
              if s.get("responded") and s.get("latency_ms") is not None and s.get("source") != "cache"]
    return round(mean(values), 2) if values else None


def average_cache_latency_ms(steps: Iterable[dict]) -> Optional[float]:
    values = [s["cache_latency_ms"] for s in steps if s.get("source") == "cache" and s.get("cache_latency_ms") is not None]
    return round(mean(values), 3) if values else None


def api_call_count(steps: Iterable[dict]) -> int:
    return sum(int(s.get("api_calls") or 0) for s in steps)


def cache_hit_count(steps: Iterable[dict]) -> int:
    return sum(1 for s in steps if s.get("cache_hit"))


def total_cost_usd(steps: Iterable[dict]) -> tuple[Optional[float], int]:
    """(sum of provider-reported costs or None, number of LIVE calls whose cost was not reported)."""
    steps = list(steps)
    costs = [s["cost_usd"] for s in steps if s.get("cost_usd") is not None]
    unreported = sum(1 for s in steps if int(s.get("api_calls") or 0) > 0 and s.get("cost_usd") is None
                     and s.get("source") not in ("mock",))
    return (round(sum(costs), 10) if costs else None), unreported


def move_count(steps: Iterable[dict]) -> int:
    return sum(1 for s in steps if s.get("applied"))


def invalid_response_count(steps: Iterable[dict]) -> int:
    return sum(1 for s in steps if s.get("error_kind") == "invalid_response")


def api_error_count(steps: Iterable[dict]) -> int:
    return sum(1 for s in steps if s.get("error_kind") not in (None, "invalid_response"))


def success_rate(outcomes: Iterable[bool]) -> Optional[float]:
    """Fraction (0-1) of successful runs, ``None`` when there are no runs."""
    outcomes = list(outcomes)
    if not outcomes:
        return None
    return round(sum(1 for o in outcomes if o) / len(outcomes), 4)


def optimality_gap(moves_used: int, optimal_length: Optional[int], solved: bool) -> Optional[int]:
    """Extra moves compared with the proven optimum; ``None`` unless solved AND the optimum is known."""
    if not solved or optimal_length is None:
        return None
    return moves_used - optimal_length


def competitor_metrics(competitor: dict, optimal_length: Optional[int]) -> dict:
    """Metrics for one competitor in one run (``competitor`` is its serialised run dict)."""
    steps = competitor.get("steps", [])
    solved = competitor.get("status") == "solved"
    moves = move_count(steps)
    cost, unreported = total_cost_usd(steps)
    return {
        "solved": solved,
        "outcome": competitor.get("outcome"),
        "moves": moves,
        "api_calls": api_call_count(steps),
        "cache_hits": cache_hit_count(steps),
        "cost_usd": cost,
        "cost_unreported_calls": unreported,
        "avg_cache_latency_ms": average_cache_latency_ms(steps),
        "avg_latency_ms": average_latency_ms(steps),
        "total_time_ms": competitor.get("total_time_ms"),
        "invalid_responses": invalid_response_count(steps),
        "api_errors": api_error_count(steps),
        "attempts": len(steps),
        "optimality_gap": optimality_gap(moves, optimal_length, solved),
    }


def summarize_run(run: dict) -> dict:
    """Compact version of a run (no per-step data) for history tables and lists."""
    optimal = (run.get("optimal") or {}).get("length")
    competitors = []
    for c in run.get("competitors", []):
        m = c.get("metrics") or competitor_metrics(c, optimal)
        competitors.append(
            {
                "key": c["key"],
                "name": c["name"],
                "mode": c.get("mode"),
                "status": c.get("status"),
                "failure_reason": c.get("failure_reason"),
                **{k: m.get(k) for k in ("solved", "outcome", "moves", "avg_latency_ms", "avg_cache_latency_ms",
                                         "total_time_ms", "invalid_responses", "api_errors", "optimality_gap",
                                         "api_calls", "cache_hits", "cost_usd")},
            }
        )
    return {
        "run_id": run["run_id"],
        "created_at": run.get("created_at"),
        "completed_at": run.get("completed_at"),
        "category": run.get("category"),
        "mode": run.get("mode"),
        "status": run.get("status"),
        "scramble": run.get("scramble"),
        "scramble_length": run.get("scramble_length"),
        "move_limit": run.get("move_limit"),
        "optimal_length": optimal,
        "competitors": competitors,
    }


def aggregate_runs(runs: Iterable[dict], category: Optional[str] = None) -> list[dict]:
    """Per (mode, competitor) totals over many finished runs; optionally only one scramble category.

    Latency is weighted by number of replies (total latency / total replies), not an average of averages.
    Mock and real results are never mixed together.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    order: dict[str, int] = {}
    for run in runs:
        if run.get("status") != "completed":
            continue
        if category and run.get("category") != category:
            continue
        if run.get("profile") == "free_test":  # plumbing tests with a stand-in model are not final results
            continue
        optimal = (run.get("optimal") or {}).get("length")
        for index, c in enumerate(run.get("competitors", [])):
            metrics = c.get("metrics") or competitor_metrics(c, optimal)
            latencies = [s["latency_ms"] for s in c.get("steps", [])
                         if s.get("responded") and s.get("latency_ms") is not None]
            groups[(c.get("mode") or run.get("mode") or "unknown", c["key"])].append(
                {"metrics": metrics, "latencies": latencies}
            )
            names[c["key"]] = c["name"]
            order.setdefault(c["key"], index)

    rows = []
    for (mode, key), entries in groups.items():
        solved_entries = [e["metrics"] for e in entries if e["metrics"]["solved"]]
        all_latencies = [v for e in entries for v in e["latencies"]]
        gaps = [e["metrics"]["optimality_gap"] for e in entries if e["metrics"]["optimality_gap"] is not None]
        rows.append(
            {
                "key": key,
                "name": names[key],
                "mode": mode,
                "category": category or "all",
                "runs": len(entries),
                "solved": len(solved_entries),
                "success_rate": success_rate(e["metrics"]["solved"] for e in entries),
                "avg_latency_ms": round(mean(all_latencies), 2) if all_latencies else None,
                "avg_moves_when_solved": round(mean(m["moves"] for m in solved_entries), 2) if solved_entries else None,
                "avg_solve_time_ms": round(mean(m["total_time_ms"] for m in solved_entries
                                                if m["total_time_ms"] is not None), 2)
                if any(m["total_time_ms"] is not None for m in solved_entries) else None,
                "avg_optimality_gap": round(mean(gaps), 2) if gaps else None,
                "invalid_responses": sum(e["metrics"]["invalid_responses"] for e in entries),
                "api_errors": sum(e["metrics"]["api_errors"] for e in entries),
            }
        )
    rows.sort(key=lambda r: (r["mode"], order.get(r["key"], 99)))
    return rows
