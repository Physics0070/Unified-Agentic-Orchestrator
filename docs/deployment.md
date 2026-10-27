# Deployment

## The constraint that decides the architecture

**The *whole* backend cannot run on a serverless platform - but chat can.**
This app is mostly a long-lived process; the parts of it that are not are
deployed to Vercel (see README, "Deployment"). What breaks, and what does not:

| What the app does | On a serverless function |
|---|---|
| Chat turn: plan, parallel council, streamed answer | Works - one request, one stream, up to the 300 s function limit |
| Orchestrator run in `asyncio.create_task` | **Breaks** - the function is frozen once the response is sent, so the run never finishes |
| SSE tail of a run (`/api/runs/{id}/stream`) | Works only while a run is progressing somewhere else (a persistent host) |
| Knowledge workspace (in-memory index) | Works per warm instance; not durable across instances or cold starts |
| torch + sentence-transformers (~1 GB) | Does not fit - the semantic leg falls back to char n-gram TF-IDF, and the API says so |
| Ollama local models | Needs several GB resident on a host that stays up |

So there are three supported shapes.

---

## Option C — Render, no Docker (what is actually deployed)

Two free Render services, no Dockerfile involved: a native Python web service
for the backend, a static site for the built frontend. `render.yaml` at the
repo root defines both - connect the repo in the Render dashboard, "New +
Blueprint", and it provisions them from that file.

- **Backend** (`aco-backend`): `runtime: python`, `pip install -r
  requirements.txt`, `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
  A real persistent process, not a serverless function, so the SSE stream
  holds open for as long as an answer takes. Free tier sleeps after 15 minutes
  idle, ~1 minute cold start on the next request.
- **Frontend** (`aco-frontend`): `runtime: static`, `npm ci && npm run build`
  in `frontend/`, published from `frontend/dist`. Free, no sleep, served from
  Render's CDN. `VITE_API_BASE` points it at the backend service's URL - the
  same split-hosting mechanism Option B below uses.

Secrets go in the Render dashboard per service, never in `render.yaml`
(`sync: false` there means "prompt me, don't commit it"):
`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `SECRET_KEY`, and `OPENROUTER_API_KEY`
(or `GROQ_API_KEY` / `GEMINI_API_KEY` as a fallback - see
`backend/api/routes.py::_default_models`).

---

## Option A — single origin (simplest, and what the demo uses)

FastAPI serves the built frontend and the API from one process on port 7860.
Nothing to configure: `VITE_API_BASE` stays blank and the frontend calls
relative `/api/...` paths.

```bash
cd frontend && npm run build && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 7860
```

Verified locally:

```bash
curl localhost:7860/health      # {"status":"ok","version":"0.1.0"}
```

---

## Option B — split hosting (frontend on Netlify)

Use this if you specifically want the UI on a static host.

**Frontend** — `netlify.toml` is already in `frontend/` (`base` is already set
to `frontend`).

- Set the build env var:

```
VITE_API_BASE=https://your-backend-host.example.com
```

It is baked in at build time, so changing it requires a redeploy.

**Backend** — deploy Option A somewhere that keeps a process alive: Render,
Railway or Fly.io.

**Then allow the frontend's origin**, or every request fails CORS:

```
CORS_ORIGINS=https://your-app.netlify.app
```

The allow-list is explicit by design — `CORS_ORIGINS=*` raises at startup.

### The one thing to test after splitting

SSE across origins. `EventSource` sends no custom headers, so it should work,
but confirm the trace viewer actually streams before you rely on it live. If it
does not, the run still completes — the log is in Postgres, and
`GET /api/runs/{id}/events` returns it in full.

---

## Environment

| Variable | Where | Notes |
|---|---|---|
| `TURSO_DATABASE_URL` | backend | Required |
| `TURSO_AUTH_TOKEN` | backend | **Secret.** Server-side only, never in the bundle |
| `SECRET_KEY` | backend | Prod refuses to boot without it |
| `OPENROUTER_API_KEY` | backend | **Secret.** Preferred - one key, three model families, $0. No local-model fallback in prod |
| `GROQ_API_KEY` | backend | **Secret.** Fallback if `OPENROUTER_API_KEY` is unset |
| `GEMINI_API_KEY` | backend | **Secret.** Fallback if `OPENROUTER_API_KEY` is unset |
| `CORS_ORIGINS` | backend | Explicit list; `*` is rejected |
| `APP_ENV=prod` | backend | Disables `/docs` |
| `VITE_API_BASE` | frontend | Blank for single-origin; full URL for split |

Rotate `TURSO_AUTH_TOKEN` and `SECRET_KEY` before any public deployment.

---

## Pre-deploy checklist

```bash
pytest -m "not slow"                      # 60 passed
ruff check .
pip-audit -r requirements.txt --strict    # no known vulnerabilities
cd frontend && npm run build              # tsc clean
python -m backend.demo                    # PASS, replay diff zero
git ls-files | grep -x ".env"             # must return nothing
```
