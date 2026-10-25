"""Task-to-graph compiler.

The council proposes GraphSpecs, peer-ranks them anonymously, and the chairman
merges. The merged plan is then structurally validated and repaired here, in
code, because a model-authored graph is untrusted: it can name a node twice,
point an edge at nothing, or omit the verification node the PS requires.

The compiler guarantees the invariants; the council only supplies the shape.
"""

import json
import logging

from backend.council.council import Verdict, deliberate
from backend.engine.spec import AgentSpec, Edge, GraphSpec, Node
from backend.events import EventStore, EventType
from backend.providers.catalog import rank_models
from backend.providers.websearch import needs_fresh_data, render_results, web_search

log = logging.getLogger("aco.compiler")

PLAN_SCHEMA_HINT = """Return JSON of this exact shape:
{
  "branches": [
    {"id":"research","role":"what this stage does","tools":["read_file"]},
    {"id":"draft","role":"...","tools":["read_file"],"depends_on":["research"]},
    {"id":"review","role":"...","tools":[],"depends_on":["draft"]}
  ]
}
Rules: 2 to 5 branches. Each id is lowercase, one word, unique. Shape the plan to
the goal. Independent aspects have no depends_on and run in parallel. When a stage
needs another stage's result (research -> draft -> review), list that stage's id in
depends_on; it then runs after it and receives the upstream output. depends_on may
only name other branch ids and must not form a cycle. tools may only contain
"read_file" and "list_files"."""

FINDINGS_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["title", "severity", "recommendation"],
                "properties": {
                    "title": {"type": "string", "minLength": 3},
                    "severity": {"enum": ["low", "medium", "high", "critical"]},
                    "recommendation": {"type": "string", "minLength": 3},
                },
            },
        }
    },
}

REPORT_SCHEMA = {
    "type": "object",
    "required": ["summary", "prioritized"],
    "properties": {
        "summary": {"type": "string", "minLength": 10},
        "prioritized": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["rank", "title", "severity"],
                "properties": {
                    "rank": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "severity": {"enum": ["low", "medium", "high", "critical"]},
                },
            },
        },
    },
}

# Non-audit goals ("explain how RAG works", "build a spreadsheet for X") used to
# be forced through the audit-shaped schemas above regardless of what was asked.
# Same shape family (an array of structured items plus a summary), but headed
# points/sections instead of severity-rated findings/prioritized.
GENERAL_FINDINGS_SCHEMA = {
    "type": "object",
    "required": ["points"],
    "properties": {
        "points": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["heading", "detail"],
                "properties": {
                    "heading": {"type": "string", "minLength": 3},
                    "detail": {"type": "string", "minLength": 3},
                },
            },
        }
    },
}

