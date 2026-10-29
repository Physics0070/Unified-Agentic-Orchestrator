# Agent Council Orchestrator

An AI assistant that works like a small team. Ask it a question and it answers.
Ask it something big and it splits the work between several AI "specialists",
checks their work, and gives you one clear answer. Under every answer it shows
what the answer cost in time, tokens and money.

**Try it live:** <https://unified-agentic-orchestrator.vercel.app>

Built for CodeRush 2.0 (problem AE-03, "Unified Agent Orchestrator") by team
Latency Zero, VIT Pune.

---

## What it does, in plain words

There are two ways to use it.

**1. Chat.** Type a question.
- A simple question goes to one AI model and the answer streams back in a couple
  of seconds.
- A harder question is split across a few specialists that work at the same
  time. Their answers are merged into one reply.
- Under each answer you can open **benchmarks** to see which model answered,
  how long it took, how many tokens it used and what it cost.

**2. Orchestrator.** For bigger jobs, type a goal such as *"Compare REST and
GraphQL for a mobile app backend"*. The app then:
1. asks you a few clarifying questions,
2. asks your permission,
3. designs a team of AI agents and draws it as a graph,
4. waits for you to approve,
5. runs the agents (in parallel where it can),
6. shows live progress, and
7. lets you replay any past run and get the same result.

---

## Main features

| Feature | What it means |
|---|---|
| **Smart routing** | Easy questions use one cheap, fast model. Hard ones use a team. A router scores each model on quality, cost and speed. |
| **Knowledge workspace** | Upload PDF, Word, PowerPoint, Excel, text, Markdown or CSV files. Chat finds the relevant parts and uses them in its answer. Uploads are saved in the database. |
| **Several AI providers** | Works with Groq, Gemini, OpenAI, OpenRouter and Ollama. You set a key and pick the models. |
| **Typed hand-offs** | Agents pass each other structured forms, not loose chat. If a form is wrong, the exact error goes back and the agent tries again. |
| **Council review** | Several models propose answers, review each other without seeing who wrote what, and a chairman merges the best. |
| **Agent tools** | Agents can use a calculator, web search and a lookup in your uploaded documents. Only tools an agent is allowed to use can run. |
| **Execution trace** | For every agent you can see its input, output, tools used, time, tokens, cost and status. |
| **Replay** | Every action is written to an append-only log, so any run can be replayed and compared. |
| **Safety limits** | Per-IP rate limits and a daily token cap protect your API key on a public site. |

---

## How it works (the short version)

Everything the app does is saved as an **event** in one append-only table.
Nothing can change without leaving a record. The trace, replay, metrics and
pause/resume are all just different ways of reading that table.

```
Chat:
  question -> router -+-> simple  -> one model ------------------> answer
                      |
                      +-> harder  -> specialists (in parallel) -> merge -> answer

Orchestrator:
  goal -> questions -> approval -> agent team (parallel) -> check -> report
```

More detail: [docs/architecture.md](docs/architecture.md).

---

## Run it on your own computer

