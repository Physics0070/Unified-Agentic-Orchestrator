"""Mistral is a registered provider, keys saved under per-model names are accepted, and the
seed goes out under Mistral's own parameter name."""

import asyncio

import pytest

from backend.config import settings
from backend.providers import manage
from backend.providers.registry import adapter_for
from backend.providers.secret_broker import get_handle, key_source


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for name in ("NVIDIA_API_KEY", "nemotron_3_ultra_550b_a55b_API_KEY",
                 "MISTRAL_API_KEY", "Mistral_Large_4_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_mistral_endpoint_and_key_handling(monkeypatch):
    assert get_handle("mistral").base_url == "https://api.mistral.ai/v1"
    assert not get_handle("mistral").configured
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key-12345")
    handle = get_handle("mistral")
    assert handle.configured
    assert handle.auth_headers() == {"Authorization": "Bearer mistral-test-key-12345"}


@pytest.mark.parametrize("provider, alias", [
    ("nvidia", "Mistral_Large_4_API_KEY"),
    ("nvidia", "nemotron_3_ultra_550b_a55b_API_KEY"),
])
def test_a_key_saved_under_a_per_model_name_is_accepted(monkeypatch, provider, alias):
    monkeypatch.setenv(alias, "alias-test-key-12345")
    assert get_handle(provider).configured
    assert key_source(provider) == "env"


def test_the_standard_name_wins_over_the_alias(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "standard-key-12345")
    monkeypatch.setenv("Mistral_Large_4_API_KEY", "alias-key-12345")
    assert get_handle("nvidia").auth_headers() == {"Authorization": "Bearer standard-key-12345"}


def test_it_contributes_models_only_when_named(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key-12345")
    monkeypatch.setattr(settings, "mistral_models", "")
    assert not [m for m in manage.all_models() if m.startswith("mistral:")]
    monkeypatch.setattr(settings, "mistral_models", "mistral-large-latest")
    assert "mistral:mistral-large-latest" in manage.all_models()


class _Resp:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Client:
    def __init__(self):
        self.body = None

    async def post(self, url, json, headers):
        self.body = json
        return _Resp()


@pytest.mark.parametrize("spec, param", [
    ("mistral:mistral-large-latest", "random_seed"),
    ("groq:openai/gpt-oss-20b", "seed"),
])
def test_the_seed_is_sent_under_each_providers_own_parameter_name(monkeypatch, spec, param):
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key-12345")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key-12345")
    adapter, model = adapter_for(spec)
    client = _Client()
    monkeypatch.setattr(adapter, "_client", lambda timeout: client)
    asyncio.run(adapter.complete([{"role": "user", "content": "hi"}], model, seed=7))
    assert client.body[param] == 7
    assert ("seed" in client.body) == (param == "seed")
