"""A node that dies with an exception must leave a failure row in the log."""

from backend.council.compiler import build_graph
from backend.engine.executor import Executor
from backend.events.types import Event, EventType
from backend.trace import build_trace


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


async def test_exception_in_a_node_is_logged_and_shown_as_failed():
    graph = build_graph([{"id": "a", "role": "A"}, {"id": "b", "role": "B"}],
                        ["ollama:x:1"], verifier_model="ollama:x:1", intent="explain")

    async def down(*a, **k):
        raise ConnectionError("provider is down")

    store = MemStore()
    res = await Executor(store, graph, "r", completer=down, approvals={"approve"}).run()
    assert not res.ok
    steps = {s["node_id"]: s for s in build_trace(store.events, graph.model_dump())["steps"]}
    assert steps["a"]["status"] == "failed" and "provider is down" in steps["a"]["error"]
    assert any(e.event_type == EventType.BRANCH_FAILED and e.node_id == "a"
               for e in store.events)
