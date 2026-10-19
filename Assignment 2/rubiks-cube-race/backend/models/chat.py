"""Real adapter for ordinary chat models on OpenRouter (Claude Opus 5.5).

    POST {base}/v1/chat/completions   (default base: https://openrouter.ai/api)

Only ``model``, ``messages`` and ``max_tokens`` are sent. ``temperature`` is deliberately NOT sent: Claude
Opus 5.5 uses always-on adaptive thinking, and a rejected parameter would waste one of Claude's 5 requests.
Content that is empty (reasoning-only, truncated by ``finish_reason: length``) is reported as an invalid reply;
reasoning text is never turned into a move. Every request goes through ``RequestGuard``.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from .base import AdapterError, BaseAdapter, ModelSpec, MoveDecision, StepContext
from .guard import RequestGuard, call_meta
from .http import check_served_model, post_json
from .parsing import extract_move_candidate
from .prompts import CHAT_SYSTEM_PROMPT, chat_user_prompt

ENDPOINT = "POST /api/v1/chat/completions"

# Strip <thinking>…</thinking> blocks that extended-thinking models embed inline in the content string.
# We never use the reasoning chain as a move; only the text that follows the closing tag matters.
_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)

# Block types that carry reasoning rather than the final answer; never used as a move.
_REASONING_TYPES = {"thinking", "redacted_thinking"}


def _message_text(message: object) -> str:
    """Return only the visible text of a chat message, stripping all forms of reasoning.

    OpenRouter / Anthropic extended-thinking models can return reasoning in three ways:
      1. A list of content blocks with ``"type": "thinking"`` – skipped explicitly.
      2. A string with ``<thinking>…</thinking>`` tags embedded inline – stripped by regex.
      3. A separate ``message.reasoning`` / ``message.reasoning_content`` field – already ignored
         (we only read ``message.content``).
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        # Strip inline <thinking> blocks before any other processing.
        return _THINKING_RE.sub("", content).strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                # Bare strings in a list are rare but possible; strip thinking tags.
                parts.append(_THINKING_RE.sub("", part))
            elif isinstance(part, dict):
                if part.get("type") in _REASONING_TYPES:
                    continue  # skip reasoning blocks entirely
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


class ChatAdapter(BaseAdapter):
    mode = "real"
    provider = "openrouter"

    def __init__(self, spec: ModelSpec, *, guard: RequestGuard, api_key: str = "",
                 base_url: str = "https://openrouter.ai/api", timeout: float = 60.0, max_tokens: int = 4096,
                 client: Optional[httpx.AsyncClient] = None, allow_network: bool = True,
                 ledger_key: Optional[str] = None, check_model: bool = True):
        if spec.protocol != "chat":
            raise ValueError(f"{spec.key} does not use chat completions")
        if allow_network and not api_key:
            raise AdapterError("OPENROUTER_API_KEY is not set", fatal=True, kind="auth")
        self.spec = spec
        self._guard = guard
        self._api_key = api_key
        self._allow_network = allow_network
        self.mode = "real" if allow_network else "replay"
        self._ledger_key = ledger_key or spec.key
        self._check_model = check_model  # False only for router models (the served model legitimately differs)
        self._url = f"{base_url.rstrip('/')}/v1/chat/completions"
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._owns_client = client is None and allow_network
        self._client = client or (httpx.AsyncClient(follow_redirects=False) if allow_network else None)

    @property
    def ledger_key(self) -> str:
        return self._ledger_key

    

    def build_payload(self, ctx: StepContext) -> dict:
        return {
         "model": self.spec.openrouter_id,
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": chat_user_prompt(ctx)},
        ],
            "max_tokens": self._max_tokens,
            "reasoning": {"effort": "low"},
    }



    def parse_response(self, data: dict) -> MoveDecision:
        served = check_served_model(self.spec.openrouter_id, data) if self._check_model else data.get("model")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise AdapterError("Reply has no choices", kind="bad_response")
        message = choices[0].get("message")
        text = _message_text(message)
        meta = {"served_model": served, "request_id": data.get("id"),
                "finish_reason": choices[0].get("finish_reason"), "usage": data.get("usage")}
        if not text.strip():
            has_reasoning = isinstance(message, dict) and bool(message.get("reasoning") or message.get("reasoning_details"))
            meta["note"] = ("Empty content (reasoning only; reasoning is never used as a move)" if has_reasoning
                            else f"Empty content (finish_reason={choices[0].get('finish_reason')})")
            return MoveDecision(raw="", move=None, meta={k: v for k, v in meta.items() if v is not None})
        if choices[0].get("finish_reason") == "length":
            meta["note"] = "Reply was cut off (finish_reason=length); a truncated reply is never used as a move"
            return MoveDecision(raw=text, move=None, meta={k: v for k, v in meta.items() if v is not None})
        return MoveDecision(raw=text, move=extract_move_candidate(text),
                            meta={k: v for k, v in meta.items() if v is not None})

    async def choose_move(self, ctx: StepContext) -> MoveDecision:
        payload = self.build_payload(ctx)

        async def send() -> dict:
            if self._client is None:
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
