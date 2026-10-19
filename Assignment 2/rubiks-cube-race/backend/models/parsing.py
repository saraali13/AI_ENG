"""Pull a single move candidate out of a model's reply.

The result is only a *candidate*: the benchmark runner validates it with ``validate_move`` before
anything touches the cube. We prefer returning a clearly illegal candidate (``"U3"``) over ``None`` so the
recorded error says exactly what the model answered.
"""
from __future__ import annotations

import json
import re
from typing import Optional

_TOKEN = r"[URFDLB](?:2'|'2|2|'|\u2019|\u2032)?"
_WHOLE = re.compile(rf"[\"`']?\s*({_TOKEN})\s*[\"`']?[.!]?")
_AFTER_KEYWORD = re.compile(rf"(?i:\bmove\b)\s*(?:is|:|=|->)\s*[\"`']?\s*({_TOKEN})(?![A-Za-z0-9])")
_FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")
_JSON_KEYS = ("move", "next_move", "answer", "choice")


def _first_json_object(text: str) -> Optional[str]:
    """Return the first *valid* JSON object in text, using the decoder's own length tracking.

    The old approach (first-'{' … last-'}') broke when reasoning text from extended-thinking
    models contained curly braces from the cube-state JSON that was echoed in the prompt.
    ``raw_decode`` advances past exactly as many bytes as belong to the object, so it
    correctly finds ``{"move": "R"}`` even when ``{…}`` appears earlier in the string.
    """
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        idx = text.find("{", i)
        if idx == -1:
            break
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                return json.dumps(obj)  # canonical form for downstream json.loads
        except ValueError:
            pass
        i = idx + 1
    return None


def extract_move_candidate(text: object) -> Optional[str]:
    """Return the move a reply proposes, or ``None`` if no single candidate can be found."""
    if not isinstance(text, str):
        return None
    cleaned = _FENCE.sub("", text.strip()).strip()
    if not cleaned:
        return None

    for blob in (cleaned, _first_json_object(cleaned)):
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if isinstance(data, str):
            return data.strip().strip("\"`") or None
        if isinstance(data, dict):
            for key in _JSON_KEYS:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().strip("\"`")

    whole = _WHOLE.fullmatch(cleaned)
    if whole:
        return whole.group(1)
    keyword = _AFTER_KEYWORD.search(cleaned)
    if keyword:
        return keyword.group(1)
    # A short single "word" such as U3 or Rw: hand it back so validation can reject it by name.
    if " " not in cleaned and "\n" not in cleaned and len(cleaned) <= 6:
        return cleaned
    return None
