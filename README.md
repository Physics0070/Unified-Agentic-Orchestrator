# Agent Council Orchestrator

**CodeRush 2.0 · Problem statement AE-03 — Unified Agent Form Orchestrator**
Team **Latency Zero** · VIT Pune

**Live:** <https://sprightly-salmiakki-bb8ed0.netlify.app/>

**Ask it anything and it answers, like any assistant. Then it shows you what
the answer cost.**

Simple questions go straight to one model and stream back in about two seconds.
Harder ones are split across specialists that run in parallel, and the answers
are merged into one reply. You get prose, not a findings table — and under every
answer, the measured truth about how it was produced.

For heavier work there is a second surface: type a goal, and the system asks
clarifying questions, asks permission, designs an agent team as a visual graph,
shows it for approval, executes it parallel-first, streams live state, and
replays any past run with a provably identical result.

Runs at **₹0** — Ollama Cloud's free tier answers by default, Groq and Gemini
widen the council with a different model family each when configured, and
persistence is Turso's free tier. The same Ollama adapter also talks to a
local or tunnelled instance for development.

---

## What makes it different

**0. It decides how hard your question is, and spends accordingly.**
A council of four is not free. The router reads what you actually asked and
picks the cheapest shape that answers it well — most messages skip the council
entirely. It also derives the specialists *from your question*: ask about
locking in a ledger and you get `locking_strategies` and `ledger_architecture`,
not a generic security/quality/docs template.

**1. Agents pass typed forms, not chat messages.**
Every edge in the graph carries a JSON Schema. A wrong shape is rejected, the
*exact* validator error goes back to the producing agent, and it retries. Like a
hospital handover chart with required fields, not "the patient seems unwell."

**2. A council, not a single boss.**
Several models each propose, then **peer-review each other anonymously** and
rank. A chairman merges. Authorship is stripped before ranking — a reviewer
shown "proposal by qwen2.5" rates the label, not the content.

**3. It reports when it is overkill.**
We measure whether more agents actually helped, and say so when they did not.
An agent scoring MIG below 0.1 produced nothing the others did not already have,
and the system names it out loud.

---

## Workspace features

**Multi-provider management** (`providers` tab). Ollama, Groq, Gemini, OpenAI
and OpenRouter each have a card: paste a key, pick models, test the connection,
disconnect. A key set here lives in process memory only - never in a log, an
event row or a response - and an `ADMIN_TOKEN` (or `SECRET_KEY`) guards the
write endpoints. Environment variables remain the durable path.

**Knowledge workspace** (`knowledge` tab). Upload PDF, DOCX, PPTX, XLSX, TXT, MD,
CSV and common code/text formats. Files are validated by magic bytes, chunked on
paragraph boundaries and indexed in memory per workspace (not persisted - the
UI says so). Retrieval is **hybrid**: BM25 keyword search and a semantic leg
(sentence embeddings when `requirements-embeddings.txt` is installed, otherwise
character n-gram TF-IDF - the API reports which one ran), merged with
Reciprocal Rank Fusion, reranked by idf-weighted query coverage and adjacent-
term matches, then de-duplicated. Chat turns retrieve automatically from your
documents and cite them; every score is visible under "Test retrieval".

