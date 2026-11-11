"""An empty completion is a failed call, not an answer.

Regression: a free-tier model returned "" and the empty text was accepted as a council proposal
and as an agent result, so a flaky member silently degraded the run instead of failing over.
"""

import pytest

from backend.providers import registry
from backend.providers.base import Completion, ProviderError, ToolCall

EMPTY, GOOD = "nvidia:empty-model", "groq:good-model"
MSG = [{"role": "user", "content": "hi"}]


class _Empty:
    async def complete(self, messages, model, **kw):
        return Completion(text="  \n", model=model)


class _Good:
    async def complete(self, messages, model, **kw):
        return Completion(text="ok", tokens_in=1, tokens_out=1, model=model)


class _ToolOnly:
    async def complete(self, messages, model, **kw):
        return Completion(text="", model=model, tool_calls=[ToolCall(name="x", arguments={})])


@pytest.fixture(autouse=True)
def _adapters(monkeypatch):
    table = {EMPTY: _Empty(), GOOD: _Good(), "groq:tools": _ToolOnly()}
    monkeypatch.setattr(registry, "adapter_for", lambda spec: (table[spec], spec))


async def test_an_empty_reply_fails_over_to_the_backup_model():
    c = await registry.complete(EMPTY, MSG, fallback_model=GOOD)
    assert c.used_fallback and c.text == "ok"


async def test_an_empty_reply_with_no_backup_is_a_provider_error():
    with pytest.raises(ProviderError, match="empty"):
        await registry.complete(EMPTY, MSG)


async def test_a_tool_call_with_no_text_is_still_a_valid_completion():
    c = await registry.complete("groq:tools", MSG)
    assert c.tool_calls and not c.used_fallback
