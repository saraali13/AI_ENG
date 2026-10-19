import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test: results/state go to temp dirs, mocks answer instantly, no API key, fixed student info."""
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MOCK_DELAY_SCALE", "0")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0")
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_GLOBAL_LIMIT", "CLAUDE_REQUEST_LIMIT", "MODEL_REQUEST_LIMIT",
                 "OPENROUTER_PROFILE", "ALLOW_LIVE_NON_FINAL", "MOVE_LIMIT", "OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STUDENT_NAME", "Test Student")
    monkeypatch.setenv("ROLL_NUMBER", "00-TEST")


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Safety net: no test may ever open a real network connection (so no test can spend OpenRouter requests).
    Tests that need HTTP use httpx.MockTransport, which never reaches this code."""
    async def blocked(self, request):
        raise RuntimeError(f"REAL NETWORK BLOCKED IN TESTS: {request.method} {request.url}")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
