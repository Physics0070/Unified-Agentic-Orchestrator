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

## Where to put the keys

Keys are environment variables on the Vercel project, never in the repo:

1. Open <https://vercel.com> -> your team -> the **unified-agentic-orchestrator** project.
2. **Settings -> Environment Variables -> Add New**.
3. Enter the **Key** (for example `NVIDIA_API_KEY`) and paste the **Value**. Tick **Production**
   (add Preview only if you use preview deployments). Mark secrets as **Sensitive**.
4. **Save**, then **Deployments -> the latest -> ... -> Redeploy**. A change to an environment
   variable only reaches a new deployment, so skip this and nothing changes.
5. Check `https://<your-site>/api/models`: the new provider's models appear once its key is set
   and its `*_MODELS` variable names at least one model.

| Provider | Key variable | Model variable (comma-separated, no `provider:` prefix) |
|---|---|---|
| Groq | `GROQ_API_KEY` | `GROQ_MODELS` (default `openai/gpt-oss-120b,openai/gpt-oss-20b`) |
| Gemini | `GEMINI_API_KEY` | `GEMINI_MODELS` (default blank: opt-in, its free tier returned 429 under light load) |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_MODELS`, e.g. `nvidia/nemotron-3-nano-30b-a3b:free` |
| NVIDIA NIM (Nemotron) | `NVIDIA_API_KEY` (or the per-model names `nemotron_3_ultra_550b_a55b_API_KEY`, `Mistral_Large_4_API_KEY`) | `NVIDIA_MODELS` (default `nvidia/nemotron-3-ultra-550b-a55b`) |
| Mistral (La Plateforme) | `MISTRAL_API_KEY` | `MISTRAL_MODELS`, e.g. `mistral-large-latest` |
| Ollama Cloud | `OLLAMA_API_KEY` and `OLLAMA_BASE_URL=https://ollama.com` | `OLLAMA_MODELS` |

OpenRouter and NVIDIA contribute **no** models until you name some, so adding a key alone
changes nothing. Free-tier limits are tight (OpenRouter `:free` is about 50 requests a day, and a
full orchestrator run makes about a dozen calls), so spread load across providers rather than
leaning on one.

## Environment

| Variable | Notes |
|---|---|
| `TURSO_DATABASE_URL` | Required for a durable event log. Without it the app falls back to a local SQLite file that is lost between invocations |
| `TURSO_AUTH_TOKEN` | **Secret.** Turso tokens can expire: if runs fail with `event_log_unavailable` and "token expired", create a new token and update this variable |
| `SECRET_KEY` | Prod refuses to boot without it |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | **Secret.** At least one provider key. Set `*_MODELS` to a model your key currently lists, or calls return 404 |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODELS`, `NVIDIA_API_KEY`, `NVIDIA_MODELS` | See "Where to put the keys" above |
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
