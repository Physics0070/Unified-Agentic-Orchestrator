"""HTTP surface. Everything the UI needs is a read of the event log."""

import asyncio
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, Response, UploadFile
from sse_starlette.sse import EventSourceResponse

from backend import budget
from backend.api.schemas import (
    ApprovalRequest,
    ChatRequest,
    CompileRequest,
    ExecuteRequest,
    GoalRequest,
    KnowledgeQuery,
    MarginalValueRequest,
    ProviderTest,
    ProviderUpdate,
    RouterExplain,
    RunRequest,
)
from backend.chat.files import get_file
from backend.config import settings
from backend.council import clarifying_questions, compile_graph, permission_prompt
from backend.engine import Executor, GraphSpec
from backend.engine.migrations import migrate
from backend.events import EventStore, EventType
from backend.handoff import artifact_store
from backend.limiter import limiter
from backend.permissions.red_agent import red_agent_graph
from backend.providers import manage
from backend.providers.secret_broker import configured_providers, known_providers
from backend.rag import SUPPORTED, KnowledgeError, ParseError, knowledge_base, parse_document
from backend.trace import build_trace
from backend.workflow_dna.orchestrator_hooks import (
    decision_payload as dna_decision_payload,
)
from backend.workflow_dna.orchestrator_hooks import predict as dna_predict
from backend.workflow_dna.orchestrator_hooks import predict_and_log, record_and_log

log = logging.getLogger("aco.api")
router = APIRouter(prefix="/api")

# In-flight runs, so cancel/pause can reach them. State of record is still the
# event log; this is only a handle to the asyncio task.
_TASKS: dict[str, asyncio.Task] = {}

async def _default_models() -> list[str]:
    """Model specs across every configured provider, Ollama first.

    Ollama is the tested primary path - models[0] answers, is preferred by
    the planner, and is council chairman by default. Other providers widen the
    council with genuinely different model families when configured: different
    labs have different blind spots, which is the actual point of a council
    over asking one model twice. Which providers are live and which models each
    contributes is owned by backend/providers/manage.py (env config plus
    whatever a user set at runtime), so this function never changes to add one.
    """
    return manage.all_models()


NO_MODELS = (
    "No model provider is configured. Set OLLAMA_BASE_URL (and OLLAMA_API_KEY "
    "for Ollama Cloud), or GROQ_API_KEY / GEMINI_API_KEY, as environment variables."
)


def _require_models(models: list[str]) -> list[str]:
    """Fail loudly and early rather than deep inside a turn.

    With no hosted key there is nothing to answer with, and the old local
    fallback is gone. A 503 naming the missing variable is far easier to act on
    than an IndexError on `models[0]` three frames down.
    """
    if not models:
        raise HTTPException(status_code=503, detail=NO_MODELS)
    return models


@router.get("/models")
async def models() -> dict:
    return {"models": await _default_models(), "providers": configured_providers()}


# ------------------------------------------------------- provider management


def _require_admin(token: str | None) -> None:
    """Gate for writes that change which keys the process uses.

    Reads (status) are open - they never contain a key. With neither ADMIN_TOKEN
    nor SECRET_KEY set the writes are open in dev and refused in prod, so a
    deployment cannot be left with an unauthenticated "set the API key" endpoint
    by forgetting a variable.
    """
    expected = settings.admin_token or settings.secret_key
    if not expected:
        if settings.is_prod:
            raise HTTPException(403, "set ADMIN_TOKEN to enable provider management")
        return
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "invalid admin token")


def _check_provider(provider: str) -> None:
    if provider not in known_providers():
        raise HTTPException(404, "unknown provider")


def _provider_payload() -> dict:
    return {"providers": manage.status(),
            "admin_required": bool(settings.admin_token or settings.secret_key)
                              or settings.is_prod}


@router.get("/providers")
async def providers_status() -> dict:
    return _provider_payload()


