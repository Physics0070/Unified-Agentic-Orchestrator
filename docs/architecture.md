# Architecture

## Why event sourcing

One append-only table in Turso (libSQL, a SQLite dialect) is the single source of truth, with a
local SQLite file as the no-account fallback. This is the decision the rest of the system falls
out of.

Six requirements in the problem statement are, structurally, the same
requirement — they are all *reads* of one ordered log:

| Requirement | Implementation |
|---|---|
| Observability dashboard (62) | `SELECT * FROM events WHERE run_id = ?` |
| Replay a completed run (67) | Re-execute, serving completions from the log |
| All metrics (69) | Aggregate over event types |
| Trace viewer, pause/resume/cancel (74) | Tail the log via SSE; controls append events. Pause, resume and cancel need a persistent process, so they work under local `uvicorn` but **not on the Vercel deployment** (a function is frozen once it responds) |
| Reproducibility report (77) | Compare two runs' logs |
| Marginal value of each agent (70) | Embed the validated outputs recorded in the log. Needs the optional embeddings package (about 1 GB), which is **not installed on Vercel**, so the live app reports these metrics as unavailable |

Building one structure well beat building six features shallowly.

**The rule:** no state change happens anywhere without an event row. If a value
exists only in a Python variable, it does not exist.

## Sequence allocation is atomic, in SQL

The obvious implementation of "next seq for this run" is
`SELECT MAX(seq)+1 FROM events WHERE run_id = ?`. It is wrong under concurrency:
two branches of a fanout read the same maximum and one loses on the primary key.

Instead `runs` carries a `last_seq` column. Turso has no stored functions, so an append is one
transaction of two statements: `UPDATE runs SET last_seq = last_seq + 1 WHERE run_id = ?
RETURNING last_seq`, then a dependent `INSERT` that reads the value the `UPDATE` just wrote.

The `UPDATE` takes the write lock, so concurrent appends for one run queue behind it and each
gets a distinct, gapless number (the sequence check in the live end-to-end runs confirms no
gaps). `tests/test_events.py` fires five appends concurrently and asserts `[1,2,3,4,5]`; that
test fails against the naive implementation.

Append-only is enforced by triggers that `RAISE(ABORT)` on `UPDATE` and `DELETE` of `events`,
not by convention. Turso has no row-level security: the database token is held server-side
only, which is the whole access boundary (see `migrations/001_events.sql`).

## Schema

```
runs        run_id, goal, config_hash, seed, graph_spec, status,
            replay_of, last_seq, started_at, ended_at

events      run_id, seq, node_id, agent_id, event_type, payload,
            tokens_in, tokens_out, cost_usd, latency_ms, ts
            PRIMARY KEY (run_id, seq)

artifacts   artifact_id, run_id, seq, written_by, node_id,
            content, storage_path, ts
```

Provenance lives on every artifact: `written_by`, `run_id`, `seq`, `ts`. That
chain is what lets the trace viewer answer "where did this claim come from."

## Node types

| Type | Purpose |
|---|---|
| `clarify` | Ask up to 3 questions, then ask permission |
| `approval` | Human gate. Nothing downstream runs until granted |
| `fanout` | Split into independent branches |
| `agent` | One specialist, one model, one output schema |
| `join` | Fan-in, including partial failure |
| `conditional` | Branch on a prior node's output |
| `verify` | Council reviews merged output; escalates on disagreement |
| `council` | Propose → anonymous peer-rank → chairman merge |
| `compensate` | Runs when the node it compensates for fails |
| `subgraph` | One level of nesting (deliberately not recursive) |

## The execution contract

The problem statement's closing line says it grades *"the quality of the
execution contract between agents."* So this is the most important code in the
repository, and it was built before the compiler and before the UI.

On every handoff:

1. Producer emits output
2. Validate against `edge.handoff_schema` with `jsonschema`
3. On failure the **exact validator text** goes back to the producer, up to
   `MAX_REPAIR_ATTEMPTS`
