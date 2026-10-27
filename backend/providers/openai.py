"""OpenAI adapter - the same OpenAI-compatible wire format Groq speaks, so the
whole implementation is inherited and only the identity and price table differ.
"""

from backend.providers.groq import GroqAdapter

# Published OpenAI rates, USD per 1M tokens (input, output). A model missing
# from this table is reported as 0 rather than guessed - same convention as the
# other adapters.
RATES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


class OpenAIAdapter(GroqAdapter):
    provider = "openai"
    RATES = RATES
