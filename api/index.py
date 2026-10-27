"""Vercel entrypoint: the whole FastAPI app as one Python function.

The frontend is built to frontend/dist and served statically by Vercel; every
/api/* request is rewritten here (see vercel.json). Same app object Render
runs, no separate code path.

What a function cannot do, and this file does not pretend otherwise: hold a
process between requests. Chat (one request, one streamed answer) works.
Orchestrator runs started with POST /api/runs execute in a background task
after the response is sent, which a function does not keep alive - run those on
a persistent host (render.yaml). See docs/deployment.md.
"""

from backend.main import app

__all__ = ["app"]