@router.put("/providers/{provider}")
async def providers_update(
    provider: str, body: ProviderUpdate, x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_provider(provider)
    _require_admin(x_admin_token)
    try:
        manage.connect(provider, body.api_key, body.base_url)
        if body.models is not None:
            manage.set_models(provider, body.models)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _provider_payload()


@router.delete("/providers/{provider}")
async def providers_remove(
    provider: str, x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_provider(provider)
    _require_admin(x_admin_token)
    manage.disconnect(provider)
    return _provider_payload()


@router.post("/providers/{provider}/test")
async def providers_test(provider: str, body: ProviderTest) -> dict:
    _check_provider(provider)
    return await manage.check_connection(provider, body.model)


@router.post("/router/explain")
async def router_explain(req: RouterExplain) -> dict:
    """Which model the router would pick for this message, and why - every
    candidate's capability/cost/latency components, not just the winner."""
    from backend.chat import plan_for
    from backend.chat.planner import pick_planner_model
    from backend.providers.router import route_model

    models_ = _require_models(req.models or await _default_models())
    plan = await plan_for(req.message, pick_planner_model(models_))
    decision = route_model(models_, plan.intent, plan.complexity, req.priority)
    return {"plan": {"intent": plan.intent, "complexity": plan.complexity,
                     "rationale": plan.rationale, "source": plan.source},
            "decision": decision.model_dump()}


# ------------------------------------------------------ knowledge workspace


def _knowledge_summary(workspace_id: str) -> dict:
    try:
        ws = knowledge_base.get(workspace_id)
    except KnowledgeError as e:
        raise HTTPException(400, str(e)) from e
    return {**ws.summary(), "supported": SUPPORTED, "max_bytes": settings.max_upload_bytes}


@router.get("/knowledge/documents")
async def knowledge_documents(workspace_id: str) -> dict:
    return await asyncio.to_thread(_knowledge_summary, workspace_id)


@router.post("/knowledge/upload")
@limiter.limit(lambda: f"{settings.rate_upload_per_min}/minute")
async def knowledge_upload(request: Request, workspace_id: str = Form(...),
                           file: UploadFile = File(...),  # noqa: B008
) -> dict:
    name = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:120]
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"file is over the {settings.max_upload_bytes // 1024} KB limit")
    try:
        ws = knowledge_base.get(workspace_id)
        text = await asyncio.to_thread(parse_document, name, data)
        info = await asyncio.to_thread(ws.add_document, name, text)
    except (ParseError, KnowledgeError) as e:
        raise HTTPException(422, str(e)) from e
    return {**(await asyncio.to_thread(_knowledge_summary, workspace_id)),
            "document": info.to_dict()}


@router.delete("/knowledge/documents/{doc_id}")
async def knowledge_delete(doc_id: str, workspace_id: str) -> dict:
    try:
        ws = knowledge_base.get(workspace_id, create=False)
    except KnowledgeError as e:
        raise HTTPException(400, str(e)) from e
    if ws is None or not await asyncio.to_thread(ws.remove_document, doc_id):
        raise HTTPException(404, "no such document")
    return await asyncio.to_thread(_knowledge_summary, workspace_id)


@router.post("/knowledge/query")
async def knowledge_query(req: KnowledgeQuery) -> dict:
    try:
        ws = knowledge_base.get(req.workspace_id, create=False)
    except KnowledgeError as e:
        raise HTTPException(400, str(e)) from e
    if ws is None:
        return {"query": req.query, "mode": req.mode, "reranked": False, "hits": [],
                "semantic_backend": None, "documents": 0, "chunks": 0}
    return await asyncio.to_thread(
        ws.search, req.query, top_k=req.top_k, mode=req.mode, rerank=req.rerank)


@router.get("/models/catalog")
async def models_catalog() -> dict:
    """Per-model scores across the 6 routing axes - curated estimates (see
    backend/providers/catalog.py's own docstring), not live-measured. Exposes
    what rank_models() already uses internally so the UI can chart it."""
    from backend.providers.catalog import profile_for

    out = []
    for m in await _default_models():
        profile, key = profile_for(m)
        out.append({"model": m, "matched_key": key, "profile": profile.model_dump()})
    return {"models": out}