4. Still failing → `BRANCH_FAILED` → the compensate node runs
5. Validated output is stored with provenance

Two design points worth defending:

- **All errors are collected, not just the first.** Fixing one field per round
  trip would exhaust the repair budget on a payload with three missing keys.
- **A markdown fence is stripped before judging.** Models wrap JSON in
  ```` ```json ````. That is a formatting artefact, not a contract violation,
  and rejecting it would burn repairs on nothing.

## Parallel-first execution

The DAG is layered by real data dependency (Kahn). Everything in a layer runs in
one `asyncio.gather`. A node drops to the next layer only when it genuinely
consumes an upstream output. Parallelism is the default; sequencing is the
exception.

**Parallel Efficiency** = Σ(node latencies) / wall clock.

An honest caveat we state rather than hide: every event append is a network
round trip charged to wall clock but *not* to node latency, so PE understates
parallelism for very fast nodes. At 0.6 s per branch it measured 1.89 despite
the branches provably overlapping by timestamp. At realistic agent latencies
(6–13 s) the overhead should be noise. On the live site we measured 0.77 to 0.83 on runs
whose compiled graph was mostly a sequential chain (where about 1 is expected). A fully
parallel graph has not been measured live; phrasing a goal as independent parts made the planner
produce three parallel branches in about half of our compiles, but a model plans the graph, so
the shape varies run to run.

Budgets are enforced in code. `budget_tokens` is counted per node and the call
is cut off; `timeout_s` is an `asyncio.timeout` around the node. The model is
never *asked* to respect a ceiling.

Node-level retry sits **outside** the repair loop deliberately: repair fixes
payload shape, retry covers a transient provider failure. Different faults,
different budgets.

## Conditions are parsed, never `eval`'d

A graph is model-authored. `eval()` on model-authored text is remote code
execution. Conditions are a fixed grammar: `<node_id>.<key> <op> <value>`.

## The council

`propose` → `peer_rank` (anonymous) → `chairman_merge`.

Anonymity is load-bearing. Labels are shuffled with the run seed, so the
assignment is reproducible for replay but does not track member order.
Authorship is re-attached only in the verdict.

Ranking is Borda, so second place still scores. A member that returns unusable
JSON, times out, is rate-limited (HTTP 429) or returns an empty reply drops out
rather than corrupting the tally, so the council deliberates with fewer members.
It needs at least two to rank; with one it is effectively a single call (measured:
this happened on every live compile before the default models were fixed). Disagreement is the share of
rankers who disagreed on first place; above the threshold the verdict is flagged
`escalated` rather than silently passed.

Chairman is a **policy field**, not a constant: cheapest for a low-stakes merge,
strongest for verification.

The council appears in exactly three places: the graph compiler, the verify
node, and the conflict arbiter.

## The compiler treats the council as untrusted

A model-authored graph can name a node twice, point an edge at nothing, or omit
the verification node. The compiler de-duplicates branch ids, drops
non-identifiers, caps at four branches, and **guarantees** the fanout, the verify
node and per-branch compensators regardless of what the models returned. The
council supplies the shape; the compiler guarantees the invariants.

## Scope decisions

Deliberately minimal, and we say so:

- **Memory conflict resolution** is last-write-wins *with full provenance
  history*, so conflicts are visible and auditable. Not a CRDT.
- **Artifact store** is a table in the same database, not a content-addressed blob system and
  not object storage.
- **Memory** is process-local with TTL-based staleness eviction, so it does not survive across
  serverless instances.

Deliberately deferred:

- Nested subgraph recursion beyond one level

Implemented beyond the core contract: adversarial blocking (undeclared tools and injected
instructions), conflict arbitration, workflow version migration (`backend/engine/migrations.py`)
and stale-memory eviction. Each has a live demo endpoint (`/api/demo/red-agent`,
`/api/demo/version-migration`, `/api/demo/stale-memory`).
