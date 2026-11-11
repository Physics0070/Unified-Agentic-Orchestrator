<div align="center">

# 🧭 Agent Council Orchestrator

**An AI assistant that works like a small team: it splits big jobs between specialist agents, checks their work, and shows exactly what every answer cost.**

`FastAPI` · `React` · `Turso / libSQL` · `Typed agent hand-offs` · `Council review` · `Event-sourced replay`

[Live demo](https://unified-agentic-orchestrator.vercel.app) · [Features](#-core-features) · [Install](#-installation-guide) · [Settings](#-environment-variables) · [Roadmap](#-challenges--roadmap)

</div>

---

## 💡 The problem and what inspired it

Ask one AI model a hard question and you get one unchecked answer, with no clue
what it cost or how it got there. Multi-agent tools promise better results, but
most of them are black boxes: agents pass loose chat to each other, nobody
reviews the work, and a run can't be reproduced.

This project was built for **CodeRush 2.0 (problem AE-03, "Unified Agent
Orchestrator")** by team **Latency Zero, VIT Pune**. We wanted a team of agents
that is *accountable*:

- simple questions stay cheap and fast, and only hard ones bring in a team;
- agents hand each other **structured forms**, and a wrong form is sent back
  with the exact error;
- several models propose answers, review each other **blind**, and a chairman
  merges the best;
- every action is recorded, so any run can be **replayed** and compared;
- every answer shows its time, tokens and money.

---

## 🔗 Demo links

| What | Where |
|---|---|
| **Live app** | <https://unified-agentic-orchestrator.vercel.app> (checked reachable, HTTP 200) |
| **Demo walkthrough** | [docs/demo-script.md](docs/demo-script.md) |
| **Saved example runs** | [examples/](examples/): a clean run, a recovered run and an attacked run |
| **Pitch video** | not added yet |

---

## ✨ Core features

- **Smart routing.** Easy questions use one cheap, fast model. Hard ones use a
  team. A router scores each model on quality, cost and speed.
- **Orchestrator mode.** Give it a goal. It asks clarifying questions, asks your
  permission, designs a team of agents, draws it as a graph, waits for your
  approval, runs the agents (in parallel where it can) and streams live progress.
- **Typed hand-offs.** Agents pass each other schema-checked forms, not loose chat.
  If a form is wrong, the exact error goes back and the agent tries again.
- **Council review.** Several models propose, review each other without seeing who
  wrote what, and a chairman merges the best.
- **Agent tools.** Calculator, web search and a lookup in your uploaded documents.
  Only tools an agent is allowed to use can run.
- **Knowledge workspace.** Upload PDF, Word, PowerPoint, Excel, text, Markdown or
  CSV. Chat finds the relevant parts and uses them in its answer.
- **Execution trace.** For every agent: input, output, tools used, time, tokens,
  cost and status.
- **Replay.** Every action goes into one append-only log, so any run can be
  replayed and compared.
- **Many providers.** Groq, Gemini, OpenAI, OpenRouter and Ollama. Set a key and
  pick the models.
- **Safety limits.** Per-IP rate limits and a daily token cap protect your API key
  on a public site. Optional user accounts (email and password).

---

## 🧱 Tech stack

| Layer | Used |
|---|---|
| **Backend** | Python 3.11+, FastAPI, Pydantic, httpx, sse-starlette (live streaming), jsonschema (hand-off checks) |
| **Database** | Turso / libSQL, with a local SQLite file fallback |
| **Frontend** | React 18, TypeScript, Vite 6, Tailwind CSS 4, React Flow (agent graph) |
| **AI providers** | Groq, Gemini, OpenAI, OpenRouter, Ollama |
| **Documents and tools** | pypdf, python-docx, python-pptx, openpyxl, fpdf2, DuckDuckGo search (`ddgs`) |
| **Security** | slowapi (rate limits), PyJWT (sessions), python-magic (upload type checks) |
| **Extras** | scikit-learn (Workflow DNA fitness prediction), optional local embeddings |
| **Tooling** | pytest, ruff, pip-audit, GitHub Actions |
| **Hosting** | Vercel (free), Render (always on), Docker |

---

## 🚀 Installation guide

You need **Python 3.11+**, **Node 20+**, and at least one AI provider key, for
example a free [Groq](https://console.groq.com) key. A free
[Turso](https://turso.tech) database is recommended.

```bash
git clone https://github.com/Physics0070/Unified-Agentic-Orchestrator.git
cd Unified-Agentic-Orchestrator
```

```bash
# 1. Backend
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

```bash
# 2. Settings: open .env and fill in TURSO_DATABASE_URL, TURSO_AUTH_TOKEN and GROQ_API_KEY
cp .env.example .env
```

```bash
# 3. Create the database tables (run once)
turso db shell <your-db-name> < migrations/001_events.sql
```

```bash
# 4. Frontend
cd frontend && npm install && npm run build && cd ..
```

```bash
# 5. Start
uvicorn backend.main:app --port 7860
```

Open <http://localhost:7860>.

**No Turso account?** Leave `TURSO_DATABASE_URL` blank. The app keeps its log in a
local file (`data/events.db`). Fine for trying it out, but the file is lost if the
server's disk is wiped.

### Run the checks

```bash
pytest -q                                  # all tests
ruff check .                               # code style and security lint
pip-audit -r requirements.txt --strict     # known vulnerabilities
```

Some tests need a real Turso database and are skipped without one. GitHub Actions
runs the tests, the linter, the audit and the frontend build on every pull request.

### Put it online

- **Render (always on).** One service serves the website and the API with no
  5-minute limit, so pause, resume, cancel and live streaming all work. See
  [docs/render.md](docs/render.md) and `render.yaml`. The free tier sleeps when
  idle and takes about a minute to wake.
- **Vercel (free).** `vercel deploy --prod`, then add your Turso values and a
  provider key under **Settings → Environment Variables**. An orchestrator run must
  finish within one request (under 5 minutes), and pause/resume/cancel are not
  available. Ollama on `localhost` is not reachable there.

Firebase's free plan cannot run this backend, so it is not supported. More in
[docs/deployment.md](docs/deployment.md).

---

## 🔐 Environment variables

Put these in `.env` (or your host's environment settings), as plain text with no
quotes. **Every setting is listed in [.env.example](.env.example).**

| Variable | Needed? | What it is |
|---|---|---|
| `TURSO_DATABASE_URL` | recommended | Database address, like `libsql://name.turso.io` |
| `TURSO_AUTH_TOKEN` | with Turso | Database token |
| `GROQ_API_KEY` | one provider needed | Groq key. Default model: `openai/gpt-oss-120b` |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | optional | More providers to widen the team |
| `OLLAMA_BASE_URL`, `OLLAMA_API_KEY` | optional | Ollama (local, or Ollama Cloud) |
| `GROQ_MODELS`, `GEMINI_MODELS`, `OPENAI_MODELS` | optional | Pick models per provider, comma separated |
| `ADMIN_TOKEN` | for public sites | Protects the "set a key from the UI" endpoints (falls back to `SECRET_KEY`) |
| `SECRET_KEY` | for public sites / accounts | Signs sessions; required when `AUTH_REQUIRED` is on |
| `AUTH_REQUIRED` | optional | `true` = runs, compile, clarify and knowledge need a signed-in user |
| `DAILY_TOKEN_CAP` | optional | Most tokens the site may use per day (default 2,000,000, `0` = no limit) |
| `MAX_UPLOAD_BYTES` | optional | Biggest upload (default 5 MB) |

API keys are read in one place only (`backend/providers/secret_broker.py`) and
never appear in logs, events or the website code.

---

## 🛣️ Challenges and roadmap

**Challenges we hit and how we handled them**

- **Serverless time limits.** Vercel ends a request after 5 minutes, which can't
  hold a long agent run. We added an always-on Render deployment for long runs and
  pause/resume.
- **Agents that return malformed output.** Free-form chat between agents broke
  easily, so hand-offs became typed forms with the exact error sent back for a retry.
- **Reproducing a run.** Instead of scattered logs, everything is one append-only
  event table. The trace, replay, metrics and pause/resume are just different ways
  of reading it.
- **Protecting a public API key.** Per-IP rate limits, a daily token cap, tool
  allow-lists and optional sign-in.

**Known limits (honest list)**

- No provider key means no answers.
- The daily cap is per server process, so a restart resets it. Treat it as a safety
  net, not billing.
- Without the optional embeddings package (about 1 GB, `requirements-embeddings.txt`)
  document search uses keyword and character matching. The API says which method ran.
- On Vercel, a fresh upload can take a moment to show on another instance.
- The router's speed and cost scores are hand-written estimates in
  `backend/providers/catalog.py`, not live measurements.
- Tests that need a real Turso database are skipped without one.

**Roadmap**

- Concurrency-safe shared budget across parallel agents.
- Tests for execution, permissions, replay and persistence that always run in CI
  without Turso credentials, with real-Turso tests in their own job.
- A reproducible benchmark: single agent vs sequential vs parallel vs full council,
  on identical tasks, reported with its limits.
- Live cost and latency measurement to replace the router's estimates.
- A pitch video for the demo links.

---

## 🧪 Data and honesty

Test data comes from real public sets:
[SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
(CC BY 4.0) and [BFCL](https://github.com/ShishirPatil/gorilla) (Apache 2.0). No
sample result in this repository is made up. Where data is missing, the code raises
an error instead of inventing something.

## 📚 More documentation

- [docs/architecture.md](docs/architecture.md): events, node types, data model
- [docs/threat-model.md](docs/threat-model.md): permissions, prompt-injection checks, key handling
- [docs/reproducibility.md](docs/reproducibility.md): how replay gives identical results
- [docs/deployment.md](docs/deployment.md): hosting notes
- [docs/render.md](docs/render.md): Render step by step
- [examples/](examples/): saved runs (clean, recovered, attacked)

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'backend'` | Run commands from the project root, not from inside `backend/`. |
| Packages install but imports fail | Wrong Python. Use `.venv/bin/python -m pip install ...`. |
| Chat says "provider returned 404" | The model name is wrong or retired. Set `GROQ_MODELS` to a model your account lists. |
| `/api/runs` returns 500 | Check `TURSO_AUTH_TOKEN`. A token pasted twice or with extra spaces breaks it. |
| `503 event_log_unavailable` | No writable database. Set `TURSO_DATABASE_URL`, or use a writable `file:` path. |
| Tests skip with "turso not configured" | `.env` has no Turso values, or the migration has not been run. |

## 📄 Licence

[Apache 2.0](LICENSE)