@router.get("/usage")
async def usage() -> dict:
    """Today's (UTC) model usage against the daily cap. Read-only."""
    return budget.usage()


@router.post("/chat")
@limiter.limit(lambda: f"{settings.rate_chat_per_min}/minute")
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    """Ask anything, get an answer. SSE so the answer streams like a chat should.

    The turn also reports what it cost to produce - route taken, specialists
    consulted, overlap between them - which is the whole point of orchestrating
    rather than forwarding the question to one model.
    """
    from backend.chat import run_turn

    # Non-blocking: a stale daily-usage total is refreshed on a background
    # thread instead of making this request wait on Turso before the stream opens.
    budget.ensure_available(blocking=False)
    models_ = _require_models(req.models or await _default_models())
    history = [m.model_dump() for m in req.history]

    async def gen():
        # The event log is opened lazily by run_turn, only for its single
        # end-of-turn write - nothing connects to the database before the first
        # token. If the store cannot even be constructed (no Turso credentials)
        # the chat just runs without a trace.
        open_store = None
        try:
            EventStore()  # validates configuration only; connects in __aenter__
            open_store = EventStore
        except Exception as e:
            log.warning("chat running without the event log: %s", e)
        try:
            async for evt in run_turn(
                req.message, history=history, models=models_, seed=req.seed,
                open_store=open_store,
                force_web_search=req.force_web_search, forced_model=req.forced_model,
                priority=req.priority, workspace_id=req.workspace_id,
                use_knowledge=req.use_knowledge,
            ):
                if await request.is_disconnected():
                    return
                yield {"event": evt["event"], "data": json.dumps(evt["data"], default=str)}
        except budget.DailyCapReached:
            yield {"event": "error", "data": json.dumps({"error": "daily_cap_reached"})}
        except Exception as e:
            log.exception("chat turn failed")
            yield {"event": "error", "data": json.dumps({"detail": str(e)[:300]})}

    # ping + anti-buffering header, matching /runs/{run_id}/stream - Render's
    # edge is nginx-family and buffers a response by default, which would hold
    # the whole stream until it ends.
    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})


