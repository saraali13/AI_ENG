"""Shared HTTP plumbing for the real OpenRouter adapters (Decisions API and chat completions)."""
from __future__ import annotations

from typing import Optional

import httpx

from .base import AdapterError


def redact(text: str, api_key: str) -> str:
    """Make sure an API key can never leak into an error message or a saved result."""
    if api_key and api_key in text:
        text = text.replace(api_key, "***")
    return text


def _error_text(resp: httpx.Response, api_key: str) -> str:
    try:
        data = resp.json()
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or str(error)
        elif error:
            message = str(error)
        else:
            message = resp.text
    except ValueError:
        message = resp.text
    return redact((message or "").strip().replace("\n", " ")[:300], api_key)


def _retry_after(resp: httpx.Response) -> Optional[float]:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


def error_for_status(resp: httpx.Response, api_key: str) -> AdapterError:
    status = resp.status_code
    detail = _error_text(resp, api_key)
    suffix = f": {detail}" if detail else ""
    if status in (401, 403):
        return AdapterError(f"HTTP {status} authentication/permission failed - check OPENROUTER_API_KEY{suffix}",
                            fatal=True, kind="auth")
    if status == 402:
        return AdapterError(f"HTTP 402 insufficient OpenRouter credits{suffix}", fatal=True, kind="payment")
    if status == 404:
        return AdapterError(
            f"HTTP 404 model or endpoint not available to this account{suffix}", fatal=True, kind="not_found"
        )
    if status == 408:
        return AdapterError(f"HTTP 408 request timeout{suffix}", kind="timeout")
    if status == 429:
        return AdapterError(f"HTTP 429 rate limited{suffix}", kind="rate_limit", retry_after=_retry_after(resp))
    if status >= 500:
        return AdapterError(f"HTTP {status} provider/server error{suffix}", kind="server_error")
    return AdapterError(f"HTTP {status} request rejected{suffix}", fatal=True, kind="bad_request")


async def post_json(client: httpx.AsyncClient, url: str, *, api_key: str, payload: dict, timeout: float) -> dict:
    """POST JSON with a Bearer key. Returns the decoded JSON object or raises ``AdapterError``."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Rubik's Cube Race",
    }
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        raise AdapterError("Request timed out", kind="timeout") from None
    except httpx.HTTPError as exc:
        raise AdapterError(redact(f"Network error ({type(exc).__name__})", api_key), kind="network") from None

    if resp.status_code != 200:
        raise error_for_status(resp, api_key)
    try:
        data = resp.json()
    except ValueError:
        raise AdapterError("API reply was not valid JSON", kind="bad_response") from None
    if not isinstance(data, dict):
        raise AdapterError("API reply was not a JSON object", kind="bad_response")
    if data.get("error"):
        raise AdapterError(f"API returned an error: {_error_text(resp, api_key)}", kind="api_error")
    return data


def check_served_model(requested: str, data: dict) -> Optional[str]:
    """Return the model name the API says it used, after making sure it is the one we asked for.

    A date or version suffix (``typesafe/jev-1.13-20260918``) is fine; a different model is a fatal error,
    because silently benchmarking another model would give wrong results.
    """
    served = data.get("model")
    if isinstance(served, str) and served and not served.startswith(requested):
        raise AdapterError(
            f"Model mismatch: requested {requested!r} but the API served {served!r}", fatal=True,
            kind="model_mismatch",
        )
    return served if isinstance(served, str) else None
