import asyncio
import base64

import httpx
import pytest
from fastapi import HTTPException

from backend.github_context import MAX_CHARS, fetch_repo_context


def _t(readme="hi", status=None):
    def h(req: httpx.Request):
        assert req.url.host == "api.github.com"
        p = req.url.path
        if status and p.endswith(status[0]):
            return httpx.Response(status[1], headers=status[2] if len(status) > 2 else {})
        if p.endswith("/readme"):
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(readme.encode()).decode()},
            )
        if p.endswith("/contents"):
            return httpx.Response(
                200, json=[{"name": "src", "type": "dir"}, {"name": "a.py", "type": "file"}]
            )
        if p.endswith("/commits"):
            return httpx.Response(
                200, json=[{"sha": "abcdef123", "commit": {"message": "fix\nbody"}}]
            )
        return httpx.Response(
            200,
            json={
                "full_name": "o/n",
                "description": "d",
                "language": "Py",
                "stargazers_count": 1,
                "default_branch": "main",
            },
        )

    return httpx.MockTransport(h)


def run(repo, **kw):
    return asyncio.run(fetch_repo_context(repo, **kw))


@pytest.mark.parametrize(
    "bad", ["", "a", "a/b/c", "../x", "a/..", "a b/c", "-a/b", "http://evil/x/y"]
)
def test_bad_repo_rejected(bad):
    with pytest.raises(HTTPException) as e:
        run(bad, transport=_t())
    assert e.value.status_code == 422


def test_ok_and_truncation():
    r = run("o/n", transport=_t("x" * 10000))
    assert r["truncated"] and len(r["text"]) == MAX_CHARS
    assert "src/" in r["text"] and "abcdef1 fix" in r["text"]
    assert not run("o/n", transport=_t("short"))["truncated"]


@pytest.mark.parametrize("status,code", [(404, 404), (429, 429), (403, 403), (500, 502)])
def test_error_mapping(status, code):
    with pytest.raises(HTTPException) as e:
        run("o/n", transport=_t(status=("/commits", status)))
    assert e.value.status_code == code


def test_rate_limited_403():
    with pytest.raises(HTTPException) as e:
        run("o/n", transport=_t(status=("/commits", 403, {"x-ratelimit-remaining": "0"})))
    assert e.value.status_code == 429


def test_network_error():
    def boom(req):
        raise httpx.ConnectError("x")

    with pytest.raises(HTTPException) as e:
        run("o/n", transport=httpx.MockTransport(boom))
    assert e.value.status_code == 502


def test_a_redirected_repo_is_followed_not_a_500():
    """A renamed or transferred repo answers 301; it used to fall through to
    r.json() and crash."""
    inner = _t()

    def h(req: httpx.Request):
        if req.url.path.startswith("/repos/old/name"):
            target = "https://api.github.com/repos/o/n" + req.url.path[len("/repos/old/name"):]
            return httpx.Response(301, headers={"Location": target})
        return inner.handler(req)

    out = run("old/name", transport=httpx.MockTransport(h))
    assert out["repo"] == "o/n"


def test_an_empty_repository_still_returns_context():
    """GitHub answers 409 on /commits for a repo with no commits yet."""
    inner = _t()

    def h(req: httpx.Request):
        if req.url.path.endswith("/commits"):
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        return inner.handler(req)

    out = run("o/n", transport=httpx.MockTransport(h))
    assert "Recent commits:" in out["text"]


@pytest.mark.parametrize("token", ["tökén", "has space", "line\nbreak", "ghp_ok\r"])
def test_non_ascii_or_malformed_tokens_are_rejected_before_httpx(token):
    from pydantic import ValidationError

    from backend.github_context import ContextRequest

    with pytest.raises(ValidationError):
        ContextRequest(repo="o/n", token=token)
    assert ContextRequest(repo="o/n", token="github_pat_11AB_cd-ef.gh").token  # noqa: S106