GENERAL_REPORT_SCHEMA = {
    "type": "object",
    "required": ["summary", "sections"],
    "properties": {
        "summary": {"type": "string", "minLength": 10},
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["heading", "content"],
                "properties": {
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    },
}


def schemas_for(intent: str) -> tuple[dict, dict]:
    """Audit goals keep the severity-rated shape; everything else gets a
    general points/sections shape instead of being forced into it."""
    if intent == "audit":
        return FINDINGS_SCHEMA, REPORT_SCHEMA
    return GENERAL_FINDINGS_SCHEMA, GENERAL_REPORT_SCHEMA


ALLOWED_TOOLS = {"read_file", "list_files"}


def _branch_agent(
    bid: str, role: str, tools: list[str], model: str, schema: dict,
    web_context: str = "", after: list[str] | None = None,
) -> AgentSpec:
    upstream = (
        f"You receive the outputs of upstream stages ({', '.join(after)}); build on them.\n"
        if after else ""
    )
    return AgentSpec(
        id=bid,
        role=role,
        system_contract=(
            f"You are the {bid} specialist. {role}\n" + upstream +
            "Report only what you can support from the material given to you. "
            "Output JSON only, matching the required schema. No prose, no markdown fence."
            + web_context
        ),
        tools=[t for t in tools if t in ALLOWED_TOOLS],
        model=model,
        output_schema=schema,
        budget_tokens=4000,
        timeout_s=180,
    )


def _fallback_branches(intent: str) -> list[dict]:
    """Deterministic, intent-shaped plan used when the council gave no usable one.
    Only audits get security/quality/docs; a build is a pipeline."""
    def b(bid: str, role: str, *deps: str) -> dict:
        return {"id": bid, "role": role, "tools": ["read_file"], "depends_on": list(deps)}

    if intent == "audit":
        return [b("security", "Find security weaknesses."),
                b("quality", "Find code quality and maintainability issues."),
                b("docs", "Find documentation gaps.")]
    if intent == "build":
        return [b("plan", "Break the goal into concrete requirements and steps."),
                b("implement", "Produce the deliverable following the plan.", "plan"),
                b("review", "Check the deliverable against the plan and fix gaps.", "implement")]
    if intent == "compare":
        return [b("strengths", "Cover what each option does well."),
                b("weaknesses", "Cover where each option falls short."),
                b("tradeoffs", "Cover which option fits which situation.")]
    if intent in ("explain", "chat"):
        return [b("core", "Explain the core idea clearly."),
                b("context", "Give background, examples and common pitfalls.")]
    return [b("facts", "Establish the relevant facts and data."),
            b("drivers", "Identify the causes and drivers behind them."),
            b("implications", "Draw out the implications and recommendations.")]


def _sanitize(branches: list[dict], limit: int) -> list[dict]:
    """Clean untrusted branches: unique identifier ids, capped at `limit`, and
    depends_on reduced to known, non-self deps with cycles broken by dropping the
    back edge (branches are processed in order, so the result is deterministic)."""
    seen: set[str] = set()
    clean: list[dict] = []
    for b in branches:
        bid = str(b.get("id", "")).strip().lower().replace(" ", "_")
        if not bid or bid in seen or not bid.isidentifier():
            continue
        seen.add(bid)
        raw = b.get("depends_on")
        clean.append({
            "id": bid,
            "role": str(b.get("role") or f"Examine the {bid} aspects of the repository."),
            "tools": list(b.get("tools") or ["read_file"]),
            "depends_on": [str(d).strip().lower().replace(" ", "_") for d in raw]
            if isinstance(raw, list) else [],
        })
    clean = clean[:limit]
    ids = {b["id"] for b in clean}
    deps: dict[str, list[str]] = {b["id"]: [] for b in clean}

    def reaches(src: str, target: str) -> bool:  # does src depend (transitively) on target?
        return src == target or any(reaches(d, target) for d in deps[src])

    for b in clean:
        for d in b["depends_on"]:
            if d in ids and d not in deps[b["id"]] and not reaches(d, b["id"]):
                deps[b["id"]].append(d)
        b["depends_on"] = deps[b["id"]]
    return clean


def build_graph(
    branches: list[dict],
    models: list[str],
    verifier_model: str,
    *,
    allow_degenerate: bool = False,
    intent: str = "audit",
    web_context: str = "",
) -> GraphSpec:
    """Assemble fanout -> agents (parallel, or chained via depends_on) -> join -> verify.

    The verify node and at least one parallel branch are invariants, not
    suggestions - PS requirement 64 names both. So when the council returns
    fewer than two usable branches we substitute the default set rather than
    emit a graph that cannot satisfy the requirement.

    `allow_degenerate` opts out of that substitution. The marginal-value report
    deliberately builds depth-1 graphs to measure them, and silently widening
    those to three branches would make the depth sweep compare a graph against
    itself.
    """
    # Up to 5 agents when the plan has dependencies (a pipeline needs room), else 4.
    limit = 5 if any(b.get("depends_on") for b in branches) else 4
    clean = _sanitize(branches, limit)
    if len(clean) < 2 and not allow_degenerate:
        # The council failed to give us a usable plan. Fall back to a shape
        # derived from the intent rather than emitting a single-branch graph.
        log.warning("council returned %d usable branches, using %s default set",
                    len(clean), intent)
        clean = _sanitize(_fallback_branches(intent), limit)

    nodes = [
        Node(id="clarify", type="clarify"),
        Node(id="approve", type="approval"),
        Node(id="fanout", type="fanout"),
    ]
    edges = [
        Edge(from_node="clarify", to_node="approve"),
        Edge(from_node="approve", to_node="fanout"),
    ]

    findings_schema, report_schema = schemas_for(intent)

    # Ranked against the real intent (default "audit" only when the caller
    # supplies none - marginal_value.py's depth sweep relies on this default so
    # every depth it compares still ranks identically). See
    # backend/providers/catalog.py.
    ranked = rank_models(models, intent)
    for i, b in enumerate(clean):
        model = ranked[i % len(ranked)][0]
        nodes.append(Node(id=b["id"], type="agent", agent=_branch_agent(
            b["id"], b["role"], b["tools"], model, findings_schema, web_context,
            b["depends_on"]),
            max_retries=1))
        # No deps: hang off fanout. Deps: receive each upstream agent's output.
        for src in b["depends_on"] or ["fanout"]:
            edges.append(Edge(from_node=src, to_node=b["id"], handoff_schema=findings_schema))

    nodes.append(Node(id="join", type="join"))
    needed = {d for b in clean for d in b["depends_on"]}
    for b in clean:
        if b["id"] in needed:
            continue
        edges.append(Edge(from_node=b["id"], to_node="join", handoff_schema=findings_schema))

    verifier_contract = (
        "You verify findings produced by other agents. Drop anything unsupported, "
        "merge duplicates, and rank by severity. Output JSON only."
        if intent == "audit" else
        "You verify the points produced by other agents. Drop anything unsupported, "
        "merge duplicates, and organize the rest into a coherent report. Output JSON only."
    )
    nodes.append(Node(
        id="verify", type="verify",
        agent=AgentSpec(
            id="verifier",
            role="Independent verification of the merged output",
            system_contract=verifier_contract,
            tools=[], model=verifier_model, output_schema=report_schema,
            budget_tokens=4000, timeout_s=180,
        ),
    ))
    edges.append(Edge(from_node="join", to_node="verify", handoff_schema=report_schema))

    # Compensation for each analyst: a failed branch degrades the report rather
    # than killing the run.
    for b in clean:
        nodes.append(Node(id=f"comp_{b['id']}", type="compensate", compensates=b["id"]))

    return GraphSpec(version=1, nodes=nodes, edges=edges).finalize()


async def compile_graph(
    goal: str,
    members: list[str],
    *,
    store: EventStore | None = None,
    run_id: str | None = None,
    seed: int = 42,
    chairman_policy: str = "strongest",
    answers: dict[str, str] | None = None,
    intent: str | None = None,
) -> tuple[GraphSpec, Verdict | None]:
    # Clarifying answers used to be logged for audit purposes and then
    # discarded - compile_graph never saw them. Folded into the task text so
    # they actually shape the council's proposal and the fallback planner call.
    task = goal
    if answers:
        qa = "\n".join(f"Q: {q}\nA: {a}" for q, a in answers.items())
        task = f"{goal}\n\nClarifying context:\n{qa}"

    web_context = ""
    if needs_fresh_data(task):
        try:
            results = await web_search(task)
            if results:
                web_context = "\n\n" + render_results(results)
        except Exception as e:
            log.warning("web search failed, continuing without it: %s", e)

    council_task = (
        f"Design a team of independent specialist agents for this goal:\n\n{task}\n\n"
        "Independent specialists run in parallel; if a stage needs another's output "
        "(e.g. research -> draft -> review), express that with depends_on."
    )
    verdict = None
    branches: list[dict] = []
    try:
        verdict = await deliberate(
            council_task, members, schema_hint=PLAN_SCHEMA_HINT,
            policy=chairman_policy, store=store, run_id=run_id, seed=seed,
        )
        content = verdict.content
        if isinstance(content, dict):
            branches = content.get("branches") or []
        elif isinstance(content, list):
            branches = content
    except Exception as e:
        log.warning("council compile failed: %s", e)

    from backend.chat.planner import heuristic_plan, plan_for

    plan = None
    if len(branches) < 2:
        # Ask a planner what THIS goal's dimensions are before falling back to
        # the generic security/quality/docs set. That set is right for a code
        # audit and wrong for everything else, and it used to be applied to
        # every goal regardless of what was asked.
        plan = await plan_for(task, members[0] if members else None)
        if len(plan.specialists) >= 2:
            log.info("council gave %d branches, planner supplied %d",
                     len(branches), len(plan.specialists))
            branches = plan.branch_dicts()

    # Prefer the intent the frontend already computed during /api/clarify (a
    # real model-backed classification); fall back to the planner call just
    # made above if there was one, else the free heuristic classifier.
    resolved_intent = intent or (plan.intent if plan else None) or heuristic_plan(task).intent

    graph = build_graph(
        branches, members, verifier_model=members[-1],
        intent=resolved_intent, web_context=web_context,
    )
    agent_ids = {n.id for n in graph.nodes if n.type == "agent"}
    agent_levels = [
        lv for lv in ([a for a in lvl if a in agent_ids] for lvl in graph.levels()) if lv
    ]
    shape = ("parallel" if len(agent_levels) <= 1
             else "pipeline" if all(len(lv) == 1 for lv in agent_levels) else "mixed")

    if store and run_id:
        await store.append(
            run_id, EventType.GRAPH_PROPOSED,
            payload={
                "config_hash": graph.config_hash,
                "node_count": len(graph.nodes),
                "levels": graph.levels(),
                "branches": [n.id for n in graph.nodes if n.type == "agent"],
                "council_used": verdict is not None,
                "intent": resolved_intent,
                "shape": shape,
                "web_search_used": bool(web_context),
            },
        )
    return graph, verdict


def clarifying_questions(goal: str, intent: str | None = None) -> list[str]:
    """Up to 3 questions, then an explicit permission request.

    Deterministic rather than model-generated: the demo must ask the same
    questions every time, and these are the axes that actually change the graph.
    Audit (and the legacy no-intent call) keeps the audit questions.
    """
    if intent in (None, "audit"):
        return [
            "Which areas should the audit prioritise - security, code quality, "
            "documentation, or tests?",
            "Should findings be limited to what is provable from the code, or may they "
            "include advisory suggestions?",
            "What severity floor should appear in the final report?",
        ][:3]
    return [
        "What scope should this cover, and is anything explicitly out of scope?",
        "Who is the audience, and how much background do they already have?",
        "What format should the result take - short summary, detailed report, or steps?",
    ]


def permission_prompt(branches: list[str]) -> str:
    return (
        "I can run "
        + ", ".join(branches)
        + " as parallel read-only agents, verify their merged output with an independent "
        "council node, and produce a prioritized remediation report. "
        "No files will be written. Shall I proceed?"
    )


def schema_json(schema: dict) -> str:
    return json.dumps(schema, indent=2)
