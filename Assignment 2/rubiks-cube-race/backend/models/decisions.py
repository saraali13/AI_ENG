"""Real adapter for models served by OpenRouter's *Decisions API* (Jev, GPT-6 Luna Decisions, Clef).

These models are NOT chat models. OpenRouter documents them as decision/classification models that take a
``state`` plus typed ``questions`` and return calibrated probabilities (chat-completions SDKs do not work):

    POST {base}/alpha/decisions     (default base: https://openrouter.ai/api)
    {"model": "...", "state": {...}, "questions": {"next_move": {"type": "choice", "criteria": {...}}}}

and the move is read from ``answers.next_move.choice``. Every request goes through ``RequestGuard`` (cache ->
budget -> network). An HTTP 200 alone is never trusted: the reply must contain a non-empty ``choice`` string, and
the benchmark still validates it as one of the 18 legal moves before anything touches the cube.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .base import AdapterError, BaseAdapter, ModelSpec, MoveDecision, StepContext
from .guard import RequestGuard, call_meta
from .http import check_served_model, post_json
from .prompts import decisions_question, state_payload

QUESTION_ID = "next_move"
ENDPOINT = "POST /api/alpha/decisions"


class DecisionsAdapter(BaseAdapter):
    mode = "real"
    provider = "openrouter"

    def __init__(self, spec: ModelSpec, *, guard: RequestGuard, api_key: str = "",
                 base_url: str = "https://openrouter.ai/api", timeout: float = 60.0,
                 client: Optional[httpx.AsyncClient] = None, allow_network: bool = True):
        if spec.protocol != "decisions":
            raise ValueError(f"{spec.key} does not use the Decisions API")
        if allow_network and not api_key:
            raise AdapterError("OPENROUTER_API_KEY is not set", fatal=True, kind="auth")
        self.spec = spec
        self._guard = guard
        self._api_key = api_key
        self._allow_network = allow_network
        self.mode = "real" if allow_network else "replay"
        self._url = f"{base_url.rstrip('/')}/alpha/decisions"
        self._timeout = timeout
        self._owns_client = client is None and allow_network
        self._client = client or (httpx.AsyncClient(follow_redirects=False) if allow_network else None)

    def build_payload(self, ctx: StepContext) -> dict:
        return {"model": self.spec.openrouter_id, "state": state_payload(ctx), "questions": decisions_question()}

    def parse_response(self, data: dict) -> MoveDecision:
        served = check_served_model(self.spec.openrouter_id, data)
        answers = data.get("answers")
        answer = answers.get(QUESTION_ID) if isinstance(answers, dict) else None
        if not isinstance(answer, dict):
            raise AdapterError(f"Reply has no answers.{QUESTION_ID} object", kind="bad_response")
        choice = answer.get("choice")
        if not isinstance(choice, str) or not choice.strip():
            raise AdapterError(f"Reply has no usable answers.{QUESTION_ID}.choice", kind="bad_response")
        meta = {"served_model": served, "request_id": data.get("id"), "provider": data.get("provider"),
                "confidence": answer.get("confidence"), "probabilities": answer.get("probabilities"),
                "usage": data.get("usage")}
        return MoveDecision(raw=choice, move=choice.strip(), meta={k: v for k, v in meta.items() if v is not None})

    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        payload = self.build_payload(ctx)

        async def send() -> dict:
            if self._client is None:  # replay adapters never have a client
                raise AdapterError("Network disabled", fatal=True, kind="cache_miss")
            return await post_json(self._client, self._url, api_key=self._api_key, payload=payload,
                                   timeout=self._timeout)

        decision, info = await self._guard.execute(
            ledger_key=self.ledger_key, provider=self.provider, endpoint=ENDPOINT, model_id=self.spec.openrouter_id,
            payload=payload, send=send, parse=self.parse_response, on_reserved=self._mark_reserved,
            allow_network=self._allow_network)
        decision.meta.update(call_meta(info))
        return decision

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
