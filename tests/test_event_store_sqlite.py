"""EventStore on the local sqlite fallback (blank / file: URL). No network."""

import asyncio

import pytest

from backend.events.store import EventStore, EventStoreError


def _store(tmp_path):
    return EventStore(url=f"file:{tmp_path}/sub/events.db")


async def _run(s, rid="r1"):
    return await s.create_run(rid, "goal", "h", 1, {"nodes": []})


def test_run_events_artifacts_roundtrip(tmp_path):
    async def go():
        async with _store(tmp_path) as s:
            await _run(s)
            assert await s.append("r1", "RUN_START", payload={"a": 1}) == 1
            assert await s.append_many(
                "r1", [{"event_type": "NODE_START"}, {"event_type": "NODE_END"}]
            ) == [2, 3]
            assert [e.seq for e in await s.read("r1")] == [1, 2, 3]
            assert [e.seq for e in await s.read("r1", from_seq=1)] == [2, 3]
            await s.set_run_status("r1", "done", ended=True)
            r = await s.get_run("r1")
            assert r.status == "done" and r.ended_at and r.last_seq == 3
            await s.put_artifact("r1", seq=1, written_by="a", content={"k": "v"})
            assert (await s.list_artifacts("r1"))[0]["content"] == {"k": "v"}
            with pytest.raises(EventStoreError):  # append-only trigger
                await s._execute("DELETE FROM events")
            with pytest.raises(EventStoreError):
                await s.append("nope", "NODE_START")

    asyncio.run(go())


def test_concurrent_appends_are_gapless(tmp_path):
    async def go():
        async with _store(tmp_path) as s:
            await _run(s)
            seqs = await asyncio.gather(*(s.append("r1", "NODE_START") for _ in range(30)))
            assert sorted(seqs) == list(range(1, 31))
            assert [e.seq for e in await s.read("r1")] == list(range(1, 31))

    asyncio.run(go())


def test_blank_url_uses_sqlite(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "turso_database_url", "")
    assert EventStore()._sqlite
