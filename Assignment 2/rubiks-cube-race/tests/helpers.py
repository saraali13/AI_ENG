"""Shared test helpers: a fake OpenRouter/Ollama server (httpx.MockTransport) and guard/limits builders."""
import asyncio
import json

import httpx

from backend.budget import BudgetLedger, Limits
from backend.cache import ResponseCache
from backend.config import Settings
from backend.cube_engine import MOVES, CubeEngine
from backend.benchmark import CompetitorRun, run_competitor
from backend.models.guard import RequestGuard
from backend.optimal import find_optimal

TEST_KEY = "TEST-KEY-not-a-real-key-123"


def make_guard(directory, global_limit=65, default_model_limit=20, opus=5):
    limits = Limits(global_limit=global_limit, default_model_limit=default_model_limit, per_model={"opus": opus})
    return RequestGuard(BudgetLedger(directory, limits), ResponseCache(directory))


def settings(**kw):
    base = dict(max_consecutive_failures=3, live_max_consecutive_failures=2, retry_backoff_seconds=0,
                step_timeout_seconds=5)
    base.update(kw)
    return Settings(**base)


def decisions_body(model, choice, cost=0.00002):
    usage = {"input_tokens": 100, "output_tokens": 5}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "gen-1", "model": model + "-20260918", "provider": "X", "usage": usage,
            "answers": {"next_move": {"type": "choice", "choice": choice, "confidence": 0.7,
                                      "probabilities": {choice: 0.7}}}}


def chat_body(model, content, cost=0.01, finish="stop", extra_message=None):
    message = {"role": "assistant", "content": content, **(extra_message or {})}
    usage = {"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "gen-2", "model": model, "choices": [{"message": message, "finish_reason": finish}], "usage": usage}


def solver_move(facelets, history=()):
    """What a perfect player would say for this state."""
    return find_optimal(facelets).moves[0]


def never_solving(facelets, history):
    """U, U', U, ... : legal moves that can never solve a cube that is not already U-solvable."""
    return "U" if len(history) % 2 == 0 else "U'"


class FakeAPI:
    """Fake server for both OpenRouter endpoints and Ollama. Records every request it receives."""

    def __init__(self, chooser=solver_move, cost=0.00002, delay=0.0):
        self.chooser, self.cost, self.delay = chooser, cost, delay
        self.requests = []   # (path, host, model)
        self.fail_with = None  # (status, body) or Exception
        self.raw_override = None  # callable(request)->httpx.Response

    def _state(self, path, body):
        if path.endswith("/alpha/decisions"):
            return body["state"]["facelets"], body["state"]["moves_made_so_far"]
        text = body["messages"][1]["content"]
        data = json.loads(text.split("Current cube state:\n")[1].split("\n\nChoose")[0])
        return data["facelets"], data["moves_made_so_far"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, request.url.host, body.get("model")))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.fail_with, Exception):
            raise self.fail_with
        if self.fail_with:
            return httpx.Response(self.fail_with[0], json=self.fail_with[1])
        if self.raw_override:
            return self.raw_override(request)
        facelets, history = self._state(path, body)
        move = self.chooser(facelets, history)
        model = body["model"]
        if path.endswith("/alpha/decisions"):
            return httpx.Response(200, json=decisions_body(model, move, self.cost))
        if path.endswith("/chat/completions"):
            return httpx.Response(200, json=chat_body(model, json.dumps({"move": move}), cost=self.cost))
        if path.endswith("/api/chat"):  # Ollama
            return httpx.Response(200, json={"model": model, "message": {"role": "assistant", "content": json.dumps({"move": move})},
                                             "done": True})
        return httpx.Response(404, json={"error": {"message": "unknown path"}})

    @property
    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    @property
    def openrouter_requests(self):
        return [r for r in self.requests if "openrouter.ai" in r[1]]


def play(adapter, scramble, limit=20, s=None):
    start = CubeEngine.from_scramble(scramble)
    comp = CompetitorRun(key=adapter.key, name=adapter.display_name, openrouter_id=adapter.spec.openrouter_id,
                         provider="p", protocol=adapter.spec.protocol, mode=adapter.mode)
    asyncio.run(run_competitor(comp, adapter, start, scramble=scramble, move_limit=limit, settings=s or settings()))
    return comp
