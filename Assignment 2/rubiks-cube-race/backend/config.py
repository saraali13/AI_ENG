"""Application settings, read from environment variables (and a local .env file).

Settings are read on every call to ``get_settings()`` so tests can change the environment.
The API key is stored on the settings object but is never included in ``repr()`` or API responses.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

# Does not override variables that are already set in the real environment.
load_dotenv(BASE_DIR / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    student_name: str = "Your Name"
    roll_number: str = "Your Roll Number"
    openrouter_api_key: str = field(default="", repr=False)
    openrouter_base_url: str = "https://openrouter.ai/api"
    move_limit: int = 20  # the professor's limit: stop a model after 20 valid moves
    step_timeout_seconds: float = 120.0
    max_consecutive_failures: int = 3          # mock / ollama
    live_max_consecutive_failures: int = 2     # OpenRouter: at most ONE retry per move (every retry is budgeted)
    retry_backoff_seconds: float = 1.0
    mock_delay_scale: float = 1.0
    results_dir: Path = BASE_DIR / "results"
    state_dir: Path = BASE_DIR / "state"       # request ledger + response cache (persistent, never auto-cleared)
    openrouter_global_limit: int = 65          # enforced; can never exceed the hard ceiling of 100 (see budget.py)
    claude_request_limit: int = 5              # enforced; can never exceed 5 (see budget.py)
    model_request_limit: int = 20              # per non-Claude model
    chat_max_tokens: int = 1200
    openrouter_profile: str = "final"          # "final" = the four assignment models; "free_test" = free chat stand-in
    free_test_chat_model: str = "openrouter/free"
    allow_live_non_final: bool = False
    official_seed: int = 2026                  # fixed start state of the final experiment => reruns are cache hits
    ollama_base_url: str = "http://localhost:11434"
    ollama_models: dict = field(default_factory=dict)
    # Exact optimal search is only attempted when the scramble is at most this long (see backend/optimal.py).
    optimal_max_scramble: int = 8

    @property
    def api_key_configured(self) -> bool:
        return bool(self.openrouter_api_key.strip())


def get_settings() -> Settings:
    results_dir = Path(os.environ.get("RESULTS_DIR", "results"))
    if not results_dir.is_absolute():
        results_dir = BASE_DIR / results_dir
    state_dir = Path(os.environ.get("STATE_DIR", "state"))
    if not state_dir.is_absolute():
        state_dir = BASE_DIR / state_dir
    default_ollama = os.environ.get("OLLAMA_MODEL", "llama3.2").strip() or "llama3.2"
    ollama_models = {k: (os.environ.get(f"OLLAMA_MODEL_{k.upper()}", "").strip() or default_ollama)
                     for k in ("jev", "luna", "clef", "opus")}
    return Settings(
        student_name=os.environ.get("STUDENT_NAME", "Your Name").strip() or "Your Name",
        roll_number=os.environ.get("ROLL_NUMBER", "Your Roll Number").strip() or "Your Roll Number",
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api").rstrip("/"),
        move_limit=max(1, min(_int("MOVE_LIMIT", 20), 20)),
        step_timeout_seconds=max(1.0, _float("STEP_TIMEOUT_SECONDS", 120.0)),
        max_consecutive_failures=max(1, _int("MAX_CONSECUTIVE_FAILURES", 3)),
        retry_backoff_seconds=max(0.0, _float("RETRY_BACKOFF_SECONDS", 1.0)),
        mock_delay_scale=max(0.0, _float("MOCK_DELAY_SCALE", 1.0)),
        results_dir=results_dir,
        state_dir=state_dir,
        live_max_consecutive_failures=max(1, _int("LIVE_MAX_CONSECUTIVE_FAILURES", 2)),
        openrouter_global_limit=max(0, min(_int("OPENROUTER_GLOBAL_LIMIT", 65), 100)),
        claude_request_limit=max(0, min(_int("CLAUDE_REQUEST_LIMIT", 5), 5)),
        model_request_limit=max(0, min(_int("MODEL_REQUEST_LIMIT", 20), 100)),
        chat_max_tokens=max(64, _int("CHAT_MAX_TOKENS", 1200)),
        openrouter_profile="free_test" if os.environ.get("OPENROUTER_PROFILE", "final").strip() == "free_test" else "final",
        free_test_chat_model=os.environ.get("FREE_TEST_CHAT_MODEL", "openrouter/free").strip() or "openrouter/free",
        allow_live_non_final=os.environ.get("ALLOW_LIVE_NON_FINAL", "0").strip() == "1",
        official_seed=_int("OFFICIAL_SEED", 2026),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/"),
        ollama_models=ollama_models,
    )
