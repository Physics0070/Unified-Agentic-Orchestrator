"""Bounded plain-text summary of a GitHub repo, so a goal can carry real repo context.

Only ever calls https://api.github.com/repos/{owner}/{name}[...] with a strictly
validated owner/name. The optional token is used for the upstream calls and is
never logged or stored.
"""

import asyncio
import base64
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.limiter import limiter

MAX_CHARS = 3000
# Upstream README bytes we are willing to decode (base64 is ~4/3 of the bytes).
_MAX_README_B64 = 64_000
# Read-only and public (no admin token), so it is capped per client instead.
RATE_LIMIT = "10/minute"
_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
_API = "https://api.github.com"

router = APIRouter(prefix="/api/github")


class ContextRequest(BaseModel):
    repo: str = Field(max_length=200)
    # GitHub tokens are ASCII (ghp_..., github_pat_...). Anything else would
    # blow up inside httpx while encoding the header - a 500 - so refuse it here.
    token: str | None = Field(
        default=None, max_length=255, repr=False, pattern=r"^[A-Za-z0-9_\-.]+$"
    )


def _check(repo: str) -> str:
    if not _REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
        raise HTTPException(status_code=422, detail="Repository must look like owner/name.")
    return repo


def _fail(r: httpx.Response) -> HTTPException:
    s = r.status_code
    if s == 404:
        return HTTPException(404, "Repository not found (or private: provide a token).")
    if s == 429 or (s == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
        return HTTPException(429, "GitHub rate limit reached. Provide a token for a higher limit.")
    if s in (401, 403):
        return HTTPException(403, "GitHub refused access. Check the token's permissions.")
    return HTTPException(502, f"GitHub answered {s}.")


async def fetch_repo_context(
    repo: str, token: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict:
    repo = _check(repo)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "aco-github-context"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            base_url=_API, headers=headers, timeout=httpx.Timeout(8.0), transport=transport,
            # A renamed or transferred repo answers 301. GitHub redirects within its
            # own API, and httpx drops the Authorization header if one ever points
            # at another origin.
            follow_redirects=True,
        ) as c:

            async def get(p: str, missing: tuple[int, ...] = ()):
                r = await c.get(f"/repos/{repo}{p}")
                if r.status_code in missing:
                    return None
                if r.status_code >= 300:
                    raise _fail(r)
                return r.json()

            meta, readme, tree, commits = await asyncio.gather(
                get(""), get("/readme", (404,)), get("/contents", (404,)),
                # 409 is GitHub's answer for a repository with no commits yet.
                get("/commits?per_page=5", (409,)),
            )
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach api.github.com.") from None

    lines = [
        f"{meta['full_name']}: {meta.get('description') or 'no description'}",
        f"Language: {meta.get('language') or 'n/a'}; "
        f"stars: {meta.get('stargazers_count', 0)}; "
        f"default branch: {meta.get('default_branch')}",
    ]
    if isinstance(tree, list):
        lines.append(
            "Top-level: "
            + ", ".join(e["name"] + ("/" if e.get("type") == "dir" else "") for e in tree[:40])
        )
    lines.append("Recent commits:")
    for c in commits or []:
        lines.append(f"- {c['sha'][:7]} {c['commit']['message'].splitlines()[0][:100]}")
    if readme and readme.get("encoding") == "base64":
        raw = "".join((readme.get("content") or "").split())[:_MAX_README_B64]
        text = base64.b64decode(raw[: len(raw) - len(raw) % 4]).decode("utf-8", "replace")
        lines += ["README:", text.strip()]
    out = "\n".join(lines)
    return {"repo": meta["full_name"], "text": out[:MAX_CHARS], "truncated": len(out) > MAX_CHARS}


@router.post("/context")
@limiter.limit(RATE_LIMIT)
async def github_context(request: Request, req: ContextRequest) -> dict:
    return await fetch_repo_context(req.repo, req.token)
