# Deployment

Vercel is the only supported deployment. One project serves the built frontend
(static) and the whole FastAPI app as a single Python function (`api/index.py`);
`vercel.json` rewrites `/api/*`, `/health`, `/docs` and `/openapi.json` to it.

## What works on a serverless function, and what does not

| What the app does | On Vercel |
|---|---|
| Chat turn: plan, parallel council, streamed answer | Works - one request, one stream, up to the 300 s function limit |
| Orchestrator run (`POST /api/runs`, then `POST /api/runs/{id}/execute`) | Works - executes inline in one request, so a run must finish within 300 s |
| Pause, resume, cancel of a running orchestrator run | **Not available** - the function cannot be reached while another request is running it |
| Live SSE tail of a run started elsewhere (`/api/runs/{id}/stream`) | Only while that run is progressing in the same request |
| Knowledge workspace (in-memory index) | Works per warm instance; a fresh upload can take a moment to appear on another instance |
| torch + sentence-transformers (~1 GB) | Does not fit - search falls back to char n-gram TF-IDF, and the API says so |
| Ollama on `localhost` | Not reachable - use a cloud provider |

## Deploy

```bash
npm i -g vercel && vercel login
vercel deploy --prod
```

Then set the environment variables below under **Settings -> Environment
Variables** and redeploy. A push to `main` on the connected GitHub repo also
deploys to production.

`vercel.json` runs `cd frontend && npm install && npm run build` and serves
`frontend/dist`. `.vercelignore` keeps tests, docs and examples out of the function bundle.

## Environment

| Variable | Notes |
|---|---|
| `TURSO_DATABASE_URL` | Required for a durable event log. Without it the app falls back to a local SQLite file that is lost between invocations |
| `TURSO_AUTH_TOKEN` | **Secret.** Turso tokens can expire: if runs fail with `event_log_unavailable` and "token expired", create a new token and update this variable |
| `SECRET_KEY` | Prod refuses to boot without it |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | **Secret.** At least one provider key. Set `*_MODELS` to a model your key currently lists, or calls return 404 |
| `ADMIN_TOKEN` | Protects the "set a key from the UI" endpoints (falls back to `SECRET_KEY`) |
| `CORS_ORIGINS` | Not needed on Vercel (same origin); only for a local Vite dev server |
| `APP_ENV=prod` | Disables `/docs` |

Every setting is in [`.env.example`](../.env.example). Rotate `TURSO_AUTH_TOKEN`
and `SECRET_KEY` before any public deployment.

## Run locally (not a deployment)

FastAPI serves the built frontend and the API from one process on port 7860:

```bash
cd frontend && npm run build && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 7860
```

## Pre-deploy checklist

```bash
pytest -m "not slow"
ruff check .
pip-audit -r requirements.txt --strict
cd frontend && npm run build              # tsc clean
python -m backend.demo                    # PASS, replay diff zero
git ls-files | grep -x ".env"             # must return nothing
```
