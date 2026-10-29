"""One chat turn, start to finish.

Emits a stream of events the UI renders as it goes: the routing decision, each
specialist as it lands, the answer token by token, then the measured benchmarks.

The benchmarks are computed from what this turn actually did - wall clock,
token counts and embedding distances between the specialists' contributions.
Nothing here is estimated, and a turn that took the fast path reports that it
took the fast path rather than inventing council numbers.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from backend import budget
from backend.chat.answer import (
    CONTRIBUTION_SCHEMA,
    contribute,
    stream_direct,
    stream_synthesis,
)
from backend.chat.files import generate_file, store_file, wants_file
from backend.chat.planner import Plan, pick_planner_model, plan_for
from backend.engine.spec import AgentSpec, Edge, GraphSpec, Node
from backend.events import EventStore, EventType
from backend.providers.catalog import rank_models
from backend.providers.router import route_model
from backend.providers.websearch import needs_fresh_data, render_results, web_search
from backend.rag import knowledge_base, render_context
from backend.trace import new_step

log = logging.getLogger("aco.chat")

# A specialist that has not landed by now is a failed branch, not a reason to
# hold the whole turn. The Ollama client's own timeout is 300s, so without this
# one slow local model stalls the answer for five minutes and the user watches
# an empty screen - which is indistinguishable from the app being broken.
SPECIALIST_TIMEOUT_S = 45.0

# Upper bound on the single end-of-turn event-log write. It happens after the
# answer and `done` have been sent, but the stream is still open while it runs,
# so a hung database must not hold the connection (it was seen open for 126s).
FLUSH_TIMEOUT_S = 8.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _flush_turn(
    store: EventStore | None,
    open_store: Callable[[], EventStore] | None,
    run: dict,
    events: list[dict],
) -> int:
    """Persist the whole buffered turn in ONE database round trip, then settle
    the daily-usage counter off the event loop. Never raises: a failure here is
    logged and the already-delivered answer stands. Returns the number of
    event-log round trips issued (0 or 1)."""

    async def _write() -> int:
        if store is not None:
            await store.record_run(**run, events=events)
            return 1
        if open_store is not None:
            async with open_store() as s:
                await s.record_run(**run, events=events)
            return 1
        return 0

    async def _usage() -> None:
        await asyncio.to_thread(budget.flush)

    calls = 0
    done = await asyncio.gather(
        asyncio.wait_for(_write(), FLUSH_TIMEOUT_S),
        asyncio.wait_for(_usage(), FLUSH_TIMEOUT_S),
        return_exceptions=True,
    )
    if isinstance(done[0], BaseException):
        log.warning("could not record chat run %s in the event log: %r", run["run_id"], done[0])
    else:
        calls = done[0]
    if isinstance(done[1], BaseException):
        log.warning("could not flush usage counter: %r", done[1])
    return calls


def specialist_models(models: list[str]) -> list[str]:
    """The models the fanout is allowed to use.

    Round-robining across the whole list hands specialists local Ollama models,
    which generate at well under one token per second in a memory-capped
    container - far slower than any timeout worth waiting for. When a hosted
    model is configured the council runs on hosted models only. With no key at
    all the local list is still the zero-cost fallback, as before.
    """
    hosted = [m for m in models if not m.startswith("ollama:")]
    return hosted or models


async def _bounded(coro, aspect_id: str) -> dict:
    """Run one specialist under the timeout, degrading instead of raising."""
    try:
        return await asyncio.wait_for(coro, SPECIALIST_TIMEOUT_S)
    except TimeoutError:
        log.warning("specialist %s timed out after %ss", aspect_id, SPECIALIST_TIMEOUT_S)
        return {"aspect": aspect_id, "points": [],
                "error": f"timed out after {int(SPECIALIST_TIMEOUT_S)}s"}


def chat_graph(plan: Plan, models: list[str]) -> GraphSpec:
    """The shape this turn will actually run, as a real GraphSpec.

    Not decoration. Replay recomputes the config hash from the stored spec and
    refuses a run whose spec was modified, so a chat turn needs a genuine graph
    to be replayable at all. It also means a chat turn renders in the same
    canvas as an orchestrated run, because it is the same kind of object.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []

    if plan.needs_council:
        # Ranked by fit for this question's intent (see backend/providers/
        # catalog.py), cycled across specialists so the council still gets
        # more than one model family - that diversity is the point of a
        # council over asking one model twice, just no longer blind to which
        # model is actually suited to the task.
        ranked = rank_models(models, plan.intent)
        nodes.append(Node(id="fanout", type="fanout"))
        for i, s in enumerate(plan.specialists):
            nodes.append(Node(id=s.id, type="agent", agent=AgentSpec(
                id=s.id, role=s.role,
                system_contract=f"Examine one aspect of the question: {s.role}",
                tools=[], model=ranked[i % len(ranked)][0],
                output_schema=CONTRIBUTION_SCHEMA,
                budget_tokens=900, timeout_s=180,
            )))
            edges.append(Edge(from_node="fanout", to_node=s.id,
                              handoff_schema=CONTRIBUTION_SCHEMA))
        nodes.append(Node(id="join", type="join"))
        for s in plan.specialists:
            edges.append(Edge(from_node=s.id, to_node="join"))

    # A passthrough, not an agent node. The answer is prose, and agent nodes
    # are schema-validated JSON handoffs - modelling it as one makes replay try
    # to parse an English paragraph as a typed contract and fail every time.
    # The answer text is still recorded as a TOOL_RESULT row.
    nodes.append(Node(id="answer", type="join"))
    if plan.needs_council:
        edges.append(Edge(from_node="join", to_node="answer"))

    return GraphSpec(version=1, nodes=nodes, edges=edges).finalize()


