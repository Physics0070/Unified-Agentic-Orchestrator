"""Single model vs the full orchestrator, on identical short questions, against a live deployment.

Run: python -m backend.benchmarks.single_vs_pipeline --base https://<deployment> --out results.json

Arm A sends the question to /api/chat with one forced model. The questions are phrased with
no breadth marker and at most 8 words, so the planner short-circuits and makes ONE direct
call (route "fast"; the script flags any run that got a different route). The app records no
token usage for streamed direct answers, so arm A's tokens and cost are ESTIMATES (characters
divided by 4, priced at the published Groq rate); arm B's are measured from the event log.
Arm B sends the same text through clarify-free compile -> run -> execute (council design,
parallel agents, typed hand-offs, verifier). Tokens and cost for arm B include the council's
design call.

What this does NOT measure: answer quality as judged by a person or a stronger model. It
measures cost, latency, failures and two cheap objective checks (does the answer mention every
option asked about; does it make an explicit pick). Read the results with that limit in mind.
"""

import argparse
import json
import re
import statistics
import time

import httpx

from backend.providers.groq import RATES

MODEL = "groq:openai/gpt-oss-120b"
# The pipeline arm designs its team with a three-model council, set explicitly so the result
# does not depend on whatever defaults a deployment happens to run.
COUNCIL = [MODEL, "groq:openai/gpt-oss-20b", "gemini:gemini-3.6-flash"]

# (question, options the answer must mention)
TASKS = [
    ("Pick REST or GraphQL for mobile apps", ["REST", "GraphQL"]),
    ("Pick Postgres, MongoDB or DynamoDB for chat", ["Postgres", "MongoDB", "DynamoDB"]),
    ("Choose microservices or monolith for startups", ["microservice", "monolith"]),
    ("Pick Kafka, RabbitMQ or SQS for events", ["Kafka", "RabbitMQ", "SQS"]),
    ("Pick solar, wind or hydro for India", ["solar", "wind", "hydro"]),
    ("Pick React, Vue or Svelte for dashboards", ["React", "Vue", "Svelte"]),
    ("Pick Redis, Memcached or DynamoDB for caching", ["Redis", "Memcached", "DynamoDB"]),
    ("Choose Kubernetes or serverless for small teams", ["Kubernetes", "serverless"]),
]
PICK = re.compile(r"recommend|choose|pick|go with|best (choice|option|fit)|winner|verdict", re.I)


def covered(text: str, options: list[str]) -> bool:
    return all(o.lower() in text.lower() for o in options)


