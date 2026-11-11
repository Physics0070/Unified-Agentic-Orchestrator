# Benchmark: one model vs the full pipeline

**Bottom line:** on these eight questions the pipeline did **not** produce better answers than a
single model call by the two objective checks used here. It cost about 2.4x more per completed
answer and took about 10x longer. What it adds is not accuracy: it is typed hand-off validation, an
auditable event log, blocked tools and a replay that costs $0.

Run on 2026-11-11 against the production deployment (Vercel, region `bom1`). Raw results:
[`benchmark-results.json`](benchmark-results.json). Reproduce:

```bash
python -m backend.benchmarks.single_vs_pipeline --base https://<deployment> --out results.json
```

## Method

Eight short questions of the form "Pick X, Y or Z for W" (7 words or fewer, no breadth marker).
Each question goes to both arms with identical text, once each, sequentially.

- **A, single call:** `/api/chat` with one forced model (`groq:openai/gpt-oss-120b`). The questions
  are short enough that the planner short-circuits, so this is one direct model call (route
  `fast`; the script checks every run got that route, and all 8 did).
- **B, full pipeline:** `/api/compile` (a council of `gpt-oss-120b`, `gpt-oss-20b` and
  `gemini-3.6-flash` designs the agent team) -> `/api/runs` -> `/execute` (agents run with typed,
  schema-validated hand-offs, then a verifier). Tokens and cost include the council's design call.

## Results (n = 8 questions, one run each)

| | A: single call | B: full pipeline |
|---|---|---|
| Completed | 7 of 8 | 6 of 8 |
| Median latency | 7.2 s | 75.7 s |
| Mean tokens per completed answer | 1,317 (estimated) | 6,406 (measured) |
| Mean cost per completed answer | $0.00079 (estimated) | $0.00161 (measured) |
| All spend / completed answers | $0.00079 | $0.00190 |
| Answer mentions every option asked about | 6 of 8 | 4 of 8 |
| Answer makes an explicit pick | 7 of 8 | 6 of 8 |
| Hand-offs passing their typed contract | not applicable | 100% on all 6 completed runs (in an earlier pass with a flawed client, completed runs showed 0.67 to 0.83 because repairs were needed) |

Ratios (B over A): about 4.9x tokens, 2.4x cost per completed answer, 10.5x median latency.

## What this does and does not show

- It shows cost, latency, failure counts, and two cheap objective checks (does the answer name every
  option; does it pick one). It does **not** measure answer quality as judged by a person or a
  stronger model. "Not better on these checks" is not "not better".
- Arm A's tokens and cost are **estimates** (characters / 4, priced at Groq's published rate)
  because the app records no usage for streamed direct answers. Arm B's are measured from the event
  log. So the cost ratio carries that uncertainty.
- n = 8, one run each, no repeats: differences of one or two questions are within noise.
- Short questions are the single call's best case; the pipeline is built for larger goals
  (audits, multi-part analysis) that this does not test.

## Failures, and why

- **Pipeline, "Redis, Memcached or DynamoDB":** the execute call returned a non-JSON response
  (a function-level error); the run did not finish.
- **"Kubernetes or serverless":** failed on **both** arms in the same minute, so a provider-side
  problem at that moment, not a property of the pipeline.
- **Single arm:** one 240 s outlier (Postgres question) and the Kubernetes failure above.
- **The council often ran on 2 of its 3 members.** Production logs during the run show the Gemini
  member returning HTTP 429 (rate limit) on repeated calls, and `gpt-oss-120b` hitting the 45 s
  member timeout in about half of the council steps sampled. Both are provider-side and
  load-dependent. The council still deliberated (2 proposals and an anonymous ranking in 7 of 8
  compiles), which it did not before the default models were fixed.

## Earlier numbers in the repo

Counts on the PROOF slide and in `examples/` come from a scripted fixture run, not from live
models; see the note in the README under "Data and honesty".

## Next

A quality study with a human or stronger-model judge on 50+ longer prompts, repeated runs per
question, and an estimate-free usage count for the single-call arm.