def _trace(plan: Plan, plan_ms: int, contributions: list[dict], ranked: list,
           answer_model: str, total_ms: int, fanout_ms: int, answer: str,
           answer_error: str | None = None) -> list[dict]:
    """The turn as execution-trace steps, built from what actually happened so
    the trace works even when the event log is unavailable."""
    steps = [new_step(
        "plan", type="plan", status="done", duration_ms=plan_ms,
        output=f"{plan.intent} / {plan.complexity}: {plan.rationale}"[:600])]
    specs = {s.id: s for s in plan.specialists}
    for i, c in enumerate(contributions):
        failed = bool(c.get("error"))
        steps.append(new_step(
            c["aspect"], type="agent", role=getattr(specs.get(c["aspect"]), "role", None),
            model=c.get("model") or ranked[i % len(ranked)][0],
            status="failed" if failed else "done", error=c.get("error"),
            duration_ms=c.get("latency_ms"), depends_on=["plan"],
            input=c.get("input"),
            output=None if failed else json.dumps(c.get("points") or [], indent=1)[:1600],
            tokens=c.get("tokens") or 0, tokens_in=c.get("tokens_in", 0),
            tokens_out=c.get("tokens_out", 0), cost_usd=c.get("cost_usd", 0.0),
            repairs=1 if c.get("repaired") else 0,
            used_fallback=bool(c.get("used_fallback"))))
    steps.append(new_step(
        "answer", type="answer", status="failed" if answer_error else "done",
        error=answer_error, model=answer_model,
        depends_on=[c["aspect"] for c in contributions] or ["plan"],
        duration_ms=max(total_ms - plan_ms - fanout_ms, 0), output=answer[:1600]))
    return steps


def _mig(contributions: list[dict]) -> dict:
    """How much each specialist added that the others did not.

    1 - max cosine to any other specialist. A branch that restates its
    neighbour scores near zero, which is the honest signal that the council was
    wider than the question needed.
    """
    from backend.metrics.metrics import DUPLICATE_THRESHOLD, cosine_matrix, embeddings_available

    texts, ids = [], []
    for c in contributions:
        body = " ".join(
            f"{p.get('claim', '')} {p.get('support', '')}"
            for p in c.get("points") or []
            if isinstance(p, dict)
        ).strip()
        if body:
            texts.append(body)
            ids.append(c["aspect"])

    if len(texts) < 2 or not embeddings_available():
        return {"available": False, "per_agent": {}, "overlapping_pairs": []}

    try:
        sim = cosine_matrix(texts)
    except Exception as e:
        log.warning("embedding failed: %s", e)
        return {"available": False, "per_agent": {}, "overlapping_pairs": []}

    per_agent, pairs = {}, []
    for i, aid in enumerate(ids):
        others = [float(sim[i][j]) for j in range(len(ids)) if j != i]
        per_agent[aid] = round(1.0 - max(others), 4)
        for j in range(i + 1, len(ids)):
            if float(sim[i][j]) >= DUPLICATE_THRESHOLD:
                pairs.append({"a": aid, "b": ids[j], "similarity": round(float(sim[i][j]), 4)})
    return {"available": True, "per_agent": per_agent, "overlapping_pairs": pairs}


