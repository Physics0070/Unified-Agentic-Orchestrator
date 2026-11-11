"""A hung provider call must time out, free its concurrency slot, and fail over quickly.

Regression: a stalled call held one of the four call slots for up to the 120 s HTTP timeout, so a
rate-limited or hung provider delayed a whole run by minutes instead of failing over in seconds.
"""

import asyncio

import pytest

from backend.config import settings
from backend.providers import registry
from backend.providers.base import Completion, ProviderError

SLOW, FAST = "groq:slow-model", "mistral:fast-model"
MSG = [{"role": "user", "content": "hi"}]


class _Hangs:
    async def complete(self, *a, **kw):
        await asyncio.sleep(5)


class _Answers:
    async def complete(self, messages, model, **kw):
        return Completion(text="ok", tokens_in=1, tokens_out=1, model=model)


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch):
    monkeypatch.setattr(settings, "provider_call_timeout_s", 0.1)
    monkeypatch.setattr(
        registry, "adapter_for",
        lambda spec: (_Hangs(), spec) if spec == SLOW else (_Answers(), spec),
    )


async def test_a_hung_call_fails_over_to_the_backup_model():
    started = asyncio.get_running_loop().time()
    c = await registry.complete(SLOW, MSG, fallback_model=FAST)
    assert c.used_fallback and c.text == "ok"
    assert asyncio.get_running_loop().time() - started < 2


async def test_a_hung_call_with_no_backup_raises_a_provider_error():
    with pytest.raises(ProviderError, match="timed out"):
        await registry.complete(SLOW, MSG)


async def test_a_hung_call_releases_its_concurrency_slot():
    free_before = registry._CALL_SLOTS._value
    with pytest.raises(ProviderError):
        await registry.complete(SLOW, MSG)
    assert registry._CALL_SLOTS._value == free_before