**Model router.** Each question is scored per candidate on capability (the
catalog's fit for the intent), cost and latency, weighted by how hard the
question is and a `priority` (balanced / quality / speed / cost). "hi" no longer
buys the strongest, slowest model; a deep audit does. The decision, with every
candidate's components, is returned with each answer. The capability, cost and
latency numbers are curated estimates (`backend/providers/catalog.py`), not live
measurements.

**Dynamic orchestration.** The council designs the workflow from the goal:
branches may declare `depends_on`, so a build goal becomes a pipeline
(plan -> implement -> review) while a comparison fans out in parallel. Cycles
and unknown dependencies in model output are repaired in code. The fixed
security/quality/docs team is now used only for code audits.

**Execution trace.** Every agent's input, output, tools declared / called /
blocked, duration, tokens, cost and status - in the orchestrator's `agents`
tab (`GET /api/runs/{id}/trace`) and under each chat answer.

---

## Architecture in one paragraph

One append-only Postgres table is the single source of truth. Observability,
replay, every metric, the trace viewer, pause/resume and the reproducibility
report are all *reads* of that table. No state change happens anywhere without
an event row.

```
                        ┌─ simple ──▶ one model ─────────────────▶ answer
question ─▶ router ─────┤
                        └─ moderate/deep ─▶ fanout ─┬─▶ specialist ─┐
                                                    ├─▶ specialist ─┼─▶ merge ─▶ answer
                                                    └─▶ specialist ─┘
                                                      (concurrent)

goal ─▶ clarify ─▶ approval ─▶ fanout ─┬─▶ analyst ─┐
                                       ├─▶ analyst ─┼─▶ join ─▶ verify ─▶ report
                                       └─▶ analyst ─┘
                                        (concurrent)     (council)
```

The specialists are named by the router from your question, not from a fixed
list. Both paths write to the same event log, so a chat turn is as replayable
and as measurable as a full orchestrated run.

Full detail: [docs/architecture.md](docs/architecture.md).

---

## Setup

Prerequisites: **Python 3.11+**, **Node 20+**, an [Ollama Cloud](https://ollama.com)
API key (or a local/tunnelled Ollama instance, no key needed), and a
[Turso](https://turso.tech) database. Groq/Gemini keys are optional - they
widen the council with a different model family each, but Ollama alone is a
complete, working configuration.

```bash
git clone https://github.com/Physics0070/CodeRush2.0_Latency_Zero.git
cd CodeRush2.0_Latency_Zero

# 1. Backend
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: semantic metrics (marginal information gain, duplicate detection).
# ~1GB. Skip it and the app runs fully, reporting embeddings_available: false.
pip install -r requirements-embeddings.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

# 2. Configuration
cp .env.example .env
# fill in TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, and either OLLAMA_API_KEY
# (Ollama Cloud) or nothing (local Ollama at the default localhost URL) -
# the chat endpoint 503s naming the missing variable if no provider is
# reachable. GROQ_API_KEY / GEMINI_API_KEY are additive, not required.

# 3. Database
turso db shell <db-name> < migrations/001_events.sql

# 4. Frontend
cd frontend && npm install && npm run build && cd ..

# 5. Run
uvicorn backend.main:app --host 0.0.0.0 --port 7860
```

Open <http://localhost:7860>.

> Blank values in `.env` fall back to defaults, so `cp .env.example .env` boots
> without editing anything except the Turso and model-provider keys.

### Troubleshooting

**`pip install` succeeded but the import still fails.** Almost always the wrong
interpreter. If a newer Python is first on `PATH`, a bare `pip`/`python` installs
there instead of into the venv. Check which one you are actually using:

```bash
.venv/Scripts/python -c "import sys; print(sys.executable, sys.version)"
```

Always install with `.venv/Scripts/python -m pip install …` rather than bare
`pip`, so the interpreter and the target are the same by construction.

**The torch install hangs.** `download.pytorch.org` is slow or blocked on some
networks — we measured zero bytes in 120 seconds, and separately a 50-minute
stall. Symptom: pip sits at "Collecting torch" with no progress bar movement.

This is why torch is **not** in `requirements.txt`. It is optional:

```bash
# add explicit timeouts so pip gives up and retries instead of hanging
pip install --retries 8 --timeout 60 -r requirements-embeddings.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu
```

Torch is needed **only** for the two semantic metrics. Everything else — the
chat, the event log, typed handoffs, the engine, the council, permissions,
replay, the UI, and every timing and cost number — runs without it. Skip it and
`/api/runs/{id}/metrics` still returns, with `embeddings_available: false` and
marginal information gain and duplicate pairs omitted rather than guessed.

Note the CPU index carries no cp311 linux wheel above 2.6.0, so
`requirements-embeddings.txt` uses a range rather than an exact pin — a pin
that resolves on Windows can be unsatisfiable in a linux container.

**`ModuleNotFoundError: No module named 'backend'`.** Run from the repository
root, not from inside `backend/`. `pytest` reads `pythonpath = ["."]` from
`pyproject.toml`.

**Ollama connection refused.** Only relevant if you are running local models
for development. Start it (`ollama serve`) and confirm the models are present
with `ollama list`. Not required otherwise - it is not on the default path.

**Tests skip with "turso not configured".** `.env` is missing
`TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`, or the migration has not been applied.

### Environment

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TURSO_DATABASE_URL` | yes | — | Event log, runs, artifacts |
| `TURSO_AUTH_TOKEN` | yes | — | Server-side only, never sent to the browser |
| `LLM_PROVIDER` | prod only | — | Which provider `_default_models()` treats as primary; only `ollama` is implemented |
| `OLLAMA_BASE_URL` | prod only | `http://localhost:11434` | `https://ollama.com` for Cloud, or a local/tunnelled URL |
| `OLLAMA_API_KEY` | Cloud only | — | Required for Ollama Cloud; must be absent for an unauthenticated local instance |
| `OLLAMA_MODELS` | no | `llama3.2:3b,qwen2.5:7b` | Comma-separated tags, no `ollama:` prefix - local and `:cloud` tags are interchangeable |
| `GROQ_API_KEY` | no | — | Additive - widens the council with a different model family when set |
| `OPENAI_API_KEY` | no | — | Additive - OpenAI models join the council and the router |
| `GROQ_MODELS` / `GEMINI_MODELS` / `OPENAI_MODELS` / `OPENROUTER_MODELS` | no | one sensible model each (OpenRouter: none) | Comma-separated tags per provider - switching models is configuration, not code |
| `ADMIN_TOKEN` | prod only | falls back to `SECRET_KEY` | Guards setting/clearing a provider key at runtime; refused in prod when neither is set |
| `KNOWLEDGE_MAX_DOCS` / `KNOWLEDGE_MAX_CHUNKS` | no | `50` / `5000` | Per-workspace limits for uploaded documents |
| `GEMINI_API_KEY` | no | — | Additive - widens the council with a different model family when set |
| `OPENROUTER_API_KEY` | no | — | Not part of the default mix; reachable only by explicitly passing an `openrouter:model` spec in a request |
| `MAX_CONCURRENT_MODEL_CALLS` | no | `4` | Caps simultaneous in-flight model calls across the council |
| `MAX_REPAIR_ATTEMPTS` | no | `2` | Repair retries before a branch fails |
| `DEFAULT_SEED` | no | `42` | Recorded per run; required for replay |
| `DEFAULT_TEMPERATURE` | no | `0.0` | Passed to every provider call |
| `DEFAULT_BUDGET_TOKENS` | no | `8000` | Engine-enforced, per agent |
| `DEFAULT_TIMEOUT_S` | no | `60` | Engine-enforced, per agent |
| `SECRET_KEY` | prod only | — | Prod refuses to boot without it |
| `CORS_ORIGINS` | no | `http://localhost:5173` | Explicit allow-list; `*` is rejected |
| `FRONTEND_ORIGIN` | prod only | — | Deployed frontend's exact origin; Netlify preview subdomains are matched separately by `.netlify.app` suffix |
| `MAX_UPLOAD_BYTES` | no | `5242880` | Cap on a single uploaded file, in bytes |
| `RATE_LIMIT_*` | no | see `.env.example` | No threshold is hardcoded |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `PORT` | no | `7860` | Render sets this itself in prod; local-dev fallback only |

Provider keys are read by **one module**, `backend/providers/secret_broker.py`.
Nowhere else. They never enter a log line, an event payload, or the frontend
bundle.

---

## Deployment

**Live on Vercel (free):** <https://unified-agentic-orchestrator.vercel.app>
One project serves both halves: the built frontend as static files and the
FastAPI app as a single Python function (`api/index.py`, routed by
`vercel.json`). Same origin, so no `VITE_API_BASE` and no CORS setup.

```bash
npm i -g vercel && vercel login
vercel deploy --prod          # from the repo root
```

Set secrets in the Vercel dashboard (Project -> Settings -> Environment
Variables), not in the repo: at least one model provider (`OLLAMA_BASE_URL` +
`OLLAMA_API_KEY`, or `GROQ_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY`), and
optionally `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` for the event log. An
`ADMIN_TOKEN` is already set on the deployed project; it guards the "set a key
from the UI" endpoints. Vercel's Deployment Protection is on by default for new
projects, so the URL asks for a Vercel login until you switch it off in
Settings -> Deployment Protection.

**What works on a serverless function, and what does not** - a function is not a
process that stays up between requests:

| Works on Vercel | Needs a persistent host |
|---|---|
| Chat: routing, council fanout, streamed answers (up to 300 s per request) | Orchestrator runs (`POST /api/runs`): execution continues in a background task after the response, which a function does not keep alive |
| Provider management, model router, execution trace of a chat turn | Pause / resume / cancel of an in-flight run |
| Knowledge workspace upload + hybrid retrieval | Knowledge documents surviving between instances or restarts (in memory, per instance) |

For the full orchestrator, run the backend on a persistent host: `render.yaml`
defines a free Render web service (sleeps after 15 min idle, ~1 min cold start)
and `frontend/netlify.toml` a static Netlify site pointed at it via
`VITE_API_BASE`. Firebase's free Spark plan cannot run this backend at all
(no Python runtime without billing), so it is not a target.

Notes learned deploying: `vercel.json` sets `"framework": null` - without it
Vercel auto-detects FastAPI (and reads `render.yaml` as a "services" config),
which conflicts with the static-plus-function layout. `api/requirements.txt`
holds the runtime-only dependencies so the function bundle stays ~60 MB (the
root `requirements.txt` also carries `datasets` and the dev tools).

---

## Running the demo

### Chat (the default surface)

1. Ask *"What is a race condition?"* → answers in ~2s, routed `simple`,
   no council convened
2. Ask *"Compare optimistic and pessimistic locking for a high-write ledger"* →
   routed `moderate`, two specialists named from the question run in parallel,
   answers merge into one reply
3. Click **benchmarks** under either answer → route, timings, tokens, cost, and
   the measured overlap between specialists

The second question is the one to show: the system reports when its own
specialists said the same thing, which is a claim nobody else's demo makes
about itself.

### Orchestrator (the `orchestrator` tab)

1. Type: *"Audit this repository and produce a prioritized remediation report."*
2. Answer the clarifying questions → approve
3. Council compiles a graph — parallel branches and a verify node appear
4. Inspect a node → lock → **Approve & run**
5. Watch typed handoffs validate live in the trace
6. Click **Red Agent** → `TOOL_BLOCKED` and `INJECTION_BLOCKED` in red
7. Click **Replay** → diff is zero, cost compared side by side
8. Click **Marginal value** → the system recommends a graph size

---

## Verification

```bash
pytest -v                    # full suite
pytest -m "not slow"         # skips the live-model tests
python -m backend.providers.smoke_test    # real completions + fallback
python -m backend.benchmarks.show         # real SWE-bench + BFCL rows
ruff check .
pip-audit -r requirements.txt --strict
```

---

## Data

| Source | Licence | Access |
|---|---|---|
| [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) | CC BY 4.0 | datasets-server API, real rows |
| [BFCL](https://github.com/ShishirPatil/gorilla) | Apache 2.0 | JSONL — `load_dataset()` does **not** work |

No sample record, finding, or metric in this repository is fabricated. Where a
code path needs data we do not have, it raises rather than inventing.

---

## Documentation

- [docs/architecture.md](docs/architecture.md) — event sourcing, node types, data model
- [docs/threat-model.md](docs/threat-model.md) — capability enforcement, injection screening, secret broker
- [docs/reproducibility.md](docs/reproducibility.md) — config_hash, seed, zero-diff worked example
- [examples/](examples/) — saved runs: clean, recovered, adversarial

## Licence

[Apache 2.0](LICENSE).
