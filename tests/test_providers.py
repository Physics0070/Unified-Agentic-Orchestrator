"""ProviderAdapter's shared-client cache. Pure - no network, no model calls.

adapter_for() builds a fresh ProviderAdapter instance per call (see
registry.py), so a fresh httpx.AsyncClient per call used to mean a fresh
TCP+TLS handshake to the provider on every single completion - the same
class of cost events/store.py already measured and fixed for the Turso
connection (see its module docstring: ~40% slower under concurrent load
with a fresh connection per call). These tests cover the process-level
client cache that fixes it for the provider layer too.
"""

import httpx
import pytest

from backend.providers.base import ProviderAdapter
from backend.providers.ollama import OllamaAdapter
from backend.providers.secret_broker import ProviderHandle


@pytest.fixture(autouse=True)
async def _reset_client_cache():
    """Isolates each test from clients created by others in this file
    (and closes anything a test leaves behind, whether it passed or not)."""
    yield
    await ProviderAdapter.aclose_all()


def _handle(base_url: str = "http://ollama-test-host:11434") -> ProviderHandle:
    return ProviderHandle(provider="ollama", base_url=base_url)


async def test_same_provider_and_base_url_reuses_one_client():
    a = OllamaAdapter(_handle())
    b = OllamaAdapter(_handle())  # a fresh adapter instance, same handle shape

    assert a is not b  # this is the point: adapter_for() never reuses instances
    assert a._client(300.0) is b._client(300.0)


async def test_different_base_urls_get_different_clients():
    local = OllamaAdapter(_handle("http://localhost:11434"))
    tunnelled = OllamaAdapter(_handle("https://some-tunnel.example"))

    assert local._client(300.0) is not tunnelled._client(300.0)


async def test_aclose_all_closes_and_forgets_every_client():
    adapter = OllamaAdapter(_handle())
    client = adapter._client(300.0)
    assert not client.is_closed

    await ProviderAdapter.aclose_all()

    assert client.is_closed
    # A client requested after aclose_all() is a genuinely new one, not the
    # closed instance - reusing a closed httpx.AsyncClient raises on the next
    # request.
    fresh = adapter._client(300.0)
    assert fresh is not client
    assert not fresh.is_closed


async def test_client_is_a_real_async_client_pointed_at_the_handles_base_url():
    adapter = OllamaAdapter(_handle("http://localhost:11434"))
    client = adapter._client(300.0)

    assert isinstance(client, httpx.AsyncClient)
    assert str(client.base_url) == "http://localhost:11434"
