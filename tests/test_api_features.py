"""The new HTTP surface (providers, knowledge, router), the execution trace, the
goal reaching agents, and a chat turn using routing + retrieval. No network, no
database: model calls are faked, and none of these endpoints touch the event log.
"""

import pytest
from fastapi.testclient import TestClient

from backend.api import routes
from backend.chat import run_turn
from backend.config import settings
from backend.engine.executor import _render
from backend.engine.spec import AgentSpec
from backend.events.types import Event, EventType
from backend.main import app
from backend.providers.secret_broker import clear_runtime_key
from backend.rag import knowledge_base
from backend.trace import build_trace

client = TestClient(app)
WS = "test-workspace-1"
DOC = (
    b"Optimistic locking uses a version column. Writers retry when the version changed.\n\n"
    b"Pessimistic locking uses SELECT FOR UPDATE and makes writers queue."
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "secret_key", "")
    yield
    for p in ("openai", "groq", "gemini", "openrouter", "ollama"):
        clear_runtime_key(p)
    knowledge_base._spaces.pop(WS, None)


# ------------------------------------------------------------- providers


def test_provider_status_lists_all_and_never_leaks_a_key():
    r = client.put("/api/providers/openai", json={"api_key": "sk-test-1234567890abcdef"})
    assert r.status_code == 200
    assert "sk-test" not in r.text
    row = next(p for p in r.json()["providers"] if p["provider"] == "openai")
    assert row["configured"] and row["key_source"] == "runtime"
    assert "sk-test" not in client.get("/api/providers").text
    assert "openai:gpt-4o-mini" in client.get("/api/models").json()["models"]


def test_disconnect_removes_the_provider():
    client.put("/api/providers/openai", json={"api_key": "sk-test-1234567890abcdef"})
    r = client.delete("/api/providers/openai")
    row = next(p for p in r.json()["providers"] if p["provider"] == "openai")
    assert not row["configured"]


def test_models_can_be_switched_without_code():
    client.put("/api/providers/openai", json={"api_key": "sk-test-1234567890abcdef",
                                              "models": ["gpt-4o"]})
    assert "openai:gpt-4o" in client.get("/api/models").json()["models"]


def test_unknown_provider_and_bad_key_are_rejected():
    assert client.put("/api/providers/nope", json={"api_key": "x" * 20}).status_code == 404
    assert client.put("/api/providers/groq", json={"api_key": "short"}).status_code == 422


