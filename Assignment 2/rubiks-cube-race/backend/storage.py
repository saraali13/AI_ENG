"""Saves each finished benchmark run as a JSON file in ``results/`` and reads them back."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResultStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, run: dict) -> Path:
        """Write the run atomically (temp file, then rename) so a crash never leaves half a file."""
        self._ensure()
        stamp = datetime.fromisoformat(run["created_at"]).strftime("%Y%m%d_%H%M%S")
        path = self.directory / f"run_{stamp}_{run['run_id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(run, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def list(self) -> list[dict]:
        """All saved runs, newest first. Unreadable files are skipped, never fatal."""
        if not self.directory.exists():
            return []
        runs = []
        for path in sorted(self.directory.glob("run_*.json"), reverse=True):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                log.warning("Skipping unreadable result file %s: %s", path.name, exc)
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs

    def get(self, run_id: str) -> Optional[dict]:
        if not _SAFE_ID.match(run_id) or not self.directory.exists():
            return None
        for path in self.directory.glob(f"run_*_{run_id}.json"):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None
