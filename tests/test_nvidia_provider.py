"""NVIDIA NIM (Nemotron) is a registered provider, joins the council only when configured, and
OpenRouter is no longer the planner's first choice."""

import pytest

from backend.chat.planner import pick_planner_model
from backend.config import settings
from backend.providers import manage
from backend.providers.registry import adapter_for, parse_model
from backend.providers.secret_broker import get_handle, known_providers

NEMOTRON = "nvidia:nvidia/nemotron-3-nano-30b-a3b"


def test_nvidia_is_a_known_provider_with_the_nim_endpoint():
    assert "nvidia" in known_providers()
    assert get_handle("nvidia").base_url == "https://integrate.api.nvidia.com/v1"


def test_a_model_id_with_slashes_parses_to_provider_and_model():
    assert parse_model(NEMOTRON) == ("nvidia", "nvidia/nemotron-3-nano-30b-a3b")
    adapter, model = adapter_for(NEMOTRON)
    assert adapter.provider == "nvidia" and model == "nvidia/nemotron-3-nano-30b-a3b"


def test_a_key_is_needed_and_is_sent_as_a_bearer_token(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert not get_handle("nvidia").configured
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-1234")
    handle = get_handle("nvidia")
    assert handle.configured
    assert handle.auth_headers() == {"Authorization": "Bearer nvapi-test-key-1234"}


def test_it_contributes_models_only_when_named(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-1234")
    monkeypatch.setattr(settings, "nvidia_models", "")
    assert not [m for m in manage.all_models() if m.startswith("nvidia:")]
    monkeypatch.setattr(settings, "nvidia_models", "nvidia/nemotron-3-nano-30b-a3b")
    assert NEMOTRON in manage.all_models()


@pytest.mark.parametrize("models, expected", [
    (["openrouter:x:free", "groq:openai/gpt-oss-120b"], "groq:openai/gpt-oss-120b"),
    (["openrouter:x:free", NEMOTRON, "gemini:g"], NEMOTRON),
    (["openrouter:x:free"], "openrouter:x:free"),
])
def test_the_planner_prefers_reliable_providers_over_openrouter_free(models, expected):
    assert pick_planner_model(models) == expected
