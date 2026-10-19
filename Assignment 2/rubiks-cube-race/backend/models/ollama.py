"""Local test adapter: any model served by Ollama (http://localhost:11434 by default).

Used for free, unlimited local experiments. It can NEVER reach OpenRouter: the base URL is refused if it points at
openrouter.ai, and requests are tagged ``provider="ollama"`` so they are tracked in a separate local counter
and never touch the OpenRouter budget. The four assignment models are not available in Ollama, so in this mode
each competitor slot is filled by a local stand-in model (clearly labelled "ollama" everywhere).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from .base import AdapterError, BaseAdapter, ModelSpec, MoveDecision, StepContext
from .guard import RequestGuard, call_meta
from .parsing import extract_move_candidate
from .prompts import CHAT_SYSTEM_PROMPT, chat_user_prompt

ENDPOINT = "POST /api/chat"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OllamaAdapter(BaseAdapter):
    mode = "ollama"
    provider = "ollama"

    def __init__(self, spec: ModelSpec, *, guard: RequestGuard, model_tag: str,
                 base_url: str = "http://localhost:11434", timeout: float = 120.0, max_tokens: int = 512,
                 client: Optional[httpx.AsyncClient] = None, allow_network: bool = True):
        host = (urlparse(base_url).hostname or "").lower()
        if "openrouter" in host:
            raise ValueError("OLLAMA_BASE_URL must be a local Ollama server, not OpenRouter")
        self.spec = spec
        self._guard = guard
        self._tag = model_tag
        self._allow_network = allow_network
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=False)

    def build_payload(self, ctx: StepContext) -> dict:
        return {
            "model": self._tag,
            "messages": [{"role": "system", "content": CHAT_SYSTEM_PROMPT},
                         {"role": "user", "content": chat_user_prompt(ctx)}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": self._max_tokens},
        }

    def parse_response(self, data: dict) -> MoveDecision:
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise AdapterError("Ollama reply has no message.content", kind="bad_response")
        text = _THINK.sub("", content).strip()
        meta = {"served_model": data.get("model"), "done_reason": data.get("done_reason")}
        if not text:
            meta["note"] = "Empty content (reasoning only or truncated; reasoning is never used as a move)"
            return MoveDecision(raw="", move=None, meta={k: v for k, v in meta.items() if v is not None})
        return MoveDecision(raw=text, move=extract_move_candidate(text), meta={k: v for k, v in meta.items() if v is not None})

    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        payload = self.build_payload(ctx)

        async def send() -> dict:
            try:
                resp = await self._client.post(self._url, json=payload, timeout=self._timeout)
            except httpx.TimeoutException:
                raise AdapterError("Ollama request timed out", kind="timeout") from None
            except httpx.HTTPError:
                raise AdapterError(f"Cannot reach Ollama at {self._url}. Is it running? (ollama serve)",
                                   fatal=True, kind="network") from None
            if resp.status_code == 404:
                raise AdapterError(f"Ollama does not have model '{self._tag}'. Run: ollama pull {self._tag}",
                                   fatal=True, kind="not_found")
            if resp.status_code != 200:
                raise AdapterError(f"Ollama HTTP {resp.status_code}", kind="server_error")
            try:
                data = resp.json()
            except ValueError:
                raise AdapterError("Ollama reply was not valid JSON", kind="bad_response") from None
            if not isinstance(data, dict):
                raise AdapterError("Ollama reply was not a JSON object", kind="bad_response")
            return data

        decision, info = await self._guard.execute(
            ledger_key=self.spec.key, provider=self.provider, endpoint=ENDPOINT, model_id=self._tag, payload=payload,
            send=send, parse=self.parse_response, on_reserved=self._mark_reserved, allow_network=self._allow_network)
        decision.meta.update(call_meta(info))
        return decision

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
