"""Agent tool loop: allowlist, guard enforcement, iteration cap, safe calculator."""

import json

import pytest

from backend.engine import AgentSpec, Executor, GraphSpec, Node
from backend.engine.tools import (
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_OUTPUT_CHARS,
    CalculatorError,
    bounded,
    complete_with_tools,
    register_tools,
    safe_eval,
    wrap_output,
)
from backend.events.types import Event, EventType
from backend.permissions import CapabilityGuard
from backend.providers.base import Completion, ToolCall


class MemStore:
    def __init__(self):
        self.events = []

    async def get_run(self, run_id):
        return None

    async def set_run_status(self, *a, **k):
        return None

    async def append(self, run_id, et, node_id=None, agent_id=None, payload=None, **kw):
        self.events.append(Event(run_id=run_id, seq=len(self.events) + 1, event_type=et,
                                 node_id=node_id, agent_id=agent_id, payload=payload,
                                 latency_ms=kw.get("latency_ms")))
        return len(self.events)

    async def append_many(self, run_id, rows, node_id=None):
        for r in rows:
            await self.append(run_id, r["event_type"], node_id=r.get("node_id", node_id),
                              payload=r.get("payload"))

    async def append_and_set_status(self, run_id, et, payload, status, ended=False):
        await self.append(run_id, et, payload=payload)

    async def read(self, run_id, from_seq=0):
        return self.events


