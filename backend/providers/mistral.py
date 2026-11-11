"""Mistral La Plateforme adapter - OpenAI-compatible chat completions.

Everything is inherited from the OpenAI-style adapter; the one wire difference is that Mistral
calls the sampling seed `random_seed`. No per-token rate is recorded (Mistral's free tier is
unpriced), so cost reports $0 rather than a guessed figure; add a row to RATES for a paid plan.
"""

from backend.providers.groq import GroqAdapter

RATES: dict[str, tuple[float, float]] = {}


class MistralAdapter(GroqAdapter):
    provider = "mistral"
    RATES = RATES
    SEED_PARAM = "random_seed"
