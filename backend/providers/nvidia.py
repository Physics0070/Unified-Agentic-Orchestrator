"""NVIDIA NIM adapter (build.nvidia.com) - hosted Nemotron and other open models.

NIM speaks the same OpenAI-compatible wire format Groq does, so the whole implementation
is inherited and only the identity differs. NVIDIA publishes no per-token rate for the free
evaluation endpoints, so every model is reported at $0 (see RATES) rather than a guessed
figure; add a row if you move to a paid plan.
"""

from backend.providers.groq import GroqAdapter

RATES: dict[str, tuple[float, float]] = {}


class NvidiaAdapter(GroqAdapter):
    provider = "nvidia"
    RATES = RATES
