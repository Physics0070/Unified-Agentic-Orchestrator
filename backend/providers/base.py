"""Provider-agnostic completion interface.

Every adapter returns the same `Completion`, so an agent's `model` string is the
only thing that changes when you swap Groq for a local Ollama model - the graph
itself is untouched. That is demo step 8.
"""

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx
from pydantic import BaseModel

from backend.providers.secret_broker import ProviderHandle


class ToolCall(BaseModel):
    name: str
    arguments: dict


class Completion(BaseModel):
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""
    tool_calls: list[ToolCall] = []
    # True when this came from `fallback_model` after the primary failed.
    used_fallback: bool = False
    # True when this was served from the event log rather than a provider.
    replayed: bool = False


class ProviderError(RuntimeError):
    """Adapter call failed. Never carries a key."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProviderAdapter(ABC):
    """One adapter per provider. Stateless apart from the handle.

    `adapter_for()` in registry.py builds a fresh adapter instance for every
    call, so per-instance client state would not help - the HTTP client
    cache below is process-level instead, keyed by (provider, base_url), and
    outlives any one adapter. This is the same fix `events/store.py`
    already made for the Turso connection (see its module docstring: opening
    a fresh connection per call adds a full TLS handshake every time and was
    measured 40% slower under concurrent load) - it just hadn't been applied
    to the provider layer, where every model call was paying a fresh TCP+TLS
    handshake to Ollama Cloud/Groq/Gemini/OpenRouter on top of the model's
    own latency.
    """

    provider: str  # set by each subclass, e.g. OllamaAdapter.provider = "ollama"
    _clients: dict[tuple[str, str], httpx.AsyncClient] = {}

    def __init__(self, handle: ProviderHandle) -> None:
        self.handle = handle

    def _client(self, default_timeout: float) -> httpx.AsyncClient:
        """Shared keep-alive client for this (provider, base_url).

        Created once and reused for the life of the process - safe without a
        lock because construction does no I/O and nothing here awaits
        between the dict lookup and the assignment, so no other coroutine
        can interleave. `default_timeout` only applies to requests that
        don't pass their own `timeout=` (see OllamaAdapter.list_models,
        which needs a shorter one than `complete`/`stream`).
        """
        key = (self.provider, self.handle.base_url)
        client = ProviderAdapter._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(base_url=self.handle.base_url, timeout=default_timeout)
            ProviderAdapter._clients[key] = client
        return client

    @staticmethod
    async def aclose_all() -> None:
        """Closes every cached client. Call once, on app shutdown."""
        clients, ProviderAdapter._clients = ProviderAdapter._clients, {}
        for client in clients.values():
            await client.aclose()

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        model: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        json_mode: bool = False,
    ) -> Completion: ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        model: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> AsyncIterator[str]:
        """Token stream for the trace viewer."""
        ...

    # ---------- shared retry behaviour ----------

    async def _with_backoff(self, fn, attempts: int = 3):
        """Exponential backoff with jitter, honouring Retry-After.

        Jitter matters: without it, every branch of a fanout that hits the same
        rate limit retries in lockstep and hits it again together.
        """
        delay = 0.5
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return await fn()
            except ProviderError as e:
                last = e
                if not e.retryable or attempt == attempts - 1:
                    raise
                wait = getattr(e, "retry_after", None) or delay
                await asyncio.sleep(wait + random.uniform(0, 0.3))  # noqa: S311
                delay *= 2
        raise last  # pragma: no cover

    @staticmethod
    def _classify(r: httpx.Response) -> None:
        """Map an HTTP response to a ProviderError without echoing the body."""
        if r.status_code < 400:
            return
        retryable = r.status_code == 429 or r.status_code >= 500
        err = ProviderError(
            f"provider returned {r.status_code}", status=r.status_code, retryable=retryable
        )
        ra = r.headers.get("Retry-After")
        if ra:
            try:
                err.retry_after = float(ra)  # type: ignore[attr-defined]
            except ValueError:
                pass
        raise err
