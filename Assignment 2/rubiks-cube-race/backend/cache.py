"""Persistent response cache (JSON file ``state/response_cache.json``).

* Key = SHA-256 of {provider, endpoint, model id, the COMPLETE effective request payload}. Two requests share an
  entry only if every field that could change the answer (prompt, state, questions, max_tokens, ...) is identical.
  The API key is never part of the key or of the stored entry.
* Only usable responses are stored (see ``guard.py``): empty, reasoning-only, malformed or illegal-move replies are
  not cached, so a bad reply never becomes a permanent answer.
* The file survives restarts and is never cleared by the application.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from .persist import locked, write_json_atomic

log = logging.getLogger(__name__)


class ResponseCache:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.path = self.directory / "response_cache.json"

    @staticmethod
    def make_key(provider: str, endpoint: str, model_id: str, payload: dict) -> str:
        canonical = json.dumps({"provider": provider, "endpoint": endpoint, "model": model_id, "payload": payload},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            if isinstance(entries, dict):
                return entries
            raise ValueError("bad structure")
        except (OSError, ValueError):
            quarantine = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            try:
                os.replace(self.path, quarantine)
                log.warning("Response cache was unreadable; moved to %s and starting an empty cache", quarantine.name)
            except OSError:
                pass
            return {}

    def get(self, key: str) -> Optional[dict]:
        with locked(self.path):
            entry = self._load().get(key)
        return entry if isinstance(entry, dict) else None

    def put(self, key: str, entry: dict) -> None:
        with locked(self.path):
            entries = self._load()
            entries[key] = entry
            write_json_atomic(self.path, {"version": 1, "entries": entries})

    def stats(self) -> dict:
        with locked(self.path):
            entries = self._load()
        by_model: dict[str, int] = {}
        for e in entries.values():
            by_model[e.get("model", "?")] = by_model.get(e.get("model", "?"), 0) + 1
        return {"entries": len(entries), "by_model": by_model, "persistent_file": f"{self.directory.name}/{self.path.name}"}