@router.get("/chat/files/{file_id}")
async def chat_file(file_id: str) -> Response:
    """One-time download for a file generated during a chat turn.

    Not a document store: the in-memory cache this reads from (see
    backend/chat/files.py) has a short TTL and nothing survives a restart -
    ask again and it regenerates rather than 500ing.
    """
    entry = get_file(file_id)
    if not entry:
        raise HTTPException(404, "file not found or expired - ask again to regenerate it")
    content, filename, mime = entry
    return Response(
        content=content, media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/chat/plan")
async def chat_plan(req: ChatRequest) -> dict:
    """The routing decision on its own, without answering.

    Exposed because "why did this question get four agents and that one get
    none" is the first thing anyone asks about the router.
    """
    from backend.chat import plan_for
    from backend.chat.planner import pick_planner_model

    models_ = _require_models(req.models or await _default_models())
    plan = await plan_for(req.message, pick_planner_model(models_))
    return {
        **plan.model_dump(),
        "route": "council" if plan.needs_council else "fast",
    }


@router.post("/clarify")
async def clarify(req: GoalRequest) -> dict:
    """Ask about THIS goal, then ask permission. Nothing runs yet.

    The questions come from a planner that reads the goal. The fixed list is
    still the fallback, because a clarify step that fails because the planner
    model is down is worse than three generic questions.
    """
    from backend.chat import plan_for
    from backend.chat.planner import pick_planner_model

    models_ = _require_models(req.models or await _default_models())
    plan = await plan_for(req.goal, pick_planner_model(models_))

    questions = plan.clarifying_questions or clarifying_questions(req.goal, plan.intent)
    branches = [s.id for s in plan.specialists] or ["security", "quality", "docs"]
    return {
        "questions": [{"id": f"q{i}", "text": q} for i, q in enumerate(questions)],
        "permission": permission_prompt(branches, plan.intent),
        "plan": {"intent": plan.intent, "complexity": plan.complexity,
                 "rationale": plan.rationale, "source": plan.source,
                 "specialists": [{"id": s.id, "role": s.role} for s in plan.specialists]},
    }


@router.post("/compile")
@limiter.limit(lambda: f"{settings.rate_runs_per_min}/minute")
async def compile_endpoint(req: CompileRequest, request: Request) -> dict:
    """Council designs the graph. Not executed until /runs is called."""
    budget.ensure_available()
    models_ = _require_models(req.models or await _default_models())
    async with EventStore() as store:
        run_id = str(uuid.uuid4())
        await store.create_run(run_id, req.goal, "pending", req.seed, {}, "pending")
        if req.answers:
            events = []
            for i, (q, a) in enumerate(req.answers.items()):
                events.append({"event_type": EventType.CLARIFY_ASKED,
                                "payload": {"index": i, "question": q}})
                events.append({"event_type": EventType.CLARIFY_ANSWERED,
                                "payload": {"question": q, "answer": a}})
            await store.append_many(run_id, events)
        graph, verdict = await compile_graph(
            req.goal, models_, store=store, run_id=run_id, seed=req.seed,
            chairman_policy=req.chairman_policy, answers=req.answers, intent=req.intent,
        )
        # Workflow DNA: shown here, before the user has locked or approved
        # anything, because this is the only point a prediction can actually
        # inform the decision - by the time /runs starts a run, the graph is
        # already approved and execution begins immediately. Not logged as an
        # event: no execution run_id exists yet at compile time (this run_id
        # is only for council-design bookkeeping - /runs mints its own).
        dna_decision = await dna_predict(graph, run_id)
        return {
            "run_id": run_id,
            "graph": graph.model_dump(),
            "dna_prediction": dna_decision_payload(dna_decision) if dna_decision else None,
            "council": None if not verdict else {
                "chairman": verdict.chairman,
                "borda": verdict.borda,
                "winner_label": verdict.winner_label,
                "winner_author": verdict.winner_author,
                "disagreement": verdict.disagreement,
                "escalated": verdict.escalated,
                "proposals": [
                    {"label": p.label, "author_model": p.author_model,
                     "tokens": p.tokens_in + p.tokens_out}
                    for p in verdict.proposals
                ],
                "rankings": [{"ranker": r.ranker_model, "order": r.order}
                             for r in verdict.rankings],
            },
        }


@router.post("/runs")
@limiter.limit(lambda: f"{settings.rate_runs_per_min}/minute")
async def start_run(req: RunRequest, request: Request) -> dict:
    budget.ensure_available()
    # Migrate before reconstruction: a graph built or persisted under an older
    # schema version is walked forward to CURRENT_VERSION here, once, at the
    # point a *new* run starts from it. Replay never calls this - see
    # backend/engine/migrations.py's module docstring for why.
    graph = GraphSpec(**migrate(req.graph.model_dump())).finalize()
    if graph.locked and req.graph.locked is False:
        raise HTTPException(status_code=409, detail="graph is locked")

    problems = graph.validate_side_effects()
    if problems:
        raise HTTPException(status_code=400, detail={"unapproved_side_effects": problems})

    run_id = str(uuid.uuid4())
    async with EventStore() as store:
        await store.create_run(
            run_id, req.goal, graph.config_hash, req.seed, graph.model_dump(), "pending"
        )
        await store.append(run_id, EventType.GRAPH_APPROVED,
                           payload={"config_hash": graph.config_hash})
        # Workflow DNA: advisory pre-execution fitness prediction, logged as
        # its own event. Never blocks or alters this run — see
        # backend/workflow_dna/orchestrator_hooks.py.
        dna_decision = await predict_and_log(store, graph, run_id)

    predicted_fitness = dna_decision.original.predicted_fitness if dna_decision else None
    out = {"run_id": run_id, "config_hash": graph.config_hash}

    if os.environ.get("VERCEL"):
        # Serverless freezes the function once the response is sent, so a
        # background task never finishes, and a run executed inside this request
        # is invisible to the browser until the POST returns. So only create the
        # run here; the client then calls POST /runs/{id}/execute (which runs it
        # inline) and watches the event log while that request is open.
        return {**out, "status": "pending"}

    _TASKS[run_id] = asyncio.create_task(
        _execute_run(run_id, graph, req.goal, req.seed, set(req.approvals),
                     req.workspace_id or "", predicted_fitness)
    )
    return {**out, "status": "running"}


async def _fail_run(run_id: str, reason: str) -> None:
    """End a run that can no longer finish, on a fresh connection - the one the
    run was using is the likeliest thing to be stuck."""
    try:
        async with EventStore() as s:
            await s.append_and_set_status(
                run_id, EventType.RUN_END, {"ok": False, "reason": reason},
                status="failed", ended=True,
            )
    except Exception:
        log.exception("could not mark run %s failed (%s)", run_id, reason)


async def _execute_run(run_id: str, graph: GraphSpec, goal: str, seed: int,
                       approvals: set[str], workspace_id: str,
                       predicted_fitness: float | None) -> None:
    """Run the executor under the run watchdog. Never leaves a run 'running'."""
    try:
        async with EventStore() as s:
            try:
                async with asyncio.timeout(settings.run_timeout_s or None) as watchdog:
                    result = await Executor(
                        s, graph, run_id, seed=seed, approvals=approvals,
                        goal=goal, workspace_id=workspace_id,
                    ).run()
            except TimeoutError:
                if not watchdog.expired():
                    raise
                log.error("run %s hit the %ss watchdog", run_id, settings.run_timeout_s)
                await _fail_run(run_id, "run_timeout")
            except Exception:
                log.exception("run %s crashed", run_id)
                await s.set_run_status(run_id, "failed", ended=True)
            else:
                # Advisory bookkeeping runs after the executor, outside the
                # watchdog: a run that finished must not be failed because this
                # part ran past the limit.
                try:
                    await record_and_log(s, graph, run_id, result, predicted_fitness)
                except Exception:
                    log.warning("Workflow DNA recording failed for %s", run_id, exc_info=True)
    except Exception:
        # The store itself is unusable (or timed out): still try to end the run.
        log.exception("run %s lost its event store", run_id)
        await _fail_run(run_id, "store_error")
    finally:
        _TASKS.pop(run_id, None)


@router.post("/runs/{run_id}/execute")
async def execute_run(run_id: str, req: ExecuteRequest) -> dict:
    """Execute a run created by POST /runs (status 'pending'), inline.

    Used on serverless hosts, where POST /runs only creates the row. Goal, graph
    and seed come from the row. Answers 409 unless the run is still 'pending',
    so a double click or a retry cannot execute it twice.
    """
    budget.ensure_available()
    async with EventStore() as store:
        row = await store.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown run")
        if row.status != "pending" or not row.graph_spec.get("nodes"):
            raise HTTPException(status_code=409, detail=f"run is {row.status}, not executable")
        if not await store.claim_run(run_id):
            raise HTTPException(status_code=409, detail="run already started")
    graph = GraphSpec(**migrate(row.graph_spec)).finalize()
    await _execute_run(run_id, graph, row.goal, row.seed, set(req.approvals),
                       req.workspace_id or "", None)
    async with EventStore() as store:
        final = await store.get_run(run_id)
    return {"run_id": run_id, "status": final.status if final else "failed"}


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, req: ApprovalRequest) -> dict:
    async with EventStore() as store:
        if await store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="unknown run")
        await store.append(
            run_id,
            EventType.APPROVAL_GRANTED if req.granted else EventType.CANCEL,
            node_id=req.node_id, payload={"granted": req.granted},
        )
    return {"ok": True}


