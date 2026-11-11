"""Compiled agents get a backup model, and a dead primary model no longer sinks a branch.

Regression for a live run where the research agent was assigned
`gemini:gemini-2.5-flash`, the call returned 404, and with `fallback_model=None`
the branch failed, compensated, and the whole run was reported failed.
"""

import pytest

from backend.council.compiler import build_graph
from backend.providers import registry
from backend.providers.base import Completion, ProviderError

GROQ = "groq:openai/gpt-oss-120b"
GEMINI = "gemini:gemini-2.5-flash"
BRANCHES = [{"id": "a", "role": "A"}, {"id": "b", "role": "B"}, {"id": "c", "role": "C"}]


def _agents(graph):
    return [n.agent for n in graph.nodes if n.agent is not None]


def _provider(spec: str) -> str:
    return spec.partition(":")[0]


def test_a_non_tool_provider_agent_falls_back_to_a_different_provider():
    graph = build_graph(BRANCHES, [GROQ, GEMINI], verifier_model=GROQ)
    gemini_agents = [a for a in _agents(graph) if a.model == GEMINI]
    assert gemini_agents, "the router should have placed at least one agent on gemini"
    for agent in gemini_agents:
        assert agent.fallback_model == GROQ


def test_a_fallback_never_switches_off_a_tool_capable_agents_tools():
    # groq speaks native tool calling; gemini does not. Pairing them would make
    # supports_tools() false for the groq agent and silently disable read_file.
    graph = build_graph(BRANCHES, [GROQ, GEMINI], verifier_model=GROQ)
    for agent in _agents(graph):
        if _provider(agent.model) == "groq":
            assert agent.fallback_model is None


def test_tool_capable_agents_get_a_tool_capable_backup_when_one_exists():
    other = "openrouter:some/model"
    graph = build_graph(BRANCHES, [GROQ, other], verifier_model=GROQ)
    for agent in _agents(graph):
        assert agent.fallback_model is not None
        assert agent.fallback_model != agent.model
        assert _provider(agent.fallback_model) != _provider(agent.model)


def test_a_single_model_has_no_backup():
    graph = build_graph(BRANCHES, [GROQ], verifier_model=GROQ)
    assert all(a.fallback_model is None for a in _agents(graph))


class _DeadAdapter:
    async def complete(self, *a, **kw):
        raise ProviderError("provider returned 404", status=404)


class _GoodAdapter:
    async def complete(self, messages, model, **kw):
        return Completion(text="{}", tokens_in=5, tokens_out=3, model=model)


async def test_a_404_on_the_primary_is_served_by_the_fallback(monkeypatch):
    monkeypatch.setattr(
        registry, "adapter_for",
        lambda spec: (_DeadAdapter(), spec) if spec == GEMINI else (_GoodAdapter(), spec),
    )
    c = await registry.complete(GEMINI, [{"role": "user", "content": "hi"}], fallback_model=GROQ)
    assert c.used_fallback is True
    assert c.text == "{}"


async def test_without_a_fallback_the_404_still_propagates(monkeypatch):
    monkeypatch.setattr(registry, "adapter_for", lambda spec: (_DeadAdapter(), spec))
    with pytest.raises(ProviderError):
        await registry.complete(GEMINI, [{"role": "user", "content": "hi"}])