def _specialist_rows(contributions: list[dict], ts: str) -> list[dict]:
    """Every specialist's event-log rows, in the order they are stored."""
    batch: list[dict] = []
    for c in contributions:
        if c.get("error"):
            batch.append({
                "event_type": EventType.BRANCH_FAILED, "ts": ts,
                "node_id": c["aspect"], "agent_id": c["aspect"],
                "payload": {k: v for k, v in c.items() if k != "text"},
            })
            continue
        # TOOL_RESULT is what replay serves from, keyed by (node_id, agent_id).
        # Without this row a chat turn records that it happened but cannot be
        # replayed, which would make the drawer's replay claim false for
        # exactly the turns most people will look at.
        batch.append({
            "event_type": EventType.TOOL_RESULT, "ts": ts,
            "node_id": c["aspect"], "agent_id": c["aspect"],
            "tokens_in": c.get("tokens_in", 0), "tokens_out": c.get("tokens_out", 0),
            "cost_usd": c.get("cost_usd", 0.0), "latency_ms": c.get("latency_ms"),
            "payload": {"model": c.get("model", ""),
                        "used_fallback": c.get("used_fallback", False),
                        "replayed": False, "text": c.get("text", ""),
                        "input": c.get("input")},
        })
        # The validated handoff is what a replay diff compares. Without it the
        # original run has no recorded outputs and every node reads as a
        # difference.
        batch.append({
            "event_type": EventType.HANDOFF_VALIDATED, "ts": ts,
            "node_id": c["aspect"], "agent_id": c["aspect"],
            "payload": {"attempt": 1 if c.get("repaired") else 0,
                        "payload": {"points": c.get("points") or []}},
        })
        batch.append({
            "event_type": EventType.NODE_END, "ts": ts,
            "node_id": c["aspect"], "agent_id": c["aspect"],
            "payload": {k: v for k, v in c.items() if k != "text"},
        })
    return batch


