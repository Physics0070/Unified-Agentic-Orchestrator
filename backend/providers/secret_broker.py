"""The only module in this repo that reads a provider API key.

Grep any key name and exactly one file comes back. Agents receive a
`ProviderHandle`, never a raw key: the handle carries the base URL and knows how
to build an auth header, but the key itself never crosses a return boundary,
never enters an event payload, never reaches a log line, never ships to the
frontend.

Keys are read from os.environ here rather than through backend.config on
purpose - config.py deliberately does not declare them, so there is no second
place they could leak from.
"""

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

from backend.config import settings

# Keys are read from os.environ below, but a `.env` file is only parsed into
# `settings` (pydantic-settings never exports it) - so a key written in `.env`
# was invisible here: the provider showed as unconfigured, or Ollama Cloud was
# called with no auth header. Load it into the environment, cwd-relative like
# the settings' own env_file; a real environment variable still wins.
load_dotenv(find_dotenv(usecwd=True), override=False)


# `repr=False` on the secret field: a stray f-string or traceback that prints a
# handle cannot print the key.
@dataclass(frozen=True)
class ProviderHandle:
    provider: str
    base_url: str
    _secret: str | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        """Local providers need no key; remote ones do."""
        if self.provider == "ollama":
            # A function on Vercel has no localhost Ollama; offering it sends
            # agents to a model that can never answer.
            local = any(h in self.base_url for h in ("localhost", "127.0.0.1"))
            return not (os.environ.get("VERCEL") and local)
        return bool(self._secret)

    def auth_headers(self) -> dict[str, str]:
        """Built at call time, handed straight to httpx, never stored.

        Ollama is deliberately included here, not defaulted to it: a local or
        tunnelled instance has no key and must see no Authorization header at
        all (some dev proxies reject an empty/garbage bearer token), while
        Ollama Cloud requires one. `not self._secret` above already covers
        "no key configured" for both cases.
        """
        if not self._secret:
            return {}
        if self.provider in ("groq", "openrouter", "ollama", "openai"):
            return {"Authorization": f"Bearer {self._secret}"}
        if self.provider == "gemini":
            return {"x-goog-api-key": self._secret}
        return {}

    def __str__(self) -> str:
        return f"<ProviderHandle {self.provider} configured={self.configured}>"

    __repr__ = __str__


_SPEC = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta"),
    # OLLAMA_API_KEY is optional (see config.py) - present for Ollama Cloud,
    # absent for a local/tunnelled instance.
    "ollama": ("OLLAMA_API_KEY", "http://localhost:11434"),
}


# Keys and endpoints supplied at runtime through the provider-management API.
# Process-local on purpose: they live in memory only, never reach a log line or
# an event payload, and vanish on restart. A key from the environment is the
# durable path; this is for "I pasted a key into the UI" without a redeploy.
_RUNTIME_KEYS: dict[str, str] = {}
_RUNTIME_BASE_URLS: dict[str, str] = {}


def known_providers() -> list[str]:
    return list(_SPEC)


def set_runtime_key(provider: str, key: str) -> None:
    if provider not in _SPEC:
        raise ValueError(f"unknown provider {provider!r}")
    key = key.strip()
    if not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise ValueError("that does not look like an API key")
    _RUNTIME_KEYS[provider] = key


def clear_runtime_key(provider: str) -> None:
    _RUNTIME_KEYS.pop(provider, None)
    _RUNTIME_BASE_URLS.pop(provider, None)


def set_runtime_base_url(provider: str, url: str) -> None:
    """Only Ollama has a user-chosen endpoint (cloud vs. local vs. tunnelled)."""
    if provider != "ollama":
        raise ValueError("only the ollama endpoint can be changed")
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")) or len(url) > 300:
        raise ValueError("base URL must be http(s)")
    _RUNTIME_BASE_URLS[provider] = url


def key_source(provider: str) -> str | None:
    """Where the active credential comes from: "runtime", "env" or None. Never
    the value itself."""
    if provider in _RUNTIME_KEYS:
        return "runtime"
    env_key, _ = _SPEC[provider]
    return "env" if os.environ.get(env_key) else None


def get_handle(provider: str) -> ProviderHandle:
    if provider not in _SPEC:
        raise ValueError(f"unknown provider {provider!r}")
    env_key, default_url = _SPEC[provider]
    secret = _RUNTIME_KEYS.get(provider) or os.environ.get(env_key) or None

    if provider == "ollama":
        # Base URL is not a secret, so it comes from settings (backend.config
        # is the single source for it) rather than a second os.environ read
        # here. Falls back to localhost only when unset - config.py's prod
        # validator refuses to boot with it blank in prod, so this fallback
        # only ever fires in dev.
        base_url = _RUNTIME_BASE_URLS.get(provider) or settings.ollama_base_url or default_url
        return ProviderHandle(provider, base_url, secret)

    return ProviderHandle(provider, default_url, secret)


def configured_providers() -> list[str]:
    """Which providers can actually be called right now."""
    return [p for p in _SPEC if get_handle(p).configured]


def assert_no_key_in(text: str) -> None:
    """Guard for the event-log write path: refuse to persist a payload that
    contains a live key. Cheap, and it turns a silent leak into a crash."""
    for provider, (env_key, _) in _SPEC.items():
        for secret in (os.environ.get(env_key), _RUNTIME_KEYS.get(provider)):
            if secret and len(secret) > 8 and secret in text:
                raise RuntimeError(f"refusing to persist payload containing {env_key}")