You need **Python 3.11+**, **Node 20+**, and a free [Turso](https://turso.tech)
database. You also need at least one AI provider, for example a free
[Groq](https://console.groq.com) key.

```bash
git clone https://github.com/Physics0070/Unified-Agentic-Orchestrator.git
cd Unified-Agentic-Orchestrator

# 1. Backend
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Settings
cp .env.example .env
# open .env and fill in TURSO_DATABASE_URL, TURSO_AUTH_TOKEN and GROQ_API_KEY

# 3. Create the database tables (run once)
turso db shell <your-db-name> < migrations/001_events.sql

# 4. Frontend
cd frontend && npm install && npm run build && cd ..

# 5. Start
uvicorn backend.main:app --port 7860
```

Open <http://localhost:7860>.

**No Turso account?** Leave `TURSO_DATABASE_URL` blank. The app then keeps its
log in a local file (`data/events.db`). That is fine for trying it out, but the
file is lost if the server's disk is wiped.

### The main settings

Put these in `.env` (or in your host's environment settings). Values are
plain text with no quotes and no spaces around them.

| Setting | Needed? | What it is |
|---|---|---|
| `TURSO_DATABASE_URL` | recommended | Your database address, like `libsql://name.turso.io` |
| `TURSO_AUTH_TOKEN` | with Turso | The database token |
| `GROQ_API_KEY` | one provider needed | Groq key. Default model: `openai/gpt-oss-120b` |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | optional | More providers to widen the team |
| `OLLAMA_BASE_URL`, `OLLAMA_API_KEY` | optional | Ollama (local, or Ollama Cloud) |
| `GROQ_MODELS`, `GEMINI_MODELS`, `OPENAI_MODELS` | optional | Choose models per provider, comma separated |
| `ADMIN_TOKEN` | for public sites | Protects the "set a key from the UI" endpoints |
| `DAILY_TOKEN_CAP` | optional | Most tokens the site may use per day (default 2,000,000, `0` = no limit) |
| `MAX_UPLOAD_BYTES` | optional | Biggest file you can upload (default 5 MB) |

Every setting, with its default, is listed in [.env.example](.env.example).
API keys are read in one place only (`backend/providers/secret_broker.py`) and
never appear in logs, events or the website code.

---

## Put it online

**Vercel (free), what the live site uses.** One project serves the website and
the API.

```bash
npm i -g vercel && vercel login
vercel deploy --prod
```

Then in the Vercel dashboard go to **Settings → Environment Variables** and add
your Turso values and at least one provider key. Redeploy after changing them.

What to expect on Vercel:
- Chat, knowledge uploads, the router and orchestrator runs all work.
- An orchestrator run finishes inside one request, so it must take less than
  5 minutes. Pause, resume and cancel need a server that stays running.
- Ollama on `localhost` is not available there, so use a cloud provider.

**Render or another always-on server.** `render.yaml` describes a free Render
service. Use it if you need long runs or pause and resume. The free tier sleeps
when idle and takes about a minute to wake up.

Firebase's free plan cannot run this backend, so it is not supported.

---

## Check that it works

```bash
pytest -q                    # all tests
ruff check .                 # code style and security lint
pip-audit -r requirements.txt --strict   # known vulnerabilities
```

Some tests need a real Turso database and are skipped without one. GitHub
Actions runs the tests, the linter, the audit and the frontend build on every
pull request.

---

## Good to know (current limits)

- **No model key, no answers.** The site needs at least one provider key.
- **Daily cap is per server process.** A restart resets the day's counter, so
  treat it as a safety net rather than exact billing.
- **Search quality.** Without the optional embeddings package
  (`requirements-embeddings.txt`, about 1 GB) document search uses keyword and
  character matching. It still works well, and the API says which method ran.
- **Knowledge on Vercel.** Each server instance keeps a copy in memory after the
  first load, so a fresh upload can take a moment to show on another instance.
- **Router numbers are estimates.** The speed and cost scores for each model are
  hand-written estimates in `backend/providers/catalog.py`, not live
  measurements.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'backend'` | Run commands from the project root, not from inside `backend/`. |
| Packages install but imports fail | You are using the wrong Python. Use `.venv/bin/python -m pip install ...`. |
| Chat says "provider returned 404" | The model name is wrong or retired. Set `GROQ_MODELS` to a model your account lists. |
| `/api/runs` returns 500 | Check `TURSO_AUTH_TOKEN`. A token pasted twice or with extra spaces breaks it (the app now uses the first copy). |
| `503 event_log_unavailable` | No writable database. Set `TURSO_DATABASE_URL`, or use a writable `file:` path. |
| Tests skip with "turso not configured" | `.env` has no Turso values, or the migration has not been run. |

---

## Data and honesty

Test data comes from real public sets:
[SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
(CC BY 4.0) and [BFCL](https://github.com/ShishirPatil/gorilla) (Apache 2.0).
No sample result in this repository is made up. Where data is missing, the code
raises an error instead of inventing something.

**Render (always on).** One service serves the website and the API, with no
5-minute limit, so pause / resume / cancel and live streaming all work. Step by
step in [docs/render.md](docs/render.md).

## More documentation

- [docs/architecture.md](docs/architecture.md): events, node types, data model
- [docs/threat-model.md](docs/threat-model.md): permissions, prompt-injection checks, key handling
- [docs/reproducibility.md](docs/reproducibility.md): how replay gives identical results
- [docs/deployment.md](docs/deployment.md): hosting notes
- [examples/](examples/): saved runs (clean, recovered, attacked)

## Licence

[Apache 2.0](LICENSE)
