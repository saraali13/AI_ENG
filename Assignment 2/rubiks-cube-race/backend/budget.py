"""Persistent OpenRouter request budget (the "ledger").

The professor's limits, enforced HERE in the backend (not in the browser, not in the docs):

* Overall target: at most 65 actual OpenRouter requests across the four models (default enforced limit).
* Hard ceiling: 100 requests. This number is a constant; no setting can raise the limit above it.
* Claude (ledger key ``opus``): at most 5 actual requests for the whole experiment. Also a constant ceiling.

How it cannot be bypassed:

* ``reserve()`` increments the counter and writes it to disk (fsync) BEFORE any network attempt. A request that
  later fails, times out, is cancelled or crashes the process is therefore still counted.
* The counters live in ``state/budget_ledger.json`` (+ a backup copy). They are never reset by restarting the
  app, by the Reset button, or by clearing the response cache.
* Every read-modify-write runs under an inter-process file lock, so concurrent requests cannot both pass the
  same check.
* If the ledger file exists but cannot be read, the ledger FAILS CLOSED: live requests are refused instead of
  silently starting again from zero.
* Cache hits are recorded separately and never count as API requests.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .persist import locked, write_json_atomic

log = logging.getLogger(__name__)

TARGET_GLOBAL = 65          # the professor's overall target
HARD_CEILING = 100          # absolute ceiling, cannot be raised by configuration
CLAUDE_KEY = "opus"         # ledger key of Claude Opus 5.5
CLAUDE_HARD_LIMIT = 5       # absolute ceiling for Claude, cannot be raised by configuration
MAX_EVENTS = 300


class LedgerError(RuntimeError):
    """The ledger file is unreadable. Live requests are refused until it is fixed."""


class BudgetExceeded(RuntimeError):
    def __init__(self, scope: str, message: str):
        super().__init__(message)
        self.scope = scope
        self.message = message


@dataclass(frozen=True)
class Limits:
    global_limit: int = TARGET_GLOBAL
    default_model_limit: int = 20
    per_model: dict = field(default_factory=dict)

    @property
    def effective_global(self) -> int:
        return max(0, min(int(self.global_limit), HARD_CEILING))

    def model_limit(self, key: str) -> int:
        limit = int(self.per_model.get(key, self.default_model_limit))
        if key == CLAUDE_KEY:
            limit = min(limit if key in self.per_model else CLAUDE_HARD_LIMIT, CLAUDE_HARD_LIMIT)
        return max(0, limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh() -> dict:
    return {
        "version": 1,
        "created_at": _now(),
        "openrouter": {"requests": 0, "succeeded": 0, "failed": 0, "by_model": {}, "cost_usd": 0.0,
                       "cost_reported_requests": 0, "input_tokens": 0, "output_tokens": 0},
        "local": {"requests": 0, "by_model": {}},
        "cache_hits": {"total": 0, "by_model": {}},
        "events": [],
    }


def _normalize(data: dict) -> dict:
    base = _fresh()
    for section in ("openrouter", "local", "cache_hits"):
        merged = dict(base[section])
        merged.update(data.get(section) or {})
        data[section] = merged
    data.setdefault("version", 1)
    data.setdefault("created_at", base["created_at"])
    data.setdefault("events", [])
    return data


class BudgetLedger:
    def __init__(self, directory: Path | str, limits: Optional[Limits] = None):
        self.directory = Path(directory)
        self.path = self.directory / "budget_ledger.json"
        self.backup = self.directory / "budget_ledger.backup.json"
        self.limits = limits or Limits()

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _read(path: Path) -> tuple[Optional[dict], str]:
        if not path.exists():
            return None, "missing"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("openrouter"), dict):
                return None, "corrupt"
            return data, "ok"
        except (OSError, ValueError):
            return None, "corrupt"

    def _load(self) -> dict:
        main, main_state = self._read(self.path)
        back, back_state = self._read(self.backup)
        good = [d for d in (main, back) if d is not None]
        if good:  # if the two copies disagree (crash between writes) keep the one with MORE requests
            return _normalize(max(good, key=lambda d: int(d["openrouter"].get("requests", 0))))
        if "corrupt" in (main_state, back_state):
            raise LedgerError(
                f"The request ledger in {self.directory} is unreadable, so live OpenRouter requests are blocked "
                "to protect the API budget. Restore state/budget_ledger.json from a backup (do not delete it)."
            )
        return _fresh()

    def _save(self, data: dict) -> None:
        data["events"] = data["events"][-MAX_EVENTS:]
        write_json_atomic(self.backup, data)   # backup first: a crash between the two never loses a count
        write_json_atomic(self.path, data)

    @staticmethod
    def _event(data: dict, etype: str, model: str, **extra) -> None:
        # NOTE: the first parameter must not be called ``kind``: finish() passes a ``kind=`` extra (the error kind),
        # and a clash raised TypeError, which hid the real API error and lost the failure record.
        data["events"].append({"t": _now(), "type": etype, "model": model, **extra})

    # ------------------------------------------------------------------ operations
    def reserve(self, model_key: str, *, provider: str = "openrouter", model_id: str = "") -> int:
        """Count one request BEFORE it is sent. Raises ``BudgetExceeded`` (nothing is counted) when over a limit."""
        with locked(self.path):
            data = self._load()
            if provider != "openrouter":  # local models (Ollama) are tracked but never limited here
                local = data["local"]
                local["requests"] += 1
                local["by_model"][model_key] = local["by_model"].get(model_key, 0) + 1
                self._event(data, "local_request", model_key)
                self._save(data)
                return local["requests"]

            o = data["openrouter"]
            g, m = self.limits.effective_global, self.limits.model_limit(model_key)
            used_m = o["by_model"].get(model_key, {}).get("requests", 0)
            if o["requests"] >= g:
                raise BudgetExceeded("global", f"Global OpenRouter request limit reached ({o['requests']}/{g}). "
                                               "No more live requests will be sent.")
            if used_m >= m:
                raise BudgetExceeded("model", f"Request limit for '{model_key}' reached ({used_m}/{m}). "
                                              "No more live requests will be sent for this model.")
            o["requests"] += 1
            bm = o["by_model"].setdefault(model_key, {"requests": 0, "succeeded": 0, "failed": 0})
            bm["requests"] += 1
            self._event(data, "reserved", model_key, n=o["requests"], model_id=model_id)
            self._save(data)
            return o["requests"]

    def finish(self, model_key: str, *, ok: bool, kind: Optional[str] = None, cost: Optional[float] = None,
               usage: Optional[dict] = None, provider: str = "openrouter") -> None:
        """Record how a counted request ended (success/failure, cost, tokens). Never changes the request count."""
        if provider != "openrouter":
            return
        with locked(self.path):
            data = self._load()
            o = data["openrouter"]
            bm = o["by_model"].setdefault(model_key, {"requests": 0, "succeeded": 0, "failed": 0})
            if ok:
                o["succeeded"] += 1
                bm["succeeded"] += 1
            else:
                o["failed"] += 1
                bm["failed"] += 1
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
                o["cost_usd"] = round(float(o["cost_usd"]) + float(cost), 10)
                o["cost_reported_requests"] += 1
            if isinstance(usage, dict):
                for src, dst in (("input_tokens", "input_tokens"), ("prompt_tokens", "input_tokens"),
                                 ("output_tokens", "output_tokens"), ("completion_tokens", "output_tokens")):
                    v = usage.get(src)
                    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                        o[dst] += v
            self._event(data, "ok" if ok else "failed", model_key, **({"kind": kind} if kind else {}))
            self._save(data)

    def record_cache_hit(self, model_key: str) -> None:
        with locked(self.path):
            data = self._load()
            c = data["cache_hits"]
            c["total"] += 1
            c["by_model"][model_key] = c["by_model"].get(model_key, 0) + 1
            self._save(data)

    # ------------------------------------------------------------------ read-only view
    def status(self) -> dict:
        lim = self.limits
        try:
            with locked(self.path):
                data = self._load()
            healthy, error = True, None
        except (LedgerError, TimeoutError) as exc:
            data, healthy, error = _fresh(), False, str(exc)
        o = data["openrouter"]
        keys = sorted(set(o["by_model"]) | {"jev", "luna", "clef", CLAUDE_KEY})
        by_model = {}
        for k in keys:
            used = o["by_model"].get(k, {}).get("requests", 0)
            by_model[k] = {**{"requests": 0, "succeeded": 0, "failed": 0}, **o["by_model"].get(k, {}),
                           "limit": lim.model_limit(k), "remaining": max(0, min(lim.model_limit(k) - used,
                                                                              lim.effective_global - o["requests"]))}
        return {
            "healthy": healthy,
            "error": error,
            "persistent_file": f"{self.directory.name}/{self.path.name}",
            "limits": {"global_limit": lim.effective_global, "global_target": TARGET_GLOBAL,
                       "hard_ceiling": HARD_CEILING, "claude_limit": lim.model_limit(CLAUDE_KEY),
                       "default_model_limit": lim.default_model_limit},
            "openrouter": {"requests": o["requests"], "succeeded": o["succeeded"], "failed": o["failed"],
                           "unfinished": max(0, o["requests"] - o["succeeded"] - o["failed"]),
                           "cost_usd": o["cost_usd"], "cost_reported_requests": o["cost_reported_requests"],
                           "input_tokens": o["input_tokens"], "output_tokens": o["output_tokens"],
                           "by_model": by_model},
            "remaining_global": max(0, lim.effective_global - o["requests"]),
            "cache_hits": data["cache_hits"],
            "local": data["local"],
            "created_at": data.get("created_at"),
        }
