"""Real adapters tested against a fake HTTP transport (no network, no key needed). Every adapter requires a guard."""
import asyncio
import json

import httpx
import pytest

from backend.config import get_settings
from backend.cube_engine import MOVES, CubeEngine
from backend.models import build_adapters
from backend.models.base import AdapterError, StepContext
from backend.models.chat import ChatAdapter
from backend.models.clef import ClefAdapter
from backend.models.decisions import DecisionsAdapter
from backend.models.jev import JevAdapter
from backend.models.luna import LunaAdapter
from backend.models.opus import OpusAdapter
from tests.helpers import TEST_KEY, chat_body, decisions_body, make_guard


def ctx():
    cube = CubeEngine.from_scramble(["R", "U"])
    return StepContext(step=1, facelets=cube.facelets, faces=cube.faces(), net=cube.net(), history=[], move_limit=20,
                       hidden={"scramble": ["R", "U"]})


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def adapter(cls, handler, tmp_path, **kw):
    return cls(guard=make_guard(tmp_path / "s"), api_key=TEST_KEY, client=client_for(handler), **kw)


def choose(a):
    return asyncio.run(a.choose_move(ctx()))


@pytest.mark.parametrize("cls,model", [(JevAdapter, "typesafe/jev-1.13"), (LunaAdapter, "openai/gpt-6-luna-decisions"),
                                       (ClefAdapter, "cloudflare/clef")])
def test_decisions_adapters_use_the_documented_endpoint_and_payload(cls, model, tmp_path):
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=decisions_body(model, "U'"))

    d = choose(adapter(cls, handler, tmp_path))
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["auth"] == f"Bearer {TEST_KEY}"
    assert seen["body"]["model"] == model
    assert set(seen["body"]) == {"model", "state", "questions"}
    q = seen["body"]["questions"]["next_move"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(MOVES)
    assert d.move == "U'" and d.meta["confidence"] == 0.7 and d.meta["served_model"].startswith(model)
    assert d.meta["source"] == "live" and d.meta["api_calls"] == 1 and d.meta["cost_usd"] == 0.00002


def test_models_never_receive_the_scramble(tmp_path):
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        return httpx.Response(200, json=decisions_body("typesafe/jev-1.13", "U'"))

    choose(adapter(JevAdapter, handler, tmp_path))
    assert '"hidden"' not in seen["body"] and "R U" not in seen["body"]


def test_opus_uses_chat_completions_and_sends_only_safe_parameters(tmp_path):
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json=chat_body("anthropic/claude-opus-5.5-20260921", '{"move": "U\'"}'))

    d = choose(adapter(OpusAdapter, handler, tmp_path))
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["body"]["model"] == "anthropic/claude-opus-5.5"
    # temperature etc. are NOT sent: a rejected parameter would waste one of Claude's 5 requests
    assert set(seen["body"]) == {
    "model",
    "messages",
    "max_tokens",
    "reasoning",
}
    assert seen["body"]["reasoning"] == {"effort": "low"}   
    assert d.move == "U'"


@pytest.mark.parametrize("message_extra,content,note", [
    ({"reasoning": "thinking about the cube..."}, None, "reasoning"),
    ({}, "", "Empty content"),
    ({}, "   ", "Empty content"),
])
def test_empty_or_reasoning_only_chat_replies_are_not_moves(message_extra, content, note, tmp_path):
    def handler(req):
        return httpx.Response(200, json=chat_body("anthropic/claude-opus-5.5", content, finish="length",
                                                  extra_message=message_extra))
    a = adapter(OpusAdapter, handler, tmp_path)
    d = choose(a)
    assert d.move is None and d.raw == "" and note.lower() in d.meta["note"].lower()
    # never cached: a rerun must be allowed to try again
    assert a._guard.cache.stats()["entries"] == 0


def test_chat_reply_with_garbage_gives_a_candidate_for_validation_not_a_crash(tmp_path):
    def handler(req):
        return httpx.Response(200, json=chat_body("anthropic/claude-opus-5.5", "Let me think about this cube..."))
    d = choose(adapter(OpusAdapter, handler, tmp_path))
    assert d.move is None and d.raw


