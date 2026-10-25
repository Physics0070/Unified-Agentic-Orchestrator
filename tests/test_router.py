"""Model router: capability vs cost vs latency, weighted by task difficulty and
user priority. Pure - no network."""

import pytest

from backend.providers import manage
from backend.providers.router import PRIORITIES, route_model, weights_for
from backend.providers.secret_broker import (
    ProviderHandle,
    clear_runtime_key,
    get_handle,
    key_source,
    set_runtime_key,
)

POOL = [
    "ollama:llama3.2:3b",
    "ollama:qwen2.5:7b",
    "groq:llama-3.3-70b-versatile",
    "gemini:gemini-2.0-flash",
    "openai:gpt-4o",
]


def test_weights_always_sum_to_one():
    for cx in ("simple", "moderate", "deep"):
        for pr in PRIORITIES:
            assert sum(weights_for(cx, pr).values()) == pytest.approx(1.0, abs=1e-3)


def test_harder_tasks_weight_capability_more():
    assert (
        weights_for("simple", "balanced")["capability"]
        < weights_for("moderate", "balanced")["capability"]
        < weights_for("deep", "balanced")["capability"]
    )


def test_quality_priority_picks_the_strongest_model_on_a_deep_task():
    assert route_model(POOL, "audit", "deep", "quality").model == "openai:gpt-4o"


def test_speed_priority_picks_a_fast_hosted_model_not_a_slow_local_one():
    pick = route_model(POOL, "chat", "simple", "speed").model
    assert not pick.startswith("ollama:")


def test_cost_priority_never_picks_the_priciest_model_for_a_simple_question():
    assert route_model(POOL, "chat", "simple", "cost").model != "openai:gpt-4o"


def test_decision_exposes_every_candidates_components():
    d = route_model(POOL, "explain", "moderate")
    assert [c.model for c in d.candidates][0] == d.model
    assert len(d.candidates) == len(POOL)
    assert all(0.0 <= c.cost <= 1.0 and 0.0 <= c.latency <= 1.0 for c in d.candidates)
    assert d.reason and d.model in d.reason or "next best" in d.reason


def test_routing_is_deterministic():
    assert route_model(POOL, "analyze", "deep").model_dump() == (
        route_model(POOL, "analyze", "deep").model_dump())


def test_unknown_priority_falls_back_to_balanced():
    assert route_model(POOL, "explain", "simple", "nonsense").priority == "balanced"


def test_single_model_and_empty_pool():
    assert route_model(["ollama:x:1"], "chat", "simple").reason == "only model available"
    with pytest.raises(IndexError):
        route_model([], "chat", "simple")


def test_unrecognised_model_is_flagged_as_estimated_not_hidden():
    d = route_model(["ollama:mystery:1b", "groq:llama-3.3-70b-versatile"], "chat", "simple")
    mystery = next(c for c in d.candidates if "mystery" in c.model)
    assert mystery.estimated is True


# ----------------------------------------------------- provider management


def test_runtime_key_is_used_reported_and_never_exposed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clear_runtime_key("openai")
    assert get_handle("openai").configured is False
    assert key_source("openai") is None

    set_runtime_key("openai", "sk-test-1234567890abcdef")
    try:
        h = get_handle("openai")
        assert h.configured and key_source("openai") == "runtime"
        assert "sk-test" not in repr(h) and "sk-test" not in str(h)
        assert h.auth_headers() == {"Authorization": "Bearer sk-test-1234567890abcdef"}
        assert all("sk-test" not in str(row) for row in manage.status())
    finally:
        clear_runtime_key("openai")
    assert get_handle("openai").configured is False


def test_obviously_bad_keys_are_rejected():
    for bad in ("short", "has space in it 1234", ""):
        with pytest.raises(ValueError):
            set_runtime_key("groq", bad)
    with pytest.raises(ValueError):
        set_runtime_key("nope", "x" * 20)


def test_models_are_selectable_and_cleaned():
    try:
        assert manage.set_models("groq", ["a-model", " a-model ", "bad model", "b"]) == [
            "a-model", "b"]
    finally:
        assert manage.set_models("groq", []) == manage.configured_models("groq")


def test_all_models_lists_only_configured_providers(monkeypatch):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    set_runtime_key("openai", "sk-test-1234567890abcdef")
    try:
        specs = manage.all_models()
        assert any(s.startswith("openai:") for s in specs)
        assert not any(s.startswith("groq:") for s in specs)
    finally:
        clear_runtime_key("openai")


def test_handle_is_a_provider_handle():
    assert isinstance(get_handle("ollama"), ProviderHandle)