def test_writes_need_the_admin_token_when_one_is_set(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "s3cret-admin")
    body = {"api_key": "sk-test-1234567890abcdef"}
    assert client.put("/api/providers/openai", json=body).status_code == 401
    assert client.put("/api/providers/openai", json=body,
                      headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.put("/api/providers/openai", json=body,
                      headers={"X-Admin-Token": "s3cret-admin"}).status_code == 200
    assert client.get("/api/providers").json()["admin_required"] is True


def test_writes_are_refused_in_prod_with_no_token(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    r = client.put("/api/providers/openai", json={"api_key": "sk-test-1234567890abcdef"})
    assert r.status_code == 403


def test_test_connection_without_a_key_says_so():
    r = client.post("/api/providers/groq/test", json={})
    assert r.json() == {"ok": False, "provider": "groq", "error": "no API key configured"}


def test_router_explain_returns_a_decision_with_candidates():
    client.put("/api/providers/openai", json={"api_key": "sk-test-1234567890abcdef"})
    client.put("/api/providers/groq", json={"api_key": "gsk-test-1234567890abcdef"})
    r = client.post("/api/router/explain", json={"message": "hi", "priority": "speed"})
    assert r.status_code == 200
    d = r.json()["decision"]
    assert d["priority"] == "speed" and len(d["candidates"]) >= 2
    assert d["model"] == d["candidates"][0]["model"]


# ------------------------------------------------------------- knowledge


def _upload(name="locking.md", data=DOC):
    return client.post("/api/knowledge/upload", data={"workspace_id": WS},
                       files={"file": (name, data, "text/markdown")})


def test_upload_list_query_delete_round_trip():
    r = _upload()
    assert r.status_code == 200
    assert r.json()["document"]["name"] == "locking.md"
    assert r.json()["persistent"] is False

    docs = client.get("/api/knowledge/documents", params={"workspace_id": WS}).json()
    assert [d["name"] for d in docs["documents"]] == ["locking.md"]

    q = client.post("/api/knowledge/query", json={
        "workspace_id": WS, "query": "how does optimistic locking work"}).json()
    assert q["hits"] and q["hits"][0]["doc_name"] == "locking.md"

    doc_id = docs["documents"][0]["doc_id"]
    after = client.delete(f"/api/knowledge/documents/{doc_id}", params={"workspace_id": WS})
    assert after.json()["documents"] == []
    assert client.delete(f"/api/knowledge/documents/{doc_id}",
                         params={"workspace_id": WS}).status_code == 404


def test_bad_uploads_are_rejected_with_a_reason():
    assert _upload("evil.pdf", b"MZ not a pdf").status_code == 422
    assert _upload("a.exe", b"x").status_code == 422
    assert _upload("empty.txt", b"").status_code == 422


def test_oversized_upload_is_413(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    assert _upload("big.txt", b"x" * 50).status_code == 413


def test_bad_workspace_id_is_rejected():
    r = client.get("/api/knowledge/documents", params={"workspace_id": "../x"})
    assert r.status_code == 400


def test_query_on_an_unknown_workspace_is_empty_not_an_error():
    r = client.post("/api/knowledge/query", json={"workspace_id": "nobody-here", "query": "x y"})
    assert r.status_code == 200 and r.json()["hits"] == []


# ------------------------------------------------------------------ trace


def _ev(seq, t, node=None, agent=None, payload=None, **kw):
    return Event(run_id="r", seq=seq, event_type=t, node_id=node, agent_id=agent,
                 payload=payload, ts=f"2026-01-01T00:00:{seq:02d}+00:00", **kw)


def test_trace_reports_input_output_tools_duration_and_status():
    ev = [
        _ev(1, EventType.RUN_START),
        _ev(2, EventType.NODE_START, "a", "a", {"model": "groq:x", "role": "find bugs"}),
        _ev(3, EventType.TOOL_RESULT, "a", "a", {"model": "groq:x", "text": "raw", "input": "IN"},
            tokens_in=10, tokens_out=5),
        _ev(4, EventType.TOOL_CALL, "a", "a", {"tool": "read_file", "arguments": {"p": "x"}}),
        _ev(5, EventType.HANDOFF_VALIDATED, "a", "a", {"attempt": 1, "payload": {"ok": True}}),
        _ev(6, EventType.NODE_END, "a", "a", {"ok": True}, latency_ms=1234),
        _ev(7, EventType.NODE_START, "b", "b", {"model": "groq:y"}),
        _ev(8, EventType.BRANCH_FAILED, "b", "b", {"reason": "handoff_unrepairable"}),
        _ev(9, EventType.NODE_START, "c", "c", {"model": "groq:z"}),
        _ev(10, EventType.TOOL_BLOCKED, "c", "c", {"attempted_tool": "file_delete"}),
        _ev(11, EventType.RUN_END, payload={"wall_clock_ms": 9000}),
    ]
    graph = {"nodes": [{"id": "a", "type": "agent", "agent": {"role": "r", "tools": ["read_file"]}},
                       {"id": "b", "type": "agent"}],
             "edges": [{"from_node": "a", "to_node": "b"}]}
    out = build_trace(ev, graph)
    a, b, c = out["steps"]
    assert a["status"] == "done" and a["duration_ms"] == 1234
    assert a["input"] == "IN" and "ok" in a["output"]
    assert a["tokens"] == 15 and a["tool_calls"][0]["tool"] == "read_file"
    assert a["tools_declared"] == ["read_file"] and a["repairs"] == 1
    assert b["status"] == "failed" and b["depends_on"] == ["a"] and "handoff" in b["error"]
    assert c["status"] == "blocked" and c["tools_blocked"] == ["file_delete"]
    assert out["summary"]["by_status"] == {"done": 1, "failed": 1, "blocked": 1}
    assert out["summary"]["wall_clock_ms"] == 9000 and out["summary"]["finished"]


def test_a_running_node_reads_as_running():
    out = build_trace([_ev(1, EventType.NODE_START, "a", "a", {"model": "m"})])
    assert out["steps"][0]["status"] == "running" and out["summary"]["finished"] is False


# --------------------------------------------------- the goal reaches agents


def test_agents_are_told_the_overall_goal():
    agent = AgentSpec(id="a", role="Check auth", system_contract="c")
    with_goal = _render({}, agent, "Audit my login flow")
    assert "Audit my login flow" in with_goal and "Check auth" in with_goal
    assert "Overall goal" not in _render({}, agent)
    assert "Audit my login flow" in _render({"p": {"x": 1}}, agent, "Audit my login flow")


# ------------------------------------------------- chat: routing + retrieval


async def test_chat_turn_routes_retrieves_and_traces(monkeypatch):
    seen = {}

    async def fake_stream(question, model, *, history=None, temperature=0.3, fallback_model=None):
        seen["question"], seen["model"] = question, model
        yield "answer"

    monkeypatch.setattr("backend.chat.session.stream_direct", fake_stream)
    ws = knowledge_base.get(WS)
    ws.add_document("locking.md", DOC.decode())

    events = [e async for e in run_turn(
        "what is optimistic locking?",
        models=["ollama:llama3.2:3b", "groq:llama-3.3-70b-versatile"],
        store=None, priority="speed", workspace_id=WS)]
    plan = next(e["data"] for e in events if e["event"] == "plan")
    bench = next(e["data"] for e in events if e["event"] == "benchmarks")

    assert plan["routing"]["priority"] == "speed"
    assert bench["routing"]["model"] == seen["model"] == plan["answer_model"]
    assert bench["knowledge"]["used"] is True
    assert bench["knowledge"]["hits"][0]["doc_name"] == "locking.md"
    assert "version column" in seen["question"]  # retrieved text reached the model
    assert [s["node_id"] for s in bench["trace"]] == ["plan", "answer"]


async def test_chat_turn_without_documents_is_unchanged(monkeypatch):
    async def fake_stream(question, model, *, history=None, temperature=0.3, fallback_model=None):
        assert question == "hi"
        yield "hello"

    monkeypatch.setattr("backend.chat.session.stream_direct", fake_stream)
    events = [e async for e in run_turn("hi", models=["ollama:x:1"], store=None,
                                        workspace_id="empty-workspace")]
    bench = next(e["data"] for e in events if e["event"] == "benchmarks")
    assert bench["knowledge"]["used"] is False


def test_pause_resume_cancel_on_an_unknown_run_are_404_not_500(monkeypatch):
    class Store:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get_run(self, run_id):
            return None

    monkeypatch.setattr(routes, "EventStore", Store)
    for verb in ("pause", "resume", "cancel"):
        assert client.post(f"/api/runs/missing/{verb}").status_code == 404


def test_a_missing_event_log_is_a_503_that_names_the_fix_not_a_bare_500(monkeypatch):
    monkeypatch.setattr(settings, "turso_database_url", "")
    monkeypatch.setattr(settings, "turso_auth_token", "")
    r = client.get("/api/runs")
    assert r.status_code == 503
    assert r.json()["error"] == "event_log_unavailable" and "TURSO" in r.json()["message"]
