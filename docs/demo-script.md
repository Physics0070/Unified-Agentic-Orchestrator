# Demo script (3 minutes)

This matches the recorded demo video and the live deployment, and every number below was
measured on `https://unified-agentic-orchestrator.vercel.app` on 2026-11-11. Free-tier model
quotas are small (Groq allows about 200K tokens a day on `gpt-oss-120b` and 8K a minute), so do
**not** rehearse by running it repeatedly: one pre-flight run is enough.

## Pre-flight (10 minutes before, once)

1. `GET /health` returns `{"status":"ok"}`.
2. `GET /api/models` lists the council: `groq:openai/gpt-oss-120b`, `groq:openai/gpt-oss-20b`
   and `nvidia:nvidia/nemotron-3-ultra-550b-a55b`. No key-shaped text may appear.
3. Ask one throwaway chat question so the serverless function is warm.
4. Make one orchestrator run with the demo goal below. If it fails, check the providers' quotas
   and fall back to the recorded video.

**Demo goal:** *Evaluate Postgres, MongoDB and DynamoDB independently for a chat app, then
recommend one.* Phrasing a goal as independent parts makes parallel branches possible, but a model plans the
graph: this goal gave three parallel branches in 3 of 6 compiles and a sequential chain in the
rest, including the last three live runs. Do not promise parallelism; show whichever graph
appears and quote the measured parallel efficiency.

## The three minutes

| Time | Show | Say (and what is true) |
|---|---|---|
| 0:00 | Landing page | One model gives one unchecked answer with no receipt. This is a team that shows its work. |
| 0:20 | Chat: ask a short question, open **benchmarks** | A router sends short questions to one fast model. The panel shows route, model, timing. Cost for this quick route is **not recorded** (it shows 0), so do not quote it. |
| 1:00 | Orchestrator: type the goal, **Start** | It asks clarifying questions, then asks permission before anything runs. |
| 1:20 | **Compile agent graph** | A council of models designs the team. Show the graph, the approval gate, and the predicted-fitness score (a cold start with no history). |
| 1:45 | **Approve & run**, watch the trace | Hand-offs are typed contracts; a wrong shape sends the exact error back. If a model fails or returns nothing, it falls back to another provider (after 60 s at most). |
| 2:10 | **metrics** tab | Measured time, tokens, cost, parallel efficiency, hand-off validity. |
| 2:25 | **Replay** | Served from the log: diff is zero, cost $0, no model called. |
| 2:40 | **Red Agent** | An undeclared tool call is blocked and an injected instruction is quarantined. |
| 2:50 | Close | Governance and replay are the product, not answer accuracy. |

## What a normal run looks like (measured)

| Measure | Value |
|---|---|
| Time to finish a run | about 26 to 46 s |
| Tokens | about 2,000 to 3,000 in, 1,000 to 1,600 out |
| Cost | about $0.0007 to $0.0013 per run (the benchmark averaged $0.0019 per completed answer because failed runs spent tokens too) |
| Events logged | about 36 to 43, sequence numbers with no gaps |
| Hand-offs passing their typed contract | 100% on most runs; some completed runs needed a repair (validity 0.67 to 0.83) |
| Replay | identical output, $0, no model call |
| Council | 2 or 3 proposals and anonymous rankings; disagreement 0 to 0.5 |
| Parallel efficiency | 0.77 to 0.83 on mostly sequential graphs |

## Questions you will get, with honest answers

- **Why not just ask one model?** We benchmarked it (`docs/benchmark.md`): on 8 questions the
  pipeline was not more accurate on our objective checks, at about 2.4x the cost per completed
  answer and 10x the latency. Its value is validated hand-offs, an audit log, blocked tools and
  zero-cost replay. Do not claim better answers.
- **Is the council real?** Yes, with at least two working members it proposes, ranks anonymously
  and merges. Under rate limits or slow replies a member drops out, so it often runs with 2 of
  3. Before the default models were fixed it was effectively a single call.
- **Are the example runs real?** No. `examples/` were generated with scripted stand-in answers
  (see the README). The live app and the numbers above are real.
- **Does cost show on every answer?** Orchestrator runs: yes, measured. Quick direct answers:
  no, they record 0 tokens.
- **Pause, resume, cancel?** Implemented, but they need a persistent process (local `uvicorn`).
  They are not available on the Vercel deployment.
- **Marginal-information and overlap metrics?** They need an optional 1 GB embeddings package
  that is not installed on Vercel, so the live app reports them as unavailable.
- **Prompt injection?** Pattern screening plus a hard capability guard are layers, not a proof.
  We never claim it is injection-proof (`docs/threat-model.md`).
- **Which providers?** Groq and NVIDIA NIM by default. OpenRouter, Mistral, Gemini, OpenAI and
  Ollama are supported and opt-in. Free tiers are inconsistent; see the README's known limits.

## If something breaks

- **A run fails or stalls:** a provider is probably out of quota. Say so, show the failed node
  and its compensation (the graceful-degradation behaviour is real), then play the recorded video.
- **`/api/models` shows a council of one model:** the other providers are not configured or
  their keys are wrong.
- **Never** open `/api/models` or `/api/providers` on screen without checking them first.
