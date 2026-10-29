"""Global daily token/spend cap - protects the public site's paid model key.

One counter per UTC day. It is durable: the total lives in the same database as
the EventStore/KnowledgeStore (table `usage_daily`), so a restart or a Vercel cold
start does not forgive the day's usage. To avoid a DB round trip per model call,
usage is accumulated in process and flushed as an atomic increment at most every
FLUSH_SECONDS; the cap check re-reads the shared total at most every REFRESH_SECONDS.
If the database is unavailable the counter degrades to process memory with a
logged warning - a request is never failed because of it. Per-IP rate limits
(backend/limiter.py) bound the damage in that window.
"""

import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from backend.config import settings
from backend.events.store import _sqlite_path

log = logging.getLogger("aco.budget")

FLUSH_SECONDS = 2.0
REFRESH_SECONDS = 5.0

_DDL = (
    "CREATE TABLE IF NOT EXISTS usage_daily ("
    " day TEXT PRIMARY KEY,"
    " tokens INTEGER NOT NULL DEFAULT 0,"
    " cost_usd REAL NOT NULL DEFAULT 0)"
)


class DailyCapReached(RuntimeError):  # noqa: N818 - reads better as a state than an error
    """Today's token cap is spent. Mapped to HTTP 429 in backend/main.py."""

    def __init__(self) -> None:
        super().__init__("daily_cap_reached")


_lock = threading.RLock()
_day: date = datetime.now(UTC).date()
_base_tokens = 0       # shared total as of the last DB read
_base_cost = 0.0
_pend_tokens = 0       # recorded here, not yet written to the DB
_pend_cost = 0.0
_last_flush = 0.0
_last_refresh = 0.0    # 0 = never read: first check loads the shared total
_migrated: set[str] = set()
_warned = False


def _warn(err: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("usage_daily unavailable (%s: %s); daily cap is per-process until it "
                    "recovers", type(err).__name__, err)


def _connect():
    url, token = settings.turso_database_url, settings.turso_auth_token
    if not url or url.startswith("file:"):
        path = _sqlite_path(url or "")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=10)
        c.execute("PRAGMA journal_mode = WAL")
        return c, path
    if not token:
        raise RuntimeError("TURSO_AUTH_TOKEN must be set")
    import turso_serverless

    return turso_serverless.connect(url, auth_token=token), url


def _db(fn):
    """Run fn(conn) against the shared database, creating the table lazily."""
    c, key = _connect()
    with closing(c):
        if key not in _migrated:
            c.execute(_DDL)
            c.commit()
            _migrated.add(key)
        return fn(c)


def _roll() -> None:
    """New UTC day: flush what belongs to yesterday, then start from zero."""
    global _day, _base_tokens, _base_cost, _last_refresh
    today = datetime.now(UTC).date()
    if today != _day:
        _flush_locked()
        _day, _base_tokens, _base_cost, _last_refresh = today, 0, 0.0, 0.0
        _drop_pending()


def _drop_pending() -> None:
    global _pend_tokens, _pend_cost
    _pend_tokens, _pend_cost = 0, 0.0


def _flush_locked() -> None:
    """Atomically add pending usage to the DB; keep it pending on failure."""
    global _base_tokens, _base_cost, _last_flush
    global _pend_tokens, _pend_cost
    _last_flush = time.monotonic()
    if _pend_tokens == 0 and _pend_cost == 0.0:
        return
    tokens, cost, day = _pend_tokens, _pend_cost, _day.isoformat()

    def _do(c):
        c.execute(
            "INSERT INTO usage_daily (day, tokens, cost_usd) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET tokens = tokens + excluded.tokens, "
            "cost_usd = cost_usd + excluded.cost_usd", [day, tokens, cost])
        c.commit()

    try:
        _db(_do)
    except Exception as e:  # sqlite3.Error, turso errors, OSError - never fail a request
        _warn(e)
        return
    _base_tokens += tokens
    _base_cost += cost
    _pend_tokens, _pend_cost = _pend_tokens - tokens, _pend_cost - cost


def _refresh_locked() -> None:
    """Pull the shared total (other instances' usage included), at most every 5s."""
    global _base_tokens, _base_cost, _last_refresh
    now = time.monotonic()
    if _last_refresh and now - _last_refresh < REFRESH_SECONDS:
        return
    _flush_locked()
    try:
        row = _db(lambda c: c.execute(
            "SELECT tokens, cost_usd FROM usage_daily WHERE day = ?",
            [_day.isoformat()]).fetchone())
    except Exception as e:
        _warn(e)
        _last_refresh = now  # do not hammer a down database on every call
        return
    _base_tokens, _base_cost = (int(row[0]), float(row[1])) if row else (0, 0.0)
    _last_refresh = now


