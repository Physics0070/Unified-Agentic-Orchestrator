"""Run watchdog, stale-run healing, the two-step serverless flow, store timeouts."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend.api import routes
from backend.config import settings
from backend.council.compiler import build_graph
from backend.events import EventStore, EventType
from backend.events.store import EventStoreError
from backend.limiter import limiter
from backend.main import app

GOAL = "Compare REST and GraphQL for a small team"


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def _graph():
    return build_graph([{"id": "a", "role": "A"}], ["ollama:x:1"], verifier_model="ollama:x:1",
                       intent="explain")


async def _no_dna(*a, **k):
    return None


class Hangs:
    def __init__(self, store, graph, run_id, **kw):
        self.store, self.run_id = store, run_id

    async def run(self):
        await self.store.append(self.run_id, EventType.RUN_START, payload={})
        await asyncio.sleep(3600)


class Fast:
    calls = 0

    def __init__(self, store, graph, run_id, **kw):
        self.store, self.run_id = store, run_id

    async def run(self):
        Fast.calls += 1
        await self.store.append(self.run_id, EventType.RUN_START, payload={})
        await self.store.append_and_set_status(
            self.run_id, EventType.RUN_END, {"ok": True}, status="done", ended=True)


def test_watchdog_fails_a_run_that_never_finishes(monkeypatch):
    monkeypatch.setattr(routes, "Executor", Hangs)
    monkeypatch.setattr(routes, "record_and_log", _no_dna)
    monkeypatch.setattr(settings, "run_timeout_s", 0.3)

    async def go():
        g = _graph()
        async with EventStore() as s:
            await s.create_run("r1", GOAL, g.config_hash, 1, g.model_dump(), "pending")
        await routes._execute_run("r1", g, GOAL, 1, set(), "", None)
        async with EventStore() as s:
            return await s.get_run("r1"), await s.read("r1")

    row, events = asyncio.run(go())
    assert row.status == "failed" and row.ended_at
    end = [e for e in events if e.event_type == EventType.RUN_END]
    assert len(end) == 1
    assert end[0].payload == {"ok": False, "reason": "run_timeout"}


def test_stuck_running_run_reads_as_failed(monkeypatch):
    monkeypatch.setattr(settings, "run_timeout_s", 270)

    async def setup():
        async with EventStore() as s:
            await s.create_run("old", GOAL, "h", 1, {}, "running")
            await s.create_run("fresh", GOAL, "h", 1, {}, "running")
            await s._run(lambda c: (
                c.execute("UPDATE runs SET started_at = ? WHERE run_id = 'old'",
                          ["2020-01-01T00:00:00+00:00"]), c.commit()))

    asyncio.run(setup())
    c = TestClient(app)
    old = c.get("/api/runs/old").json()["run"]
    assert old["status"] == "failed" and "abandoned" in old["note"]
    assert c.get("/api/runs/fresh").json()["run"]["status"] == "running"


def test_execute_endpoint_runs_once_then_409(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(routes, "Executor", Fast)
    monkeypatch.setattr(routes, "predict_and_log", _no_dna)
    monkeypatch.setattr(routes, "record_and_log", _no_dna)
    Fast.calls = 0
    c = TestClient(app)
    body = {"goal": GOAL, "graph": _graph().model_dump(), "approvals": []}
    started = c.post("/api/runs", json=body)
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    assert started.json()["status"] == "pending"
    assert Fast.calls == 0

    done = c.post(f"/api/runs/{run_id}/execute", json={"approvals": []})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"

    again = c.post(f"/api/runs/{run_id}/execute", json={"approvals": []})
    assert again.status_code == 409
    assert Fast.calls == 1
    assert c.post("/api/runs/nope/execute", json={}).status_code == 404
    assert routes._TASKS == {}


def test_store_operation_timeout_raises_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(settings, "db_op_timeout_s", 0.1)

    async def go():
        async with EventStore() as s:
            started = time.monotonic()
            with pytest.raises(EventStoreError, match="timed out"):
                await s._run(lambda c: time.sleep(1))
            assert time.monotonic() - started < 0.9
            # the lock was released: the store is still usable afterwards
            await asyncio.sleep(1)
            # a real write can exceed 0.1s on a slow CI machine
            monkeypatch.setattr(settings, "db_op_timeout_s", 30)
            await s.create_run("r", GOAL, "h", 1, {}, "pending")

    asyncio.run(go())


def test_a_finished_run_is_not_failed_by_slow_bookkeeping(monkeypatch, tmp_path):
    """Workflow DNA recording runs after the executor; it must not be under the watchdog."""
    import asyncio

    from backend.api import routes
    from backend.config import settings
    from backend.council.compiler import build_graph
    from backend.events.store import EventStore

    class Quick:
        def __init__(self, store, graph, run_id, **kw):
            self.store, self.run_id = store, run_id

        async def run(self):
            await self.store.append_and_set_status(
                self.run_id, routes.EventType.RUN_END, {"ok": True}, status="done", ended=True)

    async def slow_dna(*a, **k):
        await asyncio.sleep(1.5)

    monkeypatch.setattr(settings, "turso_database_url", f"file:{tmp_path / 'w.db'}")
    monkeypatch.setattr(settings, "run_timeout_s", 1)
    monkeypatch.setattr(routes, "Executor", Quick)
    monkeypatch.setattr(routes, "record_and_log", slow_dna)
    g = build_graph([{"id": "a", "role": "A"}], ["ollama:x:1"], verifier_model="ollama:x:1",
                    intent="explain")

    async def go():
        async with EventStore() as s:
            await s.create_run("r1", "goal goal goal", g.config_hash, 1, g.model_dump(), "running")
        await routes._execute_run("r1", g, "goal goal goal", 1, set(), "", None)
        async with EventStore() as s:
            return await s.get_run("r1")

    assert asyncio.run(go()).status == "done"
