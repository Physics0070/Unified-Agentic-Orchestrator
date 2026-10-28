"""Global daily token/spend cap - protects the public site's paid model key.

Process-level on purpose: one counter per UTC day, reset when the date rolls
over. It is not persisted, so a restart forgives the day's usage; per-IP rate
limits (backend/limiter.py) bound the damage in that window. Single event loop,
no awaits between read and write, so no lock is needed.
"""

import logging
from datetime import UTC, date, datetime

from backend.config import settings

log = logging.getLogger("aco.budget")


class DailyCapReached(RuntimeError):  # noqa: N818 - reads better as a state than an error
    """Today's token cap is spent. Mapped to HTTP 429 in backend/main.py."""

    def __init__(self) -> None:
        super().__init__("daily_cap_reached")


_day: date = datetime.now(UTC).date()
_tokens = 0
_cost_usd = 0.0


def _roll() -> None:
    global _day, _tokens, _cost_usd
    today = datetime.now(UTC).date()
    if today != _day:
        _day, _tokens, _cost_usd = today, 0, 0.0


def record(tokens: int, cost_usd: float = 0.0) -> None:
    """Add one model call's usage to today's total."""
    global _tokens, _cost_usd
    _roll()
    _tokens += max(0, int(tokens))
    _cost_usd += max(0.0, float(cost_usd))


def estimate_tokens(text: str) -> int:
    """Rough ~4 chars/token, for streams where the provider reports no usage."""
    return max(1, len(text) // 4) if text else 0


def usage() -> dict:
    _roll()
    cap = settings.daily_token_cap
    return {
        "date": _day.isoformat(),
        "tokens": _tokens,
        "cost_usd": round(_cost_usd, 6),
        "daily_token_cap": cap,
        "remaining": None if cap <= 0 else max(0, cap - _tokens),
        "cap_reached": cap > 0 and _tokens >= cap,
    }


def ensure_available() -> None:
    """Raise DailyCapReached if the cap is on and already spent. 0 = unlimited."""
    if usage()["cap_reached"]:
        raise DailyCapReached


def reset() -> None:
    """Zero today's counters (tests, admin)."""
    global _tokens, _cost_usd
    _roll()
    _tokens, _cost_usd = 0, 0.0
