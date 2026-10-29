"""The chat turn keeps the database off the path to the first token.

Every event-log row is buffered in memory and written once, after the answer and
`done` have been sent. These tests count store round trips with a spy so a
regression (a write sneaking back in front of the first token) fails loudly.
"""

import asyncio
import threading

import pytest

from backend import budget
from backend.chat import run_turn
from backend.chat.planner import Plan, Specialist
from backend.config import settings
from backend.events import EventStore, EventType
from backend.rag import knowledge_base


class SpyStore:
    """Counts every store call. Only `record_run` is legitimate on the chat path."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.recorded: dict | None = None
        self.fail = fail

    async def record_run(self, **kw):
        self.calls.append("record_run")
        if self.fail:
            raise RuntimeError("turso unreachable")
        self.recorded = kw
        return list(range(1, len(kw["events"]) + 1))

    def __getattr__(self, name):  # any other call is a round trip we did not budget for
        async def _unexpected(*a, **k):
            self.calls.append(name)

        return _unexpected


def _fast_stream(monkeypatch, pieces=("Hello", " there")):
    async def fake_stream(question, model, *, history=None, temperature=0.3, fallback_model=None):
        for p in pieces:
            yield p

    monkeypatch.setattr("backend.chat.session.stream_direct", fake_stream)


async def test_no_db_round_trip_before_first_token_and_one_batched_write(monkeypatch):
    _fast_stream(monkeypatch)
    store = SpyStore()
    seen_at: dict[str, int] = {}
    async for e in run_turn("hi", models=["ollama:x:1"], store=store):
        seen_at.setdefault(e["event"], len(store.calls))
        if e["event"] == "done":
            # `done` has been delivered; the single write has not happened yet.
            assert store.calls == []
    assert seen_at["token"] == 0  # zero round trips before the first token
    assert seen_at["done"] == 0
    assert store.calls == ["record_run"]  # exactly one batched write overall
    assert store.recorded["status"] == "done"


async def test_flush_failure_still_delivers_answer_and_done(monkeypatch):
    _fast_stream(monkeypatch)
    store = SpyStore(fail=True)
    events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=store)]
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["answer"] == "Hello there"
    assert store.calls == ["record_run"]


async def test_hung_flush_does_not_hold_the_stream_open(monkeypatch):
    _fast_stream(monkeypatch)
    monkeypatch.setattr("backend.chat.session.FLUSH_TIMEOUT_S", 0.05)

    class Hung(SpyStore):
        async def record_run(self, **kw):
            await asyncio.sleep(30)

    events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=Hung())]
    assert events[-1]["event"] == "done"


async def test_open_store_is_only_opened_at_flush_time(monkeypatch):
    _fast_stream(monkeypatch)
    opened: list[str] = []
    inner = SpyStore()

    class Lazy:
        async def __aenter__(self):
            opened.append("enter")
            return inner

        async def __aexit__(self, *a):
            opened.append("exit")

    at_first_token: list[int] = []
    async for e in run_turn("hi", models=["ollama:x:1"], open_store=Lazy):
        if e["event"] == "token" and not at_first_token:
            at_first_token.append(len(opened))
    assert at_first_token == [0]
    assert opened == ["enter", "exit"] and inner.calls == ["record_run"]


# ------------------------------------------- persisted log == the old behaviour


def _council_plan(question, model):
    async def _p():
        return Plan(intent="compare", complexity="moderate", rationale="r", source="model",
                    specialists=[Specialist(id="cost", role="costs"),
                                 Specialist(id="risk", role="risks")])

    return _p()


async def test_council_turn_persists_the_same_event_sequence(monkeypatch, tmp_path):
    async def contribute(question, aspect, role, model, **kw):
        if aspect == "risk":
            return {"aspect": aspect, "points": [], "error": "boom"}
        return {"aspect": aspect, "points": [{"claim": "c", "support": "s"}], "model": model,
                "tokens": 5, "latency_ms": 7, "cost_usd": 0.001, "text": "{}",
                "tokens_in": 2, "tokens_out": 3, "input": "p"}

    async def synth(question, contributions, model, *, history=None, fallback_model=None):
        yield "the "
        yield "answer"

    monkeypatch.setattr("backend.chat.session.plan_for", _council_plan)
    monkeypatch.setattr("backend.chat.session.contribute", contribute)
    monkeypatch.setattr("backend.chat.session.stream_synthesis", synth)

    async with EventStore(url=f"file:{tmp_path}/e.db") as s:
        events = [e async for e in run_turn(
            "compare a and b", models=["ollama:x:1", "groq:y"], store=s)]
        run_id = events[0]["data"]["run_id"]
        bench = next(e["data"] for e in events if e["event"] == "benchmarks")
        rows = await s.read(run_id)
        run = await s.get_run(run_id)
        cost = await s.aggregate_cost(run_id)

    t = EventType
    assert [(r.event_type, r.node_id) for r in rows] == [
        (t.RUN_START, None), (t.GRAPH_PROPOSED, None),
        (t.NODE_START, "cost"), (t.NODE_START, "risk"),
        (t.TOOL_RESULT, "cost"), (t.HANDOFF_VALIDATED, "cost"), (t.NODE_END, "cost"),
        (t.BRANCH_FAILED, "risk"),
        (t.TOOL_RESULT, "answer"), (t.RUN_END, None),
    ]
    assert [r.seq for r in rows] == list(range(1, 11))
    assert rows[0].payload == {"mode": "chat", "question": "compare a and b"}
    assert rows[2].payload == {"role": "costs"}
    assert rows[8].payload["text"] == "the answer" and rows[8].latency_ms is not None
    assert rows[9].payload["benchmarks"]["run_id"] == bench["run_id"] == run_id
    assert (rows[4].tokens_in, rows[4].tokens_out) == (2, 3)
    assert run.status == "done" and run.ended_at and run.last_seq == 10
    assert run.config_hash == rows[1].payload["config_hash"]
    assert cost.tokens_in == 2 and cost.tokens_out == 3


async def test_failed_answer_records_failed_run_without_answer_row(monkeypatch, tmp_path):
    async def boom(question, model, *, history=None, temperature=0.3, fallback_model=None):
        raise RuntimeError("down")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.chat.session.stream_direct", boom)
    async with EventStore(url=f"file:{tmp_path}/e.db") as s:
        events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=s)]
        rid = events[0]["data"]["run_id"]
        rows = await s.read(rid)
        run = await s.get_run(rid)
    assert [r.event_type for r in rows] == [
        EventType.RUN_START, EventType.GRAPH_PROPOSED, EventType.RUN_END]
    assert run.status == "failed"


# ----------------------------------------------------- the other start-up costs


def test_non_blocking_budget_check_never_does_database_io_on_the_caller(monkeypatch):
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    ran_on: list[int] = []
    done = threading.Event()

    def fake_refresh():
        ran_on.append(threading.get_ident())
        done.set()

    monkeypatch.setattr(budget, "_background_refresh", fake_refresh)
    budget.ensure_available(blocking=False)  # cold: refresh is due
    assert done.wait(2)
    assert ran_on[0] != threading.get_ident()
    done.clear()
    budget.ensure_available(blocking=False)  # claimed: no second refresh within 5s
    assert not done.wait(0.2)


def test_non_blocking_budget_check_still_enforces_in_process_usage(monkeypatch):
    monkeypatch.setattr(settings, "daily_token_cap", 100)
    monkeypatch.setattr(budget, "_background_refresh", lambda: None)
    budget.record(500)
    with pytest.raises(budget.DailyCapReached):
        budget.ensure_available(blocking=False)


def test_empty_workspace_lookup_is_cached(monkeypatch):
    from backend.rag import index

    loads: list[str] = []
    real = index.Workspace.load_from_store

    def counting(self):
        loads.append(self.workspace_id)
        return real(self)

    monkeypatch.setattr(index.Workspace, "load_from_store", counting)
    assert knowledge_base.get("empty-ws-01", create=False) is None
    assert knowledge_base.get("empty-ws-01", create=False) is None
    assert loads == ["empty-ws-01"]  # one storage read, not two
    # An upload (create=True) bypasses the cache and is then served from memory.
    ws = knowledge_base.get("empty-ws-01")
    ws.add_document("a.md", "hello world document")
    assert knowledge_base.get("empty-ws-01", create=False) is ws


async def test_record_run_is_a_single_hrana_batch_on_turso(monkeypatch):
    """Remote path: BEGIN, run insert, N event inserts, COMMIT, ROLLBACK guard
    all travel in one `_run_batch` call (one HTTP request)."""
    from backend.events import store as store_mod

    sent: list[list[dict]] = []

    def fake_batch(session, steps):
        sent.append(steps)
        return [{"affected_row_count": 1}] * len(steps), [None] * len(steps)

    class Conn:
        _session = type("S", (), {"_baton": None})()

    monkeypatch.setattr(store_mod, "_run_batch", fake_batch)
    s = EventStore(url="libsql://example.invalid", token="x")  # noqa: S106
    s._conn_obj = Conn()
    seqs = await s.record_run(
        "r1", "goal", "hash", 1, {"nodes": []},
        [{"event_type": EventType.RUN_START}, {"event_type": EventType.RUN_END}],
        status="done", ended_at="2026-01-01T00:00:00+00:00")
    assert seqs == [1, 2]
    assert len(sent) == 1 and len(sent[0]) == 6  # BEGIN, run, 2 events, COMMIT, ROLLBACK
