"""Regression tests for the audit findings: failed answers, github rate limit,
intent-aware permission wording, models_used, and the sqlite migration lock."""

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import github_context
from backend.chat import run_turn
from backend.council import permission_prompt
from backend.events import store as store_mod
from backend.events.store import EventStore
from backend.events.types import EventType
from backend.limiter import limiter
from backend.main import app
from backend.metrics import compute


async def test_failed_answer_stream_marks_trace_and_run_failed(monkeypatch, tmp_path):
    async def boom(question, model, *, history=None, temperature=0.3, fallback_model=None):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.chat.session.stream_direct", boom)

    async with EventStore(url=f"file:{tmp_path}/e.db") as s:
        events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=s)]
        bench = next(e["data"] for e in events if e["event"] == "benchmarks")
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        row = await s.get_run(bench["run_id"])

    assert "The answer stream failed: connection refused" in tokens
    step = next(t for t in bench["trace"] if t["node_id"] == "answer")
    assert step["status"] == "failed" and "connection refused" in step["error"]
    assert bench["status"] == "failed" and bench["error"]
    assert row.status == "failed"


async def test_successful_answer_stays_done(monkeypatch):
    async def ok(question, model, *, history=None, temperature=0.3, fallback_model=None):
        yield "fine"

    monkeypatch.setattr("backend.chat.session.stream_direct", ok)
    events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=None)]
    bench = next(e["data"] for e in events if e["event"] == "benchmarks")
    assert bench["status"] == "done" and bench["error"] is None
    assert next(t for t in bench["trace"] if t["node_id"] == "answer")["status"] == "done"


def test_github_context_is_rate_limited_per_ip(monkeypatch):
    async def fake(repo, token=None, **kw):
        return {"repo": repo, "text": "x", "truncated": False}

    monkeypatch.setattr(github_context, "fetch_repo_context", fake)
    limiter.reset()
    c = TestClient(app)
    codes = [c.post("/api/github/context", json={"repo": "o/n"}).status_code for _ in range(11)]
    limiter.reset()
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_permission_prompt_wording_follows_intent():
    assert "remediation report" in permission_prompt(["a"], "audit")
    assert "remediation report" in permission_prompt(["a"])
    for intent in ("explain", "compare", "build", "analyze"):
        text = permission_prompt(["a"], intent)
        assert "remediation" not in text and "final report" in text
        assert "shall i proceed" in text.lower()


async def test_metrics_models_used_from_event_payloads(tmp_path):
    async with EventStore(url=f"file:{tmp_path}/m.db") as s:
        await s.create_run("r1", "g", "h", 1, {"nodes": []})
        await s.append("r1", EventType.NODE_START, node_id="a", payload={"model": "m1"})
        await s.append("r1", EventType.TOOL_RESULT, node_id="a", payload={"model": "m2"})
        await s.append("r1", EventType.TOOL_RESULT, node_id="b", payload={"model": "m1"})
        await s.append("r1", EventType.NODE_START, node_id="c", payload={"role": "x"})
        m = await compute(s, "r1")
    assert m.models_used == ["m1", "m2"]


def test_sqlite_migration_runs_inside_begin_immediate(tmp_path, monkeypatch):
    seen: list[str] = []
    real = sqlite3.connect

    def spy(*a, **kw):
        c = real(*a, **kw)
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(store_mod.sqlite3, "connect", spy)
    monkeypatch.setattr(store_mod, "_MIGRATED", set())

    async def go():
        async with EventStore(url=f"file:{tmp_path}/mig.db"):
            pass

    asyncio.run(go())
    begin = next(i for i, q in enumerate(seen) if q.strip().upper().startswith("BEGIN IMMEDIATE"))
    first_ddl = next(i for i, q in enumerate(seen) if "DROP TRIGGER" in q.upper())
    assert begin < first_ddl


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()
