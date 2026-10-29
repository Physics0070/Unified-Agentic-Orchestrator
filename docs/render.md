# Deploy on Render (one always-on service)

Render runs the app as one service that serves both the website and the API.
Unlike Vercel, nothing is killed after 5 minutes, so long orchestrator runs
finish, pause / resume / cancel work, and the live trace streams in real time.

## Steps

1. Push this repo to GitHub (it already is).
2. Create a free account at https://render.com and connect your GitHub.
3. Click **New +**, then **Blueprint**, pick this repo. Render reads
   `render.yaml` and builds the `Dockerfile` (it builds the React app, then
   starts the API on the port Render gives it).
   (Or choose **New Web Service**, runtime **Docker**, health check path
   `/health`.)
4. In the service **Environment** tab, paste the values below.
5. Deploy. Open the `https://<name>.onrender.com` link.

## Environment variables

Already set by `render.yaml`: `APP_ENV=prod`, `RUN_TIMEOUT_S=1800`, model
lists, and `SECRET_KEY` (Render generates it).

You paste these (all optional except one provider key):

| Variable | What it is |
| --- | --- |
| `GROQ_API_KEY` | Groq key. Any one provider key is enough. |
| `GEMINI_API_KEY` | Google Gemini key (optional) |
| `OPENROUTER_API_KEY` | OpenRouter key (optional) |
| `OPENAI_API_KEY` | OpenAI key (optional) |
| `TURSO_DATABASE_URL` | Turso database URL, so history survives restarts |
| `TURSO_AUTH_TOKEN` | Turso token for that database |
| `ADMIN_TOKEN` | Password for the key-management endpoints |

Without the Turso values the app still works, but it stores history in a local
file that Render's free tier wipes on every restart or deploy.

## Health check

Render calls `/health` and expects `200 {"status":"ok"}`.

## What to expect on the free tier

- The service sleeps after about 15 minutes with no traffic. The first visit
  after that takes roughly 30 to 60 seconds to wake up (cold start).
- While it is awake, runs have no 5 minute limit (the app stops a run after
  30 minutes by default; change `RUN_TIMEOUT_S`).
- If the service restarts mid-run, that run is lost; start it again.