def test_silently_different_model_is_a_fatal_error(tmp_path):
    def handler(req):
        return httpx.Response(200, json=decisions_body("typesafe/jev-1.14", "U'"))
    with pytest.raises(AdapterError) as e:
        choose(adapter(JevAdapter, handler, tmp_path))
    assert e.value.fatal and e.value.kind == "model_mismatch"


@pytest.mark.parametrize("status,kind,fatal", [(401, "auth", True), (402, "payment", True), (404, "not_found", True),
                                                (429, "rate_limit", False), (502, "server_error", False),
                                                (503, "server_error", False), (400, "bad_request", True)])
def test_http_errors_are_classified_key_redacted_and_still_counted(status, kind, fatal, tmp_path):
    def handler(req):
        return httpx.Response(status, json={"error": {"code": status, "message": f"nope {TEST_KEY}"}})
    a = adapter(LunaAdapter, handler, tmp_path)
    with pytest.raises(AdapterError) as e:
        choose(a)
    assert e.value.kind == kind and e.value.fatal is fatal
    assert TEST_KEY not in e.value.message
    status_ = a._guard.ledger.status()["openrouter"]
    assert status_["requests"] == 1 and status_["failed"] == 1   # a failed request still consumes budget
    assert a.api_calls_started == 1


def test_http_200_is_not_trusted_malformed_replies_are_bad_response_errors(tmp_path):
    for payload in ({"answers": {}}, {"answers": {"next_move": {"choice": ""}}}, {"answers": {"next_move": {"choice": 5}}},
                    {"nothing": 1}):
        def handler(req, payload=payload):
            return httpx.Response(200, json={**payload, "model": "cloudflare/clef"})
        with pytest.raises(AdapterError) as e:
            choose(adapter(ClefAdapter, handler, tmp_path))
        assert e.value.kind == "bad_response"

    def not_json(req):
        return httpx.Response(200, content=b"<html>")
    with pytest.raises(AdapterError):
        choose(adapter(ClefAdapter, not_json, tmp_path))

    def chat_no_choices(req):
        return httpx.Response(200, json={"model": "anthropic/claude-opus-5.5", "choices": []})
    with pytest.raises(AdapterError) as e:
        choose(adapter(OpusAdapter, chat_no_choices, tmp_path))
    assert e.value.kind == "bad_response"


def test_network_failure_is_retryable_and_counted(tmp_path):
    def handler(req):
        raise httpx.ConnectError("down")
    a = adapter(JevAdapter, handler, tmp_path)
    with pytest.raises(AdapterError) as e:
        choose(a)
    assert e.value.kind == "network" and not e.value.fatal
    assert a._guard.ledger.status()["openrouter"]["requests"] == 1


def test_adapters_cannot_be_built_without_a_guard():
    with pytest.raises(TypeError):
        JevAdapter(api_key=TEST_KEY)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        OpusAdapter(api_key=TEST_KEY)  # type: ignore[call-arg]


def test_real_adapters_are_built_when_a_key_exists(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_KEY)
    adapters = build_adapters("real", get_settings())
    assert [a.mode for a in adapters] == ["real"] * 4
    assert [a.spec.openrouter_id for a in adapters] == [
        "typesafe/jev-1.13", "openai/gpt-6-luna-decisions", "cloudflare/clef", "anthropic/claude-opus-5.5"]
    assert [a.ledger_key for a in adapters] == ["jev", "luna", "clef", "opus"]


def test_replay_adapters_need_no_key_and_have_no_client():
    adapters = build_adapters("replay", get_settings())
    assert [a.mode for a in adapters] == ["replay"] * 4 and all(a._client is None for a in adapters)


def test_free_test_profile_is_separate_from_the_final_configuration(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_KEY)
    final = build_adapters("real", get_settings())
    assert final[3].spec.openrouter_id == "anthropic/claude-opus-5.5" and final[3].ledger_key == "opus"
    monkeypatch.setenv("OPENROUTER_PROFILE", "free_test")
    monkeypatch.setenv("FREE_TEST_CHAT_MODEL", "some/free-model:free")
    free = build_adapters("real", get_settings())
    assert free[3].spec.openrouter_id == "some/free-model:free"
    assert free[3].ledger_key == "free_test_chat"   # never consumes Claude's 5 requests
    assert "stand-in" in free[3].spec.display_name
