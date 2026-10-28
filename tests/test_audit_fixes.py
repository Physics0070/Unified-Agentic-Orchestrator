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


def test_pasted_turso_secrets_are_stripped():
    from backend.config import Settings

    s = Settings(turso_database_url=" libsql://x.turso.io\n", turso_auth_token="tok\n ")  # noqa: S106
    assert s.turso_database_url == "libsql://x.turso.io" and s.turso_auth_token == "tok"  # noqa: S105


def test_a_secret_pasted_twice_uses_the_first_copy():
    from backend.config import Settings

    s = Settings(turso_auth_token="abc.def\n\nabc.def")  # noqa: S106
    assert s.turso_auth_token == "abc.def"  # noqa: S105


def test_run_finishes_inline_on_vercel(monkeypatch, tmp_path):
    """Serverless freezes after the response, so the run must complete first."""
    from fastapi.testclient import TestClient

    from backend.api import routes
    from backend.config import settings
    from backend.council.compiler import build_graph
    from backend.main import app

    class Fast:
        def __init__(self, store, graph, run_id, **kw):
            self.store, self.run_id = store, run_id

        async def run(self):
            await self.store.set_run_status(self.run_id, "done", ended=True)

    async def no_dna(*a, **k):
        return None

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(settings, "turso_database_url", f"file:{tmp_path / 'e.db'}")
    monkeypatch.setattr(routes, "Executor", Fast)
    monkeypatch.setattr(routes, "predict_and_log", no_dna)
    monkeypatch.setattr(routes, "record_and_log", no_dna)
    g = build_graph([{"id": "a", "role": "A"}], ["ollama:x:1"], verifier_model="ollama:x:1",
                    intent="explain")
    body = {"goal": "Compare REST and GraphQL", "graph": g.model_dump(), "approvals": ["approve"]}
    c = TestClient(app)
    r = c.post("/api/runs", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"  # creating the run does not execute it
    r = c.post(f"/api/runs/{r.json()['run_id']}/execute", json={"approvals": ["approve"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    assert routes._TASKS == {}


def test_localhost_ollama_is_not_offered_on_vercel(monkeypatch):
    from backend.config import settings
    from backend.providers.secret_broker import configured_providers

    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    assert "ollama" in configured_providers()
    monkeypatch.setenv("VERCEL", "1")
    assert "ollama" not in configured_providers()
    monkeypatch.setattr(settings, "ollama_base_url", "https://ollama.example.com")
    assert "ollama" in configured_providers()


async def test_gpt_oss_requests_low_reasoning_effort(monkeypatch):
    import httpx

    from backend.providers.groq import GroqAdapter
    from backend.providers.secret_broker import clear_runtime_key, get_handle, set_runtime_key

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    set_runtime_key("groq", "gsk-test-1234567890abcdef")
    a = GroqAdapter(get_handle("groq"))
    monkeypatch.setattr(a, "_client", lambda t: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://x"))
    try:
        await a.complete([{"role": "user", "content": "hi"}], "openai/gpt-oss-120b")
    finally:
        clear_runtime_key("groq")
    assert seen["reasoning_effort"] == "low"