def single(client: httpx.Client, question: str) -> dict:
    t0, ev, answer, bench = time.monotonic(), None, "", {}
    body = {"message": question, "models": [MODEL], "forced_model": MODEL}
    with client.stream("POST", "/api/chat", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev in ("benchmarks", "done"):
                data = json.loads(line[5:])
                if ev == "done":
                    answer = data.get("answer", "")
                else:
                    bench = data
    tin, tout = len(question) // 4, len(answer) // 4
    rate_in, rate_out = RATES[MODEL.split(":", 1)[1]]
    return {
        "ok": bool(answer) and not bench.get("error"),
        "route": bench.get("route"),
        "seconds": round(time.monotonic() - t0, 1),
        "tokens": tin + tout,
        "cost_usd": (tin * rate_in + tout * rate_out) / 1_000_000,
        "usage": "estimated",
        "answer": answer,
    }


def run_cost(client: httpx.Client, run_id: str) -> tuple[int, float]:
    body = client.get(f"/api/runs/{run_id}/events").json()
    events = body["events"] if isinstance(body, dict) else body
    tokens = sum((e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in events)
    return tokens, sum(e.get("cost_usd") or 0.0 for e in events)


def pipeline(client: httpx.Client, question: str) -> dict:
    t0 = time.monotonic()
    out = {"ok": False, "route": "orchestrator", "seconds": 0.0, "tokens": 0, "cost_usd": 0.0,
           "usage": "measured", "answer": "", "council_proposals": 0, "handoffs_valid": None}
    run_ids: list[str] = []
    phase = "compile"
    try:
        comp = client.post("/api/compile", json={"goal": question, "models": COUNCIL}).json()
        run_ids.append(comp["run_id"])
        graph = comp["graph"]
        out["council_proposals"] = len((comp.get("council") or {}).get("proposals", []))
        approvals = [n["id"] for n in graph["nodes"] if n["type"] == "approval"]
        phase = "create"
        created = client.post("/api/runs", json={"goal": question, "graph": graph}).json()
        rid = created["run_id"]
        run_ids.append(rid)
        phase = "execute"
        status = client.post(f"/api/runs/{rid}/execute", json={"approvals": approvals}).json()
        result = client.get(f"/api/runs/{rid}/output").json()
        out["answer"] = json.dumps(result.get("outputs", []))
        out["ok"] = status.get("status") == "done" and bool(result.get("outputs"))
        out["status"] = status.get("status")
        metrics = client.get(f"/api/runs/{rid}/metrics").json()
        out["handoffs_valid"] = metrics.get("handoff_validity")
    except (httpx.HTTPError, KeyError, ValueError) as e:
        out["error"] = f"{phase}: {type(e).__name__}: {e}"[:200]
    for run_id in run_ids:  # failed runs still spent tokens: count them
        try:
            tokens, cost = run_cost(client, run_id)
            out["tokens"] += tokens
            out["cost_usd"] += cost
        except (httpx.HTTPError, KeyError, ValueError):
            pass
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def summarise(rows: list[dict], options: list[list[str]]) -> dict:
    ok = [r for r in rows if r["ok"]]
    return {
        "runs": len(rows),
        "completed": len(ok),
        "median_seconds": statistics.median(r["seconds"] for r in ok) if ok else None,
        "mean_tokens": round(statistics.mean(r["tokens"] for r in ok)) if ok else None,
        "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in ok), 6) if ok else None,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "cost_per_completed_usd": (round(sum(r["cost_usd"] for r in rows) / len(ok), 6)
                                   if ok else None),
        "mentions_every_option": sum(covered(r["answer"], o)
                                     for r, o in zip(rows, options, strict=True)),
        "makes_explicit_pick": sum(bool(PICK.search(r["answer"])) for r in rows),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default="single_vs_pipeline_results.json")
    args = ap.parse_args()
    # No connection reuse: a serverless host drops idle keep-alive connections after a long
    # request, and a pooled client then fails its next call with RemoteProtocolError.
    client = httpx.Client(base_url=args.base.rstrip("/"), timeout=300,
                          limits=httpx.Limits(max_keepalive_connections=0))
    options = [o for _, o in TASKS]
    rows: dict[str, list[dict]] = {"single": [], "pipeline": []}
    for question, _ in TASKS:
        for arm, fn in (("single", single), ("pipeline", pipeline)):
            try:
                row = fn(client, question)
            except httpx.HTTPError as e:
                row = {"ok": False, "seconds": 0.0, "tokens": 0, "cost_usd": 0.0, "answer": "",
                       "error": f"{type(e).__name__}: {e}"[:200]}
            row["question"] = question
            rows[arm].append(row)
            print(f"{arm:8} {question[:44]:44} ok={row['ok']} {row['seconds']}s "
                  f"{row['tokens']} tok ${row['cost_usd']:.5f}", flush=True)
    result = {
        "base": args.base,
        "model": MODEL,
        "single_not_fast_route": [r["question"] for r in rows["single"]
                                  if r.get("route") != "fast"],
        "summary": {arm: summarise(rows[arm], options) for arm in rows},
        "rows": rows,
    }
    with open(args.out, "w", encoding="utf8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result["summary"], indent=2))
    print("single arm not on the fast route:", result["single_not_fast_route"])


if __name__ == "__main__":
    main()
