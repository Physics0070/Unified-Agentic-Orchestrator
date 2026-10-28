"""Fewer round trips per event: batched Turso appends, keep-alive, schema-in-prompt.

The Turso path is exercised against a fake Hrana endpoint backed by a real
sqlite database (same SQL, same migration), so the batch's SQL, conditions and
result parsing are genuinely executed. Not a substitute for a live-Turso check.
"""

import asyncio
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import turso_serverless
from turso_serverless.protocol import decode_value, encode_value
from turso_serverless.session import Session

from backend.engine.executor import _render
from backend.engine.spec import AgentSpec
from backend.events import store as store_mod
from backend.events.store import EventStore, EventStoreError


class _FakeSession:
    """Interprets a Hrana pipeline `batch` against sqlite and counts requests."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.posts = 0
        self._baton = None

    def _reset_stream(self) -> None:
        self._baton = None

    def _cond_ok(self, cond, results) -> bool:
        if cond is None:
            return True
        kind = cond["type"]
        if kind == "ok":
            return results[cond["step"]] is not None
        if kind == "not":
            return not self._cond_ok(cond["cond"], results)
        if kind == "is_autocommit":
            return not self.db.in_transaction
        raise AssertionError(kind)

    def _post(self, path: str, body: dict) -> bytes:
        assert path == "/v3/pipeline"
        self.posts += 1
        steps = body["requests"][0]["batch"]["steps"]
        results: list = []
        errors: list = []
        for step in steps:
            if not self._cond_ok(step.get("condition"), results):
                results.append(None)
                errors.append(None)
                continue
            stmt = step["stmt"]
            try:
                cur = self.db.execute(stmt["sql"], [decode_value(a) for a in stmt["args"]])
                rows = cur.fetchall()
                results.append({
                    "cols": [{"name": d[0]} for d in (cur.description or [])],
                    "rows": [[encode_value(v) for v in r] for r in rows],
                    "affected_row_count": max(cur.rowcount, 0),
                })
                errors.append(None)
            except sqlite3.Error as e:
                results.append(None)
                errors.append({"message": str(e)})
        out = {"results": [
            {"type": "ok", "response": {"type": "batch", "result": {
                "step_results": results, "step_errors": errors}}},
            {"type": "ok", "response": {"type": "close"}},
        ]}
        return json.dumps(out).encode()


class _FakeConn:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def execute(self, sql, params=()):
        return self._session.db.execute(sql, params)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def remote(monkeypatch, tmp_path):
    db = sqlite3.connect(tmp_path / "remote.db", isolation_level=None, check_same_thread=False)
    db.executescript(Path(store_mod._MIGRATION).read_text())
    session = _FakeSession(db)
    monkeypatch.setattr(turso_serverless, "connect", lambda *a, **k: _FakeConn(session))
    monkeypatch.setattr(store_mod, "_install_keepalive", lambda *a, **k: None)
    store = EventStore(url="libsql://fake.example", token="test-token")  # noqa: S106
    return store, session, db


def test_remote_append_is_one_round_trip_and_gapless(remote):
    s, session, db = remote

    async def go():
        async with s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            session.posts = 0
            assert await s.append("r1", "RUN_START", payload={"a": 1}) == 1
            assert session.posts == 1
            assert await s.append_many(
                "r1", [{"event_type": "NODE_START"}, {"event_type": "NODE_END"}], node_id="n"
            ) == [2, 3]
            assert session.posts == 2  # a batch of N is still one request
            seq = await s.append_and_set_status(
                "r1", "RUN_END", {"ok": True}, status="done", ended=True
            )
            assert seq == 4 and session.posts == 3
            run = await s.get_run("r1")
            assert run.status == "done" and run.ended_at and run.last_seq == 4
            assert [(e.seq, e.event_type) for e in await s.read("r1")] == [
                (1, "RUN_START"), (2, "NODE_START"), (3, "NODE_END"), (4, "RUN_END")]
            assert not db.in_transaction

    asyncio.run(go())


def test_remote_concurrent_appends_keep_order(remote):
    s, _, _ = remote

    async def go():
        async with s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            seqs = await asyncio.gather(*(s.append("r1", "NODE_START", node_id=f"n{i}")
                                          for i in range(25)))
            assert sorted(seqs) == list(range(1, 26))
            assert [e.seq for e in await s.read("r1")] == list(range(1, 26))

    asyncio.run(go())


def test_remote_batch_is_atomic_and_unknown_run_rejected(remote):
    s, _, db = remote

    async def go():
        async with s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            with pytest.raises(EventStoreError):
                await s.append("ghost", "NODE_START")
            # A bad status violates the CHECK constraint on the last step: the
            # event insert and the seq bump must roll back with it.
            with pytest.raises(EventStoreError):
                await s.append_and_set_status("r1", "RUN_END", None, status="bogus")
            assert not db.in_transaction
            assert (await s.get_run("r1")).last_seq == 0
            assert await s.read("r1") == []
            assert await s.append("r1", "RUN_START") == 1  # allocator not poisoned

    asyncio.run(go())


def test_sqlite_append_many_rolls_back_whole_batch(tmp_path):
    async def go():
        async with EventStore(url=f"file:{tmp_path}/e.db") as s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            # Plant a row at seq 2 so the second row of the batch collides.
            await s._execute(
                "INSERT INTO events (run_id, seq, event_type, ts) VALUES ('r1', 2, 'PAUSE', 't')"
            )
            with pytest.raises(EventStoreError):
                await s.append_many(
                    "r1", [{"event_type": "NODE_START"}] * 3
                )
            assert (await s.get_run("r1")).last_seq == 0  # bump rolled back too
            assert [e.event_type for e in await s.read("r1")] == ["PAUSE"]
            assert await s.append("r1", "RESUME") == 1

    asyncio.run(go())


def test_sqlite_append_many_is_ordered_under_concurrency(tmp_path):
    async def go():
        async with EventStore(url=f"file:{tmp_path}/e.db") as s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            groups = await asyncio.gather(*(
                s.append_many("r1", [{"event_type": "NODE_START", "node_id": f"g{i}"},
                                     {"event_type": "NODE_END", "node_id": f"g{i}"}])
                for i in range(10)
            ))
            for seqs in groups:
                assert seqs[1] == seqs[0] + 1  # a group is contiguous
            events = await s.read("r1")
            assert [e.seq for e in events] == list(range(1, 21))
            for i in range(0, 20, 2):  # and its S always precedes its E
                pair = (events[i].event_type, events[i + 1].event_type)
                assert pair == ("NODE_START", "NODE_END")
                assert events[i].node_id == events[i + 1].node_id

    asyncio.run(go())


def test_keepalive_reuses_one_tcp_connection():
    conns = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            conns.append(1)
            super().setup()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"results": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        session = Session(f"http://127.0.0.1:{srv.server_port}", "tok")
        store_mod._install_keepalive(session, 5.0)
        for _ in range(4):
            assert session._post("/v3/pipeline", {"requests": []}) == b'{"results": []}'
        assert len(conns) == 1
    finally:
        srv.shutdown()


# ------------------------------------------------------------ first-try handoffs


def test_prompt_shows_the_schema_and_its_required_keys():
    schema = {
        "type": "object",
        "required": ["points", "confidence"],
        "properties": {"points": {"type": "array"}, "confidence": {"type": "number"}},
    }
    agent = AgentSpec(id="research", role="Research it", system_contract="c")
    for ctx in ({}, {"up": {"x": 1}}):
        text = _render(ctx, agent, "the goal", schema)
        assert "Required top-level keys: points, confidence" in text
        assert "JSON" in text and '"type":"object"' in text


def test_prompt_falls_back_to_the_agents_own_schema():
    agent = AgentSpec(id="a", role="r", system_contract="c",
                      output_schema={"type": "object", "required": ["summary"]})
    assert "summary" in _render({}, agent)
    assert "single JSON object" in _render({}, AgentSpec(id="b", role="r", system_contract="c"))


def test_executor_sends_schema_prompt_and_json_mode_and_passes_first_try(tmp_path):
    from backend.engine import Executor, GraphSpec, Node
    from backend.engine.spec import Edge
    from backend.providers.base import Completion

    schema = {"type": "object", "required": ["points"], "properties": {"points": {}}}
    seen: list[dict] = []

    async def completer(model, messages, **kw):
        seen.append({"user": messages[1]["content"], "json_mode": kw.get("json_mode")})
        return Completion(text='{"points": ["a"]}', tokens_in=1, tokens_out=1, model=model)

    agent = AgentSpec(id="research", role="Research", system_contract="c", model="m",
                      output_schema=schema)
    graph = GraphSpec(
        nodes=[Node(id="research", type="agent", agent=agent)],
        edges=[Edge(from_node="research", to_node="end", handoff_schema=schema)],
    )

    async def go():
        async with EventStore(url=f"file:{tmp_path}/e.db") as s:
            await s.create_run("r1", "g", "h", 1, {"nodes": []})
            await Executor(s, graph, "r1", completer=completer).run()
            types = [e.event_type for e in await s.read("r1")]
            assert "HANDOFF_REJECTED" not in types and "HANDOFF_VALIDATED" in types

    asyncio.run(go())
    assert seen and seen[0]["json_mode"] is True
    assert "Required top-level keys: points" in seen[0]["user"]