class FakeAdapter:
    """Scripted completer: each call pops the next Completion and records its inputs."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def __call__(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": list(messages), "kw": kw})
        return self.script.pop(0) if self.script else final("done")


def tool_turn(name, args):
    return Completion(text="", tokens_in=5, tokens_out=5, model="groq:x",
                      tool_calls=[ToolCall(name=name, arguments=args)])


def final(text="42"):
    return Completion(text=text, tokens_in=7, tokens_out=3, model="groq:x")


def make(tools, model="groq:x"):
    store = MemStore()
    guard = CapabilityGuard(store, "r")
    register_tools(guard)
    agent = AgentSpec(id="a", role="r", system_contract="c", tools=tools, model=model)
    return store, guard, agent


def types(store):
    return [e.event_type for e in store.events]


USER = [{"role": "user", "content": "q"}]


async def test_tool_called_and_result_fed_back():
    store, guard, agent = make(["calculator"])
    fake = FakeAdapter([tool_turn("calculator", {"expression": "6*7"}), final("the answer is 42")])
    c = await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert c.text == "the answer is 42"
    assert len(fake.calls) == 2
    # Tools offered on the first call, and the wrapped result reached the second.
    assert fake.calls[0]["kw"]["tools"][0]["function"]["name"] == "calculator"
    fed_back = fake.calls[1]["messages"][-1]["content"]
    assert "<tool_output" in fed_back and "42" in fed_back and "untrusted" in fed_back
    # Totals cover both rounds.
    assert (c.tokens_in, c.tokens_out) == (12, 8)
    assert EventType.TOOL_CALL in types(store)
    ok = [e for e in store.events if e.event_type == EventType.TOOL_RESULT]
    assert ok and ok[0].payload["ok"] is True and ok[0].payload["output"] == "42"


async def test_iteration_cap():
    store, guard, agent = make(["calculator"])
    fake = FakeAdapter([tool_turn("calculator", {"expression": "1+1"})] * 20)
    await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=10_000)
    executed = [e for e in store.events if e.event_type == EventType.TOOL_CALL]
    assert len(executed) == MAX_TOOL_ITERATIONS
    # The extra, forced-final call is made without tools.
    assert "tools" not in fake.calls[-1]["kw"]
    assert len(fake.calls) == MAX_TOOL_ITERATIONS + 1


async def test_undeclared_tool_refused_and_not_offered():
    store, guard, agent = make(["calculator"])
    fake = FakeAdapter([tool_turn("web_search", {"query": "x"}), final()])
    await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    offered = [t["function"]["name"] for t in fake.calls[0]["kw"]["tools"]]
    assert offered == ["calculator"]
    blocked = [e for e in store.events if e.event_type == EventType.TOOL_BLOCKED]
    assert blocked and blocked[0].payload["attempted_tool"] == "web_search"
    assert EventType.TOOL_CALL not in types(store)
    assert "not permitted" in fake.calls[1]["messages"][-1]["content"]


async def test_declared_but_unregistered_tool_says_not_available():
    store, guard, agent = make(["calculator", "launch_missiles"])
    fake = FakeAdapter([tool_turn("launch_missiles", {}), final()])
    await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert [t["function"]["name"] for t in fake.calls[0]["kw"]["tools"]] == ["calculator"]
    assert "not available" in fake.calls[1]["messages"][-1]["content"]


async def test_tool_error_does_not_crash():
    store, guard, agent = make(["calculator"])
    fake = FakeAdapter([tool_turn("calculator", {"wrong_arg": 1}),
                        tool_turn("calculator", {"expression": "__import__('os')"}), final("ok")])
    c = await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert c.text == "ok"
    assert "error" in fake.calls[1]["messages"][-1]["content"]
    assert "error" in fake.calls[2]["messages"][-1]["content"]


async def test_unsupported_provider_gets_no_tools():
    _, guard, agent = make(["calculator"], model="ollama:llama3.2:3b")
    fake = FakeAdapter([final("plain")])
    c = await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert c.text == "plain" and "tools" not in fake.calls[0]["kw"]
    # A tool-capable primary with a non-capable fallback is also left alone.
    agent2 = agent.model_copy(update={"model": "groq:x", "fallback_model": "ollama:y:1"})
    fake2 = FakeAdapter([final("plain")])
    await complete_with_tools(fake2, guard, agent2, USER, node_id="n", max_tokens=500)
    assert "tools" not in fake2.calls[0]["kw"]


async def test_agent_without_tools_is_unchanged():
    _, guard, agent = make([])
    fake = FakeAdapter([final("x")])
    await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert "tools" not in fake.calls[0]["kw"] and len(fake.calls) == 1


async def test_output_is_truncated_and_wrapped():
    async def huge(**kw):
        return "x" * 50_000

    out = await bounded(huge)()
    assert len(out) <= MAX_TOOL_OUTPUT_CHARS + 20
    assert len(wrap_output("t", "y" * 50_000)) < MAX_TOOL_OUTPUT_CHARS + 400


async def test_knowledge_lookup_without_workspace():
    _, guard, agent = make(["knowledge_lookup"])
    fake = FakeAdapter([tool_turn("knowledge_lookup", {"query": "q"}), final()])
    await complete_with_tools(fake, guard, agent, USER, node_id="n", max_tokens=500)
    assert "no knowledge" in fake.calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize("expr,expected", [
    ("1 + 2 * 3", 7), ("(2 + 3) ** 2", 25), ("-4 + 10 // 3", -1), ("7 % 4", 3), ("10 / 4", 2.5),
])
def test_calculator_arithmetic(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')", "open('f')", "a + 1", "[1,2]", "1 if True else 2",
    "(lambda: 1)()", "2 ** 1000", "9 ** 9 ** 9", "1 / 0", "True + 1", "'a' * 3", "", "x" * 500,
    "().__class__",
])
def test_calculator_rejects_non_arithmetic(expr):
    with pytest.raises(CalculatorError):
        safe_eval(expr)


async def test_executor_runs_tool_loop_end_to_end(monkeypatch):
    from backend.handoff import artifact_store

    async def put(*a, **k):
        return None

    monkeypatch.setattr(artifact_store, "put", put)
    agent = AgentSpec(id="a", role="compute", system_contract="c", tools=["calculator"],
                      model="groq:x", output_schema={"type": "object"})
    graph = GraphSpec(nodes=[Node(id="n", type="agent", agent=agent)], edges=[])
    fake = FakeAdapter([tool_turn("calculator", {"expression": "2+2"}),
                        final(json.dumps({"v": 4}))])
    store = MemStore()
    res = await Executor(store, graph, "r", completer=fake).run()
    assert res.ok and res.results["n"].output == {"v": 4}
    # Exactly one completion row (the final one) so replay stays valid.
    rows = [e for e in store.events if e.event_type == EventType.TOOL_RESULT
            and "tool" not in e.payload]
    assert len(rows) == 1


async def test_run_workspace_reaches_knowledge_lookup(monkeypatch):
    from backend.handoff import artifact_store
    from backend.rag import knowledge_base

    async def put(*a, **k):
        return None

    monkeypatch.setattr(artifact_store, "put", put)
    ws = knowledge_base.get("ws-run-test")
    ws.add_document("notes.txt", "The launch codename is Zephyrwind.")
    agent = AgentSpec(id="a", role="r", system_contract="c", tools=["knowledge_lookup"],
                      model="groq:x", output_schema={"type": "object"})
    graph = GraphSpec(nodes=[Node(id="n", type="agent", agent=agent)], edges=[])

    async def run(workspace_id):
        fake = FakeAdapter([tool_turn("knowledge_lookup", {"query": "launch codename"}),
                            final(json.dumps({"ok": True}))])
        res = await Executor(MemStore(), graph, "r", completer=fake,
                             workspace_id=workspace_id).run()
        assert res.ok
        return json.dumps(fake.calls[1]["messages"])

    assert "Zephyrwind" in await run("ws-run-test")
    assert "Zephyrwind" not in await run("")


def test_run_request_workspace_id_validated():
    from pydantic import ValidationError

    from backend.api.schemas import RunRequest

    graph = {"nodes": [], "edges": []}
    goal = "summarise the quarterly report"
    assert RunRequest(goal=goal, graph=graph).workspace_id is None
    assert RunRequest(goal=goal, graph=graph, workspace_id="ab_c-1").workspace_id == "ab_c-1"
    with pytest.raises(ValidationError):
        RunRequest(goal=goal, graph=graph, workspace_id="bad id/..")