def forget() -> None:
    """Drop in-memory state only, as a fresh process would start (tests)."""
    global _day, _base_tokens, _base_cost, _last_flush, _last_refresh, _warned
    with _lock:
        _day = datetime.now(UTC).date()
        _base_tokens, _base_cost, _last_flush, _last_refresh, _warned = 0, 0.0, 0.0, 0.0, False
        _drop_pending()
        _migrated.clear()


def flush() -> None:
    """Write pending usage now (call at the end of a request if you can)."""
    with _lock:
        _roll()
        _flush_locked()


def record(tokens: int, cost_usd: float = 0.0, *, defer_flush: bool = False) -> None:
    """Add one model call's usage to today's total.

    `defer_flush` skips the periodic DB write: it is a blocking HTTP round trip
    and would stall the event loop in the middle of a token stream. The caller
    then owns calling `flush()` (off the loop) once the stream is over.
    """
    global _pend_tokens, _pend_cost
    with _lock:
        _roll()
        _pend_tokens += max(0, int(tokens))
        _pend_cost += max(0.0, float(cost_usd))
        if not defer_flush and time.monotonic() - _last_flush >= FLUSH_SECONDS:
            _flush_locked()


def estimate_tokens(text: str) -> int:
    """Rough ~4 chars/token, for streams where the provider reports no usage."""
    return max(1, len(text) // 4) if text else 0


def usage() -> dict:
    with _lock:
        _roll()
        _refresh_locked()
        tokens, cost = _base_tokens + _pend_tokens, _base_cost + _pend_cost
        day = _day.isoformat()
    cap = settings.daily_token_cap
    return {
        "date": day,
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "daily_token_cap": cap,
        "remaining": None if cap <= 0 else max(0, cap - tokens),
        "cap_reached": cap > 0 and tokens >= cap,
    }


def _background_refresh() -> None:
    """Re-read the shared total without holding `_lock` during the network I/O,
    so a streaming turn calling `record()` never waits behind it."""
    global _base_tokens, _base_cost
    with _lock:
        day = _day
    try:
        row = _db(lambda c: c.execute(
            "SELECT tokens, cost_usd FROM usage_daily WHERE day = ?",
            [day.isoformat()]).fetchone())
    except Exception as e:
        _warn(e)
        return
    with _lock:
        if _day == day:
            _base_tokens, _base_cost = (int(row[0]), float(row[1])) if row else (0, 0.0)


def ensure_available(*, blocking: bool = True) -> None:
    """Raise DailyCapReached if the cap is on and already spent. 0 = unlimited.

    `blocking=False` is for the chat hot path: it checks the in-memory total and,
    when that is older than REFRESH_SECONDS, claims the refresh and runs it on a
    background thread instead of making this request wait on 1-3 database round
    trips. The request that triggers it is judged on the slightly stale total
    (on a cold instance: this process's own usage); the next one sees the fresh
    value. The cap is a spend guard, not a billing ledger, so one request of lag
    is the right price for not putting Turso latency in front of the first token.
    """
    if blocking:
        if usage()["cap_reached"]:
            raise DailyCapReached
        return
    global _last_refresh
    with _lock:
        _roll()
        stale = not _last_refresh or time.monotonic() - _last_refresh >= REFRESH_SECONDS
        if stale:
            _last_refresh = time.monotonic()  # claim it: concurrent requests skip
        cap = settings.daily_token_cap
        tokens = _base_tokens + _pend_tokens
    if stale:
        threading.Thread(target=_background_refresh, name="budget-refresh", daemon=True).start()
    if cap > 0 and tokens >= cap:
        raise DailyCapReached


def reset() -> None:
    """Zero today's counters, in memory and in the database (tests, admin)."""
    global _base_tokens, _base_cost, _last_refresh
    with _lock:
        _roll()
        _drop_pending()
        _base_tokens, _base_cost = 0, 0.0
        try:
            _db(lambda c: (c.execute("DELETE FROM usage_daily WHERE day = ?",
                                     [_day.isoformat()]), c.commit()))
        except Exception as e:
            _warn(e)
        _last_refresh = time.monotonic()
