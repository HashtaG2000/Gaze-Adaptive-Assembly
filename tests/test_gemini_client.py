"""Gemini client without network: the SDK client is replaced by a fake."""
import pytest

from app.config import load_config
from app.llm.client import LLMError


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.behaviour(len(self.calls), config)


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.candidates = []


def make_client(monkeypatch, behaviour):
    from google import genai
    from app.llm import gemini_client

    fake = FakeModels(behaviour)

    class FakeClient:
        def __init__(self, api_key, http_options):
            assert api_key == "test-key"
            assert http_options.timeout == load_config().llm.gemini.timeout_s * 1000
            self.models = fake

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(genai, "Client", FakeClient)
    return gemini_client.GeminiClient(load_config().llm.gemini), fake


def test_missing_key(monkeypatch):
    from app.llm.gemini_client import GeminiClient
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient(load_config().llm.gemini)


def test_structured_request(monkeypatch):
    client, fake = make_client(monkeypatch, lambda n, cfg: FakeResp('{"ok": true}'))
    out = client.complete_json("analysis", "PROMPT", {"type": "object"}, {})
    assert out == '{"ok": true}'
    call = fake.calls[0]
    assert call["contents"] == "PROMPT" and call["model"] == load_config().llm.gemini.model
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == {"type": "object"}


def test_schema_rejected_falls_back_to_plain_json(monkeypatch):
    def behaviour(n, cfg):
        if cfg.response_json_schema is not None:
            raise RuntimeError("400 INVALID_ARGUMENT: response schema not supported")
        return FakeResp("{}")
    client, fake = make_client(monkeypatch, behaviour)
    assert client.complete_json("generation", "P", {"type": "object"}, {}) == "{}"
    assert len(fake.calls) == 2 and fake.calls[1]["config"].response_json_schema is None
    client.complete_json("generation", "P", {"type": "object"}, {})   # remembers: no schema next time
    assert fake.calls[2]["config"].response_json_schema is None


def test_errors_become_llm_errors(monkeypatch):
    def boom(n, cfg):
        raise TimeoutError("deadline exceeded")
    client, _ = make_client(monkeypatch, boom)
    with pytest.raises(LLMError, match="deadline"):
        client.complete_json("analysis", "P", {}, {})
    client, _ = make_client(monkeypatch, lambda n, cfg: FakeResp(""))
    with pytest.raises(LLMError, match="empty"):
        client.complete_json("analysis", "P", {}, {})
