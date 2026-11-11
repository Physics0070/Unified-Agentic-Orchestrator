"""A key pasted into a *_MODELS variable must never be published.

Regression: OPENROUTER_MODELS held an NVIDIA key, and the public GET /api/models and
/api/providers served it back as if it were a model name.
"""

import pytest

from backend.config import settings
from backend.providers import manage
from backend.providers.secret_broker import looks_like_secret

LEGIT = ["openai/gpt-oss-120b", "nvidia/nemotron-3.5-lightning:free", "gemini-3.6-flash",
         "qwen2.5:7b"]


@pytest.mark.parametrize("model", LEGIT)
def test_real_model_ids_are_not_mistaken_for_keys(model):
    assert not looks_like_secret(model)


@pytest.mark.parametrize("text", [
    "nvapi-AbCdEf0123456789", "sk-proj-abcdef123456", "gsk_abcdef123456", "AIzaSyAbCdEf0123456789",
])
def test_key_shaped_strings_are_caught(text):
    assert looks_like_secret(text)


def test_a_configured_key_is_caught_even_without_a_known_prefix(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "plain-looking-secret-9876")
    assert looks_like_secret("prefix-plain-looking-secret-9876")


def test_a_key_in_a_models_variable_is_dropped_from_the_published_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-for-this-test-123")
    monkeypatch.setattr(
        settings, "openrouter_models", "nvapi-AbCdEf0123456789,nvidia/nemotron-3.5-lightning:free"
    )
    assert manage.configured_models("openrouter") == ["nvidia/nemotron-3.5-lightning:free"]
    assert not any("nvapi" in m for m in manage.all_models())
    assert not any("nvapi" in str(row) for row in manage.status())


def test_a_key_chosen_at_runtime_is_dropped_too():
    manage.set_models("groq", ["gsk_abcdef0123456789", "openai/gpt-oss-20b"])
    try:
        assert manage.models_for("groq") == ["openai/gpt-oss-20b"]
    finally:
        manage.set_models("groq", [])
