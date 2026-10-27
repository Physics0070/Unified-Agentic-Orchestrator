"""Multi-provider management: which providers are live, and which models each
contributes to the council.

Everything here is a read or a small in-memory override. The adapters, the
secret broker and the registry already make a provider swap a one-string
change; this module is what lets a user do that swap from the UI (add a key,
pick models, test the connection) without editing code or redeploying.
"""

import time

from backend.config import settings
from backend.providers.base import ProviderError
from backend.providers.secret_broker import (
    clear_runtime_key,
    get_handle,
    key_source,
    known_providers,
    set_runtime_base_url,
    set_runtime_key,
)

# Order matters: the first provider's first model is models[0], which answers,
# is preferred by the planner and chairs the council. Ollama is the tested
# primary path, so it stays first.
ORDER = ("ollama", "groq", "gemini", "openai", "openrouter")

LABELS = {
    "ollama": "Ollama (Cloud or local)",
    "groq": "Groq",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}

# Models a user may toggle without typing a tag. Not exhaustive and not a claim
# that every one is enabled on every account - the connection test is the check.
SUGGESTED = {
    "ollama": ["llama3.2:3b", "qwen2.5:7b", "gpt-oss:120b-cloud"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "gemini": ["gemini-2.0-flash", "gemini-2.5-flash"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    "openrouter": ["meta-llama/llama-3.3-70b-instruct:free"],
}

# Model lists chosen at runtime. Absent means "use the configured default".
_RUNTIME_MODELS: dict[str, list[str]] = {}


def _split(csv: str) -> list[str]:
    return [m.strip() for m in csv.split(",") if m.strip()]


def configured_models(provider: str) -> list[str]:
    """The env/config default list for one provider."""
    return _split({
        "ollama": settings.ollama_models,
        "groq": settings.groq_models,
        "gemini": settings.gemini_models,
        "openai": settings.openai_models,
        "openrouter": settings.openrouter_models,
    }.get(provider, ""))


def models_for(provider: str) -> list[str]:
    return list(_RUNTIME_MODELS.get(provider) or configured_models(provider))


def set_models(provider: str, models: list[str]) -> list[str]:
    if provider not in known_providers():
        raise ValueError(f"unknown provider {provider!r}")
    clean: list[str] = []
    for m in models:
        m = m.strip()
        if not m or len(m) > 120 or any(c.isspace() for c in m) or m in clean:
            continue
        clean.append(m)
    if not clean:
        _RUNTIME_MODELS.pop(provider, None)
    else:
        _RUNTIME_MODELS[provider] = clean[:8]
    return models_for(provider)


def all_models(limit: int = 10) -> list[str]:
    """`provider:model` specs across every configured provider, in ORDER."""
    out: list[str] = []
    for p in ORDER:
        if get_handle(p).configured:
            out.extend(f"{p}:{m}" for m in models_for(p))
    return out[:limit]


def status() -> list[dict]:
    """Per-provider state for the UI. Reports where a key comes from, never the
    key, never any part of it."""
    rows = []
    for p in ORDER:
        h = get_handle(p)
        rows.append({
            "provider": p,
            "label": LABELS[p],
            "configured": h.configured,
            "key_source": key_source(p),
            "base_url": h.base_url if p == "ollama" else None,
            "key_required": p != "ollama",
            "models": models_for(p),
            "default_models": configured_models(p),
            "suggested_models": SUGGESTED[p],
            "active_specs": [f"{p}:{m}" for m in models_for(p)] if h.configured else [],
        })
    return rows


def connect(provider: str, key: str | None = None, base_url: str | None = None) -> None:
    if key:
        set_runtime_key(provider, key)
    if base_url:
        set_runtime_base_url(provider, base_url)


def disconnect(provider: str) -> None:
    clear_runtime_key(provider)
    _RUNTIME_MODELS.pop(provider, None)


async def check_connection(provider: str, model: str | None = None) -> dict:
    """One tiny real completion. The result says what happened in words a user
    can act on; provider error bodies are never echoed (see ProviderAdapter._classify)."""
    from backend.providers.registry import adapter_for

    models = [model] if model else models_for(provider)
    if not get_handle(provider).configured:
        return {"ok": False, "provider": provider, "error": "no API key configured"}
    if not models:
        return {"ok": False, "provider": provider, "error": "no model selected"}
    spec = f"{provider}:{models[0]}"
    adapter, name = adapter_for(spec)
    started = time.perf_counter()
    try:
        c = await adapter.complete(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            name, max_tokens=8,
        )
    except ProviderError as e:
        hint = {401: "the key was rejected", 403: "the key lacks access to this model",
                404: "that model does not exist for this provider",
                429: "rate limited - the key works but is out of quota right now"}.get(
                    e.status or 0, str(e))
        return {"ok": False, "provider": provider, "model": spec, "error": hint,
                "status": e.status}
    except Exception as e:
        return {"ok": False, "provider": provider, "model": spec,
                "error": f"could not reach the provider ({type(e).__name__})"}
    return {"ok": True, "provider": provider, "model": spec,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "tokens": c.tokens_in + c.tokens_out}
