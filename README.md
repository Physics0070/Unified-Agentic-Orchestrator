<div align="center">

# 🧭 Agent Council Orchestrator

**Governed, replayable, cost-auditable multi-agent runs: for when you need to prove what the agents did.**

The audit layer for agent teams: typed hand-offs, blind council review, blocked tools, and a flight recorder that replays any run for $0.

`FastAPI` · `React` · `Turso / libSQL` · `Typed agent hand-offs` · `Council review` · `Event-sourced replay`

[Live demo](https://unified-agentic-orchestrator.vercel.app) · [Features](#-core-features) · [Install](#-installation-guide) · [Settings](#-environment-variables) · [Roadmap](#-challenges--roadmap)

</div>

---

## 💡 The problem and what inspired it

Agents are about to touch money, contracts and compliance decisions. Today it is hard to prove
what an agent team did, in what order, with which model, and at what cost. Most multi-agent tools
are black boxes: agents pass loose chat to each other, nobody reviews the work, and a run can't
be reproduced.

We built the audit layer: typed hand-offs, blind council review and a replayable event log. We
also measured accuracy ([docs/benchmark.md](docs/benchmark.md)), and a single model was at least
as good on our checks (8 questions, so treat it as a signal, not a verdict). That is the point:
**governance is a separate problem from capability**, and this project is about governance.

- agents hand each other **structured forms**, and a wrong form is sent back with the exact
  error, so every hand-off is checked;
- several models propose answers, review each other **blind**, and a chairman merges the best
  (with at least two working models; under provider rate limits a member drops out and it
  deliberates with fewer);
- every action is recorded in an append-only log, so any run can be **replayed** with the same
  output and no model call, and compared;
- undeclared tool calls are blocked and injected instructions quarantined (layers of defence,
  not a proof of safety);
- orchestrator runs report their measured time, tokens and cost (quick direct answers record
  no usage, so they show none);
- simple questions stay cheap and fast, and only harder ones bring in a team.

---

## 🔗 Demo links

| What | Where |
|---|---|
| **Live app** | <https://unified-agentic-orchestrator.vercel.app> |
| **Best demo goal** | *Evaluate Postgres, MongoDB and DynamoDB independently for a chat app, then recommend one*: sometimes compiles to three parallel research branches (3 of 6 compiles in our tests; the other 3, including the last three live runs, were a sequential chain, because a model plans the graph) |
| **Demo walkthrough** | [docs/demo-script.md](docs/demo-script.md) (3 minutes, with the measured numbers to expect) |
| **Saved example runs** | [examples/](examples/): a clean run, a recovered run and an attacked run. **These use scripted stand-in answers, not live models** (see the note under *Data and honesty*). |
| **Pitch video** | not added yet |

---

## ✨ Core features

- **Smart routing.** Easy questions use one cheap, fast model. Hard ones use a
  team. A router scores each model on quality, cost and speed.
- **Orchestrator mode.** Give it a goal. It asks clarifying questions, asks your
  permission, designs a team of agents, draws it as a graph, waits for your
  approval, runs the agents and streams live progress. Independent parts run in
  parallel, but a model plans the graph, so vague goals often compile to a sequential
  chain (measured parallel efficiency 0.77 to 0.83 on such runs).
- **Typed hand-offs.** Agents pass each other schema-checked forms, not loose chat.
  If a form is wrong, the exact error goes back and the agent tries again.
- **Council review.** Several models propose, review each other without seeing who
  wrote what, and a chairman merges the best. Default members: Groq `gpt-oss-120b` and
  `gpt-oss-20b` plus NVIDIA Nemotron 550B. It needs at least two working members.
- **Agent tools.** Calculator, web search and a lookup in your uploaded documents.
  Only tools an agent is allowed to use can run.
- **Knowledge workspace.** Upload PDF, Word, PowerPoint, Excel, text, Markdown or
  CSV. Chat finds the relevant parts and uses them in its answer.
- **Execution trace.** For every agent: input, output, tools used, time, tokens,
  cost and status.
- **Replay.** Every action goes into one append-only log, so any run can be
  replayed and compared. Measured on the live site: identical output, $0 cost, no model call.
- **Many providers.** Groq and NVIDIA NIM (Nemotron) by default; OpenRouter, Mistral,
  Gemini, OpenAI and Ollama are supported and opt-in. Set a key and name the models.
- **Safety limits.** Per-IP rate limits and a daily token cap protect your API key
  on a public site. Optional user accounts (email and password).

---

## 🧱 Tech stack

| Layer | Used |
|---|---|
| **Backend** | Python 3.11+, FastAPI, Pydantic, httpx, sse-starlette (live streaming), jsonschema (hand-off checks) |
| **Database** | Turso / libSQL, with a local SQLite file fallback |
| **Frontend** | React 18, TypeScript, Vite 6, Tailwind CSS 4, React Flow (agent graph) |
| **AI providers** | Groq, NVIDIA NIM, OpenRouter, Mistral, Gemini, OpenAI, Ollama |
| **Documents and tools** | pypdf, python-docx, python-pptx, openpyxl, fpdf2, DuckDuckGo search (`ddgs`) |
| **Security** | slowapi (rate limits), PyJWT (sessions), python-magic (upload type checks) |
| **Extras** | scikit-learn (Workflow DNA fitness prediction), optional local embeddings |
| **Tooling** | pytest, ruff, pip-audit, GitHub Actions |
| **Hosting** | Vercel |

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

The suite runs on a local SQLite database, so it needs no credentials. A few tests skip
without optional extras (the embeddings package, a live Ollama). Nothing runs against a real
Turso database in CI yet. GitHub Actions runs the tests, the linter, the audit and the
frontend build.

### Put it online

Vercel is the only supported deployment. `vercel deploy --prod`, then add your Turso
values and a provider key under **Settings → Environment Variables**. An orchestrator
run must finish within one request (under 5 minutes), and pause, resume and cancel
are not available. Ollama on `localhost` is not reachable there. Details in
[docs/deployment.md](docs/deployment.md).

---

## 🔐 Environment variables

Put these in `.env` (or your host's environment settings), as plain text with no
quotes. **Every setting is listed in [.env.example](.env.example).**

| Variable | Needed? | What it is |
|---|---|---|
| `TURSO_DATABASE_URL` | recommended | Database address, like `libsql://name.turso.io` |
| `TURSO_AUTH_TOKEN` | with Turso | Database token |
| `GROQ_API_KEY` | one provider needed | Groq key. Default models: `openai/gpt-oss-120b`, `openai/gpt-oss-20b` |
| `NVIDIA_API_KEY` | optional | NVIDIA NIM key. Default model: `nvidia/nemotron-3-ultra-550b-a55b` |
| `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY` | optional | More providers; each joins the council only when its `*_MODELS` names a model (Gemini and OpenRouter default to none) |
| `OLLAMA_BASE_URL`, `OLLAMA_API_KEY` | optional | Ollama (local, or Ollama Cloud) |
| `GROQ_MODELS`, `NVIDIA_MODELS`, `OPENROUTER_MODELS`, `MISTRAL_MODELS`, `GEMINI_MODELS`, `OPENAI_MODELS` | optional | Pick models per provider, comma separated. Entries that look like an API key are ignored (model lists are public) |
| `PROVIDER_CALL_TIMEOUT_S` | optional | Cut off one model call after this many seconds and fail over (default 60) |
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

- **Serverless time limits.** Vercel ends a request after 5 minutes and freezes a
  function once it responds, so a run executes inline inside one request
  (`POST /api/runs/{id}/execute`). The cost: runs must finish within 5 minutes, and
  pause, resume and cancel are unavailable there.
- **Agents that return malformed output.** Free-form chat between agents broke
  easily, so hand-offs became typed forms with the exact error sent back for a retry.
- **Reproducing a run.** Instead of scattered logs, everything is one append-only
  event table. The trace, replay and metrics are just different ways of reading it
  (pause, resume and cancel are implemented too, but need a persistent process, so
  they are not available on Vercel).
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
- Free tiers are small and inconsistent. Groq's is about 200K tokens a day and 8K a minute on
  `gpt-oss-120b` (one run uses about 4,000 to 6,000 tokens, so a few judges can exhaust it),
  Gemini returned 429 under light load (so it is opt-in), and NVIDIA's free endpoint gave one
  empty reply in two tries on the 550B Nemotron while other Nemotron ids returned 404 or timed
  out. A hung call is cut off after 60 s and an empty reply counts as a failure; both fail over
  to a backup model.
- The council depends on its providers: under rate limits (HTTP 429) or slow replies a member
  drops out and it deliberates with fewer models. It needs at least two working models.
- Streamed direct ("fast route") answers record 0 tokens and $0 in the event log, so the cost line
  under those answers is empty. Orchestrator runs are fully measured.
- [docs/benchmark.md](docs/benchmark.md): on 8 questions the pipeline was **not** better than one
  model on the objective checks used, at about 2.4x the cost and 10x the latency. Its value is
  governance and replay, not accuracy.

**Roadmap**

- Switch on the run-wide budget by default (`RunBudget` exists and is tested, but is opt-in).
- Real-Turso tests in their own CI job (the persistence tests already run on local SQLite).
- A quality study of single model vs pipeline with a human or stronger-model judge on 50+
  prompts (the first small benchmark is in [docs/benchmark.md](docs/benchmark.md)).
- Record token usage for streamed direct answers.
- Live cost and latency measurement to replace the router's estimates.
- Link the demo video above once it is uploaded.

---

## 🧪 Data and honesty

Test data comes from real public sets:
[SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
(CC BY 4.0) and [BFCL](https://github.com/ShishirPatil/gorilla) (Apache 2.0). Where
data is missing, the code raises an error instead of inventing something.

**The saved example runs in `examples/` are scripted.** They are generated by
`python -m backend.demo --export` with `fixture_completer`, a stand-in that returns
fixed, hand-written findings and deliberately fails the `docs` agent in
`run_recovered.json`. No language model is called, so their token counts, cost
and the model names shown are fixture values, not measurements. What *is* real in
them is the engine's own behaviour: the event sequence, hand-off validation and
rejection, repair attempts, compensation, tool blocking and replay. Real-model runs
are what the live app produces (`python -m backend.demo --live` uses real models
for the clean run).

## 📚 More documentation

- [docs/architecture.md](docs/architecture.md): events, node types, data model
- [docs/threat-model.md](docs/threat-model.md): permissions, prompt-injection checks, key handling
- [docs/reproducibility.md](docs/reproducibility.md): how replay gives identical results
- [docs/deployment.md](docs/deployment.md): hosting notes
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