async def run_turn(
    question: str,
    *,
    history: list[dict] | None = None,
    models: list[str],
    seed: int = 42,
    store: EventStore | None = None,
    force_web_search: bool = False,
    forced_model: str | None = None,
    priority: str = "balanced",
    workspace_id: str | None = None,
    use_knowledge: bool = True,
    open_store: Callable[[], EventStore] | None = None,
) -> AsyncIterator[dict]:
    """Yield `{"event": ..., "data": ...}` for the whole turn.

    The event log is optional: the chat works without a database, it just loses
    the replayable trace. Pass an open `store`, or `open_store` (a factory for an
    async-context-manager store, opened only at flush time so connecting costs
    nothing before the first token).

    Nothing touches the database on the way to the first token or while the
    answer streams. Every event-log row is buffered in memory in the exact order
    it used to be written, and the whole turn (run row + all events + final
    status) goes out as ONE batched write after the last token and the `done`
    event have been yielded. That write is bounded by FLUSH_TIMEOUT_S and its
    failure is logged, never raised, so it cannot break or delay the answer
    already sent - only how long the stream stays open afterwards (one round
    trip, versus the 6-8 sequential ones it replaces). It stays inside the
    stream rather than going to a background task because a serverless host may
    freeze the instance once the response ends, silently dropping the write.
    """
    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    started_at = _now()
    first_token_ms: int | None = None
    kb_lookup = False
    # (event dict, ...) in the order they used to be appended to the store.
    log_events: list[dict] = []
    # Number of event-log round trips that happened before the first token. By
    # construction 0; kept as a counter so the timing log would show a regression.
    db_calls_before_first_token = 0

    yield {"event": "start", "data": {"run_id": run_id}}

    # ---------- plan ----------
    yield {"event": "status", "data": {"stage": "planning"}}
    plan: Plan = await plan_for(question, pick_planner_model(models))
    plan_ms = int((time.perf_counter() - t0) * 1000)

    # Computed once, here, and reused for both the declared graph (chat_graph)
    # and the actual fanout calls below - previously chat_graph assigned
    # specialist models from the full `models` list while the fanout called
    # `specialist_models(models)` (hosted-only), so the graph's declared model
    # for a specialist could silently disagree with the model that actually
    # answered. `rank_models` is a pure function of (fan_models, plan.intent),
    # so computing it again inside chat_graph on the same inputs reproduces
    # this exact ranking.
    fan_models = specialist_models(models)
    ranked = rank_models(fan_models, plan.intent)
    decision = None
    if forced_model and forced_model in models:
        # The user picked which model answers - leave specialist assignment on
        # auto below; forcing the whole council to one model would defeat the
        # point of a council, and the user asked to control the answer, not
        # the fanout.
        answer_model, answer_model_reason = forced_model, "manually selected"
        fallback_candidates = [m for m, _ in ranked if m != forced_model]
        answer_fallback = fallback_candidates[0] if fallback_candidates else None
    else:
        # The router weighs capability against cost and latency for this
        # question's difficulty and the user's priority (backend/providers/
        # router.py); a greeting no longer buys the strongest, slowest model.
        # A provider failure (rate limit, 5xx) on the pick falls back to the
        # next-best routed model rather than surfacing "the answer stream
        # failed" - see backend/chat/answer.py's stream_direct/stream_synthesis.
        decision = route_model(fan_models, plan.intent, plan.complexity, priority)
        answer_model, answer_model_reason = decision.model, decision.reason
        others = [m for m in decision.ranked_models if m != answer_model]
        answer_fallback = others[0] if others else None
    model_choices = [
        {"id": s.id, "role": s.role, "model": ranked[i % len(ranked)][0],
         "reason": ranked[i % len(ranked)][1]}
        for i, s in enumerate(plan.specialists)
    ]

    # The run is created after planning, not before: the graph is a product of
    # the plan, and replay validates the stored spec against its own hash.
    graph = chat_graph(plan, fan_models)

    # Nothing in this payload depends on the store writes below, so it is
    # yielded before them rather than after - the routing decision reaches the
    # client the moment it's known, not once logging has round-tripped Turso.
    yield {"event": "plan", "data": {
        "intent": plan.intent, "complexity": plan.complexity,
        "rationale": plan.rationale, "source": plan.source,
        "route": "fast" if not plan.needs_council else "council",
        "clarifying_questions": plan.clarifying_questions,
        "specialists": [{"id": s.id, "role": s.role} for s in plan.specialists],
        "plan_ms": plan_ms,
        "model_choices": model_choices,
        "answer_model": answer_model,
        "answer_model_reason": answer_model_reason,
        "routing": decision.model_dump() if decision else None,
        # The graph was already being built for replay provenance - sending it
        # here too is what lets the UI draw this turn's workflow instead of
        # only knowing its hash. Nodes/edges only; locked/config_hash carry no
        # meaning for a chat turn (never approved, never re-run in place).
        "graph": {"nodes": [n.model_dump(exclude_none=True) for n in graph.nodes],
                  "edges": [e.model_dump() for e in graph.edges]},
    }}

    log_events.append({
        "event_type": EventType.RUN_START, "ts": _now(),
        "payload": {"mode": "chat", "question": question[:2000]}})
    log_events.append({
        "event_type": EventType.GRAPH_PROPOSED, "ts": _now(),
        "payload": {
            "intent": plan.intent, "complexity": plan.complexity,
            "rationale": plan.rationale, "source": plan.source,
            "config_hash": graph.config_hash,
            "branches": [s.id for s in plan.specialists],
        }})

    # ---------- knowledge ----------
    # Retrieval is folded into the question text, exactly like web search below:
    # whatever text the answer was built from is what gets stored and replayed.
    kb: dict = {"used": False, "semantic_backend": None, "hits": []}
    spec_question = question
    if use_knowledge and workspace_id:
        try:
            kb_lookup = True
            # Off the loop: a cold workspace is rebuilt from the database.
            ws = await asyncio.to_thread(knowledge_base.get, workspace_id, create=False)
            if ws is not None and ws.docs:
                yield {"event": "status", "data": {"stage": "retrieving"}}
                found = await asyncio.to_thread(ws.search, question, top_k=4)
                if found["hits"]:
                    spec_question = f"{question}\n\n{render_context(found['hits'])}"
                    kb = {"used": True, "semantic_backend": found["semantic_backend"],
                          "hits": [{"doc_name": h["doc_name"], "ordinal": h["ordinal"],
                                    "score": h["score"]} for h in found["hits"]]}
        except Exception as e:
            log.warning("knowledge retrieval failed, continuing without it: %s", e)

    # ---------- specialists ----------
    contributions: list[dict] = []
    fanout_ms = 0
    if plan.needs_council:
        yield {"event": "status", "data": {"stage": "consulting",
                                           "count": len(plan.specialists)}}
        log_events.extend(
            {"event_type": EventType.NODE_START, "node_id": s.id, "ts": _now(),
             "payload": {"role": s.role}}
            for s in plan.specialists
        )

        fan_t0 = time.perf_counter()
        # One gather: every specialist starts before any of them finishes, and
        # each is bounded so the slowest cannot decide how long the turn takes.
        # Uses the same `ranked` models (see above) that chat_graph declared,
        # so the graph's stated model per specialist matches what actually ran.
        contributions = await asyncio.gather(*[
            _bounded(
                contribute(spec_question, s.id, s.role,
                           ranked[i % len(ranked)][0], seed=seed,
                           fallback_model=ranked[(i + 1) % len(ranked)][0]
                           if len(ranked) > 1 else None),
                s.id,
            )
            for i, s in enumerate(plan.specialists)
        ])
        fanout_ms = int((time.perf_counter() - fan_t0) * 1000)

        log_events.extend(_specialist_rows(contributions, _now()))

        for i, c in enumerate(contributions):
            yield {"event": "specialist", "data": {
                "id": c["aspect"], "points": len(c.get("points") or []),
                "model": c.get("model"), "reason": ranked[i % len(ranked)][1],
                "latency_ms": c.get("latency_ms"),
                "tokens": c.get("tokens"), "error": c.get("error"),
            }}

    # ---------- answer ----------
    yield {"event": "status", "data": {"stage": "answering"}}
    # Cheap, free, no-network gate (needs_fresh_data) before paying for a real
    # search round trip - most questions are timeless. Folded into the question
    # text itself rather than given to the model as a live tool call: that
    # keeps the SSE token stream (ProviderAdapter.stream has no tools param,
    # and touching it risks the 34s->4s latency fix from last session) and the
    # replay path completely unchanged - whatever text the answer was built
    # from is exactly what gets stored and replayed.
    answer_question = spec_question
    search_results: list[dict] = []
    if force_web_search or needs_fresh_data(question):
        try:
            search_results = await web_search(question)
            web_context = render_results(search_results)
            if web_context:
                answer_question = f"{answer_question}\n\n{web_context}"
        except Exception as e:
            log.warning("web search failed, continuing without it: %s", e)
    chunks: list[str] = []
    answer_error: str | None = None
    gen = (
        stream_synthesis(answer_question, contributions, answer_model, history=history,
                          fallback_model=answer_fallback)
        if plan.needs_council
        else stream_direct(answer_question, answer_model, history=history,
                            fallback_model=answer_fallback)
    )
    try:
        async for piece in gen:
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - t0) * 1000)
            chunks.append(piece)
            yield {"event": "token", "data": piece}
    except Exception as e:
        log.exception("answer stream failed")
        answer_error = str(e)[:500] or type(e).__name__
        msg = f"\n\n_The answer stream failed: {e}_"
        chunks.append(msg)
        yield {"event": "token", "data": msg}

    answer = "".join(chunks)
    total_ms = int((time.perf_counter() - t0) * 1000)

    # ---------- file (optional) ----------
    # Sits after the token stream, not inside it - this must never touch the
    # SSE token-by-token path the 34s->4s latency fix depends on. A failure
    # here (bad JSON, unsupported content) just means no file event; the text
    # answer above already stands on its own.
    file_kind = wants_file(question)
    if file_kind:
        generated = await generate_file(question, file_kind, answer_model)
        if generated:
            content, filename = generated
            file_id = store_file(content, filename, file_kind)
            yield {"event": "file", "data": {
                "file_id": file_id, "filename": filename, "kind": file_kind,
            }}

    # ---------- benchmarks ----------
    spec_latency = sum(c.get("latency_ms") or 0 for c in contributions)
    tokens = sum(c.get("tokens") or 0 for c in contributions)
    cost = sum(c.get("cost_usd") or 0.0 for c in contributions)
    bench = {
        "run_id": run_id,
        "route": "fast" if not plan.needs_council else "council",
        "intent": plan.intent,
        "complexity": plan.complexity,
        "planner_source": plan.source,
        "models_used": sorted(
            {answer_model, *(c.get("model") or "" for c in contributions)} - {""}
        ),
        "status": "failed" if answer_error else "done",
        "error": answer_error,
        "answer_model": answer_model,
        "answer_model_reason": answer_model_reason,
        "timing": {
            "plan_ms": plan_ms,
            "fanout_ms": fanout_ms,
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
        },
        "specialists": [
            {"id": c["aspect"], "points": len(c.get("points") or []),
             "model": c.get("model"), "reason": ranked[i % len(ranked)][1],
             "latency_ms": c.get("latency_ms"), "tokens": c.get("tokens"),
             "failed": bool(c.get("error"))}
            for i, c in enumerate(contributions)
        ],
        # Sequential cost divided by wall clock. >1 means the fanout genuinely
        # overlapped; 0 branches means the fast path, reported as null not 1.0.
        "parallel_efficiency": (
            round(spec_latency / fanout_ms, 2) if fanout_ms > 0 and spec_latency else None
        ),
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "answer_chars": len(answer),
        "marginal_information_gain": _mig(contributions) if contributions else {
            "available": False, "per_agent": {}, "overlapping_pairs": []},
        "web_search": {
            "used": bool(search_results),
            "sources": [{"title": r["title"], "url": r["url"]} for r in search_results],
        },
        "knowledge": kb,
        "routing": decision.model_dump() if decision else None,
        "trace": _trace(plan, plan_ms, contributions, ranked, answer_model, total_ms, fanout_ms,
                        answer, answer_error),
    }

    # Neither `bench` nor the final answer depends on the event-log write, so
    # both are yielded first. The log rows are buffered, in the order they used
    # to be written (answer TOOL_RESULT, then RUN_END carrying the benchmarks).
    if answer and not answer_error:
        # The answer is a completion too. Recording it under a stable node id
        # means a replayed turn reproduces the reply itself, not just the
        # specialist notes that fed it.
        log_events.append({
            "event_type": EventType.TOOL_RESULT, "node_id": "answer", "agent_id": "answer",
            "latency_ms": total_ms, "ts": _now(),
            "payload": {"model": answer_model, "used_fallback": False,
                        "replayed": False, "text": answer}})
    ended_at = _now()
    log_events.append({"event_type": EventType.RUN_END, "ts": ended_at,
                       "payload": {"benchmarks": bench}})
    yield {"event": "benchmarks", "data": bench}
    yield {"event": "done", "data": {"run_id": run_id, "answer": answer,
                                     **({"error": answer_error} if answer_error else {})}}

    db_calls = 0
    if store is not None or open_store is not None:
        db_calls = await _flush_turn(
            store, open_store,
            {"run_id": run_id, "goal": question[:2000], "config_hash": graph.config_hash,
             "seed": seed, "graph_spec": graph.model_dump(),
             "status": "failed" if answer_error else "done",
             "started_at": started_at, "ended_at": ended_at},
            log_events,
        )
    else:
        await asyncio.to_thread(budget.flush)
    log.info(
        "chat turn %s route=%s db_calls_before_first_token=%d kb_lookup=%s "
        "db_calls_after_done=%d plan_ms=%d first_token_ms=%s total_ms=%d flush_ms=%d",
        run_id, bench["route"], db_calls_before_first_token, kb_lookup, db_calls,
        plan_ms, first_token_ms, total_ms,
        int((time.perf_counter() - t0) * 1000) - total_ms,
    )