async def _live_run(store: EventStore, run_id: str):
    """404 for an unknown run, 409 for one that already ended - pause/resume/
    cancel used to write an event row for any id, which the foreign key turned
    into a 500, and silently "paused" runs that had already finished."""
    row = await store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown run")
    if row.ended_at or row.status in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"run already {row.status}")
    return row


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str) -> dict:
    async with EventStore() as store:
        await _live_run(store, run_id)
        task = _TASKS.get(run_id)
        if task:
            task.cancel()
        await store.append(run_id, EventType.CANCEL, payload={"by": "user"})
        await store.set_run_status(run_id, "cancelled", ended=True)
    return {"ok": True}


@router.post("/runs/{run_id}/pause")
async def pause(run_id: str) -> dict:
    async with EventStore() as store:
        await _live_run(store, run_id)
        await store.append(run_id, EventType.PAUSE, payload={"by": "user"})
        await store.set_run_status(run_id, "paused")
    return {"ok": True}


@router.post("/runs/{run_id}/resume")
async def resume(run_id: str) -> dict:
    async with EventStore() as store:
        await _live_run(store, run_id)
        await store.append(run_id, EventType.RESUME, payload={"by": "user"})
        await store.set_run_status(run_id, "running")
    return {"ok": True}


@router.get("/runs")
async def list_runs(
    limit: int = 25,
    ids: str | None = None,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Run ids are unguessable UUIDs and the browser remembers its own, so
    `?ids=a,b` returns just those. Listing every visitor's runs is admin-only."""
    limit = max(1, min(limit, 100))
    if ids is None:
        _require_admin(x_admin_token)
        async with EventStore() as store:
            rows = await store.list_runs(limit)
            return {"runs": [r.model_dump() for r in rows]}
    wanted = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
    if len(wanted) > 50:
        raise HTTPException(400, "at most 50 ids")
    try:
        wanted = [str(uuid.UUID(i)) for i in wanted]
    except ValueError as e:
        raise HTTPException(400, "ids must be UUIDs") from e
    async with EventStore() as store:
        rows = [r for r in [await store.get_run(i) for i in wanted] if r is not None]
        out = []
        for r in rows:
            note = await _stale_note(store, r)
            out.append(_as_stale_failed(r, note) if note else r.model_dump())
        return {"runs": out}


def _parse_ts(ts: object) -> datetime | None:
    try:
        d = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def _stale_note(store: EventStore, row) -> str | None:
    """A 'running' run with no event for longer than the watchdog allows is dead
    (its function was killed before it could mark itself failed). Returns the
    note to show instead of a status that will never change, else None."""
    if getattr(row, "status", None) != "running" or not settings.run_timeout_s:
        return None
    last = None
    if row.last_seq:
        tail = await store.read(row.run_id, from_seq=row.last_seq - 1)
        last = _parse_ts(tail[-1].ts) if tail else None
    last = last or _parse_ts(row.started_at)
    if last is None:
        return None
    age = (datetime.now(UTC) - last).total_seconds()
    if age <= settings.run_timeout_s + 60:
        return None
    return f"no activity for {int(age)}s; the run was abandoned and is reported as failed"


def _as_stale_failed(row, note: str) -> dict:
    return {**row.model_dump(), "status": "failed", "note": note}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    async with EventStore() as store:
        row = await store.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown run")
        cost = await store.aggregate_cost(run_id)
        lat = await store.aggregate_latency(run_id)
        note = await _stale_note(store, row)
        return {
            "run": _as_stale_failed(row, note) if note else row.model_dump(),
            "cost": cost.model_dump(),
            "latency": {**lat.model_dump(), "parallel_efficiency": lat.parallel_efficiency},
        }


@router.get("/runs/{run_id}/events")
async def events(run_id: str) -> dict:
    async with EventStore() as store:
        return {"events": [e.model_dump() for e in await store.read(run_id)]}


@router.get("/runs/{run_id}/output")
async def output(run_id: str) -> dict:
    """The run's actual deliverable - what the orchestrator tab shows
    alongside the graph once a run finishes, answering the goal itself
    rather than only describing how it was produced.

    A completed run's sink nodes (nothing else in the graph depends on
    them - typically a verify/report/merge node at the end of a fanout) are
    what the graph as a whole produced. Their validated outputs were
    already being written to the artifact store by the engine on every
    successful node (backend/handoff/artifact_store.py) - this endpoint is
    the first thing that actually reads them back out.
    """
    async with EventStore() as store:
        row = await store.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown run")
        graph = GraphSpec(**migrate(row.graph_spec)).finalize() if row.graph_spec else None
        sinks = set(graph.sink_node_ids()) if graph else set()
        artifacts = await artifact_store.for_run(store, run_id)
        return {
            "run_id": run_id,
            "status": row.status,
            "sink_node_ids": sorted(sinks),
            "outputs": [
                {"node_id": a.node_id, "written_by": a.written_by, "content": a.content}
                for a in artifacts
                if a.node_id in sinks
            ],
        }


@router.get("/runs/{run_id}/trace")
async def trace(run_id: str) -> dict:
    """Per-agent execution trace: input, output, tools, duration, status."""
    async with EventStore() as store:
        row = await store.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return {"run_id": run_id, "status": row.status,
                **build_trace(await store.read(run_id), row.graph_spec)}


@router.get("/runs/{run_id}/stream")
async def stream(run_id: str, request: Request) -> EventSourceResponse:
    """SSE tail of the event log. No polling from the browser - the browser
    holds one connection and the server does the polling, once."""

    async def gen():
        last = 0
        idle = 0
        async with EventStore() as store:
            while not await request.is_disconnected():
                rows = await store.read(run_id, from_seq=last)
                if rows:
                    idle = 0
                    for e in rows:
                        last = e.seq
                        yield {"event": "log", "data": json.dumps(e.model_dump(), default=str)}
                        if e.event_type == EventType.RUN_END:
                            yield {"event": "done", "data": json.dumps({"run_id": run_id})}
                            return
                else:
                    idle += 1
                    if idle > 600:  # ~5 min of silence
                        return
                await asyncio.sleep(0.5)

    # ping: an SSE comment every 15s so a proxy or load balancer sees traffic
    # on an otherwise-quiet long poll and doesn't time the connection out.
    # X-Accel-Buffering: nginx-family proxies (which Render's edge is) buffer
    # a response by default, which would hold the whole stream until it ends -
    # exactly what a live tail must not do.
    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/metrics")
async def metrics(run_id: str) -> dict:
    from backend.metrics import compute

    async with EventStore() as store:
        if await store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return (await compute(store, run_id)).model_dump()


@router.get("/workflow-dna/history")
async def workflow_dna_history(limit: int = 20) -> dict:
    """Recent Workflow DNA executions, newest first — powers the frontend's
    Evolution History panel. Read-only, entirely separate from the main
    event log; safe to call even if no run has ever used Workflow DNA yet
    (returns an empty list)."""
    from backend.workflow_dna.history.repository import WorkflowHistoryRepository

    repo = WorkflowHistoryRepository()
    records = repo.list_recent(limit=limit)
    return {"executions": [r.to_dict() for r in records]}


@router.post("/runs/{run_id}/replay")
async def replay(run_id: str) -> dict:
    from backend.replay import replay_run

    async with EventStore() as store:
        try:
            result, diff = await replay_run(store, run_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "replay_run_id": result.run_id,
            "original_run_id": run_id,
            "diff": diff.model_dump(),
            "cost_saved_usd": diff.cost_saved_usd,
        }


@router.post("/marginal-value")
async def marginal_value(req: MarginalValueRequest) -> dict:
    from backend.metrics import marginal_value_report

    models_ = _require_models(req.models or await _default_models())
    async with EventStore() as store:
        report = await marginal_value_report(
            store, req.goal, models_, depths=tuple(req.depths), seed=req.seed
        )
        return report.model_dump()


@router.post("/demo/red-agent")
async def red_agent_demo() -> dict:
    """Demo step 7: undeclared tool call blocked, smuggled instruction quarantined."""
    from backend.permissions import CapabilityGuard, screen_payload
    from backend.permissions.red_agent import POISONED_HANDOFF, RED_AGENT

    graph = red_agent_graph()
    run_id = str(uuid.uuid4())
    async with EventStore() as store:
        await store.create_run(run_id, "Red Agent demonstration", graph.config_hash, 42,
                               graph.model_dump(), "running")
        await store.append(run_id, EventType.RUN_START, payload={"demo": "red_agent"})

        guard = CapabilityGuard(store, run_id)

        async def file_delete(path: str) -> str:
            return f"deleted {path}"  # registered, never reached

        guard.register("file_delete", file_delete)
        blocked = await guard.call(
            agent_id=RED_AGENT.id, declared_tools=RED_AGENT.tools,
            tool="file_delete", arguments={"path": "backend/config.py"}, node_id="red",
        )
        _, quarantined = await screen_payload(
            store, run_id, {"red": POISONED_HANDOFF},
            node_id="writer", agent_id="report_writer",
        )
        await store.append(run_id, EventType.RUN_END,
                           payload={"tool_blocked": blocked.blocked,
                                    "injection_blocked": quarantined})
        await store.set_run_status(run_id, "done", ended=True)

        rows = await store.read(run_id)
        return {
            "run_id": run_id,
            "tool_blocked": blocked.blocked,
            "injection_blocked": quarantined,
            "events": [
                e.model_dump() for e in rows
                if e.event_type in (EventType.TOOL_BLOCKED, EventType.INJECTION_BLOCKED)
            ],
        }


@router.post("/demo/stale-memory")
async def stale_memory_demo() -> dict:
    """Proves TTL eviction is real: write a scratch entry with a short TTL,
    read it back live, wait past the TTL, read again and show it's gone -
    same "prove it live" shape as the red-agent demo above. Fast and
    deterministic, no model calls."""
    from backend.memory import memory_store

    run_id = str(uuid.uuid4())
    async with EventStore() as store:
        await store.create_run(run_id, "Stale memory demonstration", "n/a", 42, {}, "running")
        await memory_store.write(run_id, "demo_agent", "demo_key", {"note": "ephemeral"}, ttl_s=0.2,
                                  shared=True)
        live_before, _ = await memory_store.read_for(run_id, "demo_agent", include_shared=True)
        await asyncio.sleep(0.3)
        live_after, evicted = await memory_store.read_for(run_id, "demo_agent", include_shared=True)
        if evicted:
            await store.append(run_id, EventType.MEMORY_EVICTED, payload={"keys": evicted})
        await store.set_run_status(run_id, "done", ended=True)
        return {
            "run_id": run_id,
            "present_before_ttl": "demo_key" in live_before,
            "present_after_ttl": "demo_key" in live_after,
            "evicted": evicted,
        }


@router.post("/demo/version-migration")
async def version_migration_demo() -> dict:
    """Proves the version gate, not just a dict diff: a v1 graph migrated to
    v2 flips whether memory_scope is honored, without reshaping anything -
    see backend/engine/migrations.py for why v1->v2 is a behavioral epoch."""
    from backend.engine.migrations import CURRENT_VERSION, migrate

    v1_dict = {
        "version": 1,
        "nodes": [
            {"id": "writer", "type": "agent", "agent": {
                "id": "writer", "role": "writes shared notes", "system_contract": "c",
                "tools": [], "model": "ollama:llama3.2:3b", "memory_scope": "shared_rw",
            }},
        ],
        "edges": [],
    }
    migrated = migrate(v1_dict)
    v1_graph = GraphSpec(**v1_dict)
    v2_graph = GraphSpec(**migrated)
    return {
        "before": v1_dict,
        "after": migrated,
        "current_version": CURRENT_VERSION,
        "memory_active_before": v1_graph.version >= 2,
        "memory_active_after": v2_graph.version >= 2,
    }
