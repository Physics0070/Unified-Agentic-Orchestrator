"""Execution trace: what each agent was given, what it produced, which tools it
touched, how long it took and how it ended.

Like every other view in this system it is a read of the event log - nothing
here is recorded separately, so the trace of a replayed run is exactly as
trustworthy as the run itself. A step is one node; a chat turn and an
orchestrated run produce the same shape, so one UI component renders both.
"""

from datetime import datetime

from backend.events.types import Event, EventType

_PREVIEW = 1600

# Status precedence, strongest first: a node that was quarantined and also hit
# an error reads as blocked, because that is the more important fact.
_RANK = {"blocked": 5, "failed": 4, "done": 3, "running": 2, "skipped": 1, "pending": 0}


def _clip(value: object, limit: int = _PREVIEW) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more chars]"


def _ms(a: str | None, b: str | None) -> int | None:
    try:
        if a and b:
            delta = datetime.fromisoformat(b) - datetime.fromisoformat(a)
            return int(delta.total_seconds() * 1000)
    except ValueError:
        pass
    return None


def _new(node_id: str) -> dict:
    return {
        "node_id": node_id, "agent_id": None, "type": None, "role": None, "model": None,
        "status": "pending", "error": None,
        "started_at": None, "ended_at": None, "duration_ms": None,
        "depends_on": [], "tools_declared": [], "tool_calls": [], "tools_blocked": [],
        "input": None, "output": None,
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        "attempts": 0, "repairs": 0, "retries": 0, "used_fallback": False,
    }


def new_step(node_id: str, **fields) -> dict:
    """A step built directly (the chat turn does this from what it just did)."""
    step = _new(node_id)
    step.update(fields)
    step["agent_id"] = step["agent_id"] or node_id
    step["cost_usd"] = round(step["cost_usd"], 8)
    if "tokens" not in fields:
        step["tokens"] = step["tokens_in"] + step["tokens_out"]
    return step


def _set_status(step: dict, status: str) -> None:
    if _RANK[status] >= _RANK[step["status"]]:
        step["status"] = status


def build_trace(events: list[Event], graph: dict | None = None) -> dict:
    """`graph` (a stored GraphSpec dict) is optional enrichment: it supplies the
    node type, the agent's role and what the node depends on. Without it the
    trace still works from the events alone."""
    nodes: dict[str, dict] = {}
    order: list[str] = []
    spec_nodes = {n["id"]: n for n in (graph or {}).get("nodes", [])}
    parents: dict[str, list[str]] = {}
    for e in (graph or {}).get("edges", []):
        parents.setdefault(e["to_node"], []).append(e["from_node"])

    def step(node_id: str) -> dict:
        if node_id not in nodes:
            nodes[node_id] = _new(node_id)
            order.append(node_id)
            spec = spec_nodes.get(node_id)
            if spec:
                nodes[node_id]["type"] = spec.get("type")
                nodes[node_id]["depends_on"] = parents.get(node_id, [])
                agent = spec.get("agent") or {}
                nodes[node_id]["role"] = agent.get("role")
                nodes[node_id]["tools_declared"] = agent.get("tools") or []
        return nodes[node_id]

    run_start = run_end = None
    wall_ms = None
    for e in events:
        p = e.payload or {}
        t = e.event_type
        if t == EventType.RUN_START:
            run_start = e.ts
        elif t == EventType.RUN_END:
            run_end, wall_ms = e.ts, p.get("wall_clock_ms")
        if not e.node_id:
            continue
        s = step(e.node_id)
        s["agent_id"] = s["agent_id"] or e.agent_id

        if t == EventType.NODE_START:
            s["started_at"] = s["started_at"] or e.ts
            _set_status(s, "running")
            s["model"] = p.get("model") or s["model"]
            s["role"] = p.get("role") or s["role"]
            s["type"] = s["type"] or p.get("type")
        elif t == EventType.NODE_END:
            s["ended_at"] = e.ts
            s["duration_ms"] = e.latency_ms if e.latency_ms is not None else _ms(
                s["started_at"], e.ts)
            if p.get("blocked"):
                _set_status(s, "blocked")
            elif p.get("ok") is False:
                _set_status(s, "failed")
            else:
                _set_status(s, "done")
            if p.get("repair_attempts"):
                s["repairs"] = max(s["repairs"], p["repair_attempts"])
        elif t == EventType.TOOL_RESULT and "tool" not in p:
            # A model completion (guarded tool calls carry a "tool" key instead).
            s["attempts"] += 1
            s["model"] = p.get("model") or s["model"]
            s["tokens_in"] += e.tokens_in
            s["tokens_out"] += e.tokens_out
            s["cost_usd"] += e.cost_usd
            s["used_fallback"] = s["used_fallback"] or bool(p.get("used_fallback"))
            if s["input"] is None and p.get("input"):
                s["input"] = _clip(p["input"])
            if p.get("text"):
                s["output"] = s["output"] or _clip(p["text"])
        elif t == EventType.HANDOFF_VALIDATED:
            # The validated, schema-conformant output beats the raw completion.
            s["output"] = _clip(p.get("payload"))
            s["repairs"] = max(s["repairs"], int(p.get("attempt") or 0))
            if s["status"] == "pending":
                _set_status(s, "done")
        elif t == EventType.REPAIR_ATTEMPT:
            s["repairs"] = max(s["repairs"], int(p.get("attempt") or 0))
        elif t == EventType.RETRY:
            s["retries"] += 1
        elif t == EventType.TOOL_CALL:
            s["tool_calls"].append({"tool": p.get("tool"), "arguments": _clip(
                p.get("arguments"), 300)})
        elif t == EventType.TOOL_BLOCKED:
            s["tools_blocked"].append(p.get("attempted_tool"))
            _set_status(s, "blocked")
        elif t == EventType.INJECTION_BLOCKED:
            _set_status(s, "blocked")
            s["error"] = "handoff quarantined: " + ", ".join(p.get("rules") or ["injection"])
        elif t in (EventType.BRANCH_FAILED, EventType.BUDGET_EXCEEDED):
            _set_status(s, "failed")
            s["error"] = (
                p.get("error") or p.get("reason")
                or (f"spent {p.get('spent')}/{p.get('budget')} tokens" if p.get("spent") else None)
                or "failed"
            )
            if p.get("reason") == "timeout_s":
                s["error"] = f"timed out after {p.get('timeout_s')}s"
        elif t == EventType.APPROVAL_REQUESTED:
            _set_status(s, "running")
        elif t == EventType.APPROVAL_GRANTED:
            _set_status(s, "done")
        elif t == EventType.COMPENSATE:
            _set_status(s, "done")
            s["error"] = f"compensating for '{p.get('compensates')}'"

    steps = [nodes[i] for i in order]
    for s in steps:
        s["cost_usd"] = round(s["cost_usd"], 8)
        s["tokens"] = s["tokens_in"] + s["tokens_out"]
        if s["duration_ms"] is None:
            s["duration_ms"] = _ms(s["started_at"], s["ended_at"])

    counts: dict[str, int] = {}
    for s in steps:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {
        "steps": steps,
        "summary": {
            "steps": len(steps),
            "by_status": counts,
            "tokens": sum(s["tokens"] for s in steps),
            "cost_usd": round(sum(s["cost_usd"] for s in steps), 8),
            "wall_clock_ms": wall_ms if wall_ms is not None else _ms(run_start, run_end),
            "finished": run_end is not None,
        },
    }
