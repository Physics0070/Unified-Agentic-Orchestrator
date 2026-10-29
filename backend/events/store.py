"""The event log. Every other module reads from here.

Talks to Turso (libSQL) over its serverless HTTP driver. SQLite has no
server-side functions, so the atomic, gapless sequence allocation that used to
live in a Postgres `append_event` function (see migrations/001_events.sql) now
lives here: `UPDATE ... RETURNING` bumps `last_seq` and hands back the new
value, a dependent `INSERT` uses it, both commit as one transaction.

One connection is opened per `EventStore` lifetime (every call site already
uses `async with EventStore() as store:`, scoped to one run) and reused for
every operation, all serialised through one `asyncio.Lock`. This was not the
original design - opening a fresh connection per call was simpler but was
measured opening 20 fresh connections at once drops some of them entirely
(`turso_serverless` is a young, HTTP-only driver with no connection-pooling
story of its own) and adds a full TLS handshake to every single append. A
shared connection under a lock fixed both: zero dropped connections and roughly
40% faster under the same concurrent load, verified against the live database
before landing here.
"""

import asyncio
import http.client
import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse, urlsplit
from urllib.request import url2pathname

import turso_serverless
from turso_serverless.protocol import ProtocolError, build_batch_step, decode_value

from backend.config import settings
from backend.events.types import CostTotals, Event, EventType, LatencyTotals, RunRow

log = logging.getLogger("aco.events")

_MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "001_events.sql"
_DEFAULT_SQLITE = "data/events.db"  # `data/` is git-ignored

# The migration drops and recreates the append-only triggers, so two stores
# running it at once collide ("trigger already exists"), and between the DROP and
# the CREATE the log is briefly not append-only. Every request opens its own
# EventStore, so it runs once per database file per process, under a lock.
# Across processes the same script runs inside BEGIN IMMEDIATE, so sqlite's own
# write lock makes the drop/create pair atomic and a second process waits (up to
# the connect timeout) instead of colliding or seeing a trigger-less window.
_MIGRATED: set[str] = set()
_MIGRATE_LOCK = threading.Lock()
_WARNED_FALLBACK = False
# Databases whose `runs` table is known to have the nullable `user_id` column
# (accounts). Added lazily, once per process, by `_ensure_user_column`; the value
# is False when the ALTER could not be applied, in which case every query falls
# back to the pre-accounts SQL so a permissions problem cannot take runs down.
_USER_COL: dict[str, bool] = {}


def _ensure_user_column(c, key: str) -> bool:
    """`ALTER TABLE runs ADD COLUMN user_id TEXT`, guarded for already-exists.

    Appending a nullable column leaves the append-only triggers (on `events`)
    and every existing row untouched. Works the same on sqlite and Turso.
    """
    if key in _USER_COL:
        return _USER_COL[key]
    ok = False
    try:
        c.execute("ALTER TABLE runs ADD COLUMN user_id TEXT")
        ok = True
    except Exception as e:  # sqlite3.Error or the turso driver's own errors
        if "duplicate column" in str(e).lower():
            ok = True
        else:
            log.warning("runs.user_id could not be added (%s: %s); per-user runs are off",
                        type(e).__name__, e)
    try:
        c.commit()
    except Exception:  # noqa: S110 - autocommit connections have nothing to commit
        pass
    _USER_COL[key] = ok
    return ok


def _sqlite_path(url: str) -> str:
    """`file:rel/path`, `file:///abs/path` (and `file:///C:/x` on Windows)."""
    if not url:
        return _DEFAULT_SQLITE
    if url.startswith("file://"):
        return url2pathname(urlparse(url).path) or _DEFAULT_SQLITE
    return url[len("file:"):] or _DEFAULT_SQLITE

