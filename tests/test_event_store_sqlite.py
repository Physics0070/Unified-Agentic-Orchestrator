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


def test_many_stores_opening_at_once_do_not_collide_on_the_migration(tmp_path):
    """Every request opens its own store; the migration drops and recreates the
    append-only triggers, so concurrent opens used to crash with 'trigger already
    exists'. Open a burst of fresh stores against one file while others append."""
    from backend.events import store as store_mod

    store_mod._MIGRATED.clear()

    async def one(i):
        async with _store(tmp_path) as s:
            if i == 0:
                await _run(s)
            return i

    async def go():
        assert sorted(await asyncio.gather(*(one(i) for i in range(24)))) == list(range(24))
        async with _store(tmp_path) as s:  # still append-only afterwards
            await s.append("r1", "RUN_START")
            with pytest.raises(EventStoreError):
                await s._execute("DELETE FROM events")

    asyncio.run(go())


def test_an_unwritable_location_is_an_event_store_error_not_a_raw_exception(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where a directory is needed

    async def go():
        with pytest.raises(EventStoreError, match="could not be opened"):
            async with EventStore(url=f"file:{blocker}/sub/events.db"):
                pass

    asyncio.run(go())


@pytest.mark.parametrize(
    "url,expected",
    [("", "data/events.db"), ("file:", "data/events.db"), ("file:rel/e.db", "rel/e.db")],
)
def test_sqlite_path_parsing(url, expected):
    from backend.events.store import _sqlite_path

    assert _sqlite_path(url) == expected


def test_file_uri_with_slashes_resolves_to_an_absolute_path(tmp_path):
    from backend.events.store import _sqlite_path

    p = (tmp_path / "e.db").resolve()
    assert _sqlite_path(p.as_uri()) == str(p)
