"""User accounts: hashing, sessions, ownership, and "off means unchanged"."""

import asyncio
import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.api import routes
from backend.config import settings
from backend.council.compiler import build_graph
from backend.events import EventStore, EventType
from backend.events import store as store_mod
from backend.limiter import limiter
from backend.main import app

SECRET = "unit-test-secret-key-0123456789abcdef"  # noqa: S105
PASSWORD = "correct horse battery"  # noqa: S105
GOAL = "Compare REST and GraphQL for a small team"
HTTPS = "https://testserver"


@pytest.fixture(autouse=True)
def _accounts(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", SECRET)
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "rate_limit_auth", "1000/minute")
    monkeypatch.setattr(settings, "rate_limit_public", "1000/minute")
    limiter.reset()
    auth.forget()
    yield
    limiter.reset()


def _client(base_url: str = HTTPS) -> TestClient:
    return TestClient(app, base_url=base_url)


def _signup(email: str = "alice@example.com", password: str = PASSWORD) -> TestClient:
    c = _client()
    r = c.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return c


def _user_id(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["user"]["id"]


def _graph():
    return build_graph([{"id": "a", "role": "A"}], ["ollama:x:1"], verifier_model="ollama:x:1",
                       intent="explain")


def _make_run(run_id: str, user_id: str | None) -> None:
    async def go():
        async with EventStore() as s:
            await s.create_run(run_id, GOAL, "h", 1, {}, "done", user_id=user_id)
            await s.append(run_id, EventType.RUN_START, payload={})
    asyncio.run(go())


# ------------------------------------------------------------------ hashing


def test_hash_format_and_verification():
    h = auth.hash_password(PASSWORD)
    scheme, n, r, p, salt, digest = h.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert PASSWORD not in h and salt and digest
    assert auth.verify_password(PASSWORD, h)
    assert not auth.verify_password(PASSWORD + "x", h)
    # a fresh salt per hash
    assert auth.hash_password(PASSWORD) != h
    # malformed stored values never verify (and never raise)
    for bad in ("", "plain", "scrypt$1$2", "bcrypt$a$b$c$d$e", "scrypt$x$8$1$AA==$AA=="):
        assert auth.verify_password(PASSWORD, bad) is False


def test_password_is_never_stored_logged_or_returned(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    c = _client()
    r = c.post("/api/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    assert r.status_code == 201
    assert PASSWORD not in r.text and "scrypt" not in r.text and "password" not in r.text
    c.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    c.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-password-1"})
    assert PASSWORD not in caplog.text and "wrong-password-1" not in caplog.text
    db = sqlite3.connect(Path(settings.turso_database_url.removeprefix("file:")))
    rows = db.execute("SELECT id, email, password_hash, created_at FROM users").fetchall()
    assert len(rows) == 1
    assert PASSWORD not in " ".join(map(str, rows))
    assert rows[0][2].startswith("scrypt$16384$8$1$")
    assert rows[0][1] == "a@example.com"


# --------------------------------------------------------------- register/login


def test_register_sets_a_hardened_session_cookie_and_me_returns_the_user():
    c = _client()
    r = c.post("/api/auth/register", json={"email": "Alice@Example.com ", "password": PASSWORD})
    assert r.status_code == 201
    assert r.json()["user"]["email"] == "alice@example.com"
    cookie = r.headers["set-cookie"].lower()
    assert "aco_session=" in cookie
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "max-age=604800" in cookie and "path=/" in cookie
    assert "no-store" in r.headers["cache-control"]
    me = c.get("/api/auth/me").json()
    assert me["user"]["email"] == "alice@example.com"
    assert me["auth_required"] is False


def test_cookie_is_not_secure_on_localhost_so_dev_works():
    c = _client("http://localhost")
    r = c.post("/api/auth/register", json={"email": "dev@example.com", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" not in cookie
    assert c.get("/api/auth/me").json()["user"]["email"] == "dev@example.com"


def test_duplicate_register_is_409_case_insensitive():
    _signup("dup@example.com")
    r = _client().post("/api/auth/register",
                       json={"email": "DUP@example.com", "password": PASSWORD})
    assert r.status_code == 409


@pytest.mark.parametrize("body", [
    {"email": "not-an-email", "password": PASSWORD},
    {"email": "a@b", "password": PASSWORD},
    {"email": "a b@example.com", "password": PASSWORD},
    {"email": "ok@example.com", "password": "short"},
    {"email": "ok@example.com", "password": "x" * 201},
])
def test_register_validates_input(body):
    assert _client().post("/api/auth/register", json=body).status_code == 422


def test_login_failures_are_indistinguishable(monkeypatch):
    _signup("real@example.com")
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password",
                        lambda p, h: (calls.append(1), real(p, h))[1])
    c = _client()
    wrong = c.post("/api/auth/login", json={"email": "real@example.com", "password": "nope-nope-1"})
    n_wrong = len(calls)
    unknown = c.post("/api/auth/login",
                     json={"email": "ghost@example.com", "password": "nope-nope-1"})
    n_unknown = len(calls) - n_wrong
    malformed = c.post("/api/auth/login", json={"email": "###", "password": "nope-nope-1"})
    assert wrong.status_code == unknown.status_code == malformed.status_code == 401
    assert wrong.json() == unknown.json() == malformed.json()
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers
    # same scrypt work either way: no timing oracle for "does this email exist"
    assert n_wrong == n_unknown == 1


def test_login_then_logout_roundtrip():
    _signup("bob@example.com")
    c = _client()
    r = c.post("/api/auth/login", json={"email": "BOB@example.com", "password": PASSWORD})
    assert r.status_code == 200 and "aco_session" in c.cookies
    assert c.get("/api/auth/me").json()["user"]["email"] == "bob@example.com"
    out = c.post("/api/auth/logout")
    assert out.status_code == 200
    assert "aco_session" in out.headers["set-cookie"].lower()
    assert c.get("/api/auth/me").json()["user"] is None


def test_anonymous_me_reports_auth_required(monkeypatch):
    assert _client().get("/api/auth/me").json() == {"user": None, "auth_required": False}
    monkeypatch.setattr(settings, "auth_required", True)
    assert _client().get("/api/auth/me").json() == {"user": None, "auth_required": True}


def test_no_secret_key_means_no_tokens(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    c = _client()
    r = c.post("/api/auth/register", json={"email": "x@example.com", "password": PASSWORD})
    assert r.status_code == 503 and "SECRET_KEY" in r.text
    assert c.post("/api/auth/login",
                  json={"email": "x@example.com", "password": PASSWORD}).status_code == 503
    assert auth.read_token("anything") is None
    with pytest.raises(Exception, match="SECRET_KEY"):
        auth.issue_token(auth.User("u", "x@example.com"))


def test_register_and_login_are_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth", "3/minute")
    limiter.reset()
    c = _client()
    codes = [c.post("/api/auth/login",
                    json={"email": "a@example.com", "password": "wrong-password"}).status_code
             for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    codes = [c.post("/api/auth/register",
                    json={"email": f"u{i}@example.com", "password": PASSWORD}).status_code
             for i in range(4)]
    assert codes == [201, 201, 201, 429]


# ------------------------------------------------------------------- sessions


def _token(**over) -> str:
    now = datetime.now(UTC)
    claims = {"iss": "aco", "sub": "u1", "email": "t@example.com",
              "iat": now, "exp": now + timedelta(days=1), **over}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _me_with(token: str) -> dict:
    c = _client()
    c.cookies.set("aco_session", token)
    return c.get("/api/auth/me").json()


def test_valid_token_is_accepted_and_bad_ones_are_rejected():
    assert _me_with(_token())["user"]["email"] == "t@example.com"
    good = _token()
    head, body, sig = good.split(".")
    flipped = sig[:-2] + ("AA" if not sig.endswith("AA") else "BB")
    assert _me_with(f"{head}.{body}.{flipped}")["user"] is None  # tampered signature
    forged = jwt.encode({"iss": "aco", "sub": "evil", "email": "e@example.com",
                         "exp": datetime.now(UTC) + timedelta(days=1)},
                        "some-other-secret-0123456789abcdef0123", algorithm="HS256")
    assert _me_with(forged)["user"] is None  # signed with the wrong key
    expired = _token(exp=datetime.now(UTC) - timedelta(seconds=5))
    assert _me_with(expired)["user"] is None
    no_exp = jwt.encode({"iss": "aco", "sub": "u"}, SECRET, algorithm="HS256")
    assert _me_with(no_exp)["user"] is None
    assert _me_with(_token(iss="someone-else"))["user"] is None
    unsigned = jwt.encode({"iss": "aco", "sub": "u1", "email": "t@example.com",
                           "exp": datetime.now(UTC) + timedelta(days=1)}, None, algorithm="none")
    assert _me_with(unsigned)["user"] is None  # alg=none is not accepted
    assert _me_with("garbage")["user"] is None


def test_session_lasts_seven_days():
    c = _signup("exp@example.com")
    claims = jwt.decode(c.cookies["aco_session"], SECRET, algorithms=["HS256"], issuer="aco")
    assert 6.9 * 86400 < claims["exp"] - claims["iat"] <= 7 * 86400


# ----------------------------------------------------- AUTH_REQUIRED off = as before


def test_auth_off_leaves_anonymous_flows_alone(monkeypatch):
    anon = _client()
    # no 401 anywhere: whatever these answer, it is not "sign in"
    assert anon.post("/api/clarify", json={"goal": GOAL}).status_code != 401
    assert anon.get("/api/knowledge/documents", params={"workspace_id": "anon-ws-12345"}
                    ).status_code == 200
    assert anon.post("/api/runs/none/execute", json={}).status_code == 404
    _make_run("owned-run", "someone-else")
    for path in ("", "/events", "/output", "/trace", "/metrics"):
        # an owned run is still a capability link for anyone holding the id
        assert anon.get(f"/api/runs/owned-run{path}").status_code == 200, path
    assert "user_id" not in anon.get("/api/runs/owned-run").text


# ----------------------------------------------------- AUTH_REQUIRED on


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)


def test_orchestrator_endpoints_need_a_user(required):
    anon = _client()
    ws = {"workspace_id": "anon-ws-12345"}
    graph = _graph().model_dump()
    calls = [
        anon.post("/api/runs", json={"goal": GOAL, "graph": graph}),
        anon.post("/api/runs/x/execute", json={}),
        anon.post("/api/compile", json={"goal": GOAL}),
        anon.post("/api/clarify", json={"goal": GOAL}),
        anon.get("/api/knowledge/documents", params=ws),
        anon.post("/api/knowledge/upload", data=ws, files={"file": ("a.txt", b"hello world")}),
        anon.post("/api/knowledge/query", json={**ws, "query": "hello"}),
        anon.delete("/api/knowledge/documents/abc", params=ws),
    ]
    assert [r.status_code for r in calls] == [401] * len(calls)
    # chat is the exception: it stays anonymous (no models here, so a 503, not a 401)
    chat = anon.post("/api/chat", json={"message": "hi"})
    assert chat.status_code != 401


def test_signed_in_user_passes_the_gate(required, monkeypatch):
    c = _signup()
    assert c.post("/api/runs/none/execute", json={}).status_code == 404  # past auth
    assert c.get("/api/knowledge/documents", params={"workspace_id": "my-space-1234"}
                 ).status_code == 200


def test_user_cannot_read_another_users_run(required):
    a, b, anon = _signup("a@example.com"), _signup("b@example.com"), _client()
    _make_run("run-of-a", _user_id(a))
    for path in ("", "/events", "/output", "/trace", "/metrics"):
        assert a.get(f"/api/runs/run-of-a{path}").status_code == 200, path
        assert b.get(f"/api/runs/run-of-a{path}").status_code == 404, path  # 404, not 403
        assert anon.get(f"/api/runs/run-of-a{path}").status_code == 404, path
        assert b.get(f"/api/runs/unknown-id{path}").status_code == 404
    assert b.get("/api/runs/run-of-a/stream").status_code == 404
    for verb in ("approve", "cancel", "pause", "resume", "replay", "execute"):
        body = {"node_id": "n"} if verb == "approve" else {}
        assert b.post(f"/api/runs/run-of-a/{verb}", json=body).status_code == 404, verb
    # the 404 for a stranger is identical to the 404 for a run that does not exist
    assert b.get("/api/runs/run-of-a").json() == b.get("/api/runs/nope").json()


def test_ownerless_runs_stay_readable(required):
    _make_run("legacy-run", None)
    assert _client().get("/api/runs/legacy-run").status_code == 200


def test_list_runs_is_scoped_to_the_signed_in_user(monkeypatch):
    a, b = _signup("a@example.com"), _signup("b@example.com")
    _make_run("a-1", _user_id(a))
    _make_run("a-2", _user_id(a))
    _make_run("b-1", _user_id(b))
    _make_run("anon-1", None)
    ids = lambda c, **kw: sorted(r["run_id"] for r in c.get("/api/runs", **kw).json()["runs"])  # noqa: E731
    assert ids(a) == ["a-1", "a-2"]
    assert ids(b) == ["b-1"]
    assert "user_id" not in a.get("/api/runs").text
    # the admin token still lists everything
    monkeypatch.setattr(settings, "admin_token", "adm")
    assert ids(a, headers={"X-Admin-Token": "adm"}) == ["a-1", "a-2", "anon-1", "b-1"]
    assert a.get("/api/runs", headers={"X-Admin-Token": "bad"}).status_code == 401


def test_ids_filter_hides_other_users_runs_when_required(required):
    a, b = _signup("a@example.com"), _signup("b@example.com")
    rid = str(uuid.uuid4())
    _make_run(rid, _user_id(a))
    assert [r["run_id"] for r in a.get("/api/runs", params={"ids": rid}).json()["runs"]] == [rid]
    assert b.get("/api/runs", params={"ids": rid}).json()["runs"] == []


def test_runs_created_by_a_user_record_the_owner(required, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")  # create only; /execute runs it

    class Fast:
        def __init__(self, store, graph, run_id, **kw):
            self.store, self.run_id = store, run_id

        async def run(self):
            await self.store.append_and_set_status(
                self.run_id, EventType.RUN_END, {"ok": True}, status="done", ended=True)

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(routes, "Executor", Fast)
    monkeypatch.setattr(routes, "predict_and_log", nothing)
    monkeypatch.setattr(routes, "record_and_log", nothing)
    a, b = _signup("a@example.com"), _signup("b@example.com")
    r = a.post("/api/runs", json={"goal": GOAL, "graph": _graph().model_dump()})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    async def owner():
        async with EventStore() as s:
            return (await s.get_run(run_id)).user_id

    assert asyncio.run(owner()) == _user_id(a)
    assert b.post(f"/api/runs/{run_id}/execute", json={}).status_code == 404
    done = a.post(f"/api/runs/{run_id}/execute", json={})
    assert done.status_code == 200 and done.json()["status"] == "done"


# ----------------------------------------------------------------- knowledge


def test_knowledge_workspaces_are_namespaced_per_user(monkeypatch):
    ws = "shared-guess-1234"
    a, b, anon = _signup("a@example.com"), _signup("b@example.com"), _client()
    up = a.post("/api/knowledge/upload", data={"workspace_id": ws},
                files={"file": ("notes.txt", b"Optimistic locking uses a version column.")})
    assert up.status_code == 200, up.text
    assert up.json()["workspace_id"] == ws  # the caller's id, never the hash
    assert len(a.get("/api/knowledge/documents", params={"workspace_id": ws}
                     ).json()["documents"]) == 1
    # same id, different user: empty, and queries return nothing
    assert b.get("/api/knowledge/documents", params={"workspace_id": ws}
                 ).json()["documents"] == []
    assert b.post("/api/knowledge/query", json={"workspace_id": ws, "query": "locking"}
                  ).json()["hits"] == []
    # nor can an anonymous caller reach it by the raw id
    assert anon.get("/api/knowledge/documents", params={"workspace_id": ws}
                    ).json()["documents"] == []
    doc_id = a.get("/api/knowledge/documents", params={"workspace_id": ws}
                   ).json()["documents"][0]["doc_id"]
    assert b.delete(f"/api/knowledge/documents/{doc_id}", params={"workspace_id": ws}
                    ).status_code == 404
    assert a.post("/api/knowledge/query", json={"workspace_id": ws, "query": "locking"}
                  ).json()["hits"]
    assert a.delete(f"/api/knowledge/documents/{doc_id}", params={"workspace_id": ws}
                    ).status_code == 200


def test_scope_workspace_is_stable_valid_and_user_specific():
    u1, u2 = auth.User("1", "a@x.io"), auth.User("2", "b@x.io")
    assert auth.scope_workspace(None, "abc12345") == "abc12345"
    assert auth.scope_workspace(u1, None) is None
    s1 = auth.scope_workspace(u1, "abc12345")
    assert s1 == auth.scope_workspace(u1, "abc12345") != auth.scope_workspace(u2, "abc12345")
    assert s1 != "abc12345" and len(s1) <= 64


# ---------------------------------------------------------------- migration


def test_user_id_column_is_added_to_an_existing_runs_table(tmp_path, monkeypatch):
    """A database that predates accounts gets the column lazily, exactly once,
    without disturbing the append-only triggers; a second process is a no-op."""
    db_path = tmp_path / "old.db"
    old = sqlite3.connect(db_path)
    old.executescript(Path(store_mod._MIGRATION).read_text())
    old.execute("INSERT INTO runs (run_id, goal, config_hash, seed, graph_spec, status, "
                "last_seq, started_at) VALUES ('legacy', 'g', 'h', 1, '{}', 'done', 0, 'now')")
    old.commit()
    old.close()
    assert "user_id" not in {r[1] for r in sqlite3.connect(db_path).execute(
        "PRAGMA table_info(runs)")}
    monkeypatch.setattr(settings, "turso_database_url", f"file:{db_path}")
    monkeypatch.setattr(store_mod, "_USER_COL", {})
    monkeypatch.setattr(store_mod, "_MIGRATED", set())

    async def go():
        async with EventStore() as s:
            assert (await s.get_run("legacy")).user_id is None
            await s.create_run("mine", GOAL, "h", 1, {}, "pending", user_id="u-1")
            await s.append("mine", EventType.RUN_START, payload={})
            assert (await s.get_run("mine")).user_id == "u-1"
            assert [r.run_id for r in await s.list_runs(user_id="u-1")] == ["mine"]
            with pytest.raises(Exception, match="append-only"):
                await s._execute("UPDATE events SET payload = '{}' WHERE run_id = 'mine'")

    asyncio.run(go())
    # a "second process": caches empty, column already there -> still fine
    monkeypatch.setattr(store_mod, "_USER_COL", {})
    monkeypatch.setattr(store_mod, "_MIGRATED", set())
    asyncio.run(_reopen())


async def _reopen():
    async with EventStore() as s:
        assert (await s.get_run("mine")).user_id == "u-1"


def test_failed_alter_falls_back_to_the_old_sql(tmp_path, monkeypatch):
    """If the ALTER cannot be applied, runs keep working (just unowned)."""
    monkeypatch.setattr(settings, "turso_database_url", f"file:{tmp_path / 'ro.db'}")
    monkeypatch.setattr(store_mod, "_USER_COL", {})
    monkeypatch.setattr(store_mod, "_MIGRATED", set())

    def boom(c, key):
        store_mod._USER_COL[key] = False
        return False

    monkeypatch.setattr(store_mod, "_ensure_user_column", boom)

    async def go():
        async with EventStore() as s:
            await s.create_run("r", GOAL, "h", 1, {}, "pending", user_id="u-1")
            row = await s.get_run("r")
            assert row.user_id is None and row.goal == GOAL
            assert await s.list_runs(user_id="u-1") == []
            assert [r.run_id for r in await s.list_runs()] == ["r"]

    asyncio.run(go())
