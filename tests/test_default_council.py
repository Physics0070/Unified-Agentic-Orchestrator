"""The default model lists must give the council at least two members.

Regression: with one working model (the other returned 404) every live compile showed
"1 proposals, 0 anonymous rankings" - the peer review silently never ran.
"""

from backend.config import Settings
from backend.providers.gemini import RATES


def _models(csv: str) -> list[str]:
    return [m.strip() for m in csv.split(",") if m.strip()]


def test_default_providers_offer_at_least_two_distinct_models():
    s = Settings()
    specs = [f"groq:{m}" for m in _models(s.groq_models)]
    specs += [f"gemini:{m}" for m in _models(s.gemini_models)]
    assert len(specs) >= 2
    assert len(set(specs)) == len(specs)


def test_every_default_gemini_model_is_priced():
    # An unpriced model reports $0.00, which under-reports cost; keep the defaults priced.
    for model in _models(Settings().gemini_models):
        assert model in RATES