_INSERT_EVENT = (
    "INSERT INTO events (run_id, seq, node_id, agent_id, event_type, payload, "
    "tokens_in, tokens_out, cost_usd, latency_ms, ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
# Same columns, but the seq is derived inside the transaction from the value the
# preceding UPDATE just wrote to runs.last_seq (minus the rows still to come).
_INSERT_EVENT_FROM_RUN = (
    "INSERT INTO events (run_id, seq, node_id, agent_id, event_type, payload, "
    "tokens_in, tokens_out, cost_usd, latency_ms, ts) "
    "SELECT ?, last_seq - ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM runs WHERE run_id = ?"
)


def _event_row(
    event_type, node_id, agent_id, payload, tokens_in, tokens_out, cost_usd, latency_ms, ts
) -> tuple:
    """The 9 non-key columns of one events row, in INSERT order after seq."""
    return (
        node_id, agent_id, str(event_type),
        json.dumps(payload) if payload is not None else None,
        tokens_in, tokens_out, cost_usd, latency_ms, ts,
    )


def _status_sql(run_id: str, status: tuple[str, str | None]) -> tuple[str, list]:
    new_status, ended_at = status
    if ended_at is not None:
        return (
            "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ?",
            [new_status, ended_at, run_id],
        )
    return "UPDATE runs SET status = ? WHERE run_id = ?", [new_status, run_id]


def _set_status(c, run_id: str, status: tuple[str, str | None]) -> None:
    sql, args = _status_sql(run_id, status)
    c.execute(sql, args)


def _run_batch(session, steps: list[dict]) -> tuple[list, list]:
    """Send `steps` as one Hrana pipeline `batch` request on a throwaway stream.

    Stateless on purpose (baton null, stream closed in the same request): nothing
    carries over between calls, so an idle gap can never leave us holding an
    expired stream. Returns (step_results, step_errors)."""
    body = {
        "baton": None,
        "requests": [{"type": "batch", "batch": {"steps": steps}}, {"type": "close"}],
    }
    try:
        resp = json.loads(session._post("/v3/pipeline", body))
        first = (resp.get("results") or [None])[0] or {}
        if first.get("type") == "error":
            err = first.get("error") or {}
            raise EventStoreError(f"event store rejected the operation: {err.get('message')}")
        result = first["response"]["result"]
        return result["step_results"], result["step_errors"]
    except (ValueError, KeyError, TypeError, ProtocolError) as e:
        session._reset_stream()
        raise EventStoreError(f"event store request failed: {type(e).__name__}: {e}") from e


_IDLE_MAX_S = 15.0  # drop a pooled socket idle longer than this rather than trust it


def _install_keepalive(session, timeout: float | None) -> None:
    """Make `turso_serverless` reuse one HTTP connection.

    Its Session posts with `urllib.request.urlopen`, which opens a brand new
    TCP + TLS connection for EVERY statement (~0.7s each to a remote region -
    the measured cause of ~3s per event, 4 statements per append). Replace its
    `_post` with one that keeps a single `http.client` connection alive. Same
    contract as the original: any failure resets the stream and raises
    ProtocolError; the one addition is a single transparent retry when a REUSED
    socket turns out to have been closed by the server before it answered.
    """
    state: dict = {"conn": None, "netloc": None, "used": 0.0}

    def drop() -> None:
        conn, state["conn"] = state["conn"], None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def _post(path: str, body: dict) -> bytes:
        parts = urlsplit(f"{session._base_url}{path}")
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        for attempt in (0, 1):
            if state["conn"] is not None and (
                state["netloc"] != parts.netloc or time.monotonic() - state["used"] > _IDLE_MAX_S
            ):
                drop()
            reused = state["conn"] is not None
            if not reused:
                cls = (
                    http.client.HTTPSConnection if parts.scheme == "https"
                    else http.client.HTTPConnection
                )
                state["conn"], state["netloc"] = cls(parts.netloc, timeout=timeout), parts.netloc
            try:
                state["conn"].request("POST", parts.path, body=data, headers=session._headers())
                resp = state["conn"].getresponse()
                raw = resp.read()
            except (http.client.HTTPException, OSError) as e:
                drop()
                stale = isinstance(
                    e, http.client.RemoteDisconnected | ConnectionResetError | BrokenPipeError
                )
                if reused and attempt == 0 and stale:
                    continue
                session._reset_stream()
                raise ProtocolError(f"request to {parts.netloc} failed: {e!r}") from None
            state["used"] = time.monotonic()
            if resp.status != 200:
                session._reset_stream()
                message = None
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        message = next(
                            (parsed[k] for k in ("error", "message")
                             if isinstance(parsed.get(k), str)),
                            None,
                        )
                except ValueError:
                    pass
                detail = f": {message}" if message is not None else ""
                raise ProtocolError(f"HTTP status {resp.status}{detail}")
            return raw
        raise ProtocolError("unreachable")  # pragma: no cover

    session._post = _post


class EventStoreError(RuntimeError):
    """Persistence failed. Message is safe to log; it never contains the key."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EventStore:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self._url = url or settings.turso_database_url
        self._token = token or settings.turso_auth_token
        # Blank URL or `file:` => local stdlib sqlite3, no Turso account needed.
        self._sqlite = not self._url or self._url.startswith("file:")
        if self._sqlite:
            self._path = _sqlite_path(self._url or "")
            global _WARNED_FALLBACK
            if not self._url and not _WARNED_FALLBACK:
                _WARNED_FALLBACK = True
                log.warning(
                    "TURSO_DATABASE_URL is blank: using a local SQLite file at %s. On an "
                    "ephemeral or read-only host (Render free, Vercel) the event log will "
                    "not survive a redeploy - set TURSO_DATABASE_URL for durable history.",
                    self._path,
                )
        elif not self._token:
            raise EventStoreError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set")
        # Guards the single shared connection: every operation goes through
        # this lock, not just append's read-modify-write, because a sync
        # DB-API connection is not safe under concurrent access from the
        # different worker threads asyncio.to_thread hands calls to.
        self._lock = asyncio.Lock()
        self._conn_obj: Any = None
        # Set when the connection opens: does `runs` have a user_id column?
        self._has_user = False

    @property
    def _uid_col(self) -> str:
        """SELECT-list fragment: the real column, or NULL on a database that
        could not take the ALTER."""
        return "user_id" if self._has_user else "NULL"

    async def __aenter__(self) -> Self:
        def _open_sqlite():
            try:
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                # The WAL switch needs an exclusive lock too, so a first open racing
                # another one fails "database is locked" - serialise the whole setup.
                with _MIGRATE_LOCK:
                    c = sqlite3.connect(self._path, check_same_thread=False, timeout=30)
                    c.execute("PRAGMA journal_mode = WAL")
                    c.execute("PRAGMA foreign_keys = ON")
                    if self._path not in _MIGRATED:
                        # executescript commits first; BEGIN IMMEDIATE then takes the
                        # write lock before any DDL, so other processes queue behind it.
                        script = _MIGRATION.read_text()
                        c.executescript(f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;")
                        _MIGRATED.add(self._path)
                    self._has_user = _ensure_user_column(c, self._path)
                return c
            except (OSError, sqlite3.Error) as e:
                # Unwrapped, a read-only filesystem or a locked file escaped as
                # a bare 500 instead of the 503 that tells you what is wrong.
                log.error("sqlite event log %s could not be opened: %s", self._path, e)
                raise EventStoreError(
                    f"local event log could not be opened ({type(e).__name__}); "
                    "set TURSO_DATABASE_URL, or point it at a writable file: path"
                ) from e

        def _open():
            # isolation_level=None: no implicit BEGIN/COMMIT around DML. Every
            # statement here is a single atomic autocommit one (multi-statement
            # appends go through `_remote_append`), which saves 2 round trips each.
            c = turso_serverless.connect(
                self._url, auth_token=self._token, isolation_level=None
            )
            _install_keepalive(c._session, settings.db_op_timeout_s or None)
            with _MIGRATE_LOCK:
                self._has_user = _ensure_user_column(c, self._url)
            # No `PRAGMA foreign_keys = ON` here: every operation runs on a
            # throwaway Hrana stream (baton reset in `_run`, `_run_batch`), so a
            # per-connection pragma never applied to them - it only cost one
            # round trip on every open, before the first query.
            return c

        self._conn_obj = await asyncio.to_thread(_open_sqlite if self._sqlite else _open)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._conn_obj is not None:
            conn, self._conn_obj = self._conn_obj, None
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(conn.close), settings.db_op_timeout_s or None
                )
            except TimeoutError:
                log.error("event store close timed out; abandoning the connection")

    def _run(self, fn) -> Any:
        """Run a synchronous DB-API sequence on the shared connection.

        `fn` receives the open connection and must commit before returning.
        Serialised by `self._lock` - see the module docstring for why.
        """
        if self._conn_obj is None:
            raise EventStoreError("EventStore used outside its async context")

        driver_error = sqlite3.Error if self._sqlite else turso_serverless.dbapi.Error

        def _call():
            try:
                return fn(self._conn_obj)
            except (driver_error, ProtocolError) as e:
                log.error("turso error: %s: %s", type(e).__name__, e)
                raise EventStoreError(f"event store rejected the operation: {e}") from e
            finally:
                if not self._sqlite and self._conn_obj is not None:
                    # Each op is one autocommit request: don't resume the same
                    # server stream after an idle gap (it may have expired).
                    self._conn_obj._session._baton = None

        async def _locked():
            async with self._lock:
                limit = settings.db_op_timeout_s or None
                try:
                    # The sync driver has no timeout of its own. Without this a
                    # hung HTTP request kept the lock forever and every other
                    # node's append queued behind it - the run froze silently.
                    return await asyncio.wait_for(asyncio.to_thread(_call), limit)
                except TimeoutError as e:
                    log.error("event store operation timed out after %ss", limit)
                    raise EventStoreError(
                        f"event store operation timed out after {limit}s"
                    ) from e

        return _locked()

    # ---------- runs ----------

    async def create_run(
        self,
        run_id: str,
        goal: str,
        config_hash: str,
        seed: int,
        graph_spec: dict,
        status: str = "pending",
        replay_of: str | None = None,
        user_id: str | None = None,
    ) -> RunRow:
        """A run row must exist before any event: it owns the seq allocator.
        `user_id` records the signed-in owner; anonymous runs leave it NULL."""
        started_at = _now()
        owned = bool(user_id) and self._has_user

        def _do(c):
            c.execute(
                "INSERT INTO runs (run_id, goal, config_hash, seed, graph_spec, status, "  # noqa: S608
                "replay_of, last_seq, started_at" + (", user_id" if owned else "")
                + ") VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?" + (", ?" if owned else "") + ")",
                [run_id, goal, config_hash, seed, json.dumps(graph_spec), status,
                 replay_of, started_at, *([user_id] if owned else [])],
            )
            c.commit()

        await self._run(_do)
        return RunRow(
            run_id=run_id, goal=goal, config_hash=config_hash, seed=seed,
            graph_spec=graph_spec, status=status, replay_of=replay_of,
            last_seq=0, started_at=started_at, ended_at=None,
            user_id=user_id if owned else None,
        )

    async def record_run(
        self,
        run_id: str,
        goal: str,
        config_hash: str,
        seed: int,
        graph_spec: dict,
        events: list[dict],
        *,
        status: str,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> list[int]:
        """Create a run AND its whole event log in ONE round trip.

        For a turn whose events were buffered in memory and are persisted once
        it is over (chat). The result is identical to `create_run` followed by
        `append_many(events)` plus a status update: seqs 1..n in list order,
        `last_seq` = n, the final `status`/`ended_at`. Event dicts take the same
        keys as `append_many`, plus an optional `ts`. Atomic: all or nothing.
        """
        started_at = started_at or _now()
        n = len(events)
        rows = [
            _event_row(
                ev["event_type"], ev.get("node_id"), ev.get("agent_id"), ev.get("payload"),
                ev.get("tokens_in", 0), ev.get("tokens_out", 0), ev.get("cost_usd", 0.0),
                ev.get("latency_ms"), ev.get("ts") or started_at,
            )
            for ev in events
        ]
        run_args = [run_id, goal, config_hash, seed, json.dumps(graph_spec), status,
                    None, n, started_at, ended_at]
        run_sql = (
            "INSERT INTO runs (run_id, goal, config_hash, seed, graph_spec, status, "
            "replay_of, last_seq, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

        def _do(c):
            if self._sqlite:
                try:
                    c.execute(run_sql, run_args)
                    c.executemany(_INSERT_EVENT, [(run_id, i + 1, *r) for i, r in enumerate(rows)])
                    c.commit()
                except sqlite3.Error:
                    c.rollback()
                    raise
                return list(range(1, n + 1))
            steps = [build_batch_step("BEGIN", want_rows=False)]

            def add(sql: str, args: list) -> None:
                steps.append(build_batch_step(
                    sql, args=args, want_rows=False,
                    condition={"type": "ok", "step": len(steps) - 1},
                ))

            add(run_sql, run_args)
            for i, r in enumerate(rows):
                add(_INSERT_EVENT, [run_id, i + 1, *r])
            steps.append(build_batch_step(
                "COMMIT", want_rows=False, condition={"type": "ok", "step": len(steps) - 1}
            ))
            steps.append(build_batch_step(
                "ROLLBACK", want_rows=False,
                condition={"type": "not", "cond": {"type": "is_autocommit"}},
            ))
            results, errors = _run_batch(c._session, steps)
            for err in errors:
                if err:
                    msg = err.get("message", "unknown error") if isinstance(err, dict) else str(err)
                    raise EventStoreError(f"event store rejected the operation: {msg}")
            for k in range(1 + n):
                if (results[1 + k] or {}).get("affected_row_count") != 1:
                    raise EventStoreError("event store did not insert the run")
            return list(range(1, n + 1))

        return await self._run(_do)

    async def get_run(self, run_id: str) -> RunRow | None:
        def _do(c):
            cur = c.execute(
                "SELECT run_id, goal, config_hash, seed, graph_spec, status, replay_of, "  # noqa: S608
                f"last_seq, started_at, ended_at, {self._uid_col} FROM runs WHERE run_id = ?",
                [run_id],
            )
            row = list(cur)
            c.commit()
            return row[0] if row else None

        row = await self._run(_do)
        if row is None:
            return None
        return RunRow(
            run_id=row[0], goal=row[1], config_hash=row[2], seed=row[3],
            graph_spec=json.loads(row[4]), status=row[5], replay_of=row[6],
            last_seq=row[7], started_at=row[8], ended_at=row[9], user_id=row[10],
        )

    async def list_runs(self, limit: int = 25, user_id: str | None = None) -> list[RunRow]:
        """Newest first. With `user_id`, only that user's runs."""
        if user_id is not None and not self._has_user:
            return []

        def _do(c):
            where = " WHERE user_id = ?" if user_id is not None else ""
            cur = c.execute(
                "SELECT run_id, goal, config_hash, seed, graph_spec, status, replay_of, "  # noqa: S608
                f"last_seq, started_at, ended_at, {self._uid_col} FROM runs{where} "
                "ORDER BY started_at DESC LIMIT ?",
                [*([user_id] if user_id is not None else []), limit],
            )
            rows = list(cur)
            c.commit()
            return rows

        rows = await self._run(_do)
        return [
            RunRow(
                run_id=r[0], goal=r[1], config_hash=r[2], seed=r[3],
                graph_spec=json.loads(r[4]), status=r[5], replay_of=r[6],
                last_seq=r[7], started_at=r[8], ended_at=r[9], user_id=r[10],
            )
            for r in rows
        ]

    async def set_run_status(self, run_id: str, status: str, ended: bool = False) -> None:
        def _do(c):
            if ended:
                c.execute(
                    "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ?",
                    [status, _now(), run_id],
                )
            else:
                c.execute("UPDATE runs SET status = ? WHERE run_id = ?", [status, run_id])
            c.commit()

        await self._run(_do)

    async def claim_run(self, run_id: str) -> bool:
        """Atomically move a run from 'pending' to 'running'.

        True for exactly one caller; a second concurrent /execute loses and
        must not start the executor again.
        """
        def _do(c):
            cur = c.execute(
                "UPDATE runs SET status = 'running' WHERE run_id = ? AND status = 'pending' "
                "RETURNING run_id",
                [run_id],
            )
            got = bool(list(cur))
            c.commit()
            return got

        return await self._run(_do)

    # ---------- events ----------

    async def append(
        self,
        run_id: str,
        event_type: EventType | str,
        *,
        node_id: str | None = None,
        agent_id: str | None = None,
        payload: dict | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int | None = None,
    ) -> int:
        """Append one event, return its seq. Atomic and gapless per run.

        The lock is process-local, which matches this app's deployment: one
        backend instance, no cross-process writers. On Turso this is ONE HTTP
        round trip (see `_remote_append`); on local sqlite it is one transaction.
        """
        row = _event_row(
            event_type, node_id, agent_id, payload, tokens_in, tokens_out, cost_usd,
            latency_ms, _now(),
        )
        return (await self._append_rows(run_id, [row]))[0]

    async def append_many(
        self,
        run_id: str,
        events: list[dict],
        *,
        node_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[int]:
        """Reserve a contiguous seq range and insert N rows in ONE round trip.

        Each event is a dict: `event_type` (required), `payload`, `latency_ms`,
        `tokens_in`, `tokens_out`, `cost_usd`, `node_id`, `agent_id`. The last
        two default to this call's shared `node_id`/`agent_id` kwargs when
        omitted, so a batch can mix events for different nodes/agents.

        Atomic (all rows or none) and ordered (seqs are contiguous, in list
        order). Use it for events with nothing but wall-clock between them (no
        model call). Deliberately NOT used across a model call: that would delay
        NODE_START's visibility in the trace viewer until the node finishes.
        """
        if not events:
            return []
        ts = _now()
        rows = [
            _event_row(
                ev["event_type"], ev.get("node_id", node_id), ev.get("agent_id", agent_id),
                ev.get("payload"), ev.get("tokens_in", 0), ev.get("tokens_out", 0),
                ev.get("cost_usd", 0.0), ev.get("latency_ms"), ts,
            )
            for ev in events
        ]
        return await self._append_rows(run_id, rows)

    async def append_and_set_status(
        self,
        run_id: str,
        event_type: EventType | str,
        payload: dict | None,
        *,
        status: str,
        ended: bool = False,
        node_id: str | None = None,
        agent_id: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int | None = None,
    ) -> int:
        """One event insert plus one `runs.status` update, one round trip."""
        ts = _now()
        row = _event_row(
            event_type, node_id, agent_id, payload, tokens_in, tokens_out, cost_usd,
            latency_ms, ts,
        )
        seqs = await self._append_rows(run_id, [row], status=(status, ts if ended else None))
        return seqs[0]

    async def _append_rows(
        self,
        run_id: str,
        rows: list[tuple],
        *,
        status: tuple[str, str | None] | None = None,
    ) -> list[int]:
        """Allocate `len(rows)` contiguous seqs and insert them, optionally
        updating `runs.status` (and `ended_at` when given) in the same
        transaction. Returns the seqs in order."""
        n = len(rows)

        def _do(c):
            if not self._sqlite:
                return self._remote_append(c, run_id, rows, status)
            cur = c.execute(
                "UPDATE runs SET last_seq = last_seq + ? WHERE run_id = ? RETURNING last_seq",
                [n, run_id],
            )
            got = list(cur)
            if not got:
                raise EventStoreError(f"unknown run_id {run_id}, create the run before appending")
            first_seq = got[0][0] - n + 1
            try:
                c.executemany(
                    _INSERT_EVENT, [(run_id, first_seq + i, *r) for i, r in enumerate(rows)]
                )
                if status is not None:
                    _set_status(c, run_id, status)
                c.commit()
            except sqlite3.Error:
                c.rollback()  # else the bumped last_seq would ride on the next commit
                raise
            return list(range(first_seq, first_seq + n))

        return await self._run(_do)

    def _remote_append(
        self, c, run_id: str, rows: list[tuple], status: tuple[str, str | None] | None
    ) -> list[int]:
        """The whole append as ONE HTTP request (a Hrana pipeline `batch`).

        BEGIN, bump `runs.last_seq`, insert each row at `last_seq - k` (read back
        from the same transaction), optional status update, COMMIT. Each step is
        conditional on the previous one succeeding; a trailing ROLLBACK, gated on
        "a transaction is still open", cleans up after a failed step. Previously
        this was BEGIN + UPDATE + INSERT + COMMIT = 4 sequential round trips.
        """
        n = len(rows)
        steps = [build_batch_step("BEGIN", want_rows=False)]

        def add(sql: str, args: list, want_rows: bool = False) -> None:
            steps.append(build_batch_step(
                sql, args=args, want_rows=want_rows,
                condition={"type": "ok", "step": len(steps) - 1},
            ))

        add("UPDATE runs SET last_seq = last_seq + ? WHERE run_id = ? RETURNING last_seq",
            [n, run_id], want_rows=True)
        for i, r in enumerate(rows):
            add(_INSERT_EVENT_FROM_RUN, [run_id, n - 1 - i, *r, run_id])
        if status is not None:
            sql, args = _status_sql(run_id, status)
            add(sql, args)
        steps.append(build_batch_step(
            "COMMIT", want_rows=False, condition={"type": "ok", "step": len(steps) - 1}
        ))
        steps.append(build_batch_step(
            "ROLLBACK", want_rows=False,
            condition={"type": "not", "cond": {"type": "is_autocommit"}},
        ))

        results, errors = _run_batch(c._session, steps)
        for err in errors:
            if err:
                msg = err.get("message", "unknown error") if isinstance(err, dict) else str(err)
                raise EventStoreError(f"event store rejected the operation: {msg}")
        bump = results[1] or {}
        if not bump.get("rows"):
            raise EventStoreError(f"unknown run_id {run_id}, create the run before appending")
        last_seq = decode_value(bump["rows"][0][0])
        for k in range(n):
            if (results[2 + k] or {}).get("affected_row_count") != 1:
                raise EventStoreError("event store did not insert the event")
        return list(range(last_seq - n + 1, last_seq + 1))

    async def read(self, run_id: str, from_seq: int = 0) -> list[Event]:
        def _do(c):
            cur = c.execute(
                "SELECT run_id, seq, node_id, agent_id, event_type, payload, tokens_in, "
                "tokens_out, cost_usd, latency_ms, ts FROM events "
                "WHERE run_id = ? AND seq > ? ORDER BY seq ASC",
                [run_id, from_seq],
            )
            rows = list(cur)
            c.commit()
            return rows

        rows = await self._run(_do)
        return [
            Event(
                run_id=r[0], seq=r[1], node_id=r[2], agent_id=r[3], event_type=r[4],
                payload=json.loads(r[5]) if r[5] is not None else None,
                tokens_in=r[6] or 0, tokens_out=r[7] or 0, cost_usd=r[8] or 0.0,
                latency_ms=r[9], ts=r[10],
            )
            for r in rows
        ]

    # ---------- artifacts ----------

    async def put_artifact(
        self,
        run_id: str,
        *,
        seq: int,
        written_by: str,
        content: dict | list,
        node_id: str | None = None,
        storage_path: str | None = None,
    ) -> dict:
        artifact_id = str(uuid.uuid4())
        ts = _now()

        def _do(c):
            c.execute(
                "INSERT INTO artifacts (artifact_id, run_id, seq, written_by, node_id, "
                "content, storage_path, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [artifact_id, run_id, seq, written_by, node_id, json.dumps(content),
                 storage_path, ts],
            )
            c.commit()

        await self._run(_do)
        return {
            "artifact_id": artifact_id, "run_id": run_id, "seq": seq,
            "written_by": written_by, "node_id": node_id, "content": content,
            "storage_path": storage_path, "ts": ts,
        }

    async def list_artifacts(self, run_id: str) -> list[dict]:
        def _do(c):
            cur = c.execute(
                "SELECT artifact_id, run_id, seq, written_by, node_id, content, "
                "storage_path, ts FROM artifacts WHERE run_id = ? ORDER BY seq ASC",
                [run_id],
            )
            rows = list(cur)
            c.commit()
            return rows

        rows = await self._run(_do)
        return [
            {
                "artifact_id": r[0], "run_id": r[1], "seq": r[2], "written_by": r[3],
                "node_id": r[4], "content": json.loads(r[5]), "storage_path": r[6], "ts": r[7],
            }
            for r in rows
        ]

    # ---------- internal (test use only) ----------

    async def _execute(self, sql: str, params: list | None = None) -> None:
        """Run one raw statement and commit. Used by tests that probe database
        guarantees (e.g. the append-only trigger) directly."""

        def _do(c):
            c.execute(sql, params or [])
            c.commit()

        await self._run(_do)

    # ---------- aggregates (metrics read these, they compute nothing new) ----------

    async def aggregate_cost(self, run_id: str) -> CostTotals:
        def _do(c):
            cur = c.execute(
                "SELECT tokens_in, tokens_out, cost_usd FROM events WHERE run_id = ?",
                [run_id],
            )
            rows = list(cur)
            c.commit()
            return rows

        rows = await self._run(_do)
        return CostTotals(
            tokens_in=sum(r[0] or 0 for r in rows),
            tokens_out=sum(r[1] or 0 for r in rows),
            cost_usd=round(sum(float(r[2] or 0) for r in rows), 6),
            events=len(rows),
        )

    async def aggregate_latency(self, run_id: str) -> LatencyTotals:
        """Sum of node latencies vs wall clock - the Parallel Efficiency inputs."""

        def _do(c):
            cur = c.execute(
                "SELECT event_type, latency_ms, ts FROM events WHERE run_id = ? "
                "ORDER BY seq ASC",
                [run_id],
            )
            rows = list(cur)
            c.commit()
            return rows

        rows = await self._run(_do)
        if not rows:
            return LatencyTotals()

        node_latencies = [
            r[1] for r in rows if r[0] == EventType.NODE_END and r[1]
        ]
        stamps = sorted(r[2] for r in rows if r[2])
        wall = 0
        if len(stamps) >= 2:
            first = datetime.fromisoformat(stamps[0])
            last = datetime.fromisoformat(stamps[-1])
            wall = int((last - first).total_seconds() * 1000)

        return LatencyTotals(
            sum_ms=sum(node_latencies),
            wall_clock_ms=max(wall, 1),
            measured_nodes=len(node_latencies),
        )
